<?php
/*
 * The plugin's REST API, independent of FPP's router so that it can be tested with
 * `php -S` (dev/router.php). api.php maps FPP's /api/plugin/fpp-plugin-FacadeKit/<route>
 * onto fk_api_handle().
 *
 * Security:
 * - Callers must come from a loopback or private address (the Pi's wired projector network
 *   and the home Wi-Fi); anything else gets 403.
 * - Every route except "version" and "projector-names" needs the pairing token, sent as
 *   "X-FacadeKit-Token: <token>" (preferred: FPP's optional UI password already uses the
 *   Authorization header for HTTP Basic) or "Authorization: Bearer <token>".
 * - Only a SHA-256 hash of the token is stored; the comparison is constant-time
 *   (hash_equals). Before a token exists, the protected routes answer 403 "not paired".
 */

if (defined('FK_PLUGIN_API_CORE')) {
    return;
}
define('FK_PLUGIN_API_CORE', 1);

require_once __DIR__ . '/common.php';
require_once __DIR__ . '/power.php';
require_once __DIR__ . '/shows.php';
require_once __DIR__ . '/fpp.php';

// -- token ------------------------------------------------------------------------------

/** Create a new pairing token, store its hash, and return the token (shown once). */
function fk_token_create(): string
{
    $token = 'fkp_' . bin2hex(random_bytes(24));
    fk_write_json('token.json', array('sha256' => hash('sha256', $token), 'created_at' => fk_now_iso()));
    fk_log('INFO', 'new pairing token created (the previous one no longer works)');
    return $token;
}

function fk_token_revoke(): void
{
    $path = fk_data_dir() . '/token.json';
    if (is_file($path)) {
        @unlink($path);
    }
    fk_log('INFO', 'pairing token revoked');
}

function fk_token_info(): ?array
{
    $data = fk_read_json('token.json', null);
    if (!is_array($data) || !isset($data['sha256']) || !is_string($data['sha256']) || strlen($data['sha256']) !== 64) {
        return null;
    }
    return $data;
}

function fk_token_matches(string $presented): bool
{
    $info = fk_token_info();
    if ($info === null || $presented === '' || strlen($presented) > 256) {
        return false;
    }
    return hash_equals($info['sha256'], hash('sha256', $presented));
}

function fk_presented_token(array $server): string
{
    if (isset($server['HTTP_X_FACADEKIT_TOKEN']) && is_string($server['HTTP_X_FACADEKIT_TOKEN'])) {
        return trim($server['HTTP_X_FACADEKIT_TOKEN']);
    }
    foreach (array('HTTP_AUTHORIZATION', 'REDIRECT_HTTP_AUTHORIZATION') as $key) {
        if (isset($server[$key]) && is_string($server[$key]) && preg_match('/^Bearer\s+(\S+)$/i', trim($server[$key]), $m)) {
            return $m[1];
        }
    }
    return '';
}

// -- routing ----------------------------------------------------------------------------

function fk_api_error(int $status, string $code, string $message): array
{
    return array($status, array('ok' => false, 'error' => array('code' => $code, 'message' => $message)));
}

/**
 * Handle one request. $route is the path after /api/plugin/fpp-plugin-FacadeKit/ (already
 * URL-decoded). Returns array(http_status, json_payload).
 */
