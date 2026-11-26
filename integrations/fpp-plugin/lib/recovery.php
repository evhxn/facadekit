<?php
/*
 * Thermal history and opt-in fault recovery (docs/20-fpp-plugin.md, "Thermal fault recovery").
 *
 * Two layers, both stored per projector in thermal.json:
 *
 * 1. History: the temperature severity from PJLink ERST (third character: 0 ok, 1 warning,
 *    2 error). A first warning, an escalation and a confirmed clearance are logged once each.
 *    A reading that did not arrive (timeout, wrong password, ERR1/ERR3/ERR4) changes nothing:
 *    the last known level is kept, and only a fresh valid reading can record a clearance.
 *    A warning alone never touches playback.
 *
 * 2. Recovery (off by default): after a confirmed temperature error the plugin sends no more
 *    power-on requests to that projector, lets it finish its own protection and cooling, and
 *    retries once per show when ALL of these hold: the minimum cooldown has passed, N fresh
 *    consecutive readings were valid and healthy (reachable, not cooling or warming up,
 *    temperature ok, no error in any ERST field), and the show and the operator still want the
 *    projector on. A projector that is still on when health returns is only confirmed, never
 *    power-cycled. Anything else ends in "attention" with the reason in the log.
 *
 * The state machine (fk_recovery_step) is a pure function of its inputs and the clock, so the
 * tests can walk it through a scenario without waiting.
 */

if (defined('FK_PLUGIN_RECOVERY')) {
    return;
}
define('FK_PLUGIN_RECOVERY', 1);

require_once __DIR__ . '/common.php';
require_once __DIR__ . '/pjlink.php';

const FK_THERMAL_EVENTS_MAX = 40;
const FK_RECOVERY_GIVE_UP_EXTRA_S = 1800; // cooldown that never clears: attention after cooldown + this
const FK_RECOVERY_RETRY_WAIT_S = 120;     // retry sent but the projector is not on after this long
const FK_RECOVERY_MAX_AGE_S = 20;         // a reading older than this is stale and counts for nothing
const FK_RECOVERY_RETRIES_PER_SHOW = 1;

// -- storage ------------------------------------------------------------------------------

function fk_recovery_blank(): array
{
    return array(
        'state' => 'idle',          // idle | watching | cooldown | verifying | retrying | attention
        'reason' => null,
        'wants_on' => false,        // does the current show still want this projector on?
        'show' => null,             // show key (name|started_at) this record belongs to
        'first_fault_ts' => null,
        'cooldown_ends_ts' => null,
        'streak' => 0,              // consecutive fresh, valid, healthy readings
        'attempts' => array(),      // show key => automatic power-on attempts
        'retry_token' => null,
        'retry_ts' => null,
        'retry_result' => null,
        'last_outcome' => null,
        'last_outcome_at' => null,
    );
}

function fk_thermal_blank(): array
{
    return array(
        'level' => 'unknown',       // last known temperature level: unknown | ok | warning | error
        'last_valid_ts' => null,
        'warning_since' => null,
        'events' => array(),
        'recovery' => fk_recovery_blank(),
    );
}

function fk_thermal_all(): array
{
    $data = fk_read_json('thermal.json', array());
    return (is_array($data) && isset($data['projectors']) && is_array($data['projectors'])) ? $data['projectors'] : array();
}

function fk_thermal_get(string $id): array
{
    $all = fk_thermal_all();
    $t = isset($all[$id]) && is_array($all[$id]) ? $all[$id] : array();
    $t = array_merge(fk_thermal_blank(), $t);
    $t['recovery'] = array_merge(fk_recovery_blank(), is_array($t['recovery']) ? $t['recovery'] : array());
    return $t;
}

/** Change one projector's record under the lock: $change(array $t): array $t. */
function fk_thermal_update(string $id, callable $change): array
{
    return fk_with_lock('thermal', function () use ($id, $change) {
        $data = fk_read_json('thermal.json', array());
        $all = (is_array($data) && isset($data['projectors']) && is_array($data['projectors'])) ? $data['projectors'] : array();
        $t = isset($all[$id]) && is_array($all[$id]) ? $all[$id] : array();
        $t = array_merge(fk_thermal_blank(), $t);
        $t['recovery'] = array_merge(fk_recovery_blank(), is_array($t['recovery']) ? $t['recovery'] : array());
        $t = $change($t);
        $all[$id] = $t;
        fk_write_json('thermal.json', array('projectors' => $all));
        return $t;
    });
}

