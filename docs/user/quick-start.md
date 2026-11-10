# Quick start

This guide takes you from a new install to a video playing on your house from FPP. Do the steps in order. Each step has one action and a "You should see" line so you know it worked.

Before you begin, install FacadeKit by following the [install steps in the README](../../README.md#install). Terms in **bold** are explained in the [glossary](glossary.md).

## 1. Put the projector in its final position

Set the projector where it will stay for the season. Turn off keystone and digital zoom in the projector's settings. Let it warm up for 2 minutes.

You should see: a steady, rectangular picture once the projector has warmed up.

## 2. Connect the projector to the laptop

Connect the projector to your laptop with an HDMI cable. In your computer's display settings, set the projector's display to its **native resolution** and 100 % scaling.

You should see: the same picture on the projector as on your laptop screen.

## 3. Create a project

Start FacadeKit with `facadekit serve`. In the browser page that opens, create a new project. Choose the projector's native resolution.

You should see: your new project open in FacadeKit.

## 4. Open the projector window

Go to **Setup**. Open the **projector window**. Drag it onto the projector's screen, then click it to go fullscreen.

`![screenshot: projector window with the grid on the house](img/step-04-grid.png)`

You should see: a grid of lines on the house.

## 5. Place the webcam

Put the webcam next to the projector so it can see the whole grid. Fix it in place so it cannot move.

You should see: the whole grid in the camera's live view.

## 6. Scan the house

Wait until dusk or night. Click **Start scan**. The scan takes about 30 seconds. Do not walk in front of the projector during the scan.

You should see: the scan finish without an error message. If you see an error, see [troubleshooting](troubleshooting.md).

## 7. Mark the garage door corners

Go to **Surfaces**. Click the four corners of the garage door in this order: top-left, top-right, bottom-right, bottom-left. Use the arrow keys to nudge a corner while you watch the outline on the real door.

`![screenshot: four corners marked on the garage door](img/step-07-corners.png)`

You should see: an outline that matches the edges of the garage door.

If the scan fails, click **Skip scan** instead. You can then place surfaces by eye, using the live outlines. If the scan keeps failing, see [Something moved during the scan](troubleshooting.md#something-moved-during-the-scan).

## 8. Add a video clip

Go to **Arrange**. Import a video. Add a clip to the surface. Press **Play** to preview the show on the house.

`![screenshot: clip placed on the surface in Arrange](img/step-08-arrange.png)`

You should see: the video playing on the garage door in the preview. Videos with a black background look best with blend mode **Add** or **Screen**.

Only use videos you have the right to use. FacadeKit never uploads your media anywhere except to your own FPP.

## 9. Send the show to FPP

Go to **Publish**. Click **Render show**. Enter your FPP address, for example `fpp.local`, and click **Send to FPP**.

`![screenshot: Publish page after Send to FPP](img/step-09-publish.png)`

You should see: the show sent to your Raspberry Pi without an error. If you see "Could not reach FPP", see [troubleshooting](troubleshooting.md#could-not-reach-fpp).

## 10. Move the cable to the Raspberry Pi

Unplug the HDMI cable from the laptop and connect it to the Raspberry Pi.

If the picture looks shifted, run **alignment check** from FacadeKit and look at the test video. See [picture is shifted when played from the Pi](troubleshooting.md#picture-is-shifted-when-played-from-the-pi).

You should see: the show on the house from the Raspberry Pi, lined up with the surfaces you marked. Your Raspberry Pi is now playing your show.
