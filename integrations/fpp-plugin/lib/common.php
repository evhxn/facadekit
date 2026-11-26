<?php
/*
 * FacadeKit FPP plugin: paths, JSON files, locking, logging with redaction, validation.
 *
 * Pure PHP 8, no extensions beyond the defaults Debian's php-cli ships (json, hash, ctype is
 * not required). Every file this plugin writes lives in
 *   <media>/plugindata/fpp-plugin-FacadeKit/   (config with passwords, token hash, shows, state)
 *   <media>/logs/plugin-fpp-plugin-FacadeKit.log (one log, rotated by FPP itself)
 * Tests point FK_PLUGIN_DATA_DIR / FK_PLUGIN_LOG somewhere else.
 */

if (defined('FK_PLUGIN_COMMON')) {
    return;
}
define('FK_PLUGIN_COMMON', 1);

const FK_REPO = 'fpp-plugin-FacadeKit';
const FK_PLUGIN_VERSION = '0.1.0';
const FK_API_VERSION = 1;
const FK_MAX_PROJECTORS = 16;
const FK_MAX_SHOWS = 200;
const FK_DEFAULT_PORT = 4352;
const FK_LOG_TAIL_BYTES = 65536;

/** Settings and their defaults. Projectors and secrets are handled separately. */
function fk_default_settings(): array
{
    return array(
        'facadekit_url' => '',
        'auto_on' => false,           // power on when a playlist in scope starts
        'auto_off' => false,          // power off after a playlist in scope stops (per-projector delay)
        'auto_scope' => 'facadekit',  // 'facadekit' (published shows only) or 'all'
        'guard_enabled' => false,     // watch projectors while a playlist in scope plays
        'guard_stop_playback' => false,
        'guard_interval_s' => 30,
        'recovery_enabled' => false,  // opt-in: retry a projector once after a thermal error (docs/20)
        'recovery_cooldown_s' => 300, // minimum wait after a thermal error before any retry
        'recovery_healthy_reads' => 3, // consecutive fresh healthy readings needed after the cooldown
        'connect_timeout_s' => 3,
        'io_timeout_s' => 5,
    );
}

/**
 * Per-projector timing, defaults and [min, max]. Laser projectors such as the Panasonic
 * PT-RZ570 switch on and off without a lamp warm-up or cool-down, so the defaults are near
 * zero. A wait only ever happens while the projector itself reports warm-up or cooling.
 *   ready_timeout_s     how long "On (wait)" waits for POWR to say on
 *   cooldown_timeout_s  how long to wait when the projector reports cooling before power-on
 *   off_delay_s         delay before the automatic power-off after a show stops
 */
function fk_projector_timing(): array
{
    return array(
        'ready_timeout_s' => array(5, 0, 900),
        'cooldown_timeout_s' => array(30, 0, 900),
        'off_delay_s' => array(0, 0, 7200),
    );
}

function fk_clamp_timing(string $key, $value): int
{
    $spec = fk_projector_timing()[$key];
    $n = is_numeric($value) ? (int)$value : $spec[0];
    return max($spec[1], min($spec[2], $n));
}

/** Integer settings: [min, max]. */
function fk_int_setting_ranges(): array
{
    return array(
        'guard_interval_s' => array(5, 600),
        'recovery_cooldown_s' => array(30, 3600),
        'recovery_healthy_reads' => array(2, 10),
        'connect_timeout_s' => array(1, 30),
        'io_timeout_s' => array(1, 60),
    );
}

// -- paths ------------------------------------------------------------------------------

function fk_media_dir(): string
{
    $env = getenv('MEDIADIR');
    if (is_string($env) && $env !== '') {
        return rtrim($env, '/');
    }
    if (isset($GLOBALS['settings']['mediaDirectory']) && is_string($GLOBALS['settings']['mediaDirectory'])) {
        return rtrim($GLOBALS['settings']['mediaDirectory'], '/');
    }
    return '/home/fpp/media';
}

function fk_data_dir(): string
{
    $env = getenv('FK_PLUGIN_DATA_DIR');
    $dir = (is_string($env) && $env !== '') ? rtrim($env, '/') : fk_media_dir() . '/plugindata/' . FK_REPO;
    if (!is_dir($dir)) {
        @mkdir($dir, 0700, true);
        fk_fix_owner($dir);
    }
    return $dir;
}

