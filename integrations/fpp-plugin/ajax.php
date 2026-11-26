<?php
/*
 * Back end of the settings page (setup.php), reached as
 *   plugin.php?plugin=fpp-plugin-FacadeKit&page=ajax.php&nopage=1
 * inside FPP's web UI, so it has the same access rule as the rest of FPP's UI (FPP's UI
 * password when that is on). It never returns a projector password or the token hash.
 *
 * Cross-site requests are refused: only POST with the header "X-FK-Request: 1" is accepted
 * (a custom header cannot be sent cross-site without a CORS preflight, which FPP does not
 * allow here), and an Origin header, when present, must name this host.
 */

require_once __DIR__ . '/lib/api_core.php';

function fk_ajax_reply(int $status, array $payload): void
{
    while (ob_get_level() > 0) {
        ob_end_clean();
    }
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode($payload, JSON_UNESCAPED_SLASHES);
    exit;
}

function fk_ajax_fail(string $message, int $status = 400): void
{
    fk_ajax_reply($status, array('ok' => false, 'error' => $message));
}

function fk_ajax_same_origin(): bool
{
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST' || ($_SERVER['HTTP_X_FK_REQUEST'] ?? '') !== '1') {
        return false;
    }
    if (isset($_SERVER['HTTP_ORIGIN']) && $_SERVER['HTTP_ORIGIN'] !== '') {
        $origin = parse_url((string)$_SERVER['HTTP_ORIGIN'], PHP_URL_HOST);
        $host = (string)($_SERVER['HTTP_HOST'] ?? '');
        $host = preg_replace('/:\d+$/', '', $host);
        if (is_string($host) && strpos($host, '[') === 0) {
            $host = trim($host, '[]');
        }
        if (!is_string($origin) || strcasecmp(trim($origin, '[]'), (string)$host) !== 0) {
            return false;
        }
    }
    return true;
}

function fk_ajax_projector_input(array $in, ?array $existing, array $config): array
{
    $name = trim((string)($in['name'] ?? ''));
    $host = trim((string)($in['host'] ?? ''));
    $port = (int)($in['port'] ?? FK_DEFAULT_PORT);
    $input = trim((string)($in['input'] ?? ''));
    if (!fk_valid_name($name)) {
        fk_ajax_fail('Give the projector a name (up to 64 characters, no quotes or < >).');
    }
    if (!fk_valid_host($host)) {
        fk_ajax_fail('The address must be an IP address such as 192.168.50.10 or a host name.');
    }
    if ($port < 1 || $port > 65535) {
        fk_ajax_fail('The port must be 1 to 65535 (PJLink uses 4352).');
    }
    if (!fk_valid_input($input)) {
        fk_ajax_fail('The input must be empty or a PJLink code such as 31 (HDMI 1).');
    }
    foreach ($config['projectors'] as $other) {
        if (($existing === null || $other['id'] !== $existing['id']) && strcasecmp($other['name'], $name) === 0) {
            fk_ajax_fail('Another projector already has that name.');
        }
    }
    $password = $existing !== null ? $existing['password'] : null;
    $action = (string)($in['password_action'] ?? 'keep');
    if ($action === 'set') {
        $new = (string)($in['password'] ?? '');
        if (!fk_valid_password($new)) {
            fk_ajax_fail('PJLink passwords are 1 to 32 characters without spaces.');
        }
        $password = $new;
    } elseif ($action === 'clear') {
        $password = null;
    }
    return array(
        'id' => $existing !== null ? $existing['id'] : fk_new_id($config),
        'name' => $name,
        'host' => $host,
        'port' => $port,
        'password' => $password,
        'input' => $input,
        'ready_timeout_s' => fk_clamp_timing('ready_timeout_s', $in['ready_timeout_s'] ?? null),
        'cooldown_timeout_s' => fk_clamp_timing('cooldown_timeout_s', $in['cooldown_timeout_s'] ?? null),
        'off_delay_s' => fk_clamp_timing('off_delay_s', $in['off_delay_s'] ?? null),
    );
}

function fk_ajax_check_facadekit(string $url): array
{
    if (!preg_match('~^https?://[^/\s?#@]+(:\d+)?/?$~i', $url)) {
        return array('ok' => false, 'message' => 'Enter an address such as http://192.168.1.20:8137');
    }
    $context = stream_context_create(array('http' => array('timeout' => 3, 'ignore_errors' => true),
        'ssl' => array('verify_peer' => true)));
    $body = @file_get_contents(rtrim($url, '/') . '/api/v1/health', false, $context);
    if ($body === false) {
        return array('ok' => false, 'message' => 'No answer. Is FacadeKit running with --lan on that computer?');
    }
    $data = json_decode($body, true);
    if (is_array($data) && isset($data['version'])) {
        return array('ok' => true, 'message' => 'FacadeKit ' . preg_replace('/[^\w.+-]/', '', (string)$data['version']) . ' answered.');
    }
    return array('ok' => false, 'message' => 'Something answered, but not FacadeKit.');
}

if (!fk_ajax_same_origin()) {
    fk_ajax_fail('Refused: this page only answers the plugin settings page.', 403);
}
$in = json_decode((string)file_get_contents('php://input'), true);
if (!is_array($in) || !isset($in['action']) || !is_string($in['action'])) {
    fk_ajax_fail('Bad request.');
}

