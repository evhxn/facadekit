<?php
/*
 * Power sequences, status snapshots, workers in parallel, automatic power and the fault guard.
 *
 * Rules (docs/20-fpp-plugin.md):
 * - Every decision follows what the projector itself reports in POWR. Nothing assumes a lamp:
 *   a laser projector that is "on" straight after POWR 1 is simply done. A wait happens only
 *   while the projector reports warm-up (before choosing the input) or cooling (before
 *   power-on), and only up to that projector's ready_timeout_s / cooldown_timeout_s.
 * - POWR 1 is never sent while the projector reports cooling.
 * - Each projector runs in its own worker process: one projector that does not answer cannot
 *   hold up or break another.
 * - The newest request wins: each request takes a sequence number per projector, and an older
 *   worker that sees a newer number stops (a show starting cancels a pending power-off).
 */

if (defined('FK_PLUGIN_POWER')) {
    return;
}
define('FK_PLUGIN_POWER', 1);

require_once __DIR__ . '/common.php';
require_once __DIR__ . '/pjlink.php';
require_once __DIR__ . '/fpp.php';
require_once __DIR__ . '/shows.php';

const FK_POLL_S = 2;

// Runs "$0" "$@" in the background with no terminal and returns at once. The script text is
// constant: every value reaches the worker as its own argument, never through the shell.
// Descriptors 3-9 are closed so a worker started from the web server (php-fpm) cannot hold
// the request's connection open while it runs.
const FK_DETACH_SH = '"$0" "$@" </dev/null >/dev/null 2>&1 3>&- 4>&- 5>&- 6>&- 7>&- 8>&- 9>&- &';

function fk_client(array $p, array $settings): FkPjlink
{
    return new FkPjlink($p['host'], (int)$p['port'], $p['password'],
        (float)$settings['connect_timeout_s'], (float)$settings['io_timeout_s']);
}

function fk_sleep_s(float $seconds): void
{
    $env = getenv('FK_SLEEP_SCALE'); // tests shorten waits
    $scale = (is_string($env) && is_numeric($env)) ? (float)$env : 1.0;
    usleep((int)max(0, $seconds * $scale * 1000000));
}

// -- state and intents ------------------------------------------------------------------

function fk_state(): array
{
    $state = fk_read_json('state.json', array());
    if (!is_array($state)) {
        $state = array();
    }
    if (!isset($state['projectors']) || !is_array($state['projectors'])) {
        $state['projectors'] = array();
    }
    if (!isset($state['health']) || !is_array($state['health'])) {
        $state['health'] = array('healthy' => true, 'reason' => null, 'projector' => null, 'since' => null);
    }
    return $state;
}

function fk_state_update(callable $change): array
{
    return fk_with_lock('state', function () use ($change) {
        $state = fk_state();
        $state = $change($state);
        $state['updated_at'] = fk_now_iso();
        fk_write_json('state.json', $state);
        return $state;
    });
}

function fk_record_snapshot(array $snapshot): void
{
    fk_state_update(function (array $state) use ($snapshot) {
        $old = isset($state['projectors'][$snapshot['id']]) ? $state['projectors'][$snapshot['id']] : array();
        $state['projectors'][$snapshot['id']] = array_merge($old, $snapshot);
        return $state;
    });
}

function fk_set_health(bool $healthy, ?string $reason, ?string $projector): void
{
    fk_state_update(function (array $state) use ($healthy, $reason, $projector) {
        $was = !empty($state['health']['healthy']);
        $state['health'] = array(
            'healthy' => $healthy,
            'reason' => $reason,
            'projector' => $projector,
            'since' => ($was === $healthy && isset($state['health']['since'])) ? $state['health']['since'] : fk_now_iso(),
        );
        return $state;
    });
}

/** Take a new sequence number for $id and record what is wanted. */
function fk_new_intent(string $id, string $action): int
{
    return fk_with_lock('intents', function () use ($id, $action) {
        $intents = fk_read_json('intents.json', array());
        if (!is_array($intents)) {
            $intents = array();
        }
        $seq = isset($intents['_seq']) ? (int)$intents['_seq'] + 1 : 1;
        $intents['_seq'] = $seq;
        $intents[$id] = array('action' => $action, 'seq' => $seq, 'at' => fk_now_iso());
        fk_write_json('intents.json', $intents);
        return $seq;
    });
}

