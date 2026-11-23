# 16. Magic select model (spike S-6, phase 1)

Decision record for replacing the GrabCut-only Magic select with a promptable segmentation
model that runs locally on the CPU through `onnxruntime`. Written 2026-11-22.

**Why.** The owner tried to Magic-select a small white Ring camera on a cluttered shelf.
GrabCut, which separates colours, took the whole cluster. The synthetic set built for this spike
shows the same thing: GrabCut with one click gets mean IoU 0.12 on small light objects
(§5).

**Status.** Phase 1 is done: research, code and tests. No model file has been downloaded.
The registry points at the upstream files at pinned revisions. The checksums stay
`TO_BE_FILLED_BY_SPIKE_S6` until the owner approves the downloads (§7) and phase 2 checks the
files. Until then, Magic select runs GrabCut and says so in the toolbar.

Evidence tags: **[V]** = checked in a primary source on 2026-11-22 (a licence file, a model
card, the Hugging Face file API, or the export or consumer source code; the source is named).
**[A]** = an assumption or a secondary source, to be checked in phase 2 once the files are here.

## 1. Candidates

All candidates take point prompts (include and exclude) and box prompts, run as an image
encoder (once per picture) plus a small decoder (once per click), and have ONNX files that are
already published. No candidate needs `torch`.

