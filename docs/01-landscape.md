# 01 – Landscape research

Working project name: **FacadeKit** (final name candidates are in `docs/06-project.md`).
Research date: 2026-11-09. Read this cold: no other document is required to understand it.

## 0. Assumptions and owner answers (Phase 0)

The owner answered four questions. They are binding for the whole plan.

| # | Question | Answer |
|---|---|---|
| Q1 | Where do plan files live? | A new repo next to the owner's site repo: `/Users/ethan/Downloads/ethn/facadekit` |
| Q2 | Runtime split | A laptop/desktop does calibration, editing and rendering. The Pi only runs FPP and plays pre-rendered video. **After v0 is built, look into deeper Pi integration** (planned as v2, see roadmap). |
| Q3 | AI policy | Local core at zero cost. Generative features are optional and use the user's own API key (BYO key). |
| Q4 | Mapping scope for MVP→v1 | One projector, flat surfaces (wall, windows, garage door). Multi-projector and 3D props are v2 or later. |

Assumptions made without asking (each is also logged in the ADR log in `docs/02-architecture.md`):

- A-1: The owner's laptop is the reference authoring machine; Windows, macOS and Linux must all work.
- A-2: The webcam plugs into the laptop, not the Pi, for MVP and v1.
- A-3: The current FPP major version (10.x) is the only supported target. FPP 9 and older are out of scope.
- A-4: English-only UI for MVP and v1.

## 1. How to read the evidence tags

- **[V]** = verified on 2026-11-09 against the linked source.
- **[A]** = assumption or recollection that was *not* verified. Every [A] that the design depends on has a spike ticket in the roadmap (`docs/03-roadmap.md`).

## 2. FPP (Falcon Player) – the playback target

### 2.1 Current state

