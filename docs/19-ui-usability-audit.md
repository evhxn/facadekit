# 19. UI usability audit (first-time user)

Written 2026-11-23 for the third UI revamp. The reader in mind is a hobbyist decorating a
house who has never heard of projection mapping, not a developer.

**How it was made.** `uv run facadekit serve --sim` with an empty data folder, then the
screenshot walkthrough (`pnpm walkthrough`) at 1280x800 dark (`dark/`), 1280x800 light
(`light/`), 1024x700 light (`laptop/`, new) and Pixel 7 dark (`phone/`), plus reading every
editor component for what the screenshots cannot show (keyboard, focus, labels, silent
failures). Screenshot names below are the walkthrough's file names (`<project>/<name>.png`).
The Setup wizard screens were looked at but are out of scope for this pass (they are being
reworked separately); their findings are listed at the end only.

Severity:

- **S1 blocker**: a first-time user gets stuck, loses work, or cannot tell something failed.
- **S2 major**: the user can get through, but only by guessing or by reading the docs.
- **S3 minor**: friction, polish, or a problem for some users only.

## Projects page

| # | Sev | Finding | Screenshot |
|---|---|---|---|
| P1 | S2 | The page never says what the four steps are or how long they take. "A project is one house and one projector" is the only orientation; a new user does not know there is a scan, a drawing step, an editing step and a publishing step | `dark/01-projects-empty` |
| P2 | S2 | The empty list says "Make your first one with the form" but the form sits in a second column; at phone width it is below the fold | `phone/01-projects-empty` |
| P3 | S3 | "Projector resolution" is a list of numbers with no "I don't know" path; the help line is good but there is no example of where to find it | `dark/01-projects-empty` |
| P4 | S3 | Deleting a project uses the browser's own confirm box (works, but looks foreign) | — |

## App header (every project page)

| # | Sev | Finding | Screenshot |
|---|---|---|---|
| H1 | **S1** | **Saving is invisible.** Surfaces and Arrange save in the background, but nothing on the page says "Saving…" or "Saved", and a failed save (server stopped, revision conflict) sets an error in the store that **no editor page shows**. A user can keep editing for an hour with nothing saved | `dark/09-surfaces`, `dark/11-arrange` |
| H2 | S2 | "Copy diagnostic info" is the only help in the header. There is no way to find keyboard shortcuts, what the steps mean, or the glossary from inside the app | all |
| H3 | S3 | At phone width the header takes three lines before any content | `phone/09-surfaces` |

## Surfaces

| # | Sev | Finding | Screenshot |
|---|---|---|---|
| S1 | **S1** | **No undo.** The project store keeps an undo history, but Surfaces never wires it to Ctrl/Cmd+Z or to a button. A mis-drag of a corner, a Delete key press with the canvas focused (deletes the selected surface with no confirmation when it has no clips) cannot be taken back. Every pointer-move of a drag is also its own history step | `dark/09-surfaces` |
| S2 | S2 | Jargon with no explanation: "Aspect", "Edge softness (px)", "Bend (mesh)", "Grid size", "Custom outline", "Simple selector (GrabCut)", "Test pattern". The aspect help line ("Real width ÷ height") is the only one | `dark/09-surfaces`, `phone/09-surfaces` |
| S3 | S2 | Two buttons, "Add surface" and "Add rectangle", look the same and do different things (click four corners vs drop a ready rectangle). Nothing says which to use | `light/08-surfaces-empty` |
| S4 | S2 | Zoom and pan are only by scroll wheel, Space-drag or middle button; there is no visible zoom control and no "reset view" once zoomed in (zooming back out with the wheel is the only way) | `dark/09-surfaces` |
| S5 | S2 | Corner handles are 8 px squares (10 px hit radius), hard to grab on a laptop trackpad and tiny on a phone | `dark/09-surfaces` |
| S6 | S2 | Keyboard shortcuts exist (arrows nudge, Shift+arrows by 10, Tab cycles corners, Delete removes) but are written nowhere in the UI except the one-line canvas hint, which shows "Scroll to zoom, Space or middle button to pan" | `dark/09-surfaces` |
| S7 | S2 | Deleting a surface without clips is instant and silent: no confirmation, no "Deleted, Undo" | — |
| S8 | S3 | Top tools row mixes page toggles ("Show outlines on projector", "Check on the house after Magic select") with "Use phone as remote" and the Next button; at 1024 px it wraps into a second line and pushes the canvas down | `laptop/09b-surfaces-checked` |
| S9 | S3 | The properties panel is a long scroll at 1024 px: Properties, Check on the house, Shape, Bend all stacked; the "Check on the house" result pushes "Shape" and "Bend" out of view | `laptop/09b-surfaces-checked` |