| | **SAM 2.1 tiny** (recommended) | **EfficientSAM-Ti** (runner-up) | MobileSAM | EdgeSAM | SlimSAM-77 |
|---|---|---|---|---|---|
| Weights licence | Apache-2.0 [V]: `facebookresearch/sam2` LICENSE, and the README states the checkpoints are Apache-2.0; HF `facebook/sam2.1-hiera-tiny` card `apache-2.0` | Apache-2.0 [V]: `yformer/EfficientSAM` LICENSE; HF `yunyangx/EfficientSAM` card `apache-2.0` | Apache-2.0 upstream [V] (`ChaoningZhang/MobileSAM` LICENSE). The ONNX export repo `Acly/MobileSAM` declares `mit` [V]: the two statements differ | **S-Lab License 1.0, non-commercial only** [V] (`chongzhou96/EdgeSAM` LICENSE) | Apache-2.0 [V] (`czg1225/SlimSAM` LICENSE; `Xenova/slimsam-77-uniform` card) |
| ONNX source, pinned | `onnx-community/sam2.1-hiera-tiny-ONNX` @ `814a066640debee5a91e70aa401fb8e17e030503` [V]. The repo has **no README or licence of its own** [V]: it converts Meta's Apache-2.0 weights | `yunyangx/EfficientSAM` @ `1cf49585c39567bfc49e991ab8eb31f491ad4877` [V]. Published by the paper's first author. The sizes match `weights/efficient_sam_vitt_{encoder,decoder}.onnx` in the GitHub repo [V], so they are probably the same files [A] | `Acly/MobileSAM` @ `0d3b403339b4674a82493d5e97964dd78089ddc8` [V]. Third-party export; upstream exports only the decoder [V] | `chongzhou/EdgeSAM` @ `e0564124…` [V] | `Xenova/slimsam-77-uniform` @ `5850ab45…` [V] |
| Files (fp32) | `onnx/vision_encoder.onnx` 354,238 B + `onnx/vision_encoder.onnx_data` 134,084,864 B; `onnx/prompt_encoder_mask_decoder.onnx` 213,114 B + `…onnx_data` 20,958,208 B: **155.6 MB** [V] | `efficientsam_ti_encoder.onnx` 24,799,761 B; `efficientsam_ti_decoder.onnx` 16,565,728 B: **41.4 MB** [V] | `mobile_sam_image_encoder.onnx` 28,157,093 B; `sam_mask_decoder_multi.onnx` 16,496,559 B: **44.7 MB** [V] | encoder 22,098,300 B; decoder 15,937,006 B [V] | encoder 23,276,014 B; decoder 16,557,892 B [V] |
| sha256 published | Yes: the Git LFS object ids from the HF file API are SHA-256 of the file contents [V] (listed in §7) | Yes, LFS ids [V] | Yes, LFS ids [V] | Yes, LFS ids [V] | Yes, LFS ids [V] |
| Encoder I/O | `pixel_values` f32 `(1,3,1024,1024)` → `image_embeddings.0` `(1,32,256,256)`, `.1` `(1,64,128,128)`, `.2` `(1,256,64,64)` [V: `transformers.js` `modeling_sam2.js` @ `890e398f`] | `batched_images` f32 `(b,3,h,w)` → `image_embeddings` [V: `export_to_onnx.py`] | `input_image`: either `(h,w,3)` RGB 0..255, long side 1024, preprocessing inside the graph, or `(1,3,1024,1024)` normalised [V: Acly `export_image_encoder.py`]. Which one was uploaded is **not stated**; two independent consumers feed `(h,w,3)` [A]. The adapter handles both | `image` `(1,3,1024,1024)` → `image_embeddings` [V: `scripts/export_onnx_model.py`] | `pixel_values` → `image_embeddings`, `image_positional_embeddings` [V: `modeling_sam.js`] |
| Decoder I/O | `input_points` f32 `(1,1,n,2)`, `input_labels` **int64** `(1,1,n)`, `input_boxes` f32 `(1,b,4)` (b ≤ 1 with points), `image_embeddings.0/1/2` → `iou_scores` `(1,1,3)`, `pred_masks` `(1,1,3,256,256)` logits, `object_score_logits` [V: `modeling_sam2.js`] | `image_embeddings`, `batched_point_coords` `(1,1,n,2)`, `batched_point_labels` `(1,1,n)`, `orig_im_size` int64 `[h,w]` → `output_masks` `(1,1,3,h,w)` logits, `iou_predictions` `(1,1,3)`, low-res masks [V: `export_to_onnx.py`, `EfficientSAM_onnx_example.py`] | the standard SAM decoder: `image_embeddings`, `point_coords`, `point_labels`, `mask_input`, `has_mask_input`, `orig_im_size` → `masks` (4, at `orig_im_size`), `iou_predictions`, `low_res_masks` [V: MobileSAM `export_onnx_model.py`; card says "returns 4 masks"] | `image_embeddings`, `point_coords`, `point_labels` → `scores`, `masks` [V] | `input_points`, `input_labels`, `image_embeddings`, `image_positional_embeddings` → `iou_scores`, `pred_masks` [V] |
| Preprocessing | Resize to 1024 × 1024 **without** keeping the aspect ratio, bilinear, RGB / 255, ImageNet mean/std [V: `preprocessor_config.json`]. Prompts in that 1024 frame | RGB / 255 at any size; resizing to 1024 and ImageNet normalisation happen inside the graph [V: `efficient_sam.py` `preprocess`]. Prompts in input pixels | Long side 1024; SAM mean/std; pad to 1024 [V] | as MobileSAM [V] | long side 1024, pad, ImageNet mean/std [V] |
| Prompts | include, exclude, one box [V] | include and box only. **No learned embedding for exclude (label 0)**: only 1, 2, 3 and -1 [V: `efficient_sam_decoder.py`]. **At most 6 points** (`decoder_max_num_input_points = 6`) [V] | include, exclude, box [V] | include, exclude, box [V] | include, exclude, box [V] |
| Published quality | SAM 2 paper and README: SAM 2.1 tiny 76.5 J&F on SA-V test (video) [V: README]. Meta does not publish a **single-image** score for *tiny* [V: not in README]. Has high-resolution feature levels (256² and 128²) for fine masks [V: I/O above] | COCO 1-click mIoU 45.5 (MobileSAM 43.7), 3-click 67.2 (59.7), box 75.7 (74.2); COCO AP-small 26.7 (23.7) [V: EfficientSAM paper Tables 4–5, the authors' own numbers] | the weakest in the EfficientSAM tables [V] | COCO AP 42.1 [V: EdgeSAM README] | not compared here |
| CPU speed | None published. Meta's figure is on an A100 (91.2 FPS) [V]. Hiera-T encoder on a laptop CPU: about 1–3 s per picture [A], **to measure** | None on a CPU; A100 54 img/s [V: paper Table 8]. Encoder about 0.5–1.5 s [A], **to measure** | "around 3 s" on the authors' Mac i5 CPU, for their demo [V: README; a claim, not a benchmark] | iPhone 14 > 30 FPS [V] (not a CPU) | not found |
| Verdict | **Pick** | **Runner-up** (smaller; no exclude clicks) | Keep as a third registry option (its name already exists) | **Excluded**: non-commercial licence; FacadeKit is GPL-3 and users may sell shows | Not chosen: no gain shown over the above; would need a fourth adapter |

The SAM 2.1 *small* ONNX (same repo layout, encoder data 162,476,288 B [V]) and an int8
SAM 2.1 tiny (`*_quantized.onnx*`, 52.6 MB + 8.7 MB [V]) are possible later steps. The int8
model's accuracy is unknown [A]. EfficientSAM-S (encoder 89,558,337 B [V], the same decoder
contract) can be added by registry entries alone if Ti is too weak.

