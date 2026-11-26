# 20. FacadeKit FPP plugin (projector control on the Pi)

Status: built and tested **against fakes only** (T-209). Not yet installed on a real Pi, not
yet pointed at a real projector. PHP was not available on the build machine, so the PHP was
checked statically and its shell glue was run; the PHP itself has not been executed yet (§8).

Tags as in docs/17: **[V]** read in FPP 10.4's source (tag `10.4`, commit `123b8ca`, plus the
`fpp-plugin-Template` repository's `PLUGIN_GUIDELINES.md` and `PLUGININFO_FORMAT.md`); **[T]**
covered by a test here; **[A]** assumption that only the real Pi or projectors can settle.

## 1. Why a plugin

The projectors sit on a private wired network that only the Pi can reach (Pi `eth0`
192.168.50.1, projectors .10 and .11); the Mac running FacadeKit is on the home Wi-Fi. So
anything that switches a projector has to run on the Pi. The stock
FPP-Plugin-Projector-Control handles one projector, installs Net::PJLink from CPAN and has
quirky password handling (its own privacy block says a password is used only when it starts
with a digit and is then written to its log). This plugin replaces it and the hand-written
`pjlinkutil.pl` scripts, and adds what FacadeKit needs: a list of published shows and a
status/health route.

FacadeKit shows stay ordinary FPP playlists, so they coexist with xLights sequences and the
DMX moving heads in the same FPP playlists and schedules. DMX stays in FPP/xLights.

## 2. Layout (`integrations/fpp-plugin/`)

| File | Role |
|---|---|
| `pluginInfo.json` | FPP 10.4+ metadata with the full `privacy` block (all eight keys), no dependencies |
| `menu.inc` | one menu entry, Status/Control > FacadeKit Projectors (guideline 9.1: one per area) |
| `setup.php` | settings and status page (Bootstrap classes only: light and dark themes, phone widths) |
| `ajax.php` | the page's back end (`plugin.php?plugin=…&page=ajax.php&nopage=1`) |
| `api.php` | FPP's plugin API hook: `getEndpointsfpppluginFacadeKit()` → `/api/plugin/fpp-plugin-FacadeKit/*` [V `controllers/plugin.php` `collectPluginEndpoints`] |
| `apiDocs.json` | OpenAPI fragment for FPP's `/api` page (guideline 2.10) |
| `callbacks.sh` | `--list` → `playlist`; playlist start/stop events → `lib/cli.php playlist-event` [V `src/Plugins.cpp`] |
| `commands/descriptions.json` + `on.sh off.sh input.sh status.sh` | four FPP Commands [V `LoadPluginCommands`, `ScriptCommand`] |
| `scripts/fpp_install.sh`, `fpp_uninstall.sh` | create/remove `plugindata/fpp-plugin-FacadeKit` (0700); nothing fetched |
| `lib/pjlink.php` | PJLink class 1 client, plain PHP sockets |
| `lib/power.php` | power sequences, parallel workers, automatic power, fault guard |
| `lib/api_core.php` | REST routes, token |
| `lib/common.php`, `fpp.php`, `shows.php`, `cli.php` | paths/config/log/validation, loopback FPP API, show manifest, CLI entry |
| `dev/router.php`, `dev/serve_dev_repo.py` | `php -S` router for tests; the sandbox-Pi dev loop (§6) |

Zero install-time dependencies: PHP 8 CLI, bash and the PHP core only (no CPAN, no pip, no
apt). `ctype`, `posix` and `curl` are not required.

## 3. Design

### 3.1 PJLink client (`lib/pjlink.php`)

Class 1 over TCP 4352: greeting `PJLINK 0` / `PJLINK 1 <random>`; with authentication the
first command is prefixed with `md5(random . password)`; `PJLINK ERRA` = wrong password;
`ERR1..ERR4` = undefined / bad parameter / unavailable now / projector failure. Commands used:
`POWR` (0 standby, 1 on, 2 cooling, 3 warm-up), `INPT`, `ERST` (fan, lamp, temperature, cover,
filter, other: ok / warning / error), `LAMP` (hours per light source; laser models may answer
ERR1, which is shown as "no hours"), `NAME`, `INF1`, `INF2`, `INFO`, `CLSS`.