## Arrange

| # | Sev | Finding | Screenshot |
|---|---|---|---|
| A1 | **S1** | **The timeline is below the fold.** At 1280x800 with a clip selected, the clip form (13 fields) pushes the timeline to y≈1150 px; the user sees no timeline at all without scrolling, and the Play button sits small beside the preview. On a phone the timeline is the very last thing on a 5700 px page | `dark/11-arrange`, `phone/11-arrange` |
| A2 | S2 | Removing a clip ("Remove clip", Delete key on the timeline) is instant with no undo shown. Undo works with Ctrl/Cmd+Z but nothing on the page says so, and there is no undo button | `dark/11-arrange` |
| A3 | S2 | Jargon: "In offset (s)", "Blend: Normal/Add/Screen", "Layer: Track 1", "Show onsets", the "exact" badge on the preview, "Fit: Fill/Fit/Stretch" (has a help line), "Show audio" | `dark/11-arrange` |
| A4 | S2 | Timeline zoom is Ctrl/Cmd+wheel only; there is no visible zoom. Drag to move, drag an edge to trim, double-click to edit and right-click for beat tools are all undiscoverable | `dark/11-arrange` |
| A5 | S2 | Timeline clips are `role="button"` but not focusable, and clip-list rows are clickable `<tr>`s with no keyboard access: a keyboard user cannot pick a clip | — |
| A6 | S2 | Deleting a media file is instant (no confirmation) and cannot be undone | — |
| A7 | S3 | At 1024 px the clip table is cut off on the right ("Lo…") and scrolls sideways inside its column | `laptop/10-arrange-empty` |
| A8 | S3 | Successful actions (file imported, clip added, beats found) give no feedback beyond the list changing | — |

## Publish

| # | Sev | Finding | Screenshot |
|---|---|---|---|
| U1 | S2 | "Send to FPP" and "Render and send alignment check" are disabled with no reason given until you connect; the user does not know the address is what is missing | `dark/12-publish-empty`, `dark/13-publish` |
| U2 | S2 | "FPP address", "Playlist name", "Start playing now", the light-sequence list: no explanation of what FPP is or where the address comes from on this page (the glossary has it) | `dark/13-publish` |
| U3 | S3 | The finished render is listed by its internal file name (`rnd_62aeb8b6.mp4`); the radio button meaning "this one gets sent" is not labelled visibly | `dark/13-publish` |
| U4 | S3 | Render finished / sent: success is only a line of text inside the panel, easy to miss below the fold on a phone | `phone/13-publish` |
| U5 | S3 | No "next step" line once the show is rendered (the header has no Next on the last step) | `dark/13-publish` |

## Accessibility (all pages)

