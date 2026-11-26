<?php
/*
 * Command-line entry point. Called by the FPP command scripts (commands/*.sh), by
 * callbacks.sh, and by itself for background workers. Arguments arrive as separate argv
 * entries: no value from a playlist, the scheduler or the web page ever passes through a
 * shell.
 *
 *   cli.php on [wait] [timeout_s] [projectors]     FPP command "FacadeKit Projectors On"
 *   cli.php off [wait] [projectors]                FPP command "FacadeKit Projectors Off"
 *   cli.php input <code> [projectors]              FPP command "FacadeKit Select Input"
 *   cli.php status                                 FPP command "FacadeKit Status Snapshot"
 *   cli.php playlist-event <json>                  callbacks.sh --type playlist
 *   cli.php worker <on|off|input> <id> <seq> <ready_s> <input|-> <delay_s> [retry_token]
 *   cli.php query <id>                             one projector snapshot as JSON
 *   cli.php guard <playlist>                       fault guard loop
 */

if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit(1);
}

require_once __DIR__ . '/common.php';
require_once __DIR__ . '/power.php';

function fk_cli_print($data): void
{
    echo json_encode($data, JSON_UNESCAPED_SLASHES) . "\n";
}

function fk_cli_main(array $argv): int
{
    $cmd = isset($argv[1]) ? $argv[1] : '';
    $args = array_slice($argv, 2);
    $config = fk_load_config();
    $settings = $config['settings'];

    switch ($cmd) {
        case 'on':
        case 'off':
            if (!$config['projectors']) {
                fk_log('INFO', 'FacadeKit Projectors ' . ucfirst($cmd) . ': no projectors set up, nothing to do');
                return 0;
            }
            $wait = isset($args[0]) && fk_bool($args[0]);
            $timeout = null;
            $selectorIndex = 1;
            if ($cmd === 'on') {
                if (isset($args[1]) && is_numeric($args[1]) && (int)$args[1] > 0) {
                    $timeout = min(900, (int)$args[1]);
                }
                $selectorIndex = 2;
            }
            $selector = isset($args[$selectorIndex]) ? $args[$selectorIndex] : 'all';
            $selected = fk_select_projectors($config, $selector);
            if (!$selected) {
                fk_log('WARN', 'no projector matches "' . $selector . '"');
                return 1;
            }
            $results = fk_power($cmd, $selected, $wait, $timeout);
            foreach ($results as $r) {
                fk_log($r['ok'] ? 'INFO' : 'WARN', $r['name'] . ': ' . $r['message']);
            }
            fk_cli_print($results);
            return 0;

        case 'input':
            $code = isset($args[0]) ? substr(trim($args[0]), 0, 2) : '';
            if (!fk_valid_input($code) || $code === '') {
                fk_log('WARN', 'Select Input: not a PJLink input code');
                return 1;
            }
            $selected = fk_select_projectors($config, isset($args[1]) ? $args[1] : 'all');
            $jobs = array();
            foreach ($selected as $p) {
                $jobs[] = array('id' => $p['id'], 'name' => $p['name'], 'args' => array('worker', 'input', $p['id'], '0', '0', $code, '0'));
            }
            $results = $jobs ? fk_run_parallel($jobs, 30) : array();
            foreach ($results as $r) {
                fk_log($r['ok'] ? 'INFO' : 'WARN', $r['name'] . ': ' . $r['message']);
            }
            fk_cli_print($results);
            return 0;

        case 'status':
            $snaps = fk_status_live($config['projectors']);
            foreach ($snaps as $s) {
                $line = $s['name'] . ': ' . ($s['power'] ?? 'unknown');
                if (!empty($s['lamps'])) {
                    $line .= ', ' . $s['lamps'][0]['hours'] . ' h';
                }
                if (!empty($s['error'])) {
                    $line .= ', ' . $s['error'];
                }
                fk_log('INFO', 'status ' . $line);
            }
            fk_cli_print($snaps);
            return 0;

        case 'playlist-event':
            $data = isset($args[0]) ? json_decode($args[0], true) : null;
            if (is_array($data)) {
                fk_playlist_event($data);
            }
            return 0;

        case 'query':
            $p = fk_find_projector($config, isset($args[0]) ? $args[0] : '');
            if ($p === null) {
                fk_cli_print(array('id' => $args[0] ?? '', 'ok' => false, 'power' => 'unknown', 'error' => 'unknown projector'));
                return 1;
            }
            $snap = fk_snapshot($p, $settings);
            fk_record_snapshot($snap);
            fk_cli_print($snap);
            return 0;

        case 'worker':
            return fk_cli_worker($config, $args);

        case 'guard':
            fk_guard(isset($args[0]) ? $args[0] : '');
            return 0;
    }
    fwrite(STDERR, "usage: cli.php on|off|input|status|playlist-event|query|worker|guard ...\n");
    return 2;
}

function fk_cli_worker(array $config, array $args): int
{
    list($action, $id, $seq, $ready, $input, $delay, $token) = array_pad($args, 7, '');
    $p = fk_find_projector($config, $id);
    if ($p === null || !in_array($action, array('on', 'off', 'input'), true)) {
        fk_cli_print(array('id' => $id, 'name' => $id, 'ok' => false, 'state' => 'error', 'message' => 'unknown projector or action'));
        return 1;
    }
    $settings = $config['settings'];
    $seq = (int)$seq;
    $input = ($input === '-') ? '' : $input;
    if (!fk_valid_input($input)) {
        $input = '';
    }
    $lock = null;
    $until = microtime(true) + 30;
    // One worker per projector at a time; an older one notices the newer sequence and stops.
    while (($lock = fk_try_lock('projector-' . $p['id'])) === null) {
        if (microtime(true) > $until || ($seq > 0 && !fk_intent_current($p['id'], $seq))) {
            fk_cli_print(fk_result($p, true, 'superseded', 'another request is in progress'));
            return 0;
        }
        fk_sleep_s(0.5);
    }
    try {
        if ($action === 'off' && (int)$delay > 0) {
            $early = fk_wait_before_off($p, $settings, $seq, (int)$delay);
            if ($early !== null) {
                fk_log('INFO', $p['name'] . ': ' . $early['message']);
                fk_cli_print($early);
                return 0;
            }
        }
        if ($action === 'on') {
            $precheck = null;
            if ($token !== '') { // the automatic thermal retry: valid only while nothing cancelled it
                $precheck = function () use ($p, $seq, $token) {
                    return ($seq <= 0 || fk_intent_current($p['id'], $seq)) && fk_recovery_retry_valid($p['id'], $token);
                };
            }
            $result = fk_sequence_on($p, $settings, $seq, max(0, (int)$ready), $input, $precheck);
            if ($token !== '') {
                fk_recovery_retry_report($p['id'], $token, $result);
            }
        } elseif ($action === 'off') {
            $result = fk_sequence_off($p, $settings, $seq);
        } else {
            $result = fk_sequence_input($p, $settings, $input);
        }
        fk_log($result['ok'] ? 'INFO' : 'WARN', $p['name'] . ' (' . $action . '): ' . $result['message']);
        // Refresh the stored state after acting, so the status panel follows.
        fk_record_snapshot(fk_snapshot($p, $settings));
        fk_cli_print($result);
        return 0;
    } finally {
        fk_unlock($lock);
    }
}

exit(fk_cli_main($argv));
