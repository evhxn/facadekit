import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getProject } from "../api/client";
import DiagnosticsButton, { collectDiagnostics } from "./DiagnosticsButton";

function respond(status: number, body: unknown, requestId?: string) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(requestId ? { "X-Request-ID": requestId } : {}),
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("diagnostic info", () => {
  it("holds the server report and this page's failed requests with their request ids", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.endsWith("/diagnostics")
          ? respond(200, { system: { facadekit: "0.1.0" }, log_tail: ["line"] })
          : respond(
              404,
              { error: { code: "NOT_FOUND", message: "project not found" } },
              "ab12cd34",
            ),
      ),
    );
    await expect(getProject("nope")).rejects.toMatchObject({ requestId: "ab12cd34" });
    const info = JSON.parse(await collectDiagnostics());
    expect(info.server.system.facadekit).toBe("0.1.0");
    const last = info.browser.recent_errors.at(-1);
    expect(last).toMatchObject({
      request: "GET /projects/nope",
      status: 404,
      code: "NOT_FOUND",
      request_id: "ab12cd34",
    });
  });

  it("still gives the browser part when the server is down", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    const info = JSON.parse(await collectDiagnostics());
    expect(info.server.unavailable).toBe("Failed to fetch");
    expect(info.browser.user_agent).toBeTruthy();
  });

  it("copies to the clipboard and says so", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => respond(200, { system: {} })),
    );
    const writeText = vi.fn<(text: string) => Promise<void>>(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<DiagnosticsButton />);
    fireEvent.click(screen.getByRole("button", { name: "Copy diagnostic info" }));
    await screen.findByRole("status");
    expect(writeText).toHaveBeenCalledOnce();
    expect(JSON.parse(writeText.mock.calls[0]![0]).server).toEqual({ system: {} });
  });

  it("shows the text to copy by hand when the clipboard is refused", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => respond(200, { system: {} })),
    );
    Object.defineProperty(navigator, "clipboard", {
      value: {
        writeText: async () => {
          throw new Error("denied");
        },
      },
      configurable: true,
    });
    render(<DiagnosticsButton />);
    fireEvent.click(screen.getByRole("button", { name: "Copy diagnostic info" }));
    const box = await screen.findByLabelText("Diagnostic info");
    await waitFor(() => expect((box as HTMLTextAreaElement).value).toContain("user_agent"));
  });
});
