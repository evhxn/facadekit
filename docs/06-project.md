# 06 – Project plan

Working name: **FacadeKit**. This document covers the non-code side: name, licence, how people contribute, how non-technical decorators learn it, how hardware compatibility is tested, and how the project stays alive while the core stays free.

## 1. Name

Requirements: says what it does to a decorator, easy to say in a forum post, usable as a command and a package name, not obviously taken.

| Candidate | For | Against |
|---|---|---|
| **FacadeKit** (working name) | Descriptive; `facadekit` reads well as a command | "Facade" is hard to spell for some; the cedilla question (façade) |
| **HouseCast** | Plain words; "cast a show on your house" | Sounds like a streaming product |
| **Mapkin** | Friendly, seasonal (map + pumpkin), short | Was the name of a navigation app; trademark check needed |
| **LumaLawn** | Memorable | Suggests lawn lights, not projection |
| **ShowWarp** | Sits naturally beside "xLights" and "FPP" vocabulary | "Warp" is jargon for the audience |
| **PorchLight Mapper** | Warm, clear | Long; `porchlight` is a crowded name on package registries |

**Decision:** keep **FacadeKit** for the repository, package and CLI through the MVP. Before the first public release the owner checks (not verified in this plan): PyPI name `facadekit`, GitHub organisation, the `.org` domain, and a USPTO/EUIPO trademark search for the top two choices. If FacadeKit is blocked, fall back to HouseCast. Renaming is cheap until v1: the name appears in the package name, CLI entry point, `~/FacadeKit` data folder, `.fkproj` extension and the `fk_` media prefix (listed in `docs/04-repo-structure.md` §4 and `docs/05-interfaces.md`).

## 2. Licence

| Artefact | Licence | Why |
|---|---|---|
| Studio (backend + web UI) | **GPL-3.0-or-later** | Same family as FPP (GPL-2/LGPL-2.1) and xLights (GPL-3), so the community's expectations match. Copyleft discourages a closed, subscription-gated fork of community work, which is how the two prior "easy mapping" products (Lightform, Illumibot) were built. Compatible with all dependencies, including Apache-2.0 ones (OpenCV, ONNX Runtime, MobileSAM), which GPL-2-only would not be. |
| Future FPP plugin (separate repo) | GPL-2.0-or-later | The FPP plugin template's stated convention. Code shared with Studio must be written by project contributors so it can be offered under both licences; the contribution terms below allow that. |
| Documentation, tutorials, schemas | CC BY 4.0 | Encourages translation and reuse in forums and videos |
| Sample content shipped with the project | CC0 or CC BY 4.0 only | Users must be able to project it publicly without thinking |

Contribution terms: Developer Certificate of Origin (sign-off with `git commit -s`), no CLA. To keep the dual-licensing door open for the plugin, `CONTRIBUTING.md` states that contributions to files under `shared/` (none exist yet) are licensed "GPL-2.0-or-later OR GPL-3.0-or-later".

Notes that must appear in the README or docs:

- FacadeKit does not include or redistribute FPP or xLights code; it talks to FPP over its REST API.
- The ffmpeg binary comes from the `imageio-ffmpeg` package and is a GPL build including x264. H.264 and AAC are patent-encumbered in some countries; like most open-source video tools, the project ships no licence for them. Users in doubt can render with their own ffmpeg (env `IMAGEIO_FFMPEG_EXE`).
- Purchased holiday content remains under its seller's licence. FacadeKit only sends it to the user's own FPP.

## 3. Contribution model

- **Hosting:** GitHub, public from the first commit of code. Issues for bugs, Discussions for help and show-and-tell.
- **Governance:** benevolent maintainer (the owner) until there are three regular contributors; then a small maintainers group with a written decision rule (lazy consensus, maintainer tie-break).
- **Work intake:** every change maps to a ticket in `tasks/` (the same format builder models use) or a GitHub issue labelled `bug`. New feature ideas start as a Discussion, then become a ticket file through a pull request, so the plan and the backlog never diverge.
- **AI-built code policy:** tickets may be implemented by Claude Sonnet/Haiku or by people. Either way the pull request must pass the ticket's acceptance commands in CI, and a human maintainer reviews before merge. Contracts in `docs/05-interfaces.md` change only in a pull request that also updates every ticket quoting them.
- **Labels:** `good first issue` (Haiku-level tickets double as these), `needs hardware` (requires a projector or Pi), `field report`.
- **Code of conduct:** Contributor Covenant 2.1.
- **Releases:** semantic versions; `0.x` until v1 exit. Changelog kept by hand in `CHANGELOG.md`. Tagged releases publish the wheel to PyPI and (from v1) installers to GitHub Releases (ticket T-116).
- **Security:** the server binds to localhost; the only network features are FPP calls and the opt-in LAN remote (T-117). A `SECURITY.md` gives a private contact address.
- **Where the community already is:** announce in the FPP and xLights forums/Facebook groups and Aus Christmas Lighting forum only when the MVP field test passes; hobbyists judge by videos of real houses, not by README files.

## 4. Documentation and onboarding for non-technical decorators

Principle: the app teaches the workflow; the docs explain the physical world (where to put the projector, what "native resolution" means, what to do when it rains).

