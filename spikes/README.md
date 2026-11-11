# Spikes

Hardware and model spikes that produce values the app needs before a feature can ship.

## S-6: MobileSAM ONNX models

Output of spike S-6 is two ONNX files and their checksums. Until the values below are pasted into
`studio/backend/src/facadekit/ai/model_registry.json`, the registry keeps its
`TO_BE_FILLED_BY_SPIKE_S6` placeholders and Magic select uses the GrabCut selector only.

### Files

Upload both files as assets of the GitHub release tagged `models-v1` in the `evhxn/facadekit`
repository:

- `mobilesam_encoder.onnx`
- `mobilesam_decoder.onnx`

The registry URLs point at
`https://github.com/evhxn/facadekit/releases/download/models-v1/<filename>`, so the asset names must
match exactly.

### Registry values

For each file, compute the SHA-256 and byte size of the exact file that was uploaded:

```sh
shasum -a 256 mobilesam_encoder.onnx mobilesam_decoder.onnx
stat -f %z mobilesam_encoder.onnx mobilesam_decoder.onnx   # macOS; use stat -c %s on Linux
```

Paste the lowercase hex digest into `sha256` and the byte count into `size_bytes` for the matching
entry (`mobilesam_encoder` or `mobilesam_decoder`). Once both entries have real checksums,
`GET /api/v1/ai/capabilities` lists `mobilesam` as a segment engine, provided `onnxruntime` is
installed.
