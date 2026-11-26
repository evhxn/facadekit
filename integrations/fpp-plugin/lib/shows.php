<?php
/*
 * Manifest of the shows FacadeKit published to this player (shows.json), keyed by playlist.
 * FacadeKit registers one after it uploads the video and saves the playlist; the settings
 * page lists them with Play and Stop buttons that call FPP's own playlist API.
 */

if (defined('FK_PLUGIN_SHOWS')) {
    return;
}
define('FK_PLUGIN_SHOWS', 1);

require_once __DIR__ . '/common.php';

function fk_shows_list(): array
{
    $data = fk_read_json('shows.json', array());
    $shows = (is_array($data) && isset($data['shows']) && is_array($data['shows'])) ? $data['shows'] : array();
    $out = array();
    foreach ($shows as $show) {
        if (is_array($show) && isset($show['playlist']) && is_string($show['playlist'])) {
            $out[] = $show;
        }
    }
    usort($out, function ($a, $b) {
        return strcmp((string)($b['published_at'] ?? ''), (string)($a['published_at'] ?? ''));
    });
    return $out;
}

/** Playlist names of the published shows (for the "FacadeKit shows only" scope). */
function fk_shows_playlists(): array
{
    $names = array();
    foreach (fk_shows_list() as $show) {
        $names[$show['playlist']] = true;
    }
    return $names;
}

/**
 * Validate a manifest sent by FacadeKit. Returns array(manifest, null) or array(null, error).
 */
function fk_shows_validate($in): array
{
    if (!is_array($in)) {
        return array(null, 'body must be a JSON object');
    }
    $str = function (string $key, int $max, bool $required) use ($in) {
        if (!array_key_exists($key, $in) || $in[$key] === null || $in[$key] === '') {
            return $required ? false : null;
        }
        if (!is_string($in[$key]) || strlen($in[$key]) > $max || preg_match('/[\x00-\x1f\x7f]/', $in[$key])) {
            return false;
        }
        return $in[$key];
    };
    $playlist = $str('playlist', 128, true);
    if ($playlist === false || !fk_valid_playlist_name($playlist)) {
        return array(null, 'playlist must be an FPP playlist name (no / or \\)');
    }
    $video = $str('video', 128, true);
    if ($video === false || !fk_valid_media_name($video)) {
        return array(null, 'video must be a file name of letters, digits, ".", "_" or "-"');
    }
    $name = $str('name', 128, false);
    $project = $str('project', 128, false);
    $projectId = $str('project_id', 64, false);
    $published = $str('published_at', 40, false);
    $calibrationId = $str('calibration_id', 64, false);
    $calibratedAt = $str('calibrated_at', 40, false);
    $version = $str('facadekit_version', 32, false);
    $sequence = $str('sequence', 128, false);
    foreach (array('name' => $name, 'project' => $project, 'project_id' => $projectId,
                   'published_at' => $published, 'calibration_id' => $calibrationId,
                   'calibrated_at' => $calibratedAt, 'facadekit_version' => $version,
                   'sequence' => $sequence) as $key => $value) {
        if ($value === false) {
            return array(null, $key . ' must be a short text');
        }
    }
    if ($projectId !== null && !preg_match('/^[A-Za-z0-9_-]+$/', $projectId)) {
        return array(null, 'project_id has unexpected characters');
    }
    $age = null;
    if (isset($in['calibration_age_days']) && $in['calibration_age_days'] !== null) {
        if (!is_int($in['calibration_age_days']) && !is_float($in['calibration_age_days'])) {
            return array(null, 'calibration_age_days must be a number');
        }
        $age = (float)$in['calibration_age_days'];
        if ($age < 0 || $age > 36500) {
            return array(null, 'calibration_age_days is out of range');
        }
    }
    return array(array(
        'name' => $name ?? $playlist,
        'playlist' => $playlist,
        'video' => $video,
        'sequence' => $sequence,
        'project' => $project,
        'project_id' => $projectId,
        'published_at' => $published ?? fk_now_iso(),
        'calibration_id' => $calibrationId,
        'calibrated_at' => $calibratedAt,
        'calibration_age_days' => $age,
        'facadekit_version' => $version,
        'registered_at' => fk_now_iso(),
    ), null);
}

function fk_shows_upsert(array $manifest): void
{
    fk_with_lock('shows', function () use ($manifest) {
        $shows = array();
        foreach (fk_shows_list() as $show) {
            if ($show['playlist'] !== $manifest['playlist']) {
                $shows[] = $show;
            }
        }
        array_unshift($shows, $manifest);
        fk_write_json('shows.json', array('shows' => array_slice($shows, 0, FK_MAX_SHOWS)));
    });
}

function fk_shows_delete(string $playlist): bool
{
    return fk_with_lock('shows', function () use ($playlist) {
        $shows = fk_shows_list();
        $kept = array();
        foreach ($shows as $show) {
            if ($show['playlist'] !== $playlist) {
                $kept[] = $show;
            }
        }
        if (count($kept) === count($shows)) {
            return false;
        }
        fk_write_json('shows.json', array('shows' => $kept));
        return true;
    });
}