function fk_ts_iso(?int $ts): ?string
{
    return $ts === null ? null : gmdate('Y-m-d\TH:i:s\Z', $ts);
}

// -- history (pure) -----------------------------------------------------------------------

/**
 * Apply one temperature level to a record. $level is null when no valid reading arrived: then
 * nothing changes. Returns array($record, $events) with at most one event.
 */
function fk_thermal_apply(array $t, ?string $level, int $now): array
{
    $events = array();
    if ($level === null || !in_array($level, array('ok', 'warning', 'error'), true)) {
        return array($t, $events);
    }
    $prev = $t['level'];
    $t['last_valid_ts'] = $now;
    if ($level === 'warning' && ($prev === 'ok' || $prev === 'unknown')) {
        $events[] = array('type' => 'warning', 'level' => 'warning', 'ts' => $now);
        $t['warning_since'] = $now;
    } elseif ($level === 'error' && $prev !== 'error') {
        $events[] = array('type' => 'escalation', 'level' => 'error', 'from' => $prev, 'ts' => $now);
        if ($t['warning_since'] === null) {
            $t['warning_since'] = $now;
        }
    } elseif ($level === 'ok' && ($prev === 'warning' || $prev === 'error')) {
        $events[] = array('type' => 'clearance', 'level' => 'ok', 'from' => $prev, 'ts' => $now);
        $t['warning_since'] = null;
    }
    $t['level'] = $level;
    foreach ($events as $e) {
        $t['events'][] = $e;
    }
    if (count($t['events']) > FK_THERMAL_EVENTS_MAX) {
        $t['events'] = array_slice($t['events'], -FK_THERMAL_EVENTS_MAX);
    }
    return array($t, $events);
}

/** Record a reading for projector $p; logs each new event once. Safe to call from any process. */
function fk_thermal_observe(array $p, ?array $reading): void
{
    $level = ($reading !== null && !empty($reading['valid']) && isset($reading['level'])) ? $reading['level'] : null;
    if ($level === null) {
        return;
    }
    $now = isset($reading['at']) ? (int)$reading['at'] : fk_now_ts();
    $events = array();
    fk_thermal_update($p['id'], function (array $t) use ($level, $now, &$events) {
        list($t, $events) = fk_thermal_apply($t, $level, $now);
        return $t;
    });
    foreach ($events as $e) {
        $text = array(
            'warning' => 'temperature warning',
            'escalation' => 'temperature ERROR (was ' . ($e['from'] ?? 'unknown') . ')',
            'clearance' => 'temperature back to normal (was ' . ($e['from'] ?? 'unknown') . ')',
        )[$e['type']];
        fk_log($e['type'] === 'clearance' ? 'INFO' : 'WARN', $p['name'] . ': ' . $text);
    }
}

// -- reading ------------------------------------------------------------------------------

/**
 * One fresh health reading: POWR and ERST. Never throws. Fields:
 *   valid    ERST answered and parsed (the temperature level is trustworthy)
 *   level    ok | warning | error | null
 *   power    standby | on | cooling | warmup | busy | unknown
 *   healthy  valid, temperature ok, no ERST field in error, power on or standby
 *   issue    null | unreachable | auth | unsupported | sensor | unavailable | protocol
 *   kind     the raw PJLink failure kind, for the guard
 *   at       unix time of the reading
 */
function fk_thermal_read(FkPjlink $client): array
{
    $r = array('at' => fk_now_ts(), 'valid' => false, 'level' => null, 'power' => 'unknown', 'healthy' => false,
        'issue' => null, 'kind' => null, 'errors' => null);
    try {
        $r['power'] = $client->power();
    } catch (FkPjlinkException $e) {
        $r['kind'] = $e->kind;
        if ($e->kind === 'unavailable') {
            $r['power'] = 'busy';
        } else {
            $r['issue'] = fk_issue_for($e->kind);
            return $r;
        }
    }
    try {
        $errors = $client->errorStatus();
    } catch (FkPjlinkException $e) {
        $r['kind'] = $e->kind;
        $r['issue'] = fk_issue_for($e->kind);
        return $r;
    }
    $r['errors'] = $errors;
    $r['level'] = $errors['temperature'];
    $r['valid'] = true;
    $anyError = in_array('error', $errors, true);
    $r['healthy'] = $r['level'] === 'ok' && !$anyError && ($r['power'] === 'on' || $r['power'] === 'standby');
    return $r;
}