- [V] Latest release is **FPP 10.4** (9 Nov 2026). 10.0 shipped 21 Aug 2026. ([releases](https://github.com/FalconChristmas/fpp/releases))
- [V] FPP 10.0 **removed the VLC and SDL media backends**. Playback is rebuilt on **GStreamer + KMS**, with PipeWire for audio/video routing. HDMI and DSI outputs are supported. ([10.0 notes](https://github.com/FalconChristmas/fpp/releases/tag/10.0))
- [V] FPP 10 is based on Debian 13 "Trixie". 64-bit images exist for Pi 3, 4, 5, Zero 2 W, CM4/CM5 and PocketBeagle 2. Older Pis use a 32-bit image.
- [V] 10.0 added *video output regions* (one video spanning two displays), Play Media targeting any video output, and sending video into a Pixel Overlay model.
- [V] 10.1 added RTSP video output and **fill/stretch scaling options**. FPP's own design doc lists "per-path controls for scaling, crop, and position" as *deferred work*. There is **no keystone, warp, or mesh transform at playback**. ([PipeWire video routing doc](https://github.com/FalconChristmas/fpp/blob/master/docs/PipeWire_Video_Routing.md))
- [V] 10.4 added Troubleshooting → Video diagnostics including "Pi 0-3 HVS load and underruns", and fixed a crash with some H.265 videos on Pi. This tells us Pi 3 video is supported but is a known marginal path.
- [V] The old FPP manual says the Pi plays MP4 with H.264 and not H.265, and to match the video's aspect ratio to the projector. ([old manual](https://falcon-player.gitbooks.io/falcon-player-manual/chapter_nine_miscellaneous_items/playing_video.html)) This predates FPP 10 and is only background.
- [A] The Pi 3's hardware H.264 decoder handles up to 1080p30 (High profile, level 4.0). The Pi 5 has no hardware H.264 decoder and decodes in software, which is fast enough. Pi 4 does 1080p60 H.264 in hardware. (Forum-level evidence only: [Pi forums](https://forums.raspberrypi.com/viewtopic.php?t=387547), [GStreamer discourse](https://discourse.gstreamer.org/t/raspberry-pi-3-v4l2h264dec-two-simultaneous-decoder-instances-is-it-a-driver-limitation-or-a-configuration-issue/5909).) Spike S-2 measures this on real hardware.

### 2.2 Extension points that actually exist

| Extension point | Status | Evidence |
|---|---|---|
| REST API under `/api/` with an OpenAPI document | [V] `www/api/openapi.json` in the FPP repo; browsable on the device's API help page | [openapi.json](https://github.com/FalconChristmas/fpp/blob/master/www/api/openapi.json) |
| File upload: `POST /api/file/{DirName}` then `PATCH /api/file/{DirName}` with `Upload-Name`, `Upload-Offset`, `Upload-Length` headers (chunked); or single request `POST /api/file/{DirName}/{Name}` | [V] | same |
| File listing `GET /api/files/{DirName}`; delete `DELETE /api/file/{DirName}/{Name}` | [V] | same |
| Media list `GET /api/media`, `GET /api/media/{MediaName}/duration`, `GET /api/media/{MediaName}/meta` | [V] | same |
| Playlists: `GET/POST/DELETE /api/playlist/{PlaylistName}`, `POST /api/playlists`, `GET /api/playlists`, `POST /api/playlist/{PlaylistName}/start`, `POST /api/playlists/stop` | [V] paths. Body has `name`, `mainPlaylist[]`, `playlistInfo`. | same |
| Playlist entry of type `media` with fields `mediaName` and `videoOut`; type `both` adds `sequenceName` | [A] recalled from FPP source; the OpenAPI excerpt only showed a `pause` entry example. Spike S-1 confirms the exact shape by reading a playlist saved by FPP's own UI. | – |
| Media directory names (`videos`, `music`, `sequences`, `images`) | [A] `sequences` and `uploads` are [V]; `videos` is recalled. Spike S-1 confirms. | – |
| Commands: `POST /api/command` with `{"command": "...", "args": [...]}`; `GET /api/commands` lists them | [V] | same |
| Status and version: `GET /api/fppd/status`, `GET /api/fppd/version` (fields `version`, `majorVersion`, `minorVersion`, `branch`, `fppdAPI`), `GET /api/system/info` | [V] | same |
| MultiSync remotes list `GET /api/remotes` | [V] path only | same |
| Plugins: script plugins (PHP pages, shell hooks, commands, `callbacks` script) and C++ plugins (`.so` loaded into `fppd`). Plugin API 6 lets plugins register HTTP routes. Installed by `git clone` of `srcURL`, then `scripts/fpp_install.sh`. Listed via `FalconChristmas/fpp-data/pluginList.json`. | [V] | [Plugin_Development.md](https://github.com/FalconChristmas/fpp/blob/master/docs/Plugin_Development.md), [template](https://github.com/FalconChristmas/fpp-plugin-Template) |
| Plugin manifest `pluginInfo.json` with `repoName`, `name`, `author`, `description`, `homeURL`, `srcURL`, `bugURL`, `versions[]` (`minFPPVersion`, `maxFPPVersion`, `branch`, `sha`), `dependencies` (`packages`, `python`, …), `minMemoryMB`, and a mandatory 8-key `privacy` block | [V] | [PLUGININFO_FORMAT.md](https://github.com/FalconChristmas/fpp-plugin-Template/blob/master/PLUGININFO_FORMAT.md) |
| Plugin rules: no donation/payment links, no telemetry, no `sudo`, no opening ports at install, use the HTTP API rather than editing FPP's files, one log file, sensors (cameras) must be disclosed | [V] | [PLUGIN_GUIDELINES.md](https://github.com/FalconChristmas/fpp-plugin-Template/blob/master/PLUGIN_GUIDELINES.md) |
| Video input sources (`v4l2src`, `rtspsrc`) managed through `/api/pipewire/video/input-sources` | [V] FPP 10 can already open a V4L2 webcam. Useful for the v2 "calibrate from the Pi" work. | PipeWire video routing doc |
| `.fseq` v2 sequence format; `mf` variable header stores the media filename; step time in ms | [V] | [FSEQ doc](https://github.com/FalconChristmas/fpp/blob/master/docs/FSEQ_Sequence_File_Format.txt) |
| MultiSync keeps remotes' sequences and media in step with a player | [A] long-standing feature; video sync quality on FPP 10 is unverified. Not needed until multi-projector (v2+). | – |

### 2.3 Licensing

- [V] FPP is mixed: `libfpp.so` core is **LGPL v2.1 or later**; most other code is **GPL v2**; files in `src/non-gpl` are **CC BY-ND**. A written exception lets FPP load plugins and channel-output libraries under their own licenses. ([LICENSE](https://github.com/FalconChristmas/fpp/blob/master/LICENSE), [LICENSE.exception](https://github.com/FalconChristmas/fpp/blob/master/LICENSE.exception))
- [V] The plugin template "ships with LICENSE defaulted to GPLv2 (the convention most FPP plugins use)".

### 2.4 What FPP does well and badly for this audience

Good: free, already installed by the target users, scheduling, playlists, audio, sync with pixels, a documented REST API, runs on cheap hardware. Bad: it has no idea what a facade is. It plays a rectangle of video to a display. Any geometry correction must be in the video file.

## 3. xLights – the sequencer the audience already knows

- [V] License: GPL v3 text in the repo. ([License.txt](https://github.com/xLightsSequencer/xLights/blob/master/License.txt))
- [V] An "xEssentials – Projector Mapping" talk exists (2018), so the community has tried this. ([video](https://videos.xlights.org/xessentials-projector-mapping_e5468ca5d.html))
- [A] The usual xLights approach treats the projector as a low-resolution pixel matrix (FPP "Virtual Matrix" on HDMI, listed in FPP release notes since v1.5 [V]) or simply puts a video in the FPP playlist next to the `.fseq`. Neither gives camera calibration, surface masks, or perspective-correct content.
- [V] A forum user said they used MadMapper because they "didn't realise that FPP/xlights had that functionality". ([ACL forum thread](https://auschristmaslighting.com/threads/projection-mapping-a-thread-to-discuss-and-share-links-tips-and-ideas.13796/)) Hobbyists reach for commercial mapping tools today.

Good: timeline-with-audio UX the audience already understands, huge community, free. Bad for projection: effects are designed for pixel models, not video surfaces; no calibration.

Decision this drives: FacadeKit does **not** try to be an xLights plugin (xLights has no plugin API we could find, and its GPLv3 code would constrain our license). It interoperates at the FPP level: a FacadeKit video can be the media file of an FPP playlist entry that also plays an xLights `.fseq`.

## 4. Commercial and prosumer mapping tools

Prices are from comparison blogs and vary between sources; treat them as approximate. ([source 1](https://map.club/projection-mapping-review/software-comparison), [source 2](https://lazylighting.com/blog/madmapper-alternatives))

| Tool | Price (approx.) | Calibration | Strengths | Weak for holiday hobbyists |
|---|---|---|---|---|
| HeavyM | about €199–€559; free demo is watermarked and cannot save [V] | Manual: draw shapes and drag corners while looking at the house | Easiest UI in the category; built-in effects that trace shapes; sound-reactive | Needs a PC outdoors running live every night; cost; no FPP sync |
| MadMapper | about €399 perpetual or €39/month [V] | Manual quads/meshes; has a camera-based 3D calibration and a spatial scanner using a DSLR [A] | Professional warping, masks, DMX and LED pixel output | Cost; pro UI; live PC required |
| Resolume Arena | about €799 [V] | Manual in the Advanced Output | VJ performance, layers, effects | Overkill; cost; live PC required |
| Lightform LF1/LF2 | defunct | Automatic structured-light scan with an integrated camera | The UX this project wants: scan, get a picture of the scene in projector space, pick surfaces with AI-assisted selection, apply effects | Company stopped hardware in Feb 2022 and closed on 12 Aug 2022; cloud services ended; owners were left with unsupported devices [V] ([announcement](https://lightform.com/?p=3093)) |
| Illumibot (sold via AtmosFX) | Kit about $300, all-in-one kits $3,000+; AI generation $15 per device (subscription) [V] | Phone photo of the surface taken from near the projector; cloud does the mapping [V] | Truly simple: photo, prompt, play. Plays offline after creation | Needs an account, Wi-Fi and a subscription for creation; closed; content licensed for personal use only; no FPP or light sync ([product page](https://atmosfx.com/products/illumibot-projection-mapping-kit), [guide](https://atmosfx.com/blogs/community/digital-decorating-101-projection-mapping-with-illumibot)) |
| BenQ "house mapping" projectors (e.g. LH830ST) | Projector hardware [V exists] | Bundled software approach unverified [A] | Shows that vendors now market directly to home decorators | Vendor lock-in |

Why Lightform died matters: the company blamed the length of the pandemic and said cost-cutting was not enough [V]. The lesson for us is structural. A cloud-dependent, hardware-tied product strands its users when the company ends. A free tool that writes a plain MP4 into FPP cannot strand anyone.

## 5. Open-source mapping and calibration

| Project | License | Status | What it does | Fit for us |
|---|---|---|---|---|
| Splash (SAT Metalab, now `gitlab.com/splashmapper`) | GPL v3 [V] | Active in 2026 [V] | Multi-projector mapping for domes and 3D objects; semi-automatic geometric calibration from a 3D model; blending; companion library *Calimiro* for multi-projector auto-calibration [V] | Too heavy: needs a 3D model of the surface, a Linux GPU box, and live rendering. Good reference for v2 multi-projector blending. ([overview](https://projection-mapping.org/splash/)) |
| MapMap | GPL v3 [V] | Barely alive: official repo touched in 2026; a community Windows fork `mapmap_revival` 0.7.0 (Jan 2026) [V] | Manual quad/triangle/mesh mapping of media, Qt desktop app | Manual only, needs a live PC. Idea worth borrowing: its very simple "source shape ↔ destination shape" model. ([releases](https://github.com/mapmapteam/mapmap/releases)) |
| ofxPiMapper | MIT [V] | Low activity; documented for Pi up to 3B+ [V] | openFrameworks app that does manual surface mapping *on the Pi*, live | Proves a Pi 3 can warp a few video surfaces live. It needs its own OS image and takes over the display, so it cannot coexist with FPP. ([site](https://ofxpimapper.github.io)) |
| OpenCV `structured_light` (contrib) | Apache-2.0 | Maintained | `GrayCodePattern` generates patterns and decodes camera pixel → projector pixel; includes shadow masking via black/white thresholds [V] ([tutorial](https://docs.opencv.org/4.5.0/dc/da9/tutorial_decode_graycode_pattern.html)) | The algorithm we need. It lives in `opencv-contrib`, and Gray code is about 60 lines of NumPy, so we implement it ourselves and avoid the contrib dependency (see ADR-007). |
| Procam calibration research code (Moreno & Taubin style) | varies | Mostly unmaintained [A] | Recovers projector intrinsics with a checkerboard | Not needed: our design never needs projector intrinsics (see §8). |
| FPP / xLights community projection plugins | – | None found in this research [V negative result from two searches] | – | The gap is real. |

## 6. Holiday projection content

- [V] AtmosFX sells "Digital Decorations": 1080p **MP4** files in horizontal and vertical orientations. ([product example](https://atmosfx.com/products/hanukkah-window))
- [V] They sell projection materials: Window Projection Material 4'×6' ($25), XL 5.5'×9' ($40), and "Hollusion" mesh ($50). ([gear](https://atmosfx.com/collections/gear), [materials explained](https://support.atmosfx.com/article/361-what-is-the-difference-between-each-of-the-projection-materials))
- [A] How people project it today: a cheap projector inside the house pointed at a window with rear-projection material, playing the file on loop from a USB stick or media player; alignment by physically moving the projector and using its keystone. Mapping to a facade is rare because it needs HeavyM/MadMapper skills.
- Licensing note [V]: Illumibot AI content is "personal, non-commercial". Purchased AtmosFX content is licensed, not owned. FacadeKit must treat user media as opaque user files and never redistribute them.

What this tells us about the first user: they already own several MP4s and a window or a garage door. The most valuable first experience is "put the MP4 I already bought exactly on my garage door and my two windows, in sync with my FPP show".

## 7. Comparison table (summary)

| | Free | Auto-calibrate with camera | Works on consumer gear | Timeline + audio | Plays from FPP without a live PC | Open source |
|---|---|---|---|---|---|---|
| FPP alone | yes | no | yes | playlists only | yes | yes |
| xLights + FPP | yes | no | yes | yes (pixel-centric) | yes | yes |
| HeavyM | no | no | yes | basic | no | no |
| MadMapper | no | partial (pro workflow) | yes | basic | no (except its miniMad hardware [A]) | no |
| Resolume Arena | no | no | yes | clip-launch style | no | no |
| Lightform | defunct | yes | own hardware | basic | n/a | no |
| Illumibot | no (subscription) | photo-based | yes + their box | no | its own box | no |
| Splash | yes | semi (needs 3D model) | needs GPU PC | no | no | yes |
| MapMap | yes | no | yes | no | no | yes |
| ofxPiMapper | yes | no | Pi ≤ 3B+ | no | replaces FPP | yes |
| **FacadeKit (this project)** | **yes** | **yes** | **yes** | **yes** | **yes** | **yes** |

## 8. Gaps this project fills

1. **Automatic calibration without buying hardware.** Nobody offers scan-based setup for free since Lightform closed.
2. **Baked output for a dumb player.** Every mapping tool assumes a PC rendering live. Holiday shows run unattended for 6 weeks from a Pi. Pre-warped video is the right shape for that.
3. **FPP-native delivery.** One click uploads the video and creates the playlist, so projection joins the light show the user already schedules.
4. **No cloud, no account.** Works with the internet off after install.

## 9. Ideas worth borrowing

| Idea | From | How we use it |
|---|---|---|
| Scan, then show the user a photo of their house *as the projector sees it*, and let them design on that photo | Lightform | Central design idea. All authoring happens in "projector space" on a camera-derived image. This removes any need for projector intrinsics or 3D reconstruction (ADR-003). |
| Click an object, get its outline | Lightform, Illumibot | Click-to-segment with a small local SAM-family model (v1). |
| Draw a shape, drag its corners while watching the real wall | HeavyM, MapMap, MadMapper | Fine-alignment step: surfaces are outlined live through the projector while the user nudges corners. Also the fallback when the camera scan fails. |
| Effects that trace the edges of a shape | HeavyM | Built-in generator clips "outline trace" and "color wash" (v1) so a user with no media still gets a show. |
| Gray code with inverse patterns and shadow masks | OpenCV structured light | The calibration algorithm. |
| Content stays playable offline once created | Illumibot | Our default: everything is local. |
| A ready-to-flash image | ofxPiMapper | Not copied. We rely on the stock FPP image so we never maintain an OS. |

## 10. Sources

- FPP releases: https://github.com/FalconChristmas/fpp/releases and https://github.com/FalconChristmas/fpp/releases/tag/10.0
- FPP docs folder: https://github.com/FalconChristmas/fpp/tree/master/docs (Plugin_Development.md, PipeWire_Video_Routing.md, FSEQ_Sequence_File_Format.txt)
- FPP OpenAPI: https://github.com/FalconChristmas/fpp/blob/master/www/api/openapi.json
- FPP license: https://github.com/FalconChristmas/fpp/blob/master/LICENSE and LICENSE.exception
- FPP plugin template: https://github.com/FalconChristmas/fpp-plugin-Template
- Old FPP manual, video page: https://falcon-player.gitbooks.io/falcon-player-manual/chapter_nine_miscellaneous_items/playing_video.html
- xLights license: https://github.com/xLightsSequencer/xLights/blob/master/License.txt
- xEssentials projector mapping: https://videos.xlights.org/xessentials-projector-mapping_e5468ca5d.html
- ACL forum projection thread: https://auschristmaslighting.com/threads/projection-mapping-a-thread-to-discuss-and-share-links-tips-and-ideas.13796/
- Lightform wind-down: https://lightform.com/?p=3093
- Illumibot: https://atmosfx.com/products/illumibot-projection-mapping-kit and https://atmosfx.com/blogs/community/digital-decorating-101-projection-mapping-with-illumibot
- AtmosFX gear and materials: https://atmosfx.com/collections/gear and https://support.atmosfx.com/article/361-what-is-the-difference-between-each-of-the-projection-materials
- Mapping software prices: https://map.club/projection-mapping-review/software-comparison and https://lazylighting.com/blog/madmapper-alternatives
- Splash: https://projection-mapping.org/splash/ and https://gitlab.com/splashmapper/splash
- MapMap: https://github.com/mapmapteam/mapmap/releases and https://github.com/gilforge/mapmap_revival
- ofxPiMapper: https://ofxpimapper.github.io
- OpenCV Gray code tutorial: https://docs.opencv.org/4.5.0/dc/da9/tutorial_decode_graycode_pattern.html
- Pi video decode discussions: https://forums.raspberrypi.com/viewtopic.php?t=387547 and https://discourse.gstreamer.org/t/raspberry-pi-3-v4l2h264dec-two-simultaneous-decoder-instances-is-it-a-driver-limitation-or-a-configuration-issue/5909
