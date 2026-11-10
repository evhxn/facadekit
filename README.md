# FacadeKit

FacadeKit projects videos onto your house for holiday light shows. You point a projector and a webcam at your house, FacadeKit learns where the surfaces are, and you place video clips on them. It then sends the finished show to a Raspberry Pi running Falcon Player (FPP).

FacadeKit is free and open source under the GPL-3.0-or-later licence. It runs on Windows, macOS and Linux.

## What you need

- A laptop or desktop computer.
- Any HDMI projector.
- A USB webcam.
- A Raspberry Pi (version 3 or newer) running FPP version 10 or newer, on the same network as your computer.
- Python 3.11 or newer.
- Chrome, Edge or Firefox.

## Install

```
pipx install facadekit
```

You can also run it without installing, with `uvx facadekit serve`.

## Quick start

1. Install FacadeKit (above).
2. Run `facadekit serve`. Your browser opens `http://localhost:8137`.
3. Put the projector in its final position and turn off keystone and digital zoom.
4. Connect the projector to your computer with HDMI and set its native resolution.
5. Create a project.
6. In Setup, open the projector window and make it fullscreen on the projector.
7. Place the webcam so it sees the whole grid, then scan after dusk.
8. In Surfaces, click the four corners of each surface.
9. In Arrange, add your video clips to the surfaces.
10. In Publish, render the show, enter your FPP address and send it to FPP.

The full guide is in [docs/user/quick-start.md](docs/user/quick-start.md). If something goes wrong, see [troubleshooting](docs/user/troubleshooting.md). New terms are explained in the [glossary](docs/user/glossary.md).

## Simulator mode

To try FacadeKit without a projector, camera or Raspberry Pi, run:

```
facadekit serve --sim
```

## Project status

FacadeKit is in early MVP (minimum viable product) development. Features may change.

## Getting help

Open an issue on this repository's GitHub Issues page. Please include the output of:

```
facadekit doctor
```

`facadekit doctor` prints diagnostic information for bug reports.

## Licence

FacadeKit is licensed under GPL-3.0-or-later. See [LICENSE](LICENSE).

## Developers

Read [CONTRIBUTING.md](CONTRIBUTING.md) first. Then:

```
make setup   # install Python and web dependencies
make test    # run the tests
make dev     # show how to run the backend and web UI together
```

The repository layout is described in [docs/04-repo-structure.md](docs/04-repo-structure.md).
