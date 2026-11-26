# 17. FPP integration audit (FPP 10.x)

Status: audit done against the FPP **10.4** source; fixes applied; the S-1 recorder
(`facadekit spike fpp-contract`) is built. **Not yet run against a real Pi.** One real reply is
known: the owner's FPP 10.4 answered `GET /api/fppd/version` with
`{"Message":"","Status":"OK","branch":"v10.4","fppdAPI":"v1","majorVersion":"10","minorVersion":"4","respCode":200,"version":"10.4"}`
(kept as `studio/backend/tests/fixtures/fpp/fppd_version_10_4.json`).

Tags used here: **[V]** read in FPP's source at the tag below; **[R]** seen on a real FPP;
**[A]** assumption that only a real Pi can settle (spike S-1).

## 1. What was read

- Repository: <https://github.com/FalconChristmas/fpp>, tag **`10.4`**, commit
  **`123b8ca089b98a54f7f5c0b7ee7b72ed18217f21`** (the `v10.4` branch head was the same commit
  on 2026-11-23). Links below point at that tag: `https://github.com/FalconChristmas/fpp/blob/10.4/<path>#L<line>`.
- Files: `www/api/index.php` (route table), `www/api/controllers/files.php`, `playlist.php`,
  `media.php`, `system.php`, `docs.php`, `www/api/lib/limonade.php` (body parsing),
  `www/config.php` (`GetDirSetting`), `www/common.php` (`FppdCommand`),
  `www/common/settings.php` (UI password), `www/playlistEntryTypes.json`,
  `etc/apache2.site` (Apache: port, proxy to fppd, password, cross-site gate, CORS),
  `src/httpAPI.cpp` (`/fppd/version`, `/fppd/status`), `src/fppversion.sh` (version strings),
  `src/commands/PlaylistCommands.cpp`, `src/commands/Commands.cpp` (command replies),
  `www/api/openapi.json`, `docs/DEPRECATED.md`.
- How: shallow sparse clone of the tag (`git clone --depth 1 --branch 10.4`), read locally.
  Nothing was sent to any FPP device.

## 2. Every FPP call FacadeKit makes