Not verified, because it needs the files: the exact input names, shapes and dtypes stored
inside each ONNX graph (they come from the export and consumer code above); whether
`vision_encoder.onnx` refers to its weights as `vision_encoder.onnx_data` (the Transformers.js
naming convention [A]); which MobileSAM encoder variant was uploaded. Phase 2 loads each file
and reads `get_inputs()`/`get_outputs()` before it fills in a checksum.

## 2. Decision

**Recommended: SAM 2.1 tiny** (`onnx-community/sam2.1-hiera-tiny-ONNX`, fp32), adapter
`sam2_hf`.
- It handles include *and* exclude clicks plus a box, which is how Magic select is used.
- Its decoder uses high-resolution image features (256² and 128²), the part that matters
  for a small object such as the Ring camera.
- The weights are Meta's, Apache-2.0, and it is the newest of the candidates.
- The cost: 156 MB to download, a slower encoder (once per picture, cached), and an ONNX
  repo with no licence file of its own (see risks).

**Runner-up: EfficientSAM-Ti** (`yunyangx/EfficientSAM`), adapter `efficientsam`.
- 41 MB, published by the authors, Apache-2.0.
- Better than MobileSAM on every number its paper reports, including AP for small objects.
- It cannot use exclude clicks and takes at most 6 points. The adapter therefore sends
  only the box and include clicks; post-processing still uses the exclude clicks.
- It is the better choice for slow or low-memory machines, or if SAM 2.1's CPU time turns
  out too slow.

**Kept:** MobileSAM (adapter `sam1`), now pointing at the Acly export.

Phase 2 picks the default from measured numbers (§6), not from this table. The registry order
sets which installed engine `auto` uses first: SAM 2.1 tiny, then EfficientSAM-Ti, then
MobileSAM.

## 3. What was built (works without any weights)

- **Engines from the registry** (`ai/engines.py`). Each registry file says its `engine`,
  `role` (`encoder`, `decoder`, `encoder_data`, `decoder_data`), `adapter`, `label`,
  `licence` and `source`, plus an optional `dir`, so a graph and its `.onnx_data` sit
  together. A new model is a few registry entries plus, if its tensors differ, one small
  adapter class (`ai/adapters.py`: `sam1`, `efficientsam`, `sam2_hf`). Each adapter feeds
  only the inputs the session declares and casts each one to the declared dtype.
- **Never downloads by itself.** `get_segmenter("auto")` uses the first engine whose files
  are all on disk with matching SHA-256, else GrabCut. Asking for a model that is not
  installed gives `AI_UNAVAILABLE`. Only `POST /ai/models/{engine}/download` fetches, with
  `{confirm: true, expected_bytes}` (the size the user was shown). It runs as a `download`
  job: it checks SHA-256 per file, refuses on a mismatch and leaves nothing behind, writes
  `.part`, fsyncs, and renames.
- **Box prompts.** `POST /projects/{pid}/segment` takes an optional `box`. With a box,
  `points` may be empty. For models the box goes in as box corners; GrabCut uses it as its
  init mask (outside = background, inside = probably the object) on a crop around the box.
  In the UI, "Add rectangle" draws the box while Magic select is on.
- **Small-object clean-up** (`postprocess_mask`):
  - clip to the box (with a small margin);
  - keep the connected component under the include click (or the nearest one);
  - fill holes;
  - simplify the outline with a 0.75 px floor.
- **Honest UI.** The toolbar shows "Smart select (SAM 2.1 tiny)" or "Simple selector
  (GrabCut)". When a published model is not installed it shows "Download model (156 MB)".
  The confirmation lists every file URL, the host, the size and the licence. Nothing is
  fetched before "Download".