One command per TCP connection (some Panasonic models close after each answer), a connect
timeout and an overall answer deadline (settings, default 3 s and 5 s), control bytes and NUL
padding skipped, CR, LF or CR LF accepted, stray lines skipped. A class 2 greeting is refused
with a clear message. The password only ever goes into the MD5 digest; it is not logged, not
returned to the browser, not put on a command line and not in an exception message.

### 3.2 Power, generic and projector-led

- Every decision follows what the projector reports in `POWR`. Nothing assumes a lamp.
- **On**: standby → `POWR 1`, ask again at once; on → select the input (if set); warm-up →
  wait (poll 2 s) up to the projector's `ready_timeout_s` before selecting the input;
  cooling → wait up to `cooldown_timeout_s` and only then send `POWR 1`. **`POWR 1` is never
  sent while the projector reports cooling** [T]. `ERR3` is retried until the deadline.
- **Off**: on → `POWR 0`; standby or cooling → nothing to do; warm-up → retry `POWR 0`
  (projectors answer ERR3 then) until `ready_timeout_s`.
- **Per-projector timing** (projector table): `ready_timeout_s` (default **5**),
  `cooldown_timeout_s` (default **30**, used only while the projector says cooling),
  `off_delay_s` (default **0**, the automatic power-off delay). The owner's Panasonic
  PT-RZ570 laser projectors are said to switch without warm-up or cool-down, so these
  near-zero defaults make on/off immediate; a lamp projector just needs larger values.
  **Real RZ570 behaviour is unverified [A]**: whether it reports 3/2 briefly, whether it
  answers `INPT` in standby, what `LAMP` returns, and whether it closes after each answer.
- **Parallel and isolated**: each projector runs in its own `php cli.php worker …` process;
  a projector that does not answer only delays its own result [T: one dead, one live].
- **Newest request wins**: each request takes a sequence number per projector
  (`intents.json`); an older worker that sees a newer number stops, so a show that starts
  cancels a pending delayed power-off. One worker per projector at a time (`flock`).
- States reported: `on`, `standby`, `warmup`, `cooling`, `busy` (ERR3 on the query),
  `unknown` (no answer).

### 3.3 FPP Commands (playlists, scheduler, presets)

| Command | Arguments | Notes |
|---|---|---|
| FacadeKit Projectors On | `wait` (bool), `timeout` (0–900 s, 0 = per projector), `projectors` | With wait, the playlist waits until every projector reports on or its timeout passes; use it in a lead-in. |
| FacadeKit Projectors Off | `wait`, `projectors` | |
| FacadeKit Select Input | `input` (PJLink code list, e.g. `31 Digital 1 (HDMI 1)`), `projectors` | first two characters are used |
| FacadeKit Status Snapshot | – | writes each projector's state to the log and status panel |