function fk_api_handle(string $method, string $route, array $server, string $body): array
{
    $route = trim($route, '/');
    $remote = isset($server['REMOTE_ADDR']) && is_string($server['REMOTE_ADDR']) ? $server['REMOTE_ADDR'] : '';
    $serverAddr = isset($server['SERVER_ADDR']) && is_string($server['SERVER_ADDR']) ? $server['SERVER_ADDR'] : '';
    if (!fk_is_private_ip($remote) && !fk_same_link($remote, $serverAddr)) {
        return fk_api_error(403, 'not_local', 'This API answers the local network only.');
    }
    if ($method === 'GET' && $route === 'version') {
        $config = fk_load_config();
        return array(200, array(
            'ok' => true,
            'plugin' => FK_REPO,
            'version' => FK_PLUGIN_VERSION,
            'api' => FK_API_VERSION,
            'paired' => fk_token_info() !== null,
            'configured' => count($config['projectors']) > 0,
        ));
    }
    if ($method === 'GET' && $route === 'projector-names') {
        // For the FPP command editor's drop-down (contentListUrl); names only.
        $names = array('all');
        foreach (fk_load_config()['projectors'] as $p) {
            $names[] = $p['name'];
        }
        return array(200, $names);
    }

    if (fk_token_info() === null) {
        return fk_api_error(403, 'not_paired', 'Create a pairing token on the plugin page in FPP first.');
    }
    if (!fk_token_matches(fk_presented_token($server))) {
        usleep(300000); // slow down guessing
        fk_log('WARN', 'API request with a missing or wrong token from ' . $remote);
        return fk_api_error(401, 'bad_token', 'Missing or wrong pairing token.');
    }

    $json = array();
    if ($body !== '') {
        $json = json_decode($body, true);
        if (!is_array($json)) {
            return fk_api_error(400, 'bad_json', 'The body must be a JSON object.');
        }
    }

    if ($method === 'GET' && $route === 'status') {
        $config = fk_load_config();
        $live = !isset($server['QUERY_STRING']) || !preg_match('/(^|&)cached=1(&|$)/', (string)$server['QUERY_STRING']);
        $projectors = $live ? fk_status_live($config['projectors']) : fk_status_cached($config);
        return array(200, array('ok' => true, 'live' => $live, 'projectors' => $projectors,
            'health' => fk_state()['health'], 'checked_at' => fk_now_iso()));
    }
    if ($method === 'GET' && $route === 'health') {
        return array(200, fk_health(fk_load_config()));
    }
    if ($method === 'POST' && ($route === 'projectors/on' || $route === 'projectors/off')) {
        $action = substr($route, strlen('projectors/'));
        $config = fk_load_config();
        if (!$config['projectors']) {
            return fk_api_error(409, 'not_configured', 'No projectors are set up in the plugin yet.');
        }
        $selector = isset($json['projectors']) && is_string($json['projectors']) ? $json['projectors'] : 'all';
        $selected = fk_select_projectors($config, $selector);
        if (!$selected) {
            return fk_api_error(400, 'no_match', 'No projector matches ' . $selector . '.');
        }
        $wait = isset($json['wait']) && fk_bool($json['wait']);
        $timeout = null;
        if (isset($json['timeout_s'])) {
            if (!is_int($json['timeout_s']) || $json['timeout_s'] < 0 || $json['timeout_s'] > 900) {
                return fk_api_error(400, 'bad_timeout', 'timeout_s must be a whole number from 0 to 900.');
            }
            $timeout = $json['timeout_s'];
        }
        $results = fk_power($action, $selected, $wait, $timeout);
        $ok = true;
        foreach ($results as $r) {
            $ok = $ok && !empty($r['ok']);
        }
        return array(200, array('ok' => $ok, 'action' => $action, 'waited' => $wait, 'results' => $results));
    }
    if ($method === 'GET' && $route === 'shows') {
        return array(200, array('ok' => true, 'shows' => fk_shows_list()));
    }
    if ($method === 'POST' && $route === 'shows') {
        list($manifest, $error) = fk_shows_validate($json);
        if ($manifest === null) {
            return fk_api_error(400, 'bad_manifest', $error);
        }
        fk_shows_upsert($manifest);
        fk_log('INFO', 'show registered: playlist "' . $manifest['playlist'] . '", video ' . $manifest['video']);
        return array(200, array('ok' => true, 'show' => $manifest));
    }
    if ($method === 'DELETE' && strpos($route, 'shows/') === 0) {
        $playlist = substr($route, strlen('shows/'));
        if (!fk_valid_playlist_name($playlist)) {
            return fk_api_error(400, 'bad_name', 'Not a playlist name.');
        }
        if (!fk_shows_delete($playlist)) {
            return fk_api_error(404, 'not_found', 'No show is registered for that playlist.');
        }
        return array(200, array('ok' => true));
    }
    return fk_api_error(404, 'no_route', 'Unknown route.');
}

/** Projectors (cached state), the FPP player and the published shows in one answer. */
function fk_health(array $config): array
{
    $state = fk_state();
    $projectors = fk_status_cached($config);
    $summary = array('total' => count($projectors), 'on' => 0, 'off' => 0, 'transition' => 0, 'error' => 0, 'unknown' => 0);
    foreach ($projectors as $p) {
        $power = isset($p['power']) ? $p['power'] : 'unknown';
        if (!empty($p['error'])) {
            $summary['error']++;
        } elseif ($power === 'on') {
            $summary['on']++;
        } elseif ($power === 'standby') {
            $summary['off']++;
        } elseif ($power === 'warmup' || $power === 'cooling' || $power === 'busy') {
            $summary['transition']++;
        } else {
            $summary['unknown']++;
        }
    }
    $player = fk_fpp_status();
    $healthy = !empty($state['health']['healthy']) && $summary['error'] === 0;
    return array(
        'ok' => true,
        'plugin' => array('name' => FK_REPO, 'version' => FK_PLUGIN_VERSION, 'api' => FK_API_VERSION),
        'healthy' => $healthy,
        'health' => $state['health'],
        'projectors' => $projectors,
        'summary' => $summary,
        'player' => $player,
        'shows' => fk_shows_list(),
        'settings' => array(
            'auto_on' => $config['settings']['auto_on'],
            'auto_off' => $config['settings']['auto_off'],
            'auto_scope' => $config['settings']['auto_scope'],
            'guard_enabled' => $config['settings']['guard_enabled'],
            'guard_stop_playback' => $config['settings']['guard_stop_playback'],
        ),
        'checked_at' => fk_now_iso(),
    );
}