function fk_issue_for(string $kind): string
{
    switch ($kind) {
        case 'auth':
            return 'auth';
        case 'undefined':
            return 'unsupported';
        case 'failure':
            return 'sensor';
        case 'unavailable':
            return 'unavailable';
        case 'protocol':
        case 'parameter':
            return 'protocol';
    }
    return 'unreachable';
}

// -- the state machine (pure) -------------------------------------------------------------

function fk_recovery_in_progress(array $r): bool
{
    return in_array($r['state'], array('cooldown', 'verifying', 'retrying'), true);
}

/** Put a record into a terminal "attention" state. */
function fk_recovery_attention(array $r, string $reason, int $now): array
{
    $r['state'] = 'attention';
    $r['reason'] = $reason;
    $r['retry_token'] = null;
    $r['last_outcome'] = 'needs attention: ' . $reason;
    $r['last_outcome_at'] = $now;
    return $r;
}

/** Cancel whatever is queued or counting: no retry survives this. */
function fk_recovery_apply_cancel(array $r, string $reason, int $now, bool $wantsOn): array
{
    $was = $r['state'];
    $r['retry_token'] = null;
    $r['retry_result'] = null;
    $r['streak'] = 0;
    $r['wants_on'] = $wantsOn;
    if (fk_recovery_in_progress($r) || $was === 'attention') {
        $r['last_outcome'] = 'cancelled: ' . $reason;
        $r['last_outcome_at'] = $now;
    }
    $r['state'] = 'idle';
    $r['reason'] = $reason;
    return $r;
}

/**
 * One step. $reading is fk_thermal_read()'s array or null (nothing arrived). $ctx:
 *   enabled, show_active, show_key, cooldown_s, reads_needed
 * Returns array($record, $actions); the only action is 'retry'.
 */
function fk_recovery_step(array $r, ?array $reading, array $ctx, int $now): array
{
    $actions = array();
    if (!$ctx['enabled'] || !$ctx['show_active']) {
        if (fk_recovery_in_progress($r)) {
            $r = fk_recovery_apply_cancel($r, !$ctx['enabled'] ? 'recovery was turned off' : 'the show ended', $now, false);
        }
        return array($r, $actions);
    }
    if ($r['show'] !== $ctx['show_key']) { // a new or replaced show starts fresh
        if (fk_recovery_in_progress($r)) {
            $r = fk_recovery_apply_cancel($r, 'the show was replaced', $now, true);
        }
        $r['show'] = $ctx['show_key'];
        $r['state'] = 'watching';
        $r['reason'] = null;
        $r['wants_on'] = true;
        $r['streak'] = 0;
    }
    if (!$r['wants_on']) { // the operator switched this projector off: leave it alone
        if (fk_recovery_in_progress($r)) {
            $r = fk_recovery_apply_cancel($r, 'the projector was switched off', $now, false);
        }
        return array($r, $actions);
    }
    if ($r['state'] === 'idle') {
        $r['state'] = 'watching';
    }
    $fresh = $reading !== null && !empty($reading['valid']) && ($now - (int)$reading['at']) <= FK_RECOVERY_MAX_AGE_S
        && (int)$reading['at'] <= $now + 2;
    $level = $fresh ? $reading['level'] : null;
    $healthy = $fresh && !empty($reading['healthy']);
    $power = $reading !== null ? $reading['power'] : 'unknown';

    switch ($r['state']) {
        case 'watching':
            if ($level === 'error') {
                $r['state'] = 'cooldown';
                $r['first_fault_ts'] = $now;
                $r['cooldown_ends_ts'] = $now + (int)$ctx['cooldown_s'];
                $r['streak'] = 0;
                $r['reason'] = 'temperature error; no power-on requests until it has cooled down';
            }
            break;

        case 'cooldown':
        case 'verifying':
            if ($now - (int)$r['first_fault_ts'] > (int)$ctx['cooldown_s'] + FK_RECOVERY_GIVE_UP_EXTRA_S) {
                $r = fk_recovery_attention($r, 'the projector did not report healthy after '
                    . (int)round(((int)$ctx['cooldown_s'] + FK_RECOVERY_GIVE_UP_EXTRA_S) / 60) . ' minutes', $now);
                break;
            }
            if ($r['state'] === 'cooldown') {
                if ($now < (int)$r['cooldown_ends_ts']) {
                    $r['reason'] = 'cooling down; earliest check in ' . ((int)$r['cooldown_ends_ts'] - $now) . ' s';
                    break;
                }
                $r['state'] = 'verifying';
                $r['streak'] = 0;
            }
            if ($healthy) {
                $r['streak']++;
            } else {
                $r['streak'] = 0; // a missing, stale, cooling or unhealthy reading resets the count
            }
            $r['reason'] = 'checking health (' . $r['streak'] . ' of ' . (int)$ctx['reads_needed'] . ' good readings)';
            if ($r['streak'] < (int)$ctx['reads_needed']) {
                break;
            }
            if ($power === 'on') {
                $r['state'] = 'watching';
                $r['streak'] = 0;
                $r['reason'] = null;
                $r['last_outcome'] = 'recovered: healthy again and still on (power not cycled)';
                $r['last_outcome_at'] = $now;
                break;
            }
            $used = (int)($r['attempts'][$ctx['show_key']] ?? 0);
            if ($used >= FK_RECOVERY_RETRIES_PER_SHOW) {
                $r = fk_recovery_attention($r, 'healthy again, but the automatic retry for this show was already used', $now);
                break;
            }
            $r['attempts'][$ctx['show_key']] = $used + 1;
            $r['state'] = 'retrying';
            $r['retry_token'] = bin2hex(random_bytes(6));
            $r['retry_ts'] = $now;
            $r['retry_result'] = null;
            $r['reason'] = 'switching the projector on once';
            $actions[] = 'retry';
            break;

        case 'retrying':
            $res = $r['retry_result'];
            if (is_array($res) && empty($res['ok'])) {
                $r = fk_recovery_attention($r, 'power-on failed: ' . (string)($res['message'] ?? 'no reason given'), $now);
            } elseif ($level === 'error') {
                $r = fk_recovery_attention($r, 'the temperature error came back after the retry', $now);
            } elseif ($healthy && $power === 'on') {
                $r['state'] = 'watching';
                $r['retry_token'] = null;
                $r['reason'] = null;
                $r['last_outcome'] = 'recovered: switched on again after cooling down';
                $r['last_outcome_at'] = $now;
            } elseif ($now - (int)$r['retry_ts'] > FK_RECOVERY_RETRY_WAIT_S) {
                $r = fk_recovery_attention($r, 'the projector did not switch on within ' . FK_RECOVERY_RETRY_WAIT_S . ' s', $now);
            }
            break;

        case 'attention':
            break; // stays until the show ends, the operator acts or recovery is turned off
    }
    return array($r, $actions);
}