function fk_intent_current(string $id, int $seq): bool
{
    $intents = fk_read_json('intents.json', array());
    return is_array($intents) && isset($intents[$id]['seq']) && (int)$intents[$id]['seq'] === $seq;
}

// -- snapshots --------------------------------------------------------------------------

/** Read everything class 1 offers. Only a failed POWR makes the snapshot "not ok". */
function fk_snapshot(array $p, array $settings): array
{
    $client = fk_client($p, $settings);
    $snap = array(
        'id' => $p['id'], 'name' => $p['name'], 'host' => $p['host'],
        'ok' => false, 'power' => 'unknown', 'input' => null, 'errors' => null, 'lamps' => null,
        'reported_name' => null, 'manufacturer' => null, 'model' => null, 'pjlink_class' => null,
        'error' => null, 'error_kind' => null, 'checked_at' => fk_now_iso(),
    );
    try {
        $snap['power'] = $client->power();
        $snap['ok'] = true;
    } catch (FkPjlinkException $e) {
        if ($e->kind === 'unavailable') {
            $snap['power'] = 'busy';
            $snap['ok'] = true;
        } else {
            $snap['error'] = $e->getMessage();
            $snap['error_kind'] = $e->kind;
            return $snap;
        }
    }
    $optional = array(
        'input' => function () use ($client) { return $client->input(); },
        'errors' => function () use ($client) { return $client->errorStatus(); },
        'lamps' => function () use ($client) { return $client->lampHours(); },
        'reported_name' => function () use ($client) { return $client->name(); },
        'manufacturer' => function () use ($client) { return $client->manufacturer(); },
        'model' => function () use ($client) { return $client->model(); },
        'pjlink_class' => function () use ($client) { return $client->pjlinkClass(); },
    );
    foreach ($optional as $key => $read) {
        try {
            $snap[$key] = $read();
        } catch (FkPjlinkException $e) {
            if ($e->kind === 'auth' || $e->kind === 'connect' || $e->kind === 'timeout') {
                $snap['error'] = $e->getMessage();
                $snap['error_kind'] = $e->kind;
                break;
            }
            // ERR1 (not supported, e.g. LAMP on some laser models) or ERR3: leave it empty.
        }
    }
    if (is_array($snap['errors'])) {
        foreach ($snap['errors'] as $field => $level) {
            if ($level === 'error') {
                $snap['error'] = 'the projector reports a ' . $field . ' error';
                $snap['error_kind'] = 'fault';
                break;
            }
        }
    }
    return $snap;
}

// -- sequences (run inside a worker, one projector each) ---------------------------------

function fk_result(array $p, bool $ok, string $state, string $message): array
{
    return array('id' => $p['id'], 'name' => $p['name'], 'ok' => $ok, 'state' => $state, 'message' => $message);
}

/**
 * Switch one projector on and select its input. Returns a result array.
 * $readyTimeout: how long to wait for "on" while the projector reports warm-up.
 */
function fk_sequence_on(array $p, array $settings, int $seq, int $readyTimeout, string $input): array
{
    $client = fk_client($p, $settings);
    $start = microtime(true);
    $readyDeadline = $start + $readyTimeout;
    $coolDeadline = $start + (int)$p['cooldown_timeout_s'];
    $busyDeadline = $start + max($readyTimeout, 10);
    $sent = false;
    while (true) {
        if ($seq > 0 && !fk_intent_current($p['id'], $seq)) {
            return fk_result($p, true, 'superseded', 'a newer request took over');
        }
        try {
            $state = $client->power();
        } catch (FkPjlinkException $e) {
            if ($e->kind === 'unavailable' && microtime(true) < $busyDeadline) {
                fk_sleep_s(FK_POLL_S);
                continue;
            }
            return fk_result($p, false, 'error', $e->getMessage());
        }
        if ($state === 'on') {
            return fk_apply_input($client, $p, $input, $readyDeadline);
        }
        if ($state === 'warmup') {
            if (microtime(true) >= $readyDeadline) {
                return fk_result($p, $readyTimeout === 0 || $sent, 'warmup',
                    'warming up; not ready after ' . $readyTimeout . ' s' . ($input !== '' ? ' (input not selected yet)' : ''));
            }
            fk_sleep_s(FK_POLL_S);
            continue;
        }
        if ($state === 'cooling') {
            // Only because the projector itself says so: never send POWR 1 now.
            if (microtime(true) >= $coolDeadline) {
                return fk_result($p, false, 'cooling',
                    'the projector reports cooling down; not switched on (waited ' . (int)$p['cooldown_timeout_s'] . ' s)');
            }
            fk_sleep_s(FK_POLL_S);
            continue;
        }
        // standby
        if ($sent && microtime(true) >= $readyDeadline) {
            return fk_result($p, false, 'standby', 'switched on, but the projector still reports standby');
        }
        if (!$sent) {
            try {
                $client->powerOn();
                $sent = true;
                fk_log('INFO', $p['name'] . ': power on sent');
            } catch (FkPjlinkException $e) {
                if ($e->kind === 'unavailable' && microtime(true) < $busyDeadline) {
                    fk_sleep_s(FK_POLL_S);
                    continue;
                }
                return fk_result($p, false, 'error', $e->getMessage());
            }
            continue; // ask again at once: a laser projector is usually on already
        }
        fk_sleep_s(1);
    }
}

