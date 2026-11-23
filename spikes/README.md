# Spikes

Hardware and model spikes that produce values the app needs before a feature can ship.

## S-6: Magic select model

Superseded: the plan to upload MobileSAM files to a `models-v1` release of this repository is
dropped. No FacadeKit release hosts model weights. The registry points at the upstream files at
pinned revisions; the full record is `docs/16-magic-select-model.md`.

Phase 1 (research, adapters, box prompts, confirmed download flow, evaluation harness) and
phase 2 (owner-approved download of the files in `docs/16` §7, size and SHA-256 verified,
real tensor signatures checked, registry checksums filled, models measured against GrabCut)
are done. The default engine is SAM 2.1 tiny. Results: `docs/16` §9.

To verify a file again: `shasum -a 256 FILE` and `stat -f %z FILE` (macOS), compared with
`docs/16` §7 and `model_registry.json`.
