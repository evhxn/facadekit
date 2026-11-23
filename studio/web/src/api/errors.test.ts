import { describe, expect, it } from "vitest";
import { userMessage } from "./errors";

describe("phone error messages (docs/08 section 4)", () => {
  it("maps the three phone codes", () => {
    expect(userMessage("PHONE_VIDEO_UNREADABLE", "x")).toBe(
      "FacadeKit cannot read this video. Record at 1080p or 4K in the 'Most Compatible' format and try again.",
    );
    expect(userMessage("PHONE_VIDEO_NO_PATTERN", "x")).toBe(
      "The video does not contain the whole light show. Start recording before the projector flashes and stop after it goes dark.",
    );
    expect(userMessage("PHONE_MOVED", "x")).toBe(
      "The phone moved during the light show. Use a tripod or lean it on something steady, then try again.",
    );
  });
  it("falls back to the server text for other codes", () => {
    expect(userMessage("NOT_FOUND", "This light show has expired. Start it again.")).toBe(
      "This light show has expired. Start it again.",
    );
  });
  it("keeps the numbers of a resolution mismatch", () => {
    const detail =
      "Projector window is 1512x982 but the project expects 1920x1080. The computer scales this display by 2.";
    expect(userMessage("RESOLUTION_MISMATCH", detail)).toBe(
      `The projector window does not have the project's resolution. ${detail}`,
    );
    expect(userMessage("RESOLUTION_MISMATCH", "")).toBe(
      "The projector window does not have the project's resolution.",
    );
  });
  it("keeps the FPP connection detail", () => {
    const detail = "No route to 192.168.1.50. Check the address.";
    expect(userMessage("FPP_UNREACHABLE", detail)).toBe(
      `The Falcon Player (FPP) could not be reached. Check the address and that the Pi is powered and on the same network. ${detail}`,
    );
    expect(userMessage("FPP_AUTH_REQUIRED", "FPP at 192.168.1.50 asks for a password.")).toBe(
      "FPP at 192.168.1.50 asks for a password.",
    );
  });
});
