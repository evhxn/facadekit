# Spikes

Hardware and model spikes that produce values the app needs before a feature can ship.

## S-6: Magic select model

Superseded: the plan to upload MobileSAM files to a `models-v1` release of this repository is
dropped. No FacadeKit release hosts model weights. The registry points at the upstream files at
pinned revisions; the full record is `docs/16-magic-select-model.md`.

Phase 1 (research, adapters, box prompts, confirmed download flow, evaluation harness) is
done. Phase 2 needs the owner's approval of the files listed in `docs/16` §7. For each approved
file, check the bytes against the size and SHA-256 listed there before pasting the checksum
into `sha256` in `studio/backend/src/facadekit/ai/model_registry.json`:

```sh
shasum -a 256 FILE
stat -f %z FILE   # macOS; use stat -c %s on Linux
```

Until then every entry keeps its `TO_BE_FILLED_BY_SPIKE_S6` placeholder and Magic select uses
GrabCut (and says so).