function fk_log_file(): string
{
    $env = getenv('FK_PLUGIN_LOG');
    if (is_string($env) && $env !== '') {
        return $env;
    }
    $logDir = getenv('LOGDIR');
    if (!is_string($logDir) || $logDir === '') {
        $logDir = isset($GLOBALS['settings']['logDirectory']) && is_string($GLOBALS['settings']['logDirectory'])
            ? $GLOBALS['settings']['logDirectory'] : fk_media_dir() . '/logs';
    }
    return rtrim($logDir, '/') . '/plugin-' . FK_REPO . '.log';
}

function fk_plugin_dir(): string
{
    return dirname(__DIR__);
}

/** The PHP command-line binary (the web server runs php-fpm, whose PHP_BINARY is not php-cli). */
function fk_php_binary(): string
{
    $env = getenv('FK_PHP');
    if (is_string($env) && $env !== '') {
        return $env;
    }
    if (PHP_SAPI === 'cli' && PHP_BINARY !== '') {
        return PHP_BINARY;
    }
    return '/usr/bin/php';
}

/**
 * fppd runs plugin commands and callbacks as root, the web UI runs as the fpp user. Files
 * written by root must stay readable and writable by the web UI, so hand them to the owner
 * of the data directory's parent (the fpp user on a real player).
 */
function fk_fix_owner(string $path): void
{
    if (function_exists('posix_geteuid') && posix_geteuid() !== 0) {
        return;
    }
    // Without the posix extension the calls below simply fail for a non-root user.
    $parent = dirname($path);
    $uid = @fileowner($parent);
    $gid = @filegroup($parent);
    if ($uid !== false && $uid !== 0) {
        @chown($path, $uid);
        if ($gid !== false) {
            @chgrp($path, $gid);
        }
    }
}

// -- JSON files -------------------------------------------------------------------------

function fk_read_json(string $name, $default)
{
    $path = fk_data_dir() . '/' . $name;
    if (!is_file($path)) {
        return $default;
    }
    $text = @file_get_contents($path);
    if ($text === false || $text === '') {
        return $default;
    }
    $data = json_decode($text, true);
    return ($data === null && json_last_error() !== JSON_ERROR_NONE) ? $default : $data;
}

/** Write atomically (temporary file + rename), mode 0600. */
function fk_write_json(string $name, $data): void
{
    $dir = fk_data_dir();
    $path = $dir . '/' . $name;
    $tmp = $path . '.tmp' . getmypid();
    $text = json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    if ($text === false) {
        throw new RuntimeException('could not encode ' . $name);
    }
    $old = umask(0077);
    try {
        if (@file_put_contents($tmp, $text . "\n") === false) {
            throw new RuntimeException('could not write ' . $name);
        }
        @chmod($tmp, 0600);
        fk_fix_owner($tmp);
        if (!@rename($tmp, $path)) {
            @unlink($tmp);
            throw new RuntimeException('could not replace ' . $name);
        }
    } finally {
        umask($old);
    }
}

/**
 * Run $fn while holding an exclusive lock named $name. Returns $fn's result.
 * Used for read-modify-write of the shared JSON files by concurrent workers.
 */
function fk_with_lock(string $name, callable $fn)
{
    $lockPath = fk_data_dir() . '/.' . $name . '.lock';
    $handle = @fopen($lockPath, 'c');
    if ($handle === false) {
        return $fn();
    }
    fk_fix_owner($lockPath);
    try {
        flock($handle, LOCK_EX);
        return $fn();
    } finally {
        flock($handle, LOCK_UN);
        fclose($handle);
    }
}

/** Try to take a non-blocking lock; returns the handle or null. Release with fk_unlock(). */
function fk_try_lock(string $name)
{
    $lockPath = fk_data_dir() . '/.' . $name . '.lock';
    $handle = @fopen($lockPath, 'c');
    if ($handle === false) {
        return null;
    }
    fk_fix_owner($lockPath);
    if (!flock($handle, LOCK_EX | LOCK_NB)) {
        fclose($handle);
        return null;
    }
    return $handle;
}

function fk_unlock($handle): void
{
    if (is_resource($handle)) {
        flock($handle, LOCK_UN);
        fclose($handle);
    }
}

// -- configuration ----------------------------------------------------------------------