function fk_apply_input(FkPjlink $client, array $p, string $input, float $deadline): array
{
    if ($input === '') {
        return fk_result($p, true, 'on', 'on');
    }
    while (true) {
        try {
            if ($client->input() === $input) {
                return fk_result($p, true, 'on', 'on, input ' . $input);
            }
            $client->setInput($input);
            fk_log('INFO', $p['name'] . ': input ' . $input . ' selected');
            return fk_result($p, true, 'on', 'on, input ' . $input);
        } catch (FkPjlinkException $e) {
            if ($e->kind === 'unavailable' && microtime(true) < $deadline) {
                fk_sleep_s(FK_POLL_S);
                continue;
            }
            return fk_result($p, false, 'on', 'on, but input ' . $input . ' could not be selected: ' . $e->getMessage());
        }
    }
}

/** Switch one projector off. Waits only while the projector reports warm-up (POWR 0 gives ERR3). */
function fk_sequence_off(array $p, array $settings, int $seq): array
{
    $client = fk_client($p, $settings);
    $deadline = microtime(true) + max((int)$p['ready_timeout_s'], 10);
    while (true) {
        if ($seq > 0 && !fk_intent_current($p['id'], $seq)) {
            return fk_result($p, true, 'superseded', 'a newer request took over');
        }
        try {
            $state = $client->power();
            if ($state === 'standby') {
                return fk_result($p, true, 'standby', 'already off');
            }
            if ($state === 'cooling') {
                return fk_result($p, true, 'cooling', 'already switching off (cooling)');
            }
            $client->powerOff();
            fk_log('INFO', $p['name'] . ': power off sent');
            return fk_result($p, true, 'off', 'off');
        } catch (FkPjlinkException $e) {
            if ($e->kind === 'unavailable' && microtime(true) < $deadline) {
                fk_sleep_s(FK_POLL_S);
                continue;
            }
            return fk_result($p, false, 'error', $e->getMessage());
        }
    }
}

function fk_sequence_input(array $p, array $settings, string $input): array
{
    $client = fk_client($p, $settings);
    return fk_apply_input($client, $p, $input, microtime(true) + max((int)$p['ready_timeout_s'], 10));
}

// -- workers ----------------------------------------------------------------------------

function fk_cli_path(): string
{
    return __DIR__ . '/cli.php';
}

/** Start a worker in the background and return at once. */
function fk_spawn_detached(array $args): bool
{
    $cmd = array_merge(array('/bin/sh', '-c', FK_DETACH_SH, fk_php_binary(), fk_cli_path()), $args);
    $proc = @proc_open($cmd, array(), $pipes);
    if (!is_resource($proc)) {
        fk_log('ERROR', 'could not start a background worker');
        return false;
    }
    proc_close($proc); // the shell exits as soon as the worker is in the background
    return true;
}

/**
 * Run one worker per job in parallel and collect their JSON results.
 * $jobs: list of array('id' => ..., 'name' => ..., 'args' => array(...)).
 */