fppd runs these scripts directly, with the arguments as separate argv entries and only
`MEDIADIR`, `FPPDIR`, `SCRIPTDIR` in the environment (no PATH) [V `ScriptCommand::run`], and
it waits for them (`waitpid`) on the calling thread. So the scripts use absolute paths and
`exec /usr/bin/php … "$@"`; without `wait` they hand the work to background workers and return
at once. `projectors` uses `contentListUrl` = `api/plugin/fpp-plugin-FacadeKit/projector-names`
[A: that FPP's command editor fills a datalist from a plugin route].

### 3.4 Automatic power and the fault guard (all off by default)

`callbacks.sh` receives playlist `start` / `stop` [V `Playlist.cpp`]; fppd waits for it, so it
only spawns workers. Scope: **only playlists FacadeKit published** (the manifest, default) or
**every playlist**, so xLights-only shows do not wake the projectors.

- *Power on at start*: workers without waiting. For a picture from the first second, put
  "Projectors On" with wait in the playlist's lead-in instead.
- *Power off at stop*: each projector after its `off_delay_s`; skipped when a show in scope
  is playing again by then, or superseded by a new on.
- *Fault guard*: while a show in scope plays, every `guard_interval_s` (30 s) read POWR and
  ERST. An ERST error, ERR4, or a projector in standby/cooling after its ready timeout plus a
  grace period marks the plugin **unhealthy** (status panel, `/health`, FacadeKit card) and,
  if "stop the playlist" is on, calls FPP's `POST /api/playlists/stop` on the loopback. Three
  missed answers in a row only mark it unhealthy (no stop). One guard at a time; it ends when
  nothing in scope is playing, FPP reports idle, or the recorded show is cleared.

*Stop events.* FPP 10.4 sends the real playlist name with `start` (and `playing`), but the `stop`
event, for Stop Now and the natural end alike, carries an empty placeholder playlist (`"name":""`,
`"Section":"New"`, `"currentEntry":null`) [verified on a Pi]. Matching the event name alone
would therefore never switch the projectors off. The plugin records the in-scope show it saw
start in `active_show.json` (a second start replaces it); a stop with an empty or out-of-scope
name ends the recorded show (log by name, per-projector `off_delay_s`, guard ends) and clears
the record, and with no record it is ignored. Before acting, `/api/fppd/status` is checked so a
stale record or another in-scope playlist that is playing never causes a power-off.

### 3.4b Thermal history and opt-in recovery (`lib/recovery.php`)

PT-RZ570 `ERST?` is six characters; the third is the temperature severity (`0` ok, `1`
warning, `2` error). Numeric sensor readings (intake, optics, exhaust, light source) exist on
Panasonic's web status interface only; they stay a diagnostics follow-up until an
authenticated read-only method, its freshness and its availability while running, cooling and
in Normal standby are verified on both real units. ECO standby disables network control, so a
projector in ECO standby cannot be monitored or retried at all. Recovery uses PJLink only.

*History (always, whenever the guard, a status read or a Test reads ERST).* Per projector in
`thermal.json`: the last known level, the time of the last **valid** reading, and the events
**warning** (first warning), **escalation** (to error) and **clearance** (a fresh valid `ok`
after a warning or error), each logged once with the projector's name. A reading that did not
arrive (timeout, wrong password, ERR1 unsupported, ERR3 busy, ERR4 sensor failure) changes
nothing: the last level is kept and nothing is cleared. A warning alone never touches playback.

*Recovery (off by default, `recovery_enabled`).* While a show in scope plays the guard keeps
watching after a fault (it used to end at the first one) and moves each projector through:

| state | meaning |
|---|---|
| Monitoring | normal; a fresh valid error reading starts the cooldown |
| Cooling down, no power-on | no power-on is sent; waits `recovery_cooldown_s` (default 300, min 30) |
| Checking health | after the cooldown, needs `recovery_healthy_reads` (default 3) **consecutive** readings that are fresh (< 20 s), valid, not cooling/warming, temperature ok and no ERST field in error; any other reading resets the count |
| Retrying power-on | once, only if the projector is in standby, still wanted on, and the per-show budget (1) is unused; the existing power worker sends POWR 1 |
| Needs attention | failed retry, error back after the retry, retry not on within 120 s, budget used, or no health within cooldown + 30 min; reason in the log and the status table |

If the projector is still **on** when health returns it is only confirmed (no power cycle, no
budget used). A timer, a missing answer, a wrong password, an unsupported query or a sensor
failure never counts as clearance. Everything is a pure function (`fk_recovery_step`) of the
reading and the clock, walked through in `tests/test_fpp_plugin_recovery.py`.

*Cancel, and nothing queued survives it.* Manual projector Off (UI, API, the scheduled
"Projectors Off" command), the stop event (Stop Now, scheduled end and natural end, including
FPP's empty-name stop), a replacement show, and turning recovery off all cancel counting and
queued retries (`fk_recovery_cancel`). The retry runs as a normal worker under the projector
lock with a one-off token; right before POWR 1 the worker re-checks the token, the show, the
operator's wish and its intent number, so a cancel racing the retry wins (`cancelled`). Power-on
requests (auto-on, "Projectors On", the API) are **held** for a projector whose last known
level is `error` or whose recovery is in progress. A fresh monitor process treats anything
left in progress by an earlier process (daemon restart, plugin reload) as unknown: no retry
until a new confirmed error under the current show.

*Playback.* Continue-show mode: the healthy projector keeps playing and the show is never
touched. With "Stop the playlist when a projector reports a fault" on, the existing behaviour
stands (the show is stopped, recovery has nothing left to do). Restarting a stopped playlist
automatically is deliberately **not** built; it needs rules for show windows, restart
position and manual cancellation and is a separately approved follow-up.

### 3.5 REST API (`/api/plugin/fpp-plugin-FacadeKit/…`)

| Route | Token | Answer |
|---|---|---|
| `GET version` | no | `{plugin, version, api: 1, paired, configured}` |
| `GET projector-names` | no | `["all", names…]` |
| `GET status` (`?cached=1`) | yes | live (parallel) or stored snapshots + health |
| `GET health` | yes | stored projector state, summary, FPP player state (`/api/fppd/status` on the loopback), the published shows, the automatic options |
| `POST projectors/on`, `projectors/off` | yes | `{wait, timeout_s, projectors}` → per-projector results |
| `GET shows`, `POST shows`, `DELETE shows/{playlist}` | yes | the show manifest |

Manifest fields: `name`, `playlist`, `video` (required; FPP's own name rules), `sequence`,
`project`, `project_id`, `published_at`, `calibration_id`, `calibrated_at`,
`calibration_age_days`, `facadekit_version`. The settings page lists them with **Play** /
**Stop**, which call FPP's own `POST /api/playlist/{name}/start` and `/api/playlists/stop`.

### 3.6 FacadeKit side

- `fpp/plugin_client.py`: `FppPluginClient` (routes above), `plugin_summary()` (never
  raises), token store per host in `<data dir>/secrets/fpp_plugin_tokens.json` (0600) or
  `FACADEKIT_FPP_PLUGIN_TOKEN`. FPP's UI password (`FACADEKIT_FPP_PASSWORD`) still applies.
- `fpp/plugin_fake.py`: the plugin's routes on the fake FPP (`create_fake_fpp(plugin=…)`).
- `fpp/pjlink_fake.py`: fake PJLink projectors (§6.2).
- Publish: after the playlist is saved, register the manifest and, with `power_on`, switch
  the projectors on (waiting when the show starts now) before starting. Plugin absent, not
  paired or failing → the publish still succeeds; `result.plugin.status` says why [T].
- Web: a "Projectors" card on the Send to Falcon Player page (absent / pair / status, faults,
  show count, On / Off), and a "Switch the projectors on" checkbox once paired.
- CLI: `facadekit fpp plugin-status HOST`, `facadekit fpp fake-pjlink`. The S-1 recorder
  (`facadekit spike fpp-contract`) adds `plugin` (SKIP when absent) and `plugin health`
  (with a stored token) steps; GETs only, redacted like the rest.

## 4. Security

- **Token**: `fkp_` + 48 hex characters from `random_bytes`; only its SHA-256 is stored
  (`plugindata/…/token.json`, 0600); compared with `hash_equals` (constant time); shown once
  on the settings page. Sent as `X-FacadeKit-Token` (preferred, because FPP's UI password
  uses `Authorization: Basic`) or `Authorization: Bearer` [A: whether php-fpm passes
  `Authorization` through FPP's Apache]. A wrong token costs 300 ms and a log line.
- **Inert until configured**: no token → every protected route answers 403 `not_paired`; no
  projector → commands log "nothing to do"; automatic power and guard default off.
- **Callers**: loopback or private addresses only (RFC 1918, link-local, ULA); others 403.
- **Settings page**: POST with `X-FK-Request: 1` only, and a present `Origin` must be this
  host (a cross-site page cannot add the header without a CORS preflight FPP does not grant).
  It never returns a password (only `has_password`) or the token hash.
- **No shell injection**: PHP never calls `exec`, `system`, `shell_exec`, `passthru`,
  `popen` or backticks [T static]. Processes start with `proc_open` and an argv array; the one
  `/bin/sh -c` uses a constant script (`"$0" "$@" …&`), so values are positional arguments
  [T: run with a hostile argument]. The shell scripts only pass `"$@"` / one JSON argument
  [T: run with `$(…)`, backticks, `;`].
- **Validation**: host (IP or DNS name), port, PJLink password (1–32 printable characters),
  input code, names, FPP playlist/media name rules, numeric ranges.
- **Logging**: one file, `logs/plugin-fpp-plugin-FacadeKit.log`, opened per write so FPP's
  own rotation (daily, over 10 MB) is followed (guideline 1.2: no own rotation). Every line
  passes `fk_redact` (configured passwords, token-like strings, digests, `password=` pairs),
  and again when the page shows it. Passwords are never passed to `fk_log` [T static].
- **Storage**: passwords and token hash in `plugindata/` (not `config/`, which FPP's crash
  bundle and JSON backup copy, guideline 14.11). Uninstall deletes it unless FPP is
  reinstalling (`FPP_PLUGIN_REINSTALL=1`, FPP 10.4) [V].
- **Background workers from the web server**: descriptors 3–9 are closed so a worker cannot
  hold php-fpm's connection open [A].

## 5. Install

The real `pluginInfo.json` points at a future public repository
`evhxn/fpp-plugin-FacadeKit` (FPP clones the plugin to its own folder, so it must be the
root of a repository; the FacadeKit monorepo is not). Until that exists, install with the
dev loop below. FPP's Plugin Manager only fetches plugin info from GitHub, and its install API
refuses a plugin with a privacy block unless the Plugins page showed it, which a script must
not fake [V `FetchPluginInfoProxy`, `POST /plugin` docblock], so the dev loop installs over SSH.

## 5b. The public repository

FPP installs plugins with `git clone` of a repository whose root is the plugin, so the plugin
is also published as its own small repository, `evhxn/fpp-plugin-FacadeKit` (GPL-3.0, one
snapshot commit per release, no monorepo history). The monorepo stays the source of truth.
To refresh the public copy after a change, build the tree and commit it there:

```sh
python3 integrations/fpp-plugin/dev/export_public_repo.py --out /tmp/fpp-plugin-FacadeKit
```

The script leaves out `dev/` and this design document, adds the LICENSE, a README and the user
guide, and refuses to export if the tree contains an email address, a local path, a pairing
token or a stored password.

## 6. Sandbox Pi dev loop

### 6.1 Serve and install

On the Mac (from the Terminal app; see docs/17 §7 on macOS Local Network permission):

```sh
python3 integrations/fpp-plugin/dev/serve_dev_repo.py     # --port 8765 by default
```

It copies the plugin into its own git repository under `~/.cache/fpp-plugin-FacadeKit-dev`,
commits a snapshot (fast-forward each run), `git update-server-info`, serves it read-only with
`http.server` and prints the steps below with the Mac's address filled in. On the Pi:

```sh
cd /home/fpp/media/plugins
git clone http://<mac-ip>:8765/fpp-plugin-FacadeKit.git fpp-plugin-FacadeKit
sudo FPPDIR=/opt/fpp fpp-plugin-FacadeKit/scripts/fpp_install.sh
curl -X POST http://localhost/api/fppd/plugin/fpp-plugin-FacadeKit/load   # or restart fppd
```

Update: run the script again, then on the Pi `git pull` in the plugin folder, the install
script, and the `load` call. Remove: FPP's Plugin Manager > Uninstall, or
`sudo /opt/fpp/scripts/uninstall_plugin fpp-plugin-FacadeKit` (runs our uninstall, which
deletes `plugindata/fpp-plugin-FacadeKit` and the plugin log; `git` itself leaves nothing
else behind). The sandbox Pi has no projectors, Ethernet or HDMI attached.

### 6.2 Fake projectors on the Mac

```sh
cd studio/backend
uv run facadekit fpp fake-pjlink --host 0.0.0.0 --port 4352 --count 2
#   or: python -m facadekit.fpp.pjlink_fake …   or: python3 src/facadekit/fpp/pjlink_fake.py …
```

Then on the Pi's plugin page add projectors with the Mac's Wi-Fi address and ports 4352 and
4353. Defaults: instant on/off like a laser model (Panasonic PT-RZ570 identity, input 31,
1234 h). Options: `--password <test password>` (turns PJLink authentication on),
`--warmup S` / `--cooldown S` (lamp behaviour, ERR3 during transitions), `--quirk` one of
`close-after-reply split-writes crlf nul-padding slow-greeting no-lamp silent
lowercase-random err4`. It prints every power change. Use a throwaway test password, not the
projectors' real one. macOS may ask to allow incoming connections for Python.

## 7. Recommended settings for the owner's setup

Projector table: `Left 192.168.50.10:4352`, `Right 192.168.50.11:4352`, their PJLink
password, input `31` (HDMI 1) [A: which input the Pi's HDMI is cabled to], ready wait 5 s,
cooling wait 30 s, off delay 0 s. Options: scope "Only shows FacadeKit published"; turn on
automatic power when ready; fault guard on, stop-on-fault off until the guard has been
watched through a few shows. In each FacadeKit playlist's lead-in: "FacadeKit Projectors On"
with wait (the RZ570 should make this near instant).

## 8. Verified vs unverified

Verified here (all against fakes, on macOS):

- Fake PJLink server: protocol, authentication, ERRA, warm-up/cooling with ERR3, quirks [T].
- pluginInfo.json against the template schema rules; command descriptions; apiDocs vs routes [T].
- Shell scripts: syntax, +x, shebang, arguments passed untouched; the detach shell [T].
- PHP static checks: brackets, every called `fk_` function and PJLink method defined, no
  shell functions, no closing tags in pure PHP [T].
- FacadeKit client, fake plugin, publish with/without plugin, API routes, CLI, S-1 steps,
  web card [T]. Dev loop: repository build, fast-forward, dumb-HTTP clone on 127.0.0.1 [T].

`tests/test_fpp_plugin_php.py` and `tests/test_fpp_plugin_recovery.py` run the PHP for real
against the fake projectors and `php -S` when `php` is present (`brew install php`;
`FK_REQUIRE_PHP=1` makes a missing php a failure). They ran clean on PHP 8.6 on the Mac.

Unverified, needs the Pi or the projectors [A]:

1. The plugin on FPP 10.4 at all: menu, page rendering in FPP's theme, `api.php` routing,
   live command registration after `load`, callbacks firing, `contentListUrl` filling the
   projector list, Apache passing `X-FacadeKit-Token`.
2. fppd's user (root) vs the web user (fpp) sharing `plugindata` files (`fk_fix_owner`).
3. Background workers started from php-fpm not holding the request open.
4. Real PJLink on the PT-RZ570: authentication with the owner's password, states it reports,
   `LAMP`/`INPT` answers in standby, connection behaviour, ERST codes, timing.
5. Two projectors switched in parallel on the real wired network; fault guard during a real
   show; stop-on-fault with xLights/DMX playlists running.
6. macOS firewall prompts for the dev server and the fake projectors.
7. Thermal recovery on the real PT-RZ570s: whether `ERST?` answers (and answers truthfully) in
   Normal standby and while cooling, what the projector reports during its own thermal
   protection, and how long it takes to clear. If `ERST?` does not answer in standby the
   recovery can only end in **Needs attention** (never in a blind retry). Check with the
   plugin's Test button and the status table's "Last good reading" under normal conditions;
   do not overheat the units to find out.

## 9. Limits

- PJLink class 1 only (class 2 greeting refused; no AV mute, freeze or class 2 status).
- Up to 16 projectors and 200 recorded shows. No HTTPS of its own (FPP serves plain HTTP).
- The token gives power control and the show list, nothing else; anyone who can open FPP's
  UI can also use the settings page (as with every FPP page).
- Not in this phase: DMX/moving heads (stay in FPP/xLights).

## 10. Future phase: calibration patterns through the Pi's HDMI

Sketch only, not built. Calibration today needs the projector cabled to the Mac. A later
phase could let the Pi show FacadeKit's Gray-code and outline patterns on its own HDMI output:
FacadeKit renders the pattern sequence as a short video or numbered PNG set, publishes it like
a show (a hidden `fk_calib_*` playlist), and the plugin gains `POST calibration/step` that
starts a single frame via FPP's own media/overlay API and answers when it is on screen; the
camera stays on the Mac, which steps through the patterns over the token API. Open points:
frame-accurate timing through FPP's video output, HDMI resolution/overscan (spike S-8), and
keeping a running show from colliding with a calibration session (refuse while playing).
