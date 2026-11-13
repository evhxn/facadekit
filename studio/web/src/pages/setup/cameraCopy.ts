import type { CameraCheck } from "../../api/types";

/** Every user-facing string of the camera check lives here (docs/08 section 5). */
export const COPY = {
  checkButton: "Check the camera view",
  checking: "Checking… the projector will flash and show stripes for a few seconds.",
  checkFailed:
    "The camera check did not finish. Check that the camera and the projector window are open, then press Check again.",

  good: "Your camera sees the projection clearly.",
  goodTip: "Tip: crop to the lit area to make the scan faster.",
  marginalSmall:
    "Your camera sees the projection quite small. The scan should work, but lines may land a few pixels off. For a sharper scan, move the camera closer, zoom in, or use your phone.",
  coarseSmall:
    "Your camera sees the projection very small. Crop to the lit area, or use your phone for a sharper scan.",
  coarseDetails: (n: number) =>
    `Each camera pixel covers about ${n} projector ${n === 1 ? "pixel" : "pixels"}. Cropping makes the scan faster, but only a closer camera, zoom or your phone adds detail.`,
  blurry:
    "The picture is soft. Turn the focus ring (or tap the house on your phone) until the brick lines look crisp, then check again.",
  noisy:
    "The picture is grainy. Scanning later in the evening or switching off porch lights will help.",
  clipped:
    "Parts of the picture are very bright. FacadeKit will turn the camera down during the scan.",

  hint1080:
    "Your camera may be able to send a sharper picture. To try it, set FACADEKIT_CAMERA_SIZE to 1920x1080, restart FacadeKit, then press Check again.",

  buttons: {
    continue: "Continue",
    crop: "Crop to the lit area",
    scan: "Scan with this camera",
    scanAnyway: "Scan anyway",
    phone: "Use my phone instead",
    recheck: "Check again",
  },

  cropCaption: "Only the inside of the box is used. Keep the whole lit area inside the box.",
  cropUse: "Use this crop",
  cropReset: "Reset",
  cropWhole: "Use the whole picture",
  cropEdit: "Change the crop",
  cropSize: (w: number, h: number) => `The box is ${w} by ${h} pixels.`,
  cropActive: "Only the cropped part of the picture will be scanned.",
  cropPreviewAlt: "Zoomed view of the cropped area",
  cropFromLastScan: "Using the crop from your last scan.",
  handleLabel: (corner: string) =>
    `${corner} corner of the crop box. Use the arrow keys to move it. Hold Shift for bigger steps.`,
  corners: {
    tl: "Top left",
    tr: "Top right",
    bl: "Bottom left",
    br: "Bottom right",
  },

  /** docs/08 section 5, phone panel, verbatim. The words in bold are buttons. */
  phoneSteps: [
    "Put your phone on a tripod or lean it on something steady, near the projector. Zoom in until the house fills the screen.",
    "If your phone has Action mode or extra stabilisation, turn it off. Press and hold on the house to lock focus and brightness.",
    "Start recording a video, then press **Start light show** (here or on your phone). The projector stays dark for 15 seconds, flashes three times, then shows stripes for about a minute.",
    "Stop recording when the projector goes dark. Send the video to this computer, or use the phone page, and choose it here: **Choose video…**",
  ],
  phonePrivacy:
    "The video goes straight from your phone to this computer. It is deleted after the scan.",
  phoneLink: "Scan with your phone",
  phoneTitle: "Scan with your phone",
  phoneUseCamera: "Use the camera instead",
  phoneStart: "Start light show",
  phoneShowRunning: "Light show running… keep recording",
  phoneShowDone: "Done. Stop recording and send the video.",
  phoneShowStarting: "Starting the light show…",
  phoneChoose: "Choose video…",
  phoneNoSession: "Start the light show first.",
  phoneSending: "Sending the video…",
  phoneChecking: "Checking the video…",
  phoneSendFromPhone: "Send from your phone instead",
  phonePageIntro:
    "On your phone (same Wi-Fi), scan a code or type the address, then enter the pairing code.",
  phoneLanOff: "Restart FacadeKit with `facadekit serve --lan` to use your phone on your Wi-Fi.",
  phoneMovedHint: " Did the phone move? Use a tripod or lean it on something steady.",
  phoneShowFailed:
    "The light show did not finish. Check that the projector window is open, then press Start light show again.",

  /** The phone page (/phone/:projectId). */
  phonePageTitle: "Scan with your phone",
  phonePageStart: "Start light show in 20 s",
  phonePageSwitch: "Now switch to your camera app and start recording.",
  phonePageSend: "Send video",
  phonePageDone: "Done. Look at your computer for the result.",
  phonePageBlocked:
    "This computer does not accept videos from phones. Send the video to the computer another way (AirDrop, cable) and choose it there.",

  smallResult:
    "Your camera saw the projection small, so check the outlines carefully on the house.",
  cropTight:
    "Part of the lit area touched the edge of your crop. If outlines near the edge look off, make the crop a little bigger.",
} as const;

export type CheckButton = keyof typeof COPY.buttons;

export interface CheckDescription {
  headline: string;
  details: string | null;
  extras: string[];
  /** Buttons in display order; the first one is the primary button. */
  buttons: CheckButton[];
}

/** Picks the text and buttons for a camera check result. */
export function describeCheck(check: CameraCheck): CheckDescription {
  const hasCrop = check.suggested_roi !== null;
  const lead = check.reasons.find((r) => r === "SMALL_IN_FRAME" || r === "BLURRY");
  const extras: string[] = [];
  if (check.reasons.includes("NOISY")) extras.push(COPY.noisy);
  if (check.reasons.includes("CLIPPED")) extras.push(COPY.clipped);
  // The picture size stays 720p unless the owner opts in, so only point at the way to do it.
  if (check.suggest_1080p && check.verdict !== "good") extras.push(COPY.hint1080);

  if (check.verdict === "good") {
    return {
      headline: COPY.good,
      details: hasCrop ? COPY.goodTip : null,
      extras,
      buttons: hasCrop ? ["continue", "crop"] : ["continue"],
    };
  }
  if (lead === "BLURRY") {
    return { headline: COPY.blurry, details: null, extras, buttons: ["recheck", "scanAnyway"] };
  }
  if (check.verdict === "marginal") {
    return {
      headline: COPY.marginalSmall,
      details: null,
      extras,
      buttons: hasCrop ? ["crop", "scan", "phone"] : ["scan", "phone"],
    };
  }
  return {
    headline: COPY.coarseSmall,
    details: COPY.coarseDetails(Math.round(check.proj_px_per_cam_px)),
    extras,
    buttons: hasCrop ? ["phone", "crop", "scanAnyway"] : ["phone", "scanAnyway"],
  };
}

/** The backend refuses a crop drawn for another camera picture size with this sentence. */
export const isPictureSizeMessage = (message: string): boolean =>
  message.includes("different camera picture size");
