<?php
/*
 * FPP's own HTTP API on this player, reached on the loopback address (Apache lets local
 * requests through without the UI password; etc/apache2.site in FPP 10.4). Only documented
 * routes are used: GET /api/fppd/status and POST /api/playlists/stop.
 * FK_FPP_API overrides the base URL for tests.
 */

if (defined('FK_PLUGIN_FPP')) {
    return;
}
define('FK_PLUGIN_FPP', 1);

require_once __DIR__ . '/common.php';

function fk_fpp_base(): string
{
    $env = getenv('FK_FPP_API');
    return (is_string($env) && $env !== '') ? rtrim($env, '/') : 'http://127.0.0.1';
}

/** array(status, body) or null when FPP did not answer. */
function fk_fpp_request(string $method, string $path, float $timeout = 3.0): ?array
{
    $context = stream_context_create(array(
        'http' => array(
            'method' => $method,
            'timeout' => $timeout,
            'ignore_errors' => true,
            'header' => "Accept: application/json\r\n",
            'content' => '',
        ),
    ));
    $body = @file_get_contents(fk_fpp_base() . $path, false, $context);
    if ($body === false) {
        return null;
    }
    $status = 0;
    $headers = function_exists('http_get_last_response_headers') ? http_get_last_response_headers()
        : (get_defined_vars()['http_response_header'] ?? array());
    if (is_array($headers) && isset($headers[0]) && preg_match('/^HTTP\/\S+\s+(\d{3})/', $headers[0], $m)) {
        $status = (int)$m[1];
    }
    return array($status, $body);
}

/**
 * The player state: array('status_name' => 'idle'|'playing'|..., 'current_playlist' => string|null,
 * 'seconds_played' => float|null), or null when fppd does not answer.
 */
function fk_fpp_status(): ?array
{
    $reply = fk_fpp_request('GET', '/api/fppd/status', 2.0);
    if ($reply === null || $reply[0] >= 400) {
        return null;
    }
    $data = json_decode($reply[1], true);
    if (!is_array($data)) {
        return null;
    }
    $playlist = null;
    if (isset($data['current_playlist']['playlist']) && is_string($data['current_playlist']['playlist'])
        && $data['current_playlist']['playlist'] !== '') {
        $playlist = $data['current_playlist']['playlist'];
    }
    return array(
        'status_name' => isset($data['status_name']) && is_string($data['status_name']) ? $data['status_name'] : null,
        'current_playlist' => $playlist,
        'seconds_played' => isset($data['seconds_played']) && is_numeric($data['seconds_played']) ? (float)$data['seconds_played'] : null,
        'mode_name' => isset($data['mode_name']) && is_string($data['mode_name']) ? $data['mode_name'] : null,
    );
}

function fk_fpp_stop_playback(): bool
{
    $reply = fk_fpp_request('POST', '/api/playlists/stop', 3.0);
    return $reply !== null && $reply[0] > 0 && $reply[0] < 400;
}
