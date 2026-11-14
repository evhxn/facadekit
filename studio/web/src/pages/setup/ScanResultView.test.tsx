import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import ScanResultView, { legend, reasonForGray } from "./ScanResultView";

afterEach(cleanup);

describe("scan result: why parts were not measured", () => {
  it("reads the reason codes of reasons.png", () => {
    expect([0, 50, 100, 150, 200].map(reasonForGray)).toEqual([
      null,
      "dark",
      "bright",
      "outside",
      "unclear",
    ]);
    expect(reasonForGray(52)).toBe("dark"); // survives a little rounding
  });

  it("lists only reasons that matter, largest first", () => {
    expect(legend({ dark: 0.12, bright: 0, outside: 0.003, unclear: 0.2 })).toEqual([
      { reason: "unclear", share: 0.2 },
      { reason: "dark", share: 0.12 },
    ]);
    expect(legend(null)).toEqual([]);
  });

  it("says what each colour means and what to do about it", () => {
    render(
      <ScanResultView
        photoUrl="/view.png"
        reasonsUrl="/reasons.png"
        unmeasured={{ dark: 0.14, bright: 0, outside: 0.006, unclear: 0.08 }}
      />,
    );
    const items = screen.getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(items).toHaveLength(3);
    expect(items[0]).toContain("Too dark: 14% of the projection.");
    expect(items[1]).toContain("Stripes not readable: 8% of the projection.");
    expect(items[1]).toContain("steep angle");
    expect(items[2]).toContain("Outside the camera picture: under 1% of the projection.");
    const toggle = screen.getByRole("checkbox") as HTMLInputElement;
    expect(toggle.checked).toBe(true);
    fireEvent.click(toggle);
    expect(document.querySelector("canvas")?.style.visibility).toBe("hidden");
  });

  it("shows no legend for a scan made before reasons existed", () => {
    render(<ScanResultView photoUrl="/view.png" maskUrl="/mask.png" unmeasured={null} />);
    expect(screen.queryByRole("list")).toBeNull();
    expect(document.querySelector("canvas")).not.toBeNull();
  });
});