| # | Sev | Finding |
|---|---|---|
| X1 | S2 | No `prefers-reduced-motion` handling anywhere (little motion today, but nothing guards future transitions) |
| X2 | S2 | Keyboard: timeline clips and clip-list rows not reachable (A5); the surface canvas is reachable but its keys are not documented (S6) |
| X3 | S3 | Colour tokens were chosen for AA but nothing checks them; a later change could break contrast silently |
| X4 | S3 | Several status lines that change (save state, toasts) have no live region today because they do not exist |
| X5 | ok | Focus ring (`:focus-visible`, 3 px) is present everywhere; buttons and inputs have labels; the walkthrough shows no sideways scroll at phone width on the setup, Arrange and Publish screens |

## Setup (out of scope here, for the record)

- The three steps are clear; the camera step is long at phone width (`phone/03-setup-camera`).
- These are left to the setup work running in parallel.

## What this pass fixes

In priority order (see the PR for what was done and what was not):

1. H1 save state shown in the header ("Saving…", "All changes saved", "Not saved" with a
   banner that says what to do).
2. S1/A2/S7 undo and redo on Surfaces and Arrange (buttons and Ctrl/Cmd+Z, Ctrl/Cmd+Shift+Z,
   Ctrl+Y), one undo step per drag, "Deleted. Undo" toasts.
3. A1 Arrange layout: preview and timeline above the fold, clip settings beside them.
4. H2/S6/A4 a Help dialog (press `?`) with the four steps and every keyboard shortcut.
5. S2/A3/U2 plain-language labels and "What is this?" help next to every jargon word.
6. P1/P2 a first-run guide on Projects with the four steps.
7. S3/S4/S5 surface canvas: clearer tool names, zoom buttons and Reset view, bigger handles,
   a one-time tip.
8. A4/A5 timeline zoom buttons, a hint line, keyboard-reachable clips and rows.
9. U1/U3/U5 Publish: reasons on disabled buttons, plain render names, next-step line.
10. X1/X3 reduced motion, and a contrast test on the theme tokens.

## Status after this pass (2026-11-23)

| Finding | Status |
|---|---|
| H1 save state | Fixed: header save line, `SaveProblem` banner with What to do and Try again |
| S1, A2, S7 undo | Fixed: Undo/Redo buttons and shortcuts on Surfaces and Arrange, one step per drag, toasts with Undo after deleting a surface or removing clips |
| A1 timeline below the fold | Fixed: preview and timeline first; the walkthrough checks the timeline is on screen at 1280x800 and 1024x700. On a phone it now comes right after the preview |
| H2, S6, A4 shortcuts and help | Fixed: Help dialog (`?`), Help button in the header |
| S2, A3, U2 jargon | Mostly fixed: InfoTips on Magic select, outlines, the house check, real-world shape, soft edges, Shape, Bend, Layer, Blend, onsets, Render, FPP, FPP address; plainer words for Aspect, Bend (mesh), In offset, Track, exact, Stop, Add surface. "Simple selector (GrabCut)" kept (tests pin the wording); Fit, Opacity, Fade and the effect controls unchanged (effect controls are being reworked separately) |
| P1, P2 first run | Fixed: welcome guide with the four steps and Start |
| S3, S4, S5 canvas | Fixed: "Click 4 corners", zoom −/+/Reset view and keys, 12 px handles, one-time tip |
| A5 keyboard on the timeline | Fixed: clips and clip-list rows are focusable, Enter opens a clip, Delete removes the focused clip |
| A6 media delete | Fixed: asks first |
| A7 clip table cut off | Fixed: Loop and Blend columns dropped from the list (they are in the clip settings) |
| A8, U4 feedback | Fixed: toasts after import, clip added, render ready, sent to FPP |
| U1, U3, U5 Publish | Fixed: reasons beside disabled buttons, renders listed by date, Next step line |
| X1 reduced motion | Fixed in `theme.css` |
| X3 contrast | Checked: `src/theme.test.ts` (AA in both themes) |
| S8, S9, H3 crowded tool rows and long panels at 1024 px and on phones | Not done |
| P3, P4 | Not done (P4: the project delete still uses the browser's confirm box) |
| Setup screens | Out of scope (separate work) |