try {
    $config = fk_load_config();
    switch ($in['action']) {
        case 'get':
            $token = fk_token_info();
            fk_ajax_reply(200, array(
                'ok' => true,
                'version' => FK_PLUGIN_VERSION,
                'settings' => $config['settings'],
                'projectors' => array_map('fk_public_projector', $config['projectors']),
                'timing' => fk_projector_timing(),
                'token' => array('paired' => $token !== null, 'created_at' => $token['created_at'] ?? null),
            ));
            break;

        case 'save_settings':
            $new = is_array($in['settings'] ?? null) ? $in['settings'] : array();
            foreach (fk_default_settings() as $key => $default) {
                if (array_key_exists($key, $new)) {
                    $config['settings'][$key] = fk_coerce_setting($key, $new[$key], $default);
                }
            }
            $url = trim((string)$config['settings']['facadekit_url']);
            if ($url !== '' && !preg_match('~^https?://[^/\s?#@]+(:\d+)?/?$~i', $url)) {
                fk_ajax_fail('The FacadeKit address must look like http://192.168.1.20:8137');
            }
            $config['settings']['facadekit_url'] = rtrim($url, '/');
            fk_save_config($config);
            fk_log('INFO', 'settings saved');
            fk_ajax_reply(200, array('ok' => true, 'settings' => $config['settings']));
            break;

        case 'save_projector':
            $data = is_array($in['projector'] ?? null) ? $in['projector'] : array();
            $existing = isset($data['id']) && is_string($data['id']) && $data['id'] !== '' ? fk_find_projector($config, $data['id']) : null;
            if (isset($data['id']) && $data['id'] !== '' && $existing === null) {
                fk_ajax_fail('That projector no longer exists; reload the page.');
            }
            if ($existing === null && count($config['projectors']) >= FK_MAX_PROJECTORS) {
                fk_ajax_fail('At most ' . FK_MAX_PROJECTORS . ' projectors.');
            }
            $p = fk_ajax_projector_input($data, $existing, $config);
            $list = array();
            $replaced = false;
            foreach ($config['projectors'] as $old) {
                if ($old['id'] === $p['id']) {
                    $list[] = $p;
                    $replaced = true;
                } else {
                    $list[] = $old;
                }
            }
            if (!$replaced) {
                $list[] = $p;
            }
            $config['projectors'] = $list;
            fk_save_config($config);
            fk_log('INFO', 'projector "' . $p['name'] . '" saved (' . $p['host'] . ':' . $p['port'] . ', password ' . ($p['password'] === null ? 'none' : 'set') . ')');
            fk_ajax_reply(200, array('ok' => true, 'projector' => fk_public_projector($p)));
            break;

        case 'delete_projector':
            $id = (string)($in['id'] ?? '');
            $config['projectors'] = array_values(array_filter($config['projectors'], function ($p) use ($id) {
                return $p['id'] !== $id;
            }));
            fk_save_config($config);
            fk_log('INFO', 'projector ' . $id . ' removed');
            fk_ajax_reply(200, array('ok' => true));
            break;

        case 'test_projector':
            $p = fk_find_projector($config, (string)($in['id'] ?? ''));
            if ($p === null) {
                fk_ajax_fail('Save the projector first.');
            }
            $snap = fk_snapshot($p, $config['settings']);
            fk_record_snapshot($snap);
            fk_log($snap['ok'] ? 'INFO' : 'WARN', 'test ' . $p['name'] . ': ' . ($snap['ok'] ? $snap['power'] : $snap['error']));
            fk_ajax_reply(200, array('ok' => true, 'snapshot' => $snap));
            break;

        case 'status':
            $live = !empty($in['live']);
            $projectors = $live ? fk_status_live($config['projectors']) : fk_status_cached($config);
            fk_ajax_reply(200, array('ok' => true, 'projectors' => $projectors, 'health' => fk_state()['health'],
                'player' => fk_fpp_status()));
            break;

        case 'power':
            $action = (string)($in['power'] ?? '');
            if ($action !== 'on' && $action !== 'off') {
                fk_ajax_fail('Unknown action.');
            }
            $results = fk_power($action, $config['projectors'], !empty($in['wait']), null);
            fk_ajax_reply(200, array('ok' => true, 'results' => $results));
            break;

        case 'clear_health':
            fk_set_health(true, null, null);
            fk_ajax_reply(200, array('ok' => true));
            break;

        case 'token_create':
            fk_ajax_reply(200, array('ok' => true, 'token' => fk_token_create()));
            break;

        case 'token_revoke':
            fk_token_revoke();
            fk_ajax_reply(200, array('ok' => true));
            break;

        case 'shows':
            fk_ajax_reply(200, array('ok' => true, 'shows' => fk_shows_list()));
            break;

        case 'show_delete':
            fk_shows_delete((string)($in['playlist'] ?? ''));
            fk_ajax_reply(200, array('ok' => true));
            break;

        case 'log':
            fk_ajax_reply(200, array('ok' => true, 'lines' => fk_log_tail(400), 'file' => basename(fk_log_file())));
            break;

        case 'check_facadekit':
            fk_ajax_reply(200, fk_ajax_check_facadekit((string)$config['settings']['facadekit_url']));
            break;
    }
    fk_ajax_fail('Unknown action.');
} catch (Throwable $e) {
    fk_log('ERROR', 'settings page: ' . get_class($e) . ': ' . $e->getMessage());
    fk_ajax_fail('Internal error; see the plugin log.', 500);
}