function fk_run_parallel(array $jobs, float $timeout): array
{
    $running = array();
    foreach ($jobs as $job) {
        $cmd = array_merge(array(fk_php_binary(), fk_cli_path()), $job['args']);
        $spec = array(0 => array('file', '/dev/null', 'r'), 1 => array('pipe', 'w'), 2 => array('file', '/dev/null', 'a'));
        $proc = @proc_open($cmd, $spec, $pipes);
        if (!is_resource($proc)) {
            $running[] = array('job' => $job, 'proc' => null, 'out' => '', 'pipe' => null);
            continue;
        }
        stream_set_blocking($pipes[1], false);
        $running[] = array('job' => $job, 'proc' => $proc, 'out' => '', 'pipe' => $pipes[1]);
    }
    $deadline = microtime(true) + $timeout;
    while (true) {
        $open = array();
        foreach ($running as $i => $r) {
            if ($r['pipe'] !== null && !feof($r['pipe'])) {
                $open[$i] = $r['pipe'];
            }
        }
        if (!$open || microtime(true) >= $deadline) {
            break;
        }
        $read = array_values($open);
        $write = null;
        $except = null;
        $left = max(0.05, min(0.5, $deadline - microtime(true)));
        if (@stream_select($read, $write, $except, 0, (int)($left * 1000000)) === false) {
            usleep(50000);
        }
        foreach ($open as $i => $pipe) {
            $chunk = fread($pipe, 65536);
            if (is_string($chunk) && $chunk !== '') {
                $running[$i]['out'] .= $chunk;
            }
        }
    }
    $results = array();
    foreach ($running as $r) {
        $job = $r['job'];
        if ($r['pipe'] !== null) {
            fclose($r['pipe']);
        }
        if ($r['proc'] !== null) {
            $status = proc_get_status($r['proc']);
            if (!empty($status['running'])) {
                proc_terminate($r['proc']);
            }
            proc_close($r['proc']);
        }
        $decoded = null;
        foreach (array_reverse(preg_split('/\r?\n/', trim($r['out']))) as $line) {
            $decoded = json_decode($line, true);
            if (is_array($decoded)) {
                break;
            }
        }
        $results[] = is_array($decoded) ? $decoded : array(
            'id' => $job['id'], 'name' => $job['name'], 'ok' => false, 'state' => 'error',
            'message' => $r['proc'] === null ? 'could not start the worker' : 'no answer in time',
        );
    }
    return $results;
}

/**
 * Power on (or off) the given projectors. With $wait, block until every worker is done (up to
 * the longest ready timeout); otherwise start the workers in the background and return.
 */
function fk_power(string $action, array $projectors, bool $wait, ?int $timeoutOverride, string $inputOverride = ''): array
{
    $config = fk_load_config();
    $jobs = array();
    $longest = 0;
    foreach ($projectors as $p) {
        $seq = fk_new_intent($p['id'], $action);
        $ready = ($timeoutOverride !== null && $timeoutOverride >= 0) ? $timeoutOverride : (int)$p['ready_timeout_s'];
        $longest = max($longest, $ready + ($action === 'on' ? (int)$p['cooldown_timeout_s'] : 0));
        $input = $inputOverride !== '' ? $inputOverride : $p['input'];
        $args = array('worker', $action, $p['id'], (string)$seq, (string)$ready, $input === '' ? '-' : $input, '0');
        $jobs[] = array('id' => $p['id'], 'name' => $p['name'], 'args' => $args);
    }
    if (!$jobs) {
        return array();
    }
    fk_log('INFO', 'power ' . $action . ' requested for ' . count($jobs) . ' projector(s)' . ($wait ? ', waiting' : ''));
    if (!$wait) {
        $out = array();
        foreach ($jobs as $job) {
            $started = fk_spawn_detached($job['args']);
            $out[] = array('id' => $job['id'], 'name' => $job['name'], 'ok' => $started,
                'state' => $started ? 'requested' : 'error', 'message' => $started ? 'requested' : 'could not start');
        }
        return $out;
    }
    $io = (int)$config['settings']['connect_timeout_s'] + (int)$config['settings']['io_timeout_s'];
    return fk_run_parallel($jobs, $longest + 4 * $io + 5);
}

/** Live snapshots of the given projectors, in parallel; also stored in state.json. */
function fk_status_live(array $projectors): array
{
    $config = fk_load_config();
    $jobs = array();
    foreach ($projectors as $p) {
        $jobs[] = array('id' => $p['id'], 'name' => $p['name'], 'args' => array('query', $p['id']));
    }
    $io = (int)$config['settings']['connect_timeout_s'] + (int)$config['settings']['io_timeout_s'];
    $results = fk_run_parallel($jobs, 9 * $io + 3);
    foreach ($results as $i => $r) {
        if (!isset($r['power'])) { // the worker itself failed: keep a minimal snapshot
            $results[$i] = array('id' => $r['id'], 'name' => $r['name'], 'ok' => false, 'power' => 'unknown',
                'error' => $r['message'] ?? 'no answer', 'error_kind' => 'worker', 'checked_at' => fk_now_iso());
            fk_record_snapshot($results[$i]);
        }
    }
    return $results;
}