/** The stored configuration, with defaults filled in. Contains passwords: never print it. */
function fk_load_config(): array
{
    $raw = fk_read_json('config.json', array());
    if (!is_array($raw)) {
        $raw = array();
    }
    $settings = fk_default_settings();
    foreach ($settings as $key => $default) {
        if (array_key_exists($key, $raw)) {
            $settings[$key] = fk_coerce_setting($key, $raw[$key], $default);
        }
    }
    $projectors = array();
    if (isset($raw['projectors']) && is_array($raw['projectors'])) {
        foreach ($raw['projectors'] as $p) {
            if (is_array($p) && isset($p['id'], $p['host']) && is_string($p['id']) && is_string($p['host'])) {
                $projectors[] = array(
                    'id' => $p['id'],
                    'name' => isset($p['name']) && is_string($p['name']) ? $p['name'] : $p['id'],
                    'host' => $p['host'],
                    'port' => isset($p['port']) ? (int)$p['port'] : FK_DEFAULT_PORT,
                    'password' => isset($p['password']) && is_string($p['password']) && $p['password'] !== '' ? $p['password'] : null,
                    'input' => isset($p['input']) && is_string($p['input']) ? $p['input'] : '',
                    'ready_timeout_s' => fk_clamp_timing('ready_timeout_s', $p['ready_timeout_s'] ?? null),
                    'cooldown_timeout_s' => fk_clamp_timing('cooldown_timeout_s', $p['cooldown_timeout_s'] ?? null),
                    'off_delay_s' => fk_clamp_timing('off_delay_s', $p['off_delay_s'] ?? null),
                );
            }
        }
    }
    return array('settings' => $settings, 'projectors' => $projectors);
}

function fk_save_config(array $config): void
{
    $out = $config['settings'];
    $out['projectors'] = $config['projectors'];
    $out['config_version'] = 1;
    fk_write_json('config.json', $out);
    if (empty($config['settings']['recovery_enabled']) && function_exists('fk_recovery_cancel')) {
        fk_recovery_cancel(null, 'recovery was turned off');
    }
}

function fk_coerce_setting(string $key, $value, $default)
{
    if (is_bool($default)) {
        return $value === true || $value === 1 || $value === '1' || $value === 'true';
    }
    if (is_int($default)) {
        $ranges = fk_int_setting_ranges();
        $n = is_numeric($value) ? (int)$value : $default;
        if (isset($ranges[$key])) {
            $n = max($ranges[$key][0], min($ranges[$key][1], $n));
        }
        return $n;
    }
    if ($key === 'auto_scope') {
        return in_array($value, array('facadekit', 'all'), true) ? $value : $default;
    }
    return is_string($value) ? $value : $default;
}

/** Projector view safe to show anywhere: the password becomes a flag. */
function fk_public_projector(array $p): array
{
    return array(
        'id' => $p['id'],
        'name' => $p['name'],
        'host' => $p['host'],
        'port' => $p['port'],
        'has_password' => $p['password'] !== null,
        'input' => $p['input'],
        'ready_timeout_s' => $p['ready_timeout_s'],
        'cooldown_timeout_s' => $p['cooldown_timeout_s'],
        'off_delay_s' => $p['off_delay_s'],
    );
}

function fk_find_projector(array $config, string $id): ?array
{
    foreach ($config['projectors'] as $p) {
        if ($p['id'] === $id) {
            return $p;
        }
    }
    return null;
}

/**
 * Select projectors by a selector: '' or 'all' = every projector, otherwise a comma list of
 * ids or names (case-insensitive).
 */
function fk_select_projectors(array $config, string $selector): array
{
    $selector = trim($selector);
    if ($selector === '' || strcasecmp($selector, 'all') === 0) {
        return $config['projectors'];
    }
    $wanted = array();
    foreach (explode(',', $selector) as $part) {
        $part = strtolower(trim($part));
        if ($part !== '') {
            $wanted[$part] = true;
        }
    }
    $out = array();
    foreach ($config['projectors'] as $p) {
        if (isset($wanted[strtolower($p['id'])]) || isset($wanted[strtolower($p['name'])])) {
            $out[] = $p;
        }
    }
    return $out;
}

// -- validation -------------------------------------------------------------------------

function fk_valid_host(string $host): bool
{
    if ($host === '' || strlen($host) > 253) {
        return false;
    }
    if (filter_var($host, FILTER_VALIDATE_IP) !== false) {
        return true;
    }
    return preg_match('/^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$/', $host) === 1;
}

/** PJLink class 1 passwords: up to 32 printable ASCII characters without spaces. */
function fk_valid_password(string $password): bool
{
    return $password !== '' && strlen($password) <= 32 && preg_match('/^[\x21-\x7e]+$/', $password) === 1;
}

