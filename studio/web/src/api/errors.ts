const MESSAGES: Record<string, string> = {
  RESOLUTION_MISMATCH:
    "Your computer is not sending the projector its native resolution. Set the projector display to the project's resolution and 100 % scaling.",
  CAMERA_NO_PROJECTION:
    "The camera cannot see the projection. Move the camera so the whole white rectangle is inside the picture.",
  TOO_BRIGHT: "It is too bright to scan. Try again after sunset or turn off nearby lights.",
  SCAN_FAILED:
    "The scan did not work. Try again, move the camera closer to the projector, or use manual mode.",
  PROJECTOR_NOT_CONNECTED:
    "The projector window is not open. Open the projector page on the computer connected to the projector and try again.",
  PROJECTOR_TIMEOUT:
    "The projector did not respond in time. Check that the projector window is open and visible, then try again.",
  CAMERA_OPEN_FAILED:
    "The camera could not be opened. Check that it is plugged in and not used by another program.",
  FPP_UNREACHABLE:
    "The Falcon Player (FPP) could not be reached. Check the address and that the Pi is powered and on the same network.",
  FPP_TOO_OLD: "The Falcon Player (FPP) version is too old. Update FPP and try again.",
  FPP_UPLOAD_FAILED:
    "Sending the video to the Falcon Player (FPP) failed. Check the connection and free space on the Pi, then try again.",
  UNSUPPORTED_MEDIA: "This file type is not supported. Try a common video, image or audio format.",
  ASSET_IN_USE: "This file is still used by clips on the timeline. Remove those clips first.",
  REVISION_CONFLICT:
    "The project was changed somewhere else, so it was reloaded. Please repeat your last change.",
  PHONE_VIDEO_UNREADABLE:
    "FacadeKit cannot read this video. Record at 1080p or 4K in the 'Most Compatible' format and try again.",
  PHONE_VIDEO_NO_PATTERN:
    "The video does not contain the whole light show. Start recording before the projector flashes and stop after it goes dark.",
  PHONE_MOVED:
    "The phone moved during the light show. Use a tripod or lean it on something steady, then try again.",
  NETWORK: "Cannot reach the FacadeKit server. Check that it is running and try again.",
};

export function userMessage(code: string, fallback: string): string {
  return Object.prototype.hasOwnProperty.call(MESSAGES, code)
    ? (MESSAGES[code] ?? fallback)
    : fallback;
}
