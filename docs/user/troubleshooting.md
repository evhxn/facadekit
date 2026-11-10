# Troubleshooting

Search this page for the exact message you see, in quotes. Each section gives the cause and the fix. If none of these help, see [Getting help](../../README.md#getting-help) in the README.

## "Your computer is not sending the projector its native resolution"

**Cause:** Your computer's display is set to a resolution other than the projector's native resolution, or browser zoom is not 100 %.

**Fix:** In your computer's display settings, set the projector's display to its native resolution and 100 % scaling. In the projector window, reset browser zoom to 100 %.

## "It is too bright to scan"

**Cause:** There is too much light on the house for the camera to see the projected grid.

**Fix:** Wait until after sunset. Turn off porch lights. Or use manual mode by clicking **Skip scan**, as described in the [quick start](quick-start.md#7-mark-the-garage-door-corners).

## "The camera cannot see the projection"

**Cause:** The camera is not pointed at the projected grid.

**Fix:** Move or turn the camera until the whole grid is in the live view.

## Red areas after the scan

**Cause:** The camera could not see those areas, for example because of a tree, a shadow or dark paint.

**Fix:** Move the camera closer to the projector and scan again. Or align those surfaces by eye.

## "Something moved during the scan"

Also use this section if the scan fails repeatedly.

**Cause:** The camera or projector moved while the scan was running.

**Fix:** Fix the camera and projector firmly in place. Scan again.

## Picture is shifted when played from the Pi

**Cause:** The Raspberry Pi's HDMI output, or the projector's own settings, do not match the projector's native resolution.

**Fix:**

1. Set FPP's HDMI output to the projector's native resolution.
2. Turn off overscan and zoom on the projector.
3. Send the alignment check again from FacadeKit, and check the test video.

## Video stutters on a Raspberry Pi 3

**Cause:** The video is too large for the Raspberry Pi 3 to play smoothly.

**Fix:** Render the show with the **Raspberry Pi – lighter (720p)** preset, then send it to FPP again.

## "Could not reach FPP"

**Cause:** FacadeKit cannot connect to your Raspberry Pi.

**Fix:**

- Check the FPP address you entered.
- Make sure your laptop and the Raspberry Pi are on the same network.
- Try the Raspberry Pi's IP address instead of `fpp.local`.

## "FPP is too old"

**Cause:** The version of FPP on your Raspberry Pi is older than version 10.

**Fix:** Update FPP to version 10 or newer, then try again.

## Projector was bumped

**Cause:** The projector moved, so the surfaces no longer line up.

**Fix:** Scan again. Then check each surface's corners in **Surfaces**.
