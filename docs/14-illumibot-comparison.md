# 14 – Illumibot comparison and prompt-to-visuals roadmap

Research date: 2026-11-22. Evidence tags as in `docs/01-landscape.md`: **[V]** verified against illumibot.ai on that date, **[A]** assumption or recollection, not verified.
Scope: what Illumibot is, what sets it apart, whether any of it is open source, and what that means for FacadeKit. The AI "prompt to visuals" feature is far down the roadmap; this document only records the groundwork.

## 1. What Illumibot is [V]

A commercial "AI personalized projection" product for haunts and holiday displays, selling hardware plus a subscription:

- **illumibot device**: a small box that plugs into any HDMI projector, about $400, projector not included. It runs shows on its own, has its own media server (shows keep playing when the phone is away), and is controlled from a phone app over Wi-Fi.
- **illumiFun kit** (about $100): uses the phone as the processor over USB-C to HDMI instead of the box. The phone must support video output and must stay with the projector.
- **Subscription**: "illumibot Pass", $15 per month per device. Still images are free with it; animation, video, upscaling and AtmosFX content cost "illumiTokens" (1,000 tokens for $15, cheaper in bulk).
- **Content**: no pre-made shows. The user types a prompt and cloud generative models make the artwork and animation. The page names third-party engines (for example Kling, Veo, Runway, Seedream, Qwen, Grok Imagine). A community tab shares prompts. Uploads of MP4, images and MP3 (max 1 GB) go onto a timeline editor. Shows can be scheduled; multiple projectors need one device each, synced.
- **Ecosystem**: outdoor projector enclosures, recommended BenQ and Roly projectors, an IR link for projector power (also PJLink and HDMI-CEC), a certified-installer programme, and a $40,000 on-site install service. Awards listed: Best New Exhibitor, Best New Product, First-Year Vendor.
- **Setup flow** (from tutorial titles): take a photo, masking, corner pinning, then prompt. [A] This reads as photo-based masking and manual corner alignment, not a structured-light scan. Illumibot does not state its calibration method.

## 2. What sets it apart

1. **Prompt to visuals.** Plain-language prompts generate artwork and animation that fits the user's own surface, so no two houses look the same.
2. **Low setup effort.** Phone-only workflow aimed at non-experts.
3. **Bundled, managed product.** One box, one app, one subscription, one enclosure ecosystem, plus installers.

## 3. Is it open source?

Not as far as can be found. [V] No GitHub link, licence mention, SDK, API documentation or self-hosting option appears on the home, shop, FAQ, tutorials, compatible-phones or terms pages. The AI features depend on paid cloud models billed through tokens. Conclusion: treat it as closed, proprietary hardware plus a subscription service. [A] Nothing outside illumibot.ai was checked.
They use the trademark "Personalized Projection". An open project should not reuse that wording or the "illumi" names.

## 4. FacadeKit compared

| Area | Illumibot | FacadeKit |
|---|---|---|
| Licence / cost | Proprietary, $15 per month per device plus tokens [V] | GPL-3.0-or-later, free |
| Hardware | Own box, or a phone over USB-C [V] | Any laptop, webcam, HDMI projector; Raspberry Pi running FPP for playback |
| Calibration | Photo, masking, corner pin [A] | Structured-light (Gray-code) scan, 0.28 px corner error in simulation, drift re-align, camera-quality checks |
| Content | Cloud generative AI | Your own media, effects, optional BYO-key generation (planned, see `docs/01-landscape.md` Q3) |
| Playback | Box with its own media server | Pre-rendered video to FPP |
| Cloud dependence | Required for generation and account sign-in [V] | None for the core; generation is optional |
| Audience | Haunt and holiday decorators, installers | Hobbyists comfortable with FPP and a laptop |

FacadeKit's advantages are openness, offline use, no recurring cost and measured calibration accuracy. Its gaps against Illumibot are ease of setup, content creation, a phone-first workflow and a managed hardware kit. `docs/12-feature-status.md` lists what is still thin.

## 5. Roadmap for prompt-to-visuals (far future)

Principle, already decided: local core at zero cost, generation optional and bring-your-own-key.

1. **BYO-key provider adapter.** One small interface (prompt, size or duration, optional reference image of the surface, returns image or video) with swappable providers. The user supplies their own key; nothing is bundled and no account or token system is run. Check each provider's terms for commercial display use before documenting it. [A] Prices and terms change often.
2. **Surface-aware prompts.** Feed the scan's projector-view picture and the surface masks into the prompt, so generated artwork is requested at the right aspect ratio and, where a model supports it, conditioned on the surface outline.
3. **Local models as an optional extra.** Image and short-clip models run on a user's own GPU. [A] Candidates such as Stable Diffusion XL or Flux-class image models and open video models (Wan, LTX, CogVideoX class) exist, but licences differ per model (some are non-commercial) and video generation needs a strong GPU. Verify the exact licence and hardware needs of each before listing it. Keep it off by default and state a minimum spec, as `docs/11-performance.md` does for the core.
4. **Prompt and asset sharing.** A plain-file format for a prompt plus its settings that people can share (no hosted service needed). A community gallery can come much later.
5. **Presets without AI.** Cheaper first step for the same audience: more built-in effects and sample projects (`docs/12-feature-status.md` lists only 3 effects and no samples).

## 6. Things not to do

- Do not copy Illumibot's names, trademarks, marketing text or artwork.
- Do not add a token or subscription system. It would contradict the free, offline core.
- Do not make any core feature (scan, surfaces, arrange, render, publish) depend on a cloud service.

## 7. Open questions for the owner

- Which audience matters first: hobbyists with FPP (current), or non-technical decorators who would want a phone-first flow?
- Is a "plug-in box" hardware kit ever in scope, or should FacadeKit stay software-only?
- Should generation start with BYO-key cloud providers (simple, costs the user) or with local models (free, needs a GPU)?