// -- cancel, from every place that ends or replaces a show or switches a projector ----------

/**
 * Cancel queued or counting recovery for $ids (null = all). $wantsOn false means the operator
 * took the projector off the show for the rest of it. Returns the ids that were in progress.
 */
function fk_recovery_cancel(?array $ids, string $reason, bool $wantsOn = false): array
{
    if (!is_file(fk_data_dir() . '/thermal.json')) {
        return array();
    }
    $hit = array();
    foreach (fk_thermal_all() as $id => $_) {
        if ($ids !== null && !in_array($id, $ids, true)) {
            continue;
        }
        fk_thermal_update((string)$id, function (array $t) use ($reason, $wantsOn, $id, &$hit) {
            $r = $t['recovery'];
            if (!fk_recovery_in_progress($r) && $r['state'] !== 'attention' && $r['wants_on'] === $wantsOn) {
                return $t;
            }
            if (fk_recovery_in_progress($r)) {
                $hit[] = $id;
            }
            $t['recovery'] = fk_recovery_apply_cancel($r, $reason, fk_now_ts(), $wantsOn);
            return $t;
        });
    }
    if ($hit) {
        fk_log('INFO', 'recovery cancelled (' . $reason . ')');
    }
    return $hit;
}

/** True while $token is still the retry this projector wants. Checked right before POWR 1. */
function fk_recovery_retry_valid(string $id, string $token): bool
{
    $r = fk_thermal_get($id)['recovery'];
    if ($r['state'] !== 'retrying' || $r['retry_token'] === null || !hash_equals((string)$r['retry_token'], $token)) {
        return false;
    }
    if (!$r['wants_on']) {
        return false;
    }
    $active = fk_recovery_active_show_key();
    return $active !== null && $active === $r['show'];
}