/** PJLink input codes: two digits, first 1-5 (RGB, video, digital, storage, network). */
function fk_valid_input(string $input): bool
{
    return $input === '' || preg_match('/^[1-5][1-9]$/', $input) === 1;
}

function fk_valid_name(string $name, int $max = 64): bool
{
    return $name !== '' && strlen($name) <= $max && preg_match('/^[^\x00-\x1f\x7f<>"\'`\\\\]+$/', $name) === 1;
}

/** FPP playlist names: not empty, no "/", "\" or NUL (PlaylistFilePath in playlist.php). */
function fk_valid_playlist_name(string $name): bool
{
    return $name !== '' && strlen($name) <= 128 && $name !== '.' && $name !== '..'
        && strpbrk($name, "/\\\0") === false && preg_match('/[\x00-\x1f\x7f]/', $name) !== 1;
}

/** FPP media names FacadeKit uploads: [A-Za-z0-9._-], no "..". */
function fk_valid_media_name(string $name): bool
{
    return $name !== '' && strlen($name) <= 128 && preg_match('/^[A-Za-z0-9._-]+$/', $name) === 1
        && strpos($name, '..') === false;
}

function fk_valid_id(string $id): bool
{
    return preg_match('/^[a-z0-9][a-z0-9_-]{0,31}$/', $id) === 1;
}

/** Loopback or private (RFC 1918, link-local, unique-local IPv6) address. */
function fk_is_private_ip(string $ip): bool
{
    if ($ip === '') {
        return false;
    }
    if (strpos($ip, '::ffff:') === 0 && filter_var(substr($ip, 7), FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) !== false) {
        $ip = substr($ip, 7);
    }
    if ($ip === '127.0.0.1' || $ip === '::1' || strpos($ip, '127.') === 0) {
        return true;
    }
    if (filter_var($ip, FILTER_VALIDATE_IP) === false) {
        return false;
    }
    // FILTER_FLAG_NO_PRIV_RANGE fails for 10/8, 172.16/12, 192.168/16 and fc00::/7.
    if (filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_NO_PRIV_RANGE) === false) {
        return true;
    }
    if (strpos($ip, '169.254.') === 0 || stripos($ip, 'fe80:') === 0) {
        return true;
    }
    return false;
}

/**
 * True when $remote is on the same subnet as the player's own address ($server): the same /24
 * for IPv4, the same /64 for IPv6. This covers a Mac that reaches the player through
 * fpp.local over a global (ISP-assigned) IPv6 address, which fk_is_private_ip() rejects.
 */
function fk_same_link(string $remote, string $server): bool
{
    $norm = function (string $ip): string {
        if (strpos($ip, '::ffff:') === 0 && filter_var(substr($ip, 7), FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) !== false) {
            return substr($ip, 7);
        }
        return $ip;
    };
    $r = @inet_pton($norm($remote));
    $s = @inet_pton($norm($server));
    if ($r === false || $s === false || strlen($r) !== strlen($s)) {
        return false;
    }
    $bytes = strlen($r) === 4 ? 3 : 8;
    return substr($r, 0, $bytes) === substr($s, 0, $bytes);
}

// -- time zone --------------------------------------------------------------------------

/**
 * The player's own time zone, so the log reads in the same local time as FPP's own pages.
 * php-fpm (web page, FPP callbacks) and php-cli (background workers) read different php.ini
 * files, and without date.timezone one falls back to UTC, so the log once mixed local and UTC
 * times. Every process now sets the same zone explicitly: FPP's own "TimeZone" setting first
 * (the one shown in FPP's UI; the OS zone on a Pi can still be UTC), then TZ, /etc/timezone
 * and the /etc/localtime link, else UTC.
 */
function fk_system_timezone(): string
{
    $candidates = array();
    $fpp = isset($GLOBALS['settings']['TimeZone']) && is_string($GLOBALS['settings']['TimeZone'])
        ? $GLOBALS['settings']['TimeZone'] : '';
    if ($fpp === '') {
        $settingsFile = @file_get_contents(fk_media_dir() . '/settings');
        if (is_string($settingsFile) && preg_match('/^TimeZone\s*=\s*"?([^"\r\n]+?)"?\s*$/m', $settingsFile, $m) === 1) {
            $fpp = trim($m[1]);
        }
    }
    if ($fpp !== '') {
        $candidates[] = $fpp;
    }
    $tz = getenv('TZ');
    if (is_string($tz) && $tz !== '') {
        $candidates[] = ltrim($tz, ':');
    }
    $file = @file_get_contents('/etc/timezone');
    if (is_string($file) && trim($file) !== '') {
        $candidates[] = trim($file);
    }
    $link = @readlink('/etc/localtime');
    if (is_string($link) && preg_match('~zoneinfo/(.+)$~', $link, $m) === 1) {
        $candidates[] = $m[1];
    }
    foreach ($candidates as $zone) {
        if (in_array($zone, timezone_identifiers_list(), true)) {
            return $zone;
        }
    }
    return 'UTC';
}

