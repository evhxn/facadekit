# Troubleshooting

Search this page for the exact message you see, in quotes. Each section gives the cause and the fix. If none of these help, see [Getting help](../../README.md#getting-help) in the README.

## "Your computer is not sending the projector its native resolution"

**Cause:** Your computer's display is set to a resolution other than the projector's native resolution, or browser zoom is not 100 %.

**Fix:** In your computer's display settings, set the projector's display to its native resolution and 100 % scaling. In the projector window, reset browser zoom to 100 %.

## "It is too bright to scan"

**Cause:** There is too much light on the house for the camera to see the projected grid.

**Fix:** Wait until after sunset. Turn off porch lights. Or use manual mode by clicking **Skip scan (manual mode)**, as described in the [quick start](quick-start.md#7-mark-the-garage-door-corners).

## "The camera cannot see the projection"

**Cause:** The camera is not pointed at the projected grid.

**Fix:** Move or turn the camera until the whole grid is in the live view.

## "A scan for this project is already running"

**Cause:** A scan for this project is still queued or running, and FacadeKit runs only one at a time. The message names the job.

**Fix:** Wait for the scan to finish, or cancel it with **Cancel** in the progress box, then start again.

You should see: the next scan start.

## Red areas after the scan

**Cause:** The camera could not see those areas, for example because of a tree, a shadow or dark paint.

**Fix:** Move the camera closer to the projector and scan again. Or align those surfaces by eye.

## "Something moved during the scan"

Also use this section if the scan fails repeatedly.

**Cause:** The camera or projector moved while the scan was running.

**Fix:** Fix the camera and projector firmly in place. Scan again.

## The camera list shows only the built-in camera or my phone

**Cause:** FacadeKit lists the cameras that the computer reports. A USB webcam may be missing because it is not plugged in firmly, another program is using it, or macOS has not allowed camera access to the app that started FacadeKit. On a Mac, "iPhone" cameras are your phone offered as a camera; they cannot be locked for exposure.

**Fix:**

1. Plug the USB webcam directly into the computer (not through a hub, if you can).
2. Close other programs that may use the camera, such as video calls.
3. In Setup, click **Refresh**.
4. On a Mac, check System Settings > Privacy & Security > Camera for the app you started FacadeKit from (see the next entry).

You should see: the webcam in the **Camera** list. If you still see only the built-in camera, you can scan with the built-in camera or use **Scan with your phone** instead.

## The camera permission is "not determined" or denied (Mac)

**Cause:** macOS has not been told, or has been told no, whether the app that started FacadeKit may use the camera. The message says "macOS has not been told yet whether ... may use the camera" or "macOS is blocking the camera for ...". The Claude desktop app has no camera permission, so FacadeKit started from it always ends here.

**Fix:**

1. Quit FacadeKit.
2. Open the **Terminal** app and start FacadeKit from there.
3. If a macOS window asks whether Terminal may use the camera, click **Allow**.
4. If you clicked **Don't Allow** earlier, open System Settings > Privacy & Security > Camera and switch on **Terminal** (or the app named in the message). Quit and start FacadeKit again.

You should see: the camera picture in Setup. If the message says the Mac does not allow camera use (a restriction set by an administrator or Screen Time), use the phone scan instead.

## "The projector window does not have the project's resolution" (RESOLUTION_MISMATCH)

**Cause:** The projector window has a different number of pixels than the project. The full message reads "Projector window is WxH but the project expects WxH", then the most likely reason: the computer scales the display (it says the scale), or the projector window is not fullscreen or is still on the laptop screen.

**Fix:** Do what the message says. In the display settings, set the projector to the project's resolution with scaling off (100 %), set the browser zoom of the projector window to 100 %, drag the window onto the projector and click it to go fullscreen. Then scan again.

You should see: the scan start without this message.

## How to copy diagnostic information, and where the log is

When you ask for help, send the diagnostic information.

1. Click **Copy diagnostic info** under any error message.

   You should see: the button change to say it is copied. The text holds the versions, the devices, recent jobs, recent errors and the end of the server log, with your home folder hidden. If the browser does not allow copying, the text is shown so that you can copy it by hand.

2. Paste it into your message.

FacadeKit also writes a log file, `facadekit.log`, in the `logs` folder of its data folder. The data folder is `FacadeKit` in your home folder unless you set `FACADEKIT_DATA`. So the file is usually `~/FacadeKit/logs/facadekit.log`. Each line has a short request id. The recent errors in the diagnostic info carry the same id, so a message can be matched to its lines in the log.

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

**Cause:** FacadeKit cannot connect to your Raspberry Pi. On a Mac the most common cause is that FacadeKit was started from an app that has no Local Network permission, such as the Claude desktop app. A command like `ping` or `curl` can work in the Terminal app while FacadeKit still fails.

**Fix:**

1. Quit FacadeKit and start it again from the **Terminal** app (see [Before you begin (Mac)](quick-start.md#before-you-begin-mac-start-facadekit-from-the-terminal-app)).
2. Open System Settings > Privacy & Security > Local Network and switch on **Terminal** (or the app you start FacadeKit from). Quit and start FacadeKit again.
3. Check the FPP address you entered.
4. Make sure your computer and the Raspberry Pi are on the same network.
5. Try the Raspberry Pi's IP address instead of `fpp.local`.

You should see: the show send to the Pi without this message. If you still see it, read the exact words below the message: they say which of the cases in [FPP messages](#fpp-messages-and-what-they-mean) applies.

## FPP messages and what they mean

FacadeKit says why it could not use FPP. Find the start of your message here.

| Message starts or contains | What it means | What to do |
|---|---|---|
| "The name ... could not be found on the network" | The computer cannot turn the name into an address. Names ending in `.local` need mDNS (Bonjour). | Use the Pi's IP address instead. Check the spelling. |
| "... refused the connection" | The device is on the network, but nothing answers on that port. | Check that FPP is running, and the address and port. |
| "FPP at ... took too long to connect" or "... to answer" | No answer in time. | Check that the Pi is powered on and on the same network. On a Mac, also check Local Network (see above). |
| "No route to ..." | The computer has no path to that address. | Check the address and that both are on the same network. |
| "... answered, but not with valid HTTP. Is it really FPP?" | Something else answers at that address. | Check the address. |
| "HTTPS to ... failed" | You entered `https://`. FPP normally uses plain `http://`. | Remove `https://`. |
| "... answered, but not like Falcon Player" or "... JSON that is not an FPP version reply" | The address is not the FPP Pi. | Check the address. |
| "FPP at ... asks for a password" | The FPP UI password is turned on. | Set the `FACADEKIT_FPP_PASSWORD` environment variable to that password before starting FacadeKit, or turn the UI password off in FPP's settings. |
| "FPP at ... did not accept the password" | The password is set but is wrong. | Check the UI password in FPP and in `FACADEKIT_FPP_PASSWORD`. The user name is `admin` unless `FACADEKIT_FPP_USER` is set. |
| "FPP ... is too old" or "FacadeKit needs FPP 10 or newer" | See ["FPP is too old"](#fpp-is-too-old). | Flash a fresh FPP 10.x image. |
| "FPP has no folder called ..." | FPP does not have the folder FacadeKit sends videos to. | Check the `fpp.video_dir` setting in `config.json`. |
| "FPP could not store the file" or "FPP stored ... of ... bytes" | The upload did not finish. | Check the free space on the Pi, then send again. |
| "FPP accepted the start of playlist ... but is not playing it" | The playlist was saved but did not start. | Check the playlist and the video file in FPP. |

## "FPP is too old"

**Cause:** The version of FPP on your Raspberry Pi is older than version 10. FacadeKit refuses FPP 9.5 and older. The message says "FPP 9.5 is too old. Update FPP to version 10 or newer." (with your version) or "FacadeKit needs FPP 10 or newer."

**Fix:**

1. Check the version: open `http://<pi-ip>/api/fppd/version` in a browser, using your Pi's IP address. You should see `"majorVersion":"10"` or higher. The same check works in Terminal: `curl http://<pi-ip>/api/fppd/version`.
2. If the number is lower than 10, flash a fresh FPP 10.x image to the SD card. FPP 10 is based on Debian 13, so a fresh image is the safe way to get it.
3. Try again.

## Projector was bumped

**Cause:** The projector moved, so the surfaces no longer line up.

**Fix:** Scan again. Then check each surface's corners in **Surfaces**.