- **Tests without weights.** `tests/fake_onnx.py` builds stub sessions with each export's
  input/output names, ranks and dtypes, and checks every feed. The stub decoders flood-fill
  the picture from the prompt, so the whole path (preprocessing, prompt scaling, mask
  resizing, post-processing, the route, the download job, the UI) is tested end to end.
  The download tests run against a local HTTP server.
- **Evaluation harness** (`python -m facadekit.ai.segment_eval`): `generate` writes
  synthetic shelves (a small light object touching similar-coloured clutter) and, with
  `--sim`, the simulator's features. `run` simulates a user (a centre click, then clicks on
  the largest error) and reports, per engine:
  - IoU after 1, 3 and N clicks;
  - clicks needed to reach IoU 0.85;
  - box and box+click IoU;
  - encode and decode time.

## 4. Hosting plan

- No FacadeKit release and no re-hosting. The registry URLs are the upstream Hugging Face
  `resolve/<commit>/<path>` URLs, pinned to a commit, so the bytes cannot change under a
  URL. The SHA-256 check would catch it if they did.
- The checksums are filled in only after phase 2 has downloaded each file and checked it
  against the LFS id in §7.
- Hugging Face answers `resolve` URLs with a redirect to its own download host. The
  confirmation dialog names `huggingface.co`; the final host is a Hugging Face CDN [A].
- If an upstream repository disappears, users can no longer install that model, and Magic
  select keeps working with GrabCut. A mirror can be configured without a code change
  through `FACADEKIT_MODEL_REGISTRY`. Re-hosting under FacadeKit would be a separate owner
  decision; Apache-2.0 allows it with the licence and NOTICE kept.
- Plain `http` is accepted only from `127.0.0.1`/`localhost`/`::1` and only with a registry
  override (tests, local mirrors). The bundled registry is https only.

## 5. Baseline measured now (GrabCut)

`python -m facadekit.ai.segment_eval generate DIR --count 24 --seed 0 --sim`, then `run DIR`
(27 pictures: 24 synthetic shelves and 3 simulator features; up to 5 clicks, target IoU 0.85;
MacBook, 2026-11-22):

| engine | IoU 1 click | IoU 3 | IoU 5 | clicks to 0.85 | reached | box | box + click | decode |
|---|---|---|---|---|---|---|---|---|
| grabcut | 0.116 | 0.090 | 0.134 | 5.44 (of max 6) | 11.1 % | 0.704 | 0.735 | 579 ms |

Clicking does not rescue GrabCut on small objects in clutter: it keeps taking the
neighbours. A box helps a lot, which is why the box prompt matters even before a model is
installed.

## 6. Phase 2 plan (needs the owner's approval of §7)

1. Download the approved files by hand into a scratch folder. Compare `shasum -a 256` and the
   byte size with §7.
2. Load each graph with `onnxruntime` and record `get_inputs()`/`get_outputs()`. Compare
   with §1 and fix an adapter if a name or dtype differs.
3. Paste the checksums into `model_registry.json` (`sha256` = the verified value). Install
   through the app's own Download button, so the real flow is exercised once.
4. Run `segment_eval run DIR --engines grabcut,sam2_1_tiny,efficientsam_ti` on the synthetic
   set and the simulator scenes. Record IoU at 1/3/5 clicks, clicks to 0.85, box IoU,
   encode and decode time on this Mac. Run `FK_TEST_MODELS=1 pytest -m models`.
5. Choose the default from the numbers. Update §2 and `docs/12`. Optionally try the owner's
   Ring photo locally; it stays on this Mac.

## 7. Files awaiting the owner's download approval

Nothing below has been downloaded. The sizes and SHA-256 values come from the Hugging Face
file API (Git LFS object ids) at the pinned commits [V].

**Recommended: SAM 2.1 tiny, 155,610,424 bytes (155.6 MB) in all**

