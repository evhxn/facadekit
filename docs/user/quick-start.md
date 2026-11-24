# Quick start

This guide takes you from a new install to a video playing on your house from FPP. Do the steps in order. Each step has one action and a "You should see" line so you know it worked.

Before you begin, install FacadeKit by following the [install steps in the README](../../README.md#install). Terms in **bold** are explained in the [glossary](glossary.md).

No projector yet? [Try a sample project](effects-and-samples.md) on a simulated house first.

From a source install, run commands as `uv run facadekit …` inside `studio/backend`. Where this guide says `facadekit serve`, you can also run `make run` from the repository root.

## Before you begin: check your FPP version

FacadeKit needs FPP 10 or newer on your Raspberry Pi. FPP 9.5 and older are refused. FPP 10 is based on Debian 13, so you cannot rely on updating an old install: flash a fresh 10.x image to the Pi's SD card.

To check the version, open this address in your browser, using your Pi's IP address in place of `<pi-ip>`:

```
http://<pi-ip>/api/fppd/version
```

You should see: a short block of text that contains `"majorVersion":"10"` (or a higher number). If it says 9 or lower, flash a fresh 10.x image before you go on.

## Before you begin (Mac): start FacadeKit from the Terminal app

On a Mac, the app that starts FacadeKit is the app that macOS asks about camera access and local network access. The Claude desktop app has neither permission, so FacadeKit started from it cannot see your camera or reach your Pi. Start FacadeKit from the **Terminal** app instead.

1. Open **Terminal**: press Cmd+Space, type `Terminal` and press Return.

   You should see: a window with a command prompt.

2. Go to the FacadeKit folder and start it, for example `cd ~/Downloads/facadekit && make run` (use the folder where you installed it). Or run `uv run facadekit serve` inside `studio/backend`.

   You should see: FacadeKit open in your browser at `http://localhost:8137`.

3. If macOS asks whether Terminal may use the camera, click **Allow**.

   You should see: the window disappear and the camera listed in Setup. If you clicked **Don't Allow** by mistake, see [Camera permission](troubleshooting.md#the-camera-permission-is-not-determined-or-denied-mac).

4. The first time FacadeKit talks to your Pi, macOS may ask whether Terminal may find devices on your local network. Click **Allow**. You can also check it at any time in System Settings > Privacy & Security > Local Network: switch on **Terminal**.

   You should see: **Terminal** switched on in that list.

The Camera switch is in System Settings > Privacy & Security > Camera. After you change either switch, quit FacadeKit and start it again.

## 1. Put the projector in its final position

Set the projector where it will stay for the season. Turn off keystone and digital zoom in the projector's settings. Let it warm up for 2 minutes.

You should see: a steady, rectangular picture once the projector has warmed up.

## 2. Connect the projector to the laptop

Connect the projector to your laptop with an HDMI cable. In your computer's display settings, set the projector's display to its **native resolution** and 100 % scaling.

You should see: the same picture on the projector as on your laptop screen.

## 3. Create a project

Start FacadeKit as described above (`facadekit serve`). In the browser page that opens, create a new project. Choose the projector's native resolution.

You should see: your new project open in FacadeKit.

## 4. Open the projector window

Go to **Setup**. Open the **projector window**. Drag it onto the projector's screen, then click it to go fullscreen.

You should see: a grid of lines on the house.

## 5. Place the webcam

Put the webcam next to the projector so it can see the whole grid. Fix it in place so it cannot move.

You should see: the whole grid in the camera's live view.

## 6. Scan the house

Wait until dusk or night. Click **Start scan**. The scan takes about 30 seconds. Do not walk in front of the projector during the scan. Click only once: FacadeKit runs one scan per project at a time and refuses a second start while one is running.

You should see: the scan finish without an error message. If you see an error, see [troubleshooting](troubleshooting.md).

## 7. Mark the garage door corners

Go to **Surfaces**. Click the four corners of the garage door in this order: top-left, top-right, bottom-right, bottom-left. Use the arrow keys to nudge a corner while you watch the outline on the real door.

You should see: an outline that matches the edges of the garage door.

Two tools help on the Surfaces page after a scan:

- **Magic select** picks an object when you click it. At first the toolbar says **Simple selector (GrabCut)**, a colour-based tool. To get the smarter selector, click **Download model (NN MB)** in the toolbar. FacadeKit shows the size, the source and the licence first, and downloads nothing until you press **Download**. When the model is installed, the toolbar says **Smart select (SAM 2.1 tiny)** (or the name of the model you chose). To draw a box around the object instead of clicking it, use **Add rectangle**.
- **Check on the house** (in the panel beside the canvas) lights the selected surface and uses the camera to check that the light lands on the object and that the camera and projector have not moved since the scan. It takes a few seconds. If it offers **Apply suggested fix**, click it, then click **Check again**. You should see: **On target** and the lines "Light off the object" and "Object left dark" near 0 %.

If the scan fails, click **Skip scan (manual mode)** instead. You can then place surfaces by eye, using the live outlines. In manual mode the Surfaces page also offers **Reference photo (by eye, not measured)**: choose a JPEG, PNG or WebP photo of your house (at most 25 MB) and it appears behind the surfaces. The photo is not measured, so the outlines land where you put them, not where the projector really shines. Magic select and Check on the house are off in manual mode. If the scan keeps failing, see [Something moved during the scan](troubleshooting.md#something-moved-during-the-scan).

## 8. Add a video clip

Go to **Arrange**. Import a video. Add a clip to the surface. Press **Play** to preview the show on the house.

You should see: the video playing on the garage door in the preview. Videos with a black background look best with blend mode **Add** or **Screen**.

Only use videos you have the right to use. FacadeKit never uploads your media anywhere except to your own FPP.

## 9. Send the show to FPP

Go to **Publish**. Click **Render show**. Enter your FPP address, for example `fpp.local`, and click **Send to FPP**.

You should see: the show sent to your Raspberry Pi without an error. If you see "Could not reach FPP" or an FPP message about the version, a password or a name that cannot be found, see [troubleshooting](troubleshooting.md#could-not-reach-fpp).

## 10. Move the cable to the Raspberry Pi

Unplug the HDMI cable from the laptop and connect it to the Raspberry Pi.

If the picture looks shifted, run **alignment check** from FacadeKit and look at the test video. See [picture is shifted when played from the Pi](troubleshooting.md#picture-is-shifted-when-played-from-the-pi).

You should see: the show on the house from the Raspberry Pi, lined up with the surfaces you marked. Your Raspberry Pi is now playing your show.