| # | FacadeKit call (`fpp/client.py`) | Request | What FPP 10.4 does [V] | Source | Mismatch before this audit | Severity | Fixed |
|---|---|---|---|---|---|---|---|
| 1 | `version()` | `GET /api/fppd/version` | Apache proxies `/api/fppd/*` to fppd on `localhost:32322`. Reply: `version`, `majorVersion`, `minorVersion`, `branch`, `fppdAPI`, plus `Status`, `Message`, `respCode`. **`majorVersion`/`minorVersion` are strings** (`getFPPMajorVersion()` returns `const char*`); `fppdAPI` is `"v1"`. The OpenAPI example (ints, `fppdAPI: 4`) is out of date. [R] confirms strings. | [httpAPI.cpp#L1463](https://github.com/FalconChristmas/fpp/blob/10.4/src/httpAPI.cpp#L1463), [fppversion.h](https://github.com/FalconChristmas/fpp/blob/10.4/src/fppversion.h), [apache2.site#L197](https://github.com/FalconChristmas/fpp/blob/10.4/etc/apache2.site#L197) | docs/05 §7 said "`majorVersion` (int)". The client already parsed strings (`int("10")`), so no runtime bug; the fake sent ints. | Low (doc/fake) | docs/05 corrected (flagged); fake sends strings; fixture test |
| 2 | version formats | – | `fppversion.sh`: version is `git describe --tags`: `10.4`, `10.1.2`, `10.4-12-gabc1234[-dirty]`; on master `10.x-master-N-g…` with `minorVersion` = 1000+N; an exported tree without git can give `Unknown` (then `majorVersion` is `Unknown`). | [fppversion.sh#L56](https://github.com/FalconChristmas/fpp/blob/10.4/src/fppversion.sh#L56) | `Unknown` gave a vague "no version number found". | Low | Clear `bad_version` message; tests for each format |
| 3 | `MIN_FPP_MAJOR = 10` check | – | Major is the first dot field. 10.x is the only target (ADR-023). A future 11 passes the check. | same | None. 11.x would be accepted unaudited. | Note | Recorder WARNs on a major above 10 ("re-run the audit") |
| 4 | `system_info()` | `GET /api/system/info` | JSON object: `HostName`, `HostDescription`, `Platform`, `Variant`, `Mode`, `Version`, `Branch`, `majorVersion`, `minorVersion`, `typeId`, `uuid`, `IPs`, … | [system.php#L1663](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/controllers/system.php#L1663) | None (FacadeKit reads `Platform`, `Variant`, `HostName` if present). | – | – |
| 5 | directory key | `videos` / `sequences` | `GetDirSetting()` maps keys **case-insensitively**: `sequences`, `music`, `videos`, `images`, `effects`, `scripts`, `logs`, `uploads`/`upload`, `docs`, `boot`, `config`, `playlists`, … **`videos` is right; `video` and `media` are not keys.** The folder on disk is `<media>/videos`. | [config.php#L760](https://github.com/FalconChristmas/fpp/blob/10.4/www/config.php#L760) | The default was an [A]; it is correct. Still configurable (`fpp.video_dir`), as CLAUDE.md requires. | – | Comment now says [V source] |
| 6 | `list_files(dir)` | `GET /api/files/{DirName}` | `{"status":"ok","files":[{name, mtime, sizeBytes, sizeHuman, playtimeSeconds?}]}`; subfolders appear as entries with `"isDirectory": true`; `?nameOnly=1` gives a bare list. **An unknown key answers HTTP 200 `{"status":"Invalid Directory"}` with no `files`.** | [files.php#L383](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/controllers/files.php#L383) | (a) An unknown directory (for example a mistyped `fpp.video_dir`) came back as an **empty list**, so publish uploaded and then said "FPP does not list the file", and `/fpp/sequences` showed no sequences with no reason. (b) Subfolder names were returned as file names. | **High** (a), Low (b) | (a) error `bad_directory` naming the folder and the setting; publish checks the folder **before** uploading. (b) directories skipped |
| 7 | `upload()` start | `POST /api/file/{DirName}` (empty body) | Same handler as PATCH; for POST it returns `uniqid("", true)` as the body (plain text, not JSON) and does nothing else. | [files.php#L1520](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/controllers/files.php#L1520), [index.php#L86](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/index.php#L86) | None (body ignored). The fake now returns plain text too. | – | fake |
| 8 | `upload()` chunks | `PATCH /api/file/{DirName}` headers `Upload-Name`, `Upload-Offset`, `Upload-Length`; raw body | **Raw body, not multipart; no query parameters.** Any `Content-Type` is fine (body read from `php://input`). Offset and length must be digits (else 400). Name may not contain `..`, `/`, `\` (400 "Invalid filename"). Unknown dir → 400. Each chunk is stored as `<name>.patch.<offset>`; offset 0 restarts; offset > 0 with no upload in progress → 409. When all bytes are there, the **last PATCH assembles the whole file** before answering `{"status":"OK","file","dir","size"}`; errors answer 500 `{"status":"failed","error":…}`. Apache `LimitRequestBody` is 4 TiB, so 8 MiB chunks are fine. | [files.php#L1640](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/controllers/files.php#L1640), [apache2.site#L396](https://github.com/FalconChristmas/fpp/blob/10.4/etc/apache2.site#L396) | (a) `sanitize_name("a..b.mp4")` kept `..`, which FPP refuses. (b) The last chunk's reply was not checked. (c) The last PATCH can take as long as copying the file on the SD card, but had the same 120 s timeout as any chunk: a large render could time out after FPP had in fact stored it. | Medium (a, c), Low (b) | (a) `..` collapsed and refused; (b) `status` and `size` checked; (c) last-chunk timeout = 120 s + size / 5 MB/s [A: SD card speed] |
| 9 | `upload()` alternative | `POST /api/file/{DirName}/{Name}` | Single raw-body upload (also exists). Not used: it writes straight into the target with no assembly step. | [files.php#L1788](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/controllers/files.php#L1788) | – | – | – |
| 10 | `media_duration()` | `GET /api/media/{MediaName}/duration` | `{"<name>": {"duration": seconds, …}}`; 404 when not found. | [media.php#L58](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/controllers/media.php#L58) | None. (Publish verifies by listing; docs/02 §6.4 says duration. Both work.) | – | – |
| 11 | `put_playlist()` | `POST /api/playlist/{PlaylistName}` JSON | Body is decoded only when `Content-Type` starts with `application/json` (httpx's `json=` does). Name may not be empty or contain `/`, `\`, NUL (400). FPP does **not** check `mainPlaylist`; it adds `version: 4` and recomputes `playlistInfo` (`leadIn_items`, `mainPlaylist_items`, `total_items`, `*_duration`, …) and answers with the saved playlist. **A failed write still answers HTTP 200** with `"Status":"Error","Message":"Unable to open file for writing"`. | [playlist.php#L811](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/controllers/playlist.php#L811), [limonade.php#L471](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/lib/limonade.php#L471) | A 200 with `Status: Error` counted as success. | Medium | Body checked; error `bad_status` |
| 12 | playlist entry | `{"type":"media","enabled":1,"playOnce":0,"mediaName","videoOut":"--Default--"}`; `both` adds `sequenceName` | The validator and the UI's entry definitions confirm types `media` and `both`, fields `mediaName`, `sequenceName`, `videoOut`; `--Default--` is an accepted video output ("uses the global Video Output setting"). The UI also writes `fileMode` (default `single`; empty is treated as single) and `streamSlot` (default 1); both are optional. | [playlist.php#L169](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/controllers/playlist.php#L169), [playlist.php#L128](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/controllers/playlist.php#L128), [playlistEntryTypes.json](https://github.com/FalconChristmas/fpp/blob/10.4/www/playlistEntryTypes.json) | None found. Whether fppd plays such an entry exactly like a UI-made one is [A] until S-1's write test reads one back and the owner plays it. | – | Recorder compares with a UI-made entry |
| 13 | `get_playlist()` (new, recorder) | `GET /api/playlist/{PlaylistName}` | The stored JSON; 404 `{"Status":"Error","Message":"Playlist not found: …"}`. | [playlist.php#L763](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/controllers/playlist.php#L763) | – | – | added |
| 14 | `list_playlists()` (new, recorder) | `GET /api/playlists` | Sorted JSON list of names. | [playlist.php#L14](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/controllers/playlist.php#L14) | – | – | added |
| 15 | `delete_playlist()`, `delete_file()` (new, write test only) | `DELETE /api/playlist/{Name}`, `DELETE /api/file/{Dir}/{Name}` | `{"Status":"OK"}` / `{"status":"OK","file","dir"}`; a missing playlist answers 200 with `Status: Error`; a missing file 404. | [playlist.php#L884](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/controllers/playlist.php#L884), [files.php#L1393](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/controllers/files.php#L1393) | – | – | added |
| 16 | `start_playlist()` | `POST /api/playlist/{PlaylistName}/start` | PHP calls fppd's `Start Playlist` command. fppd answers **plain text `Playlist Starting`, HTTP 200, whether or not the playlist exists** (it only logs). The GET form is deprecated until FPP 11. | [playlist.php#L1057](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/controllers/playlist.php#L1057), [PlaylistCommands.cpp#L101](https://github.com/FalconChristmas/fpp/blob/10.4/src/commands/PlaylistCommands.cpp#L101), [DEPRECATED.md](https://github.com/FalconChristmas/fpp/blob/10.4/docs/DEPRECATED.md) | A start that FPP could not honour looked like success (the fake answered 404, real FPP does not). | Medium | `start_playlist` waits up to 3 s for `/api/fppd/status` to name the playlist, else error `not_started`; `confirm=False` skips it |
| 17 | `stop()` | `POST /api/playlists/stop` | fppd `Stop Now`; plain-text reply. | [playlist.php#L992](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/controllers/playlist.php#L992) | None | – | – |
| 18 | `status()` | `GET /api/fppd/status` | `status_name` (`idle`, `playing`, `paused`, `stopping …`, `testing`), `current_playlist.playlist` (`""` when idle), `seconds_played` **string**, `mode_name`, plus `host_name`, `uuid`, `time`, … In remote mode the playlist block is absent. | [httpAPI.cpp#L175](https://github.com/FalconChristmas/fpp/blob/10.4/src/httpAPI.cpp#L175) | None (string `seconds_played` already converted). | – | – |
| 19 | (recorder) API help | `GET /api/openapi.json` | All paths FacadeKit uses are listed (with the `/api` prefix). | [openapi.json](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/openapi.json), [docs.php](https://github.com/FalconChristmas/fpp/blob/10.4/www/api/controllers/docs.php) | – | – | recorder checks it |

No other FPP endpoint or command is called by FacadeKit (checked: `grep` for `FppClient` callers:
`api/routes_fpp.py`, `api/routes_calibration.py` (scan_pi), `fpp/publish.py`, `cli.py`,
`sim/e2e.py`).

## 3. Cross-cutting behaviour

| Topic | FPP 10.4 [V] | FacadeKit before | Now |
|---|---|---|---|
| HTTP / HTTPS, port | One Apache `VirtualHost *:80`; no HTTPS site in FPP's config ([apache2.site#L8](https://github.com/FalconChristmas/fpp/blob/10.4/etc/apache2.site#L8)). HTTPS only if the user adds a proxy. | Always `http://`; an `https://` prefix was **silently dropped** (downgraded). `host:port` worked. | `http://` by default; an explicit `https://` is kept (CLI and direct calls; the web UI's host field still rejects `/`); TLS failures get their own message. |
| UI password | Optional. When on, Apache asks for **HTTP Basic** auth (realm "Falcon Player") on every page **including `/api`**; users `admin` and `fpp` share the password; requests from the Pi itself skip it ([settings.php#L101](https://github.com/FalconChristmas/fpp/blob/10.4/www/common/settings.php#L101)). | A 401 was "returned 401". | 401 → new code `FPP_AUTH_REQUIRED` with what to do. Optional password from `FACADEKIT_FPP_PASSWORD` (user `admin`, or `FACADEKIT_FPP_USER`); wrong password says so. The recorder has `--ask-password`. Not stored in any file. |
| Cross-site gate / Origin | Apache refuses (403) any non-GET whose `Origin` (or `Sec-Fetch-Site`) names another site; clients that send neither are not affected ([apache2.site#L23](https://github.com/FalconChristmas/fpp/blob/10.4/etc/apache2.site#L23)). | httpx sends no `Origin`. | Same, now tested (fake implements the gate; a test asserts no `Origin` header). A 403 is reported as `forbidden`. |
| CORS | `Access-Control-Allow-*` only when "Let other websites read the API" is on. | The browser never calls FPP; the FacadeKit server does. | No change needed; documented. |
| `.local` names | FPP does not answer DNS; `.local` is mDNS. httpx uses the OS resolver: macOS resolves `.local` through Bonjour; Linux needs `nss-mdns`. | A failure was "Could not reach". | DNS failure says the name could not be found and, for `.local`, suggests the IP address. [A] Bonjour behaviour on the owner's network. |
| Timeouts | Upload assembly happens inside the last PATCH (see row 8). Start waits up to ~150 ms inside fppd. | 10 s per call; 120 s per chunk. | Same, plus a size-scaled last-chunk timeout. Status/sequences routes keep 3 s. |
| Large uploads | Chunks of any size up to Apache's 4 TiB limit; PHP time limit disabled for PATCH; retried chunks replace their copy. | 8 MiB chunks. | Unchanged (8 MiB). [A] free space and SD speed on the Pi. |
| "Could not reach" vs bad answer | – | Two codes and one generic sentence each. | `FppError.reason`: `dns`, `refused`, `timeout`, `no_route`, `not_http`, `tls`, `unreachable` (code `FPP_UNREACHABLE`); `http_status`, `forbidden`, `not_json`, `not_fpp`, `bad_version`, `bad_directory`, `bad_status`, `not_started`, `shape` (code `FPP_BAD_RESPONSE`); `auth_required`, `auth_rejected` (`FPP_AUTH_REQUIRED`); too old stays `FPP_TOO_OLD`. Each logs the exception class and errno in `facadekit.log`. `probe_fpp` returns the sentence and `reason`; the web UI now shows the detail for `FPP_UNREACHABLE` and `FPP_UPLOAD_FAILED`. |
| macOS Local Network | A process without Local Network permission gets `EHOSTUNREACH` ("No route to host") or a timeout for LAN addresses. | Generic message. | On macOS, `no_route`, `timeout` and `unreachable` add: System Settings > Privacy & Security > Local Network, for the app that started FacadeKit, or start it from Terminal. [A] the exact errno macOS gives; the owner's run will show it in `facadekit.log`. |
| Wrong product | – | An HTML page gave "version: response is not JSON". | `not_fpp`: "answered, but not like Falcon Player". |
| Remote mode | In remote (MultiSync) mode `/api/fppd/status` has no playlist block; playlists are not run locally. | Not detected. | Recorder WARNs when `Mode` is not `player`. Publish does not check it [A: rare for this audience]. |

## 4. Fake FPP

`fpp/fake.py` now follows the rows above: string version parts, case-insensitive directory
keys, `Invalid Directory`, `isDirectory` entries, `playtimeSeconds`, plain-text upload id,
JSON chunk replies with `size`, `DELETE` routes, playlists saved with `version: 4` and
recomputed `playlistInfo`, plain-text start/stop that answer 200 for a missing playlist,
`/api/media`, a short `/api/openapi.json`, optional Basic auth (`password=`), and the
cross-site gate. Fake-only behaviours were removed with their tests (400 for a playlist
without `mainPlaylist` or named `a..b`; 404 on start of a missing playlist) and replaced by
tests of the real behaviour. The fake still refuses `..` in upload names, as FPP does.

## 5. S-1 recorder

`facadekit spike fpp-contract <host> [--out DIR] [--bundle ZIP] [--write-test]
[--ask-password] [--timeout S]`

- Read-only by default (only `GET`; tested). Steps: reachable, password, version, system info,
  videos folder, sequences folder, media list, playlists, playlist entry shape (reads up to 20
  playlists and records one made in FPP's UI, preferring one with a media entry), status, API
  help. Each prints PASS / WARN / FAIL / SKIP and the differences from `EXPECTED` in
  `fpp/contract.py`; the meaning of each verdict is printed under the table. Exit code 1 if any
  step FAILs.
- Fixtures: one JSON file per reply in `--out` (default `<data dir>/spikes/fpp-contract-<time>`),
  plus `report.txt` and `report.json`. Before writing, keys for host names, IPs, MAC, SSID,
  Wi-Fi, serials/UUIDs, passwords, tokens, keys, e-mail and location are replaced by
  `<redacted>`; IPv4/IPv6/MAC values and the host given become `<ip>`, `<ip6>`, `<mac>`,
  `<host>`; the user's file and playlist names become `<name-N>.ext` (consistently; `fk_` names
  kept). Tested against the fake, a password-protected fake and a fake with mismatched shapes.
- `--write-test` (clearly labelled; asks for `yes` on an interactive terminal and never runs
  otherwise): generates a 2 s test clip, uploads it as `fk_s1_contract_test_<hex>.mp4` to the
  videos folder, checks it is listed and has a duration, saves a playlist
  `fk_s1_contract_test_<hex>` with FacadeKit's `media_entry`, reads it back and compares each
  field, then deletes the playlist and the clip and reports the clean-up. It never starts
  playback.

## 6. What is still unverified ([A])

1. Every reply except `/api/fppd/version` has only been read in source, not seen from the
   owner's Pi. The recorder exists to capture them.
2. That a playlist saved through the API with FacadeKit's entry plays on the Pi's HDMI output
   exactly like a UI-made one (`videoOut: --Default--`). Needs the write test plus a manual
   play in FPP's UI.
3. macOS's exact error for a blocked Local Network permission (expected `EHOSTUNREACH`).
4. Upload speed and assembly time for a full-size render on the owner's SD card (the 5 MB/s
   allowance).
5. Behaviour behind a user-added HTTPS proxy or with the UI password on (source-verified only).
6. Spikes S-2 and S-8 (playback smoothness, HDMI alignment) are untouched.

## 7. Owner steps

Run these from the **macOS Terminal app**, not from the Claude desktop app, which has no
Local Network permission (or give the app that starts FacadeKit Local Network access under
System Settings > Privacy & Security > Local Network). Replace `192.168.1.50` with the Pi's
address.

```sh
cd <FacadeKit checkout>/studio/backend
uv run facadekit spike fpp-contract 192.168.1.50 --out ~/fpp-s1 --bundle ~/fpp-s1.zip
# Optional: make one playlist in FPP's UI with a video entry first, so its shape is recorded.
# Optional write test (uploads and deletes a 2 s clip and a throwaway playlist; asks first):
uv run facadekit spike fpp-contract 192.168.1.50 --out ~/fpp-s1-write --write-test
# If FPP's UI password is on: add --ask-password (or export FACADEKIT_FPP_PASSWORD).
```

Then review the files in `~/fpp-s1` (they are redacted, but look before sharing) and commit
the useful ones under `studio/backend/tests/fixtures/fpp/` with a note in
`spikes/S-1-fpp-contract.md`.

## 8. FacadeKit FPP plugin

The optional plugin that switches the projectors from the Pi, and the recorder steps that check it (`plugin`, `plugin health`), are described in [docs/20](20-fpp-plugin.md).