| File | Bytes | SHA-256 |
|---|---|---|
| https://huggingface.co/onnx-community/sam2.1-hiera-tiny-ONNX/resolve/814a066640debee5a91e70aa401fb8e17e030503/onnx/vision_encoder.onnx | 354,238 | `4f30aacd3aaefbca81a0b7fe4c1fc96345570ea0a6f80ced599493d1b3be2e8c` |
| https://huggingface.co/onnx-community/sam2.1-hiera-tiny-ONNX/resolve/814a066640debee5a91e70aa401fb8e17e030503/onnx/vision_encoder.onnx_data | 134,084,864 | `e83df9866a5afe68ea7f0f721f18f65137fc3acbf0da1c74e946d363e09c69cc` |
| https://huggingface.co/onnx-community/sam2.1-hiera-tiny-ONNX/resolve/814a066640debee5a91e70aa401fb8e17e030503/onnx/prompt_encoder_mask_decoder.onnx | 213,114 | `874414704c5d686db7d206a35f6e15d26563d50c8c4468fccc6739bd7e491dcf` |
| https://huggingface.co/onnx-community/sam2.1-hiera-tiny-ONNX/resolve/814a066640debee5a91e70aa401fb8e17e030503/onnx/prompt_encoder_mask_decoder.onnx_data | 20,958,208 | `e9874d900dd4134ed60eab1e97910327c2419e0b2954485d8fd6e7f1a1470f47` |

**Runner-up: EfficientSAM-Ti, 41,365,489 bytes (41.4 MB)**

| File | Bytes | SHA-256 |
|---|---|---|
| https://huggingface.co/yunyangx/EfficientSAM/resolve/1cf49585c39567bfc49e991ab8eb31f491ad4877/efficientsam_ti_encoder.onnx | 24,799,761 | `84ed466ffcc5c1f8d08409bc34a23bb364ab2c15e402cb12d4335a42be0e0951` |
| https://huggingface.co/yunyangx/EfficientSAM/resolve/1cf49585c39567bfc49e991ab8eb31f491ad4877/efficientsam_ti_decoder.onnx | 16,565,728 | `a62f8fa5ea080447c0689418d69e58f1e83e0b7adf9c142e2bd9bcc8045c0b11` |

**Optional, for comparison only: MobileSAM, 44,653,652 bytes (44.7 MB)**

| File | Bytes | SHA-256 |
|---|---|---|
| https://huggingface.co/Acly/MobileSAM/resolve/0d3b403339b4674a82493d5e97964dd78089ddc8/mobile_sam_image_encoder.onnx | 28,157,093 | `580f5fb648ea1062c0aabc26217aed56921985f03f0cbbd852bba81d760cc749` |
| https://huggingface.co/Acly/MobileSAM/resolve/0d3b403339b4674a82493d5e97964dd78089ddc8/sam_mask_decoder_multi.onnx | 16,496,559 | `8976b90a87ba50a6a72217a5ff994f7d25ce16f2229fcc1ed259e1294c622ffe` |

Total if all three are approved: 241,629,565 bytes (241.6 MB).

## 8. Risks

| Risk | Mitigation |
|---|---|
| The SAM 2.1 ONNX repo has no licence file of its own | The weights are Meta's Apache-2.0 checkpoints (README [V]). The dialog and this record name Apache-2.0 and the upstream source. If the owner wants certainty, export from `facebook/sam2.1-hiera-tiny` ourselves (needs `torch` once, offline, not in FacadeKit) |
| SAM 2.1 tiny is too slow on a laptop CPU | The encoder runs once per picture and is cached per calibration. Phase 2 measures it. EfficientSAM-Ti (smaller) or the int8 SAM 2.1 are the fallbacks |
| An ONNX graph differs from the export code (names, dtypes, external-data name) | Adapters feed by declared name and cast to declared dtypes. Phase 2 step 2 reads the real signatures before any checksum is filled in |
| EfficientSAM ignores exclude clicks | Exclude clicks still drive post-processing. Prefer SAM 2.1 when refinement matters |
| MobileSAM licence statements differ (Apache-2.0 upstream, MIT on the export repo) | Treated as Apache-2.0 (the weights' licence). It is the third option only |
| Users without `onnxruntime` | Magic select stays GrabCut and says so. No download is offered without `onnxruntime` (`facadekit[ai]`) |
| An upstream file is changed or removed | Pinned commits plus SHA-256; a mismatch is refused and leaves nothing behind. GrabCut keeps working |