/** Stored snapshots, in the order of the projector table, for projectors still configured. */
function fk_status_cached(array $config): array
{
    $state = fk_state();
    $out = array();
    foreach ($config['projectors'] as $p) {
        $snap = isset($state['projectors'][$p['id']]) ? $state['projectors'][$p['id']] : array(
            'id' => $p['id'], 'ok' => false, 'power' => 'unknown', 'error' => null, 'checked_at' => null);
        $snap['name'] = $p['name'];
        $snap['host'] = $p['host'];
        $out[] = $snap;
    }
    return $out;
}

// -- automatic power and the guard -------------------------------------------------------

function fk_in_scope(array $settings, string $playlist): bool
{
    if ($playlist === '') {
        return false;
    }
    if ($settings['auto_scope'] === 'all') {
        return true;
    }
    return isset(fk_shows_playlists()[$playlist]);
}

// -- the show that is running ---------------------------------------------------------------

/**
 * FPP 10.4 sends the real playlist name with the "start" event, but the "stop" event (Stop Now
 * and the natural end alike) carries an empty placeholder playlist: name "", Section "New".
 * So the plugin remembers which in-scope show it started, in active_show.json, and a stop
 * with no usable name ends that show.
 */
function fk_active_show_get(): ?string
{
    $rec = fk_read_json('active_show.json', null);
    return (is_array($rec) && isset($rec['name']) && is_string($rec['name']) && $rec['name'] !== '') ? $rec['name'] : null;
}

/** Remember $name as the running show (replaces any earlier record). */
function fk_active_show_set(string $name): void
{
    fk_with_lock('active-show', function () use ($name) {
        if (fk_active_show_get() !== $name) {
            fk_write_json('active_show.json', array('name' => $name, 'started_at' => fk_now_iso()));
        }
    });
}

/** Forget the running show; returns its name, or null when none was recorded. */
function fk_active_show_clear(): ?string
{
    return fk_with_lock('active-show', function () {
        $name = fk_active_show_get();
        if (is_file(fk_data_dir() . '/active_show.json')) {
            @unlink(fk_data_dir() . '/active_show.json');
        }
        return $name;
    });
}

/** Called from callbacks.sh for every playlist event; returns at once. */
function fk_playlist_event(array $data): void
{
    $action = isset($data['Action']) && is_string($data['Action']) ? $data['Action'] : '';
    $name = isset($data['name']) && is_string($data['name']) ? $data['name'] : '';
    if ($action !== 'start' && $action !== 'stop' && $action !== 'playing') {
        return;
    }
    $config = fk_load_config();
    $settings = $config['settings'];
    if (!$config['projectors']) {
        return;
    }
    $inScope = fk_in_scope($settings, $name);
    if ($action === 'playing') {
        if ($inScope) {
            fk_active_show_set($name); // keeps the record right after a restart of fppd
        }
        return;
    }
    if ($action === 'start') {
        if (!$inScope) {
            return;
        }
        fk_active_show_set($name);
        if ($settings['auto_on']) {
            fk_log('INFO', 'playlist "' . $name . '" started: switching projectors on');
            fk_power('on', $config['projectors'], false, null);
        }
        if ($settings['guard_enabled']) {
            fk_set_health(true, null, null);
            fk_spawn_detached(array('guard', $name));
        }
        return;
    }
    // stop
    $show = $name;
    if (!$inScope) {
        $show = fk_active_show_get() ?? '';
        if ($show === '') {
            return; // not a show of ours, and none is recorded
        }
    }
    // Another in-scope playlist already playing means this stop is not the end of the show.
    $status = fk_fpp_status();
    $now = ($status !== null && $status['current_playlist'] !== null && fk_in_scope($settings, $status['current_playlist']))
        ? $status['current_playlist'] : null;
    if ($now !== null && ($now !== $show || ($name !== '' && !$inScope))) {
        fk_log('INFO', 'playlist stop ignored: "' . $now . '" is playing');
        return;
    }
    fk_active_show_clear();
    if (!$inScope) {
        fk_log('INFO', 'playlist stopped with no name (FPP sends an empty one): ending "' . $show . '"');
    }
    if ($settings['auto_off']) {
        fk_log('INFO', 'playlist "' . $show . '" stopped: switching projectors off (per-projector delay)');
        foreach ($config['projectors'] as $p) {
            $seq = fk_new_intent($p['id'], 'off');
            fk_spawn_detached(array('worker', 'off', $p['id'], (string)$seq, '0', '-', (string)(int)$p['off_delay_s']));
        }
    }
}