| Stage | Deliverable | Ticket / owner |
|---|---|---|
| MVP | `README.md`, `docs/user/quick-start.md`, `troubleshooting.md`, `glossary.md` | T-038 |
| MVP | In-app wizard text with one instruction per step and error messages that say what to do next | T-031, T-029 (`api/errors.ts`) |
| MVP | Simulator mode (`facadekit serve --sim`) so people can learn the app on the sofa before the hardware arrives | T-024, T-027 |
| v1 | Three short videos: "Your first mapped garage door" (10 min), "Timing to music", "When the projector moves" | Owner |
| v1 | "Hardware buying guide": what matters in a projector (native resolution, lumens ≥ 3000 for facades, throw ratio, HDMI), webcams known to work, weatherproofing basics | Owner + community |
| v1 | Docs website (MkDocs Material on GitHub Pages) with screenshots; search for error text lands on the fix | Haiku ticket to be written at v1 |
| v1 | Two sample projects with CC0 content (one Halloween, one Christmas) that render out of the box in simulator mode | Owner |
| v2 | "From FPP" page inside the plugin linking the right docs for the installed version | With the plugin |

Tone rules for all user text: no jargon without a one-line explanation; say "the picture the projector sees" before "projector space"; every error ends with an action; never blame the user's hardware without offering a workaround.

First-run funnel to measure by hand during field tests (no telemetry in the product): install → projector window fullscreen → scan success → first surface → first render → first publish. Each tester reports where they stalled; that list drives v1 priorities.

## 5. Hardware compatibility testing

No project can own every projector and webcam, so the plan combines a small reference set, a self-test built into the product, and community reports.

**Reference matrix (owner-run before each release):**

| Dimension | Minimum set |
|---|---|
| Projector | One 1080p-native LCD or DLP; one cheap 720p/480p-native "1080p supported" LED projector (the kind most hobbyists own) |
| Webcam | One 720p budget UVC camera with no manual controls; one 1080p camera with exposure control (e.g. a Logitech C920-class device) |
| Laptop OS | Windows 11, macOS (Apple silicon), Ubuntu LTS |
| Player | Raspberry Pi 3B+ and Pi 4 or 5, both on the current FPP 10.x |
| Surface | Painted garage door, brick wall, a window with projection material |

**Release checklist (about one evening):** run spikes S-2, S-4 and S-8 procedures again (they are written as repeatable scripts in `spikes/`), a full scan on each surface type, render and publish to each Pi, 10 minutes of playback watching FPP's Troubleshooting → Video page for underruns.

**Built-in self-test (v1, small ticket to add):** `facadekit doctor --hardware` runs with the projector and camera attached: checks projector-window pixel size, measures camera latency and whether exposure lock works, measures scan contrast, and prints a one-page report the user can paste into a compatibility thread.

**Community compatibility table:** a `docs/user/hardware.md` table (projector model, webcam model, OS, Pi model, FPP version, result, notes) updated through pull requests or a pinned Discussion using the `doctor --hardware` output. Entries are dated; "works" needs a scan coverage figure.

**Regression fixtures from the field:** with permission, real capture sets (the stack of camera images from a scan) are stored in a separate `facadekit-fixtures` repository via Git LFS and replayed through `decode` and `build_maps` in a nightly CI job. This is how thresholds get tuned on real brick and siding without owning the houses.

## 6. Sustainability (the core stays free)

Costs are deliberately near zero: no servers, no accounts, no hosted AI. Ongoing costs are a domain, optional code-signing certificates (Windows about $100–400/year, Apple $99/year), and test hardware.

| Source | Details | Rules |
|---|---|---|
| Donations | GitHub Sponsors and Open Collective (transparent ledger). Goal 1: code-signing certificates. Goal 2: a shared test-hardware pool. | Donation links live on the website and README only. **Never inside an FPP plugin**: FPP's plugin guidelines forbid donation or payment links [verified in `docs/01-landscape.md` §2.2]. |
| Optional content packs | Original, licence-clean animation packs made for common surface shapes (garage door 16:7, window pairs, door arch), sold by their creators or pay-what-you-want. A free starter pack always ships. | Packs are plain media plus a small JSON preset; they work without payment checks or DRM. The project does not resell third-party content. |
| Creator marketplace by link | A curated docs page linking to artists who sell FacadeKit-ready content. No revenue share initially. | Clear labelling of free versus paid |
| Sponsorship in kind | Projector or webcam vendors lending hardware for the compatibility table | Results are published whatever the outcome |
| Paid help, by others | Local installers and haunt professionals may charge for setup services | Allowed by the GPL; encouraged |

What is explicitly ruled out: paywalled features, a hosted "cloud render" tier, telemetry, selling user data, a closed "pro" fork. BYO-key generative features (v2) cost the project nothing because the user pays their own provider directly.

Bus-factor measures: everything needed to continue is in the repository (this plan, tickets, interfaces); releases are reproducible from CI; model files are mirrored in GitHub Releases with checksums; the product has no server that could be switched off, so existing users keep a working tool even if development stops. That last property is the direct lesson from Lightform's shutdown.

## 7. Milestone checklist for the owner

1. Run spikes S-8, S-2, S-1 with real hardware (one evening). Record results in `spikes/`.
2. Create the public GitHub repository; move this planning repo's contents in; add `LICENSE`.
3. Assign MVP tickets by track (`tasks/INDEX.md`).
4. Field-test the MVP on your own house; run S-5; collect a capture set as a fixture.
5. Name and trademark check; first public `0.1.0` release and forum announcement with a video.
6. v1 tickets; recruit five outside testers for the v1 exit test.
7. Re-plan v2 (Pi plugin first) with the field data.