date_default_timezone_set(fk_system_timezone());

// -- logging ----------------------------------------------------------------------------

/**
 * Remove secrets from a log line: every configured projector password, anything that looks
 * like a pairing token or a PJLink digest, and "password=..."-style pairs.
 */
function fk_redact(string $text, ?array $config = null): string
{
    static $cached = null;
    if ($config === null) {
        if ($cached === null) {
            try {
                $cached = fk_load_config();
            } catch (Throwable $e) {
                $cached = array('projectors' => array());
            }
        }
        $config = $cached;
    }
    foreach ($config['projectors'] as $p) {
        if (isset($p['password']) && is_string($p['password']) && strlen($p['password']) >= 4) {
            $text = str_replace($p['password'], '<redacted>', $text);
        }
    }
    $text = preg_replace('/fkp_[A-Za-z0-9]{8,}/', 'fkp_<redacted>', $text);
    $text = preg_replace('/\b[0-9a-fA-F]{32}(?=%1)/', '<digest>', $text);
    $text = preg_replace('/\b[0-9a-fA-F]{64}\b/', '<hash>', $text);
    $text = preg_replace('/((?:pass(?:word|wd)?|token|secret)\s*[=:]\s*)\S+/i', '$1<redacted>', $text);
    return $text;
}

/** Append one line to the plugin log (opened per write, so FPP's rotation is followed). */
function fk_log(string $level, string $message): void
{
    $line = date('Y-m-d H:i:s') . ' [' . $level . '] ' . fk_redact($message);
    $line = str_replace(array("\r", "\n"), array(' ', ' '), $line) . "\n";
    $file = fk_log_file();
    $existed = is_file($file);
    @file_put_contents($file, $line, FILE_APPEND | LOCK_EX);
    if (!$existed) {
        fk_fix_owner($file);
    }
}

/** The last lines of the log (current file, then the rotated .1 when the current is short). */
function fk_log_tail(int $maxLines = 300): array
{
    $file = fk_log_file();
    $text = '';
    foreach (array($file . '.1', $file) as $path) {
        if (!is_file($path)) {
            continue;
        }
        $size = @filesize($path);
        $handle = @fopen($path, 'r');
        if ($handle === false || $size === false) {
            continue;
        }
        if ($size > FK_LOG_TAIL_BYTES) {
            fseek($handle, $size - FK_LOG_TAIL_BYTES);
            fgets($handle); // drop the partial first line
        }
        $text .= stream_get_contents($handle);
        fclose($handle);
    }
    $lines = preg_split('/\r?\n/', trim($text));
    if ($lines === false || $lines === array('')) {
        return array();
    }
    $lines = array_slice($lines, -$maxLines);
    return array_map('fk_redact', $lines);
}

// -- small helpers ----------------------------------------------------------------------

/** Unix time. FK_CLOCK_SCALE (tests only) runs the clock faster, the same in every process. */
function fk_now_ts(): int
{
    $k = getenv('FK_CLOCK_SCALE');
    if (is_string($k) && is_numeric($k) && (float)$k > 0 && (float)$k !== 1.0) {
        $epoch = 1700000000.0;
        return (int)($epoch + (microtime(true) - $epoch) * (float)$k);
    }
    return time();
}

function fk_now_iso(): string
{
    return gmdate('Y-m-d\TH:i:s\Z');
}

function fk_bool($value): bool
{
    if (is_bool($value)) {
        return $value;
    }
    if (is_int($value)) {
        return $value !== 0;
    }
    if (is_string($value)) {
        return in_array(strtolower(trim($value)), array('1', 'true', 'yes', 'on'), true);
    }
    return false;
}

function fk_new_id(array $config): string
{
    $used = array();
    foreach ($config['projectors'] as $p) {
        $used[$p['id']] = true;
    }
    for ($n = 1; $n <= 999; $n++) {
        if (!isset($used['p' . $n])) {
            return 'p' . $n;
        }
    }
    return 'p' . bin2hex(random_bytes(4));
}