/** The retry worker reports its outcome here (after the power sequence ended). */
function fk_recovery_retry_report(string $id, string $token, array $result): void
{
    fk_thermal_update($id, function (array $t) use ($token, $result) {
        $r = $t['recovery'];
        if ($r['state'] === 'retrying' && $r['retry_token'] !== null && hash_equals((string)$r['retry_token'], $token)) {
            $ok = !empty($result['ok']) && ($result['state'] ?? '') !== 'superseded' && ($result['state'] ?? '') !== 'cancelled';
            if (($result['state'] ?? '') === 'superseded' || ($result['state'] ?? '') === 'cancelled') {
                return $t; // a newer request or a cancel took over: that path decides
            }
            $r['retry_result'] = array('ok' => $ok, 'state' => (string)($result['state'] ?? ''), 'message' => (string)($result['message'] ?? ''));
            $t['recovery'] = $r;
        }
        return $t;
    });
}

/** "name|started_at" of the show in the record, or null when none runs. */
function fk_recovery_active_show_key(): ?string
{
    $rec = fk_read_json('active_show.json', null);
    if (!is_array($rec) || !isset($rec['name']) || !is_string($rec['name']) || $rec['name'] === '') {
        return null;
    }
    return $rec['name'] . '|' . (string)($rec['started_at'] ?? '');
}

/**
 * A new monitor process starts: whatever the old one left in progress is unknown now (the
 * daemon restarted, or the plugin was reloaded). It cannot lead to a retry; a fresh confirmed
 * error under the current show is needed first.
 */
function fk_recovery_reset_unknown(): void
{
    foreach (array_keys(fk_thermal_all()) as $id) {
        fk_thermal_update((string)$id, function (array $t) {
            $r = $t['recovery'];
            if (fk_recovery_in_progress($r)) {
                $t['recovery'] = fk_recovery_apply_cancel($r, 'the state was unknown after a restart; no automatic retry', fk_now_ts(), true);
            }
            return $t;
        });
    }
}

/** True while automatic power-on must not be sent to $id: last known temperature level is error. */
function fk_recovery_blocks_power_on(array $settings, string $id): bool
{
    if (!$settings['recovery_enabled']) {
        return false;
    }
    $t = fk_thermal_get($id);
    return $t['level'] === 'error' || fk_recovery_in_progress($t['recovery']) || $t['recovery']['state'] === 'attention';
}

// -- what the settings page and the API show ----------------------------------------------

function fk_recovery_label(array $r): string
{
    $labels = array(
        'idle' => 'Not recovering',
        'watching' => 'Monitoring',
        'cooldown' => 'Cooling down, no power-on',
        'verifying' => 'Checking health',
        'retrying' => 'Retrying power-on',
        'attention' => 'Needs attention',
    );
    return $labels[$r['state']] ?? 'Not recovering';
}

function fk_thermal_public(string $id, array $settings): array
{
    $t = fk_thermal_get($id);
    $r = $t['recovery'];
    $events = array();
    foreach (array_slice($t['events'], -5) as $e) {
        $events[] = array('type' => $e['type'], 'at' => fk_ts_iso((int)$e['ts']));
    }
    return array(
        'level' => $t['level'],
        'last_valid_check_at' => fk_ts_iso($t['last_valid_ts'] === null ? null : (int)$t['last_valid_ts']),
        'warning_since' => fk_ts_iso($t['warning_since'] === null ? null : (int)$t['warning_since']),
        'recovery_enabled' => $settings['recovery_enabled'],
        'recovery' => array(
            'state' => $r['state'],
            'label' => fk_recovery_label($r),
            'reason' => $r['reason'],
            'wants_on' => $r['wants_on'],
            'cooldown_ends_at' => $r['state'] === 'cooldown' ? fk_ts_iso((int)$r['cooldown_ends_ts']) : null,
            'good_readings' => (int)$r['streak'],
            'good_readings_needed' => $settings['recovery_healthy_reads'],
            'retries_used' => $r['show'] !== null ? (int)($r['attempts'][$r['show']] ?? 0) : 0,
            'last_outcome' => $r['last_outcome'],
            'last_outcome_at' => fk_ts_iso($r['last_outcome_at'] === null ? null : (int)$r['last_outcome_at']),
        ),
        'events' => $events,
    );
}

/** Add a "thermal" entry to each status snapshot. */
function fk_attach_thermal(array $snapshots, array $settings): array
{
    foreach ($snapshots as $i => $s) {
        if (isset($s['id'])) {
            $snapshots[$i]['thermal'] = fk_thermal_public((string)$s['id'], $settings);
        }
    }
    return $snapshots;
}