/**
 * Delayed power-off: wait, then switch off unless a newer request came in or a show in scope
 * is playing again.
 */
function fk_wait_before_off(array $p, array $settings, int $seq, int $delay): ?array
{
    $until = microtime(true) + $delay;
    while (microtime(true) < $until) {
        if (!fk_intent_current($p['id'], $seq)) {
            return fk_result($p, true, 'superseded', 'a newer request took over');
        }
        fk_sleep_s(min(1.0, max(0.0, $until - microtime(true))));
    }
    $status = fk_fpp_status();
    if ($status !== null && $status['current_playlist'] !== null && fk_in_scope($settings, $status['current_playlist'])) {
        return fk_result($p, true, 'kept', 'not switched off: "' . $status['current_playlist'] . '" is playing');
    }
    return null;
}

/**
 * Watch the projectors while $playlist plays. A reported fault (ERST error, ERR4, or a
 * projector that is off once the show has had time to start) marks the plugin unhealthy and,
 * when guard_stop_playback is on, stops FPP's playlist. A projector that does not answer
 * three times in a row only marks it unhealthy.
 */
function fk_guard(string $playlist): void
{
    $lock = fk_try_lock('guard');
    if ($lock === null) {
        return; // one guard at a time
    }
    try {
        $startedAt = microtime(true);
        $misses = array();
        fk_log('INFO', 'fault guard watching "' . $playlist . '"');
        while (true) {
            $config = fk_load_config();
            $settings = $config['settings'];
            if (!$settings['guard_enabled'] || !is_dir(fk_plugin_dir())) {
                return;
            }
            fk_sleep_s((int)$settings['guard_interval_s']);
            // The stop event clears the record: that ends the guard. A newer show replaces it.
            $recorded = fk_active_show_get();
            if ($recorded === null) {
                fk_log('INFO', 'fault guard stopped: the show ended');
                return;
            }
            if ($recorded !== $playlist) {
                $playlist = $recorded;
                fk_log('INFO', 'fault guard now watching "' . $playlist . '"');
            }
            $status = fk_fpp_status();
            if ($status === null || $status['status_name'] === 'idle' || $status['current_playlist'] === null
                || !fk_in_scope($settings, $status['current_playlist'])) {
                fk_log('INFO', 'fault guard stopped: no show in scope is playing');
                return;
            }
            foreach ($config['projectors'] as $p) {
                $fault = null;
                try {
                    $client = fk_client($p, $settings);
                    $power = $client->power();
                    $misses[$p['id']] = 0;
                    $grace = (int)$p['ready_timeout_s'] + (int)$settings['guard_interval_s'] + 10;
                    if (($power === 'standby' || $power === 'cooling') && microtime(true) - $startedAt > $grace) {
                        $fault = 'is ' . $power . ' during the show';
                    }
                    if ($fault === null) {
                        foreach ($client->errorStatus() as $field => $level) {
                            if ($level === 'error') {
                                $fault = 'reports a ' . $field . ' error';
                                break;
                            }
                        }
                    }
                } catch (FkPjlinkException $e) {
                    if ($e->kind === 'failure') {
                        $fault = 'reports a failure (ERR4)';
                    } elseif ($e->kind !== 'unavailable' && $e->kind !== 'undefined') {
                        $misses[$p['id']] = ($misses[$p['id']] ?? 0) + 1;
                        if ($misses[$p['id']] >= 3) {
                            fk_set_health(false, $p['name'] . ' does not answer: ' . $e->getMessage(), $p['id']);
                            fk_log('WARN', 'guard: ' . $p['name'] . ' does not answer (' . $e->kind . ')');
                        }
                    }
                }
                if ($fault !== null) {
                    fk_set_health(false, $p['name'] . ' ' . $fault, $p['id']);
                    fk_log('ERROR', 'guard: ' . $p['name'] . ' ' . $fault);
                    if ($settings['guard_stop_playback']) {
                        $stopped = fk_fpp_stop_playback();
                        fk_log('ERROR', 'guard: ' . ($stopped ? 'stopped playback' : 'could not stop playback'));
                    }
                    return;
                }
            }
        }
    } finally {
        fk_unlock($lock);
    }
}
