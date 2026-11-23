# S-1: FPP contract

Goal (docs/03-roadmap.md, risk R4): record what a real FPP 10.4 answers to every call
FacadeKit makes, keep the replies as fixtures for the fake FPP, and correct
docs/05-interfaces.md section 7 where needed.

## Status

- Source audit against FPP tag `10.4` (commit 123b8ca): done, `docs/17-fpp-integration-audit.md`.
- Recorder: `facadekit spike fpp-contract` built and tested against the fake FPP.
- Real device: **not run yet.** One real reply is known (`GET /api/fppd/version` from the
  owner's FPP 10.4), kept as `studio/backend/tests/fixtures/fpp/fppd_version_10_4.json`.

## How to run (owner)

From the macOS **Terminal** app (the Claude desktop app has no Local Network permission):

```sh
cd <FacadeKit checkout>/studio/backend
uv run facadekit spike fpp-contract 192.168.1.50 --out ~/fpp-s1 --bundle ~/fpp-s1.zip
uv run facadekit spike fpp-contract 192.168.1.50 --out ~/fpp-s1-write --write-test   # optional, asks first
```

Use the Pi's real address instead of 192.168.1.50. Add `--ask-password` if FPP's UI password
is on. The default run only reads. The write test uploads a 2 s test clip and a throwaway
playlist (both named `fk_s1_contract_test_<hex>`), reads them back, deletes both, and never
plays anything.

## Results

To be filled in from the recorder's `report.txt` after the run.
