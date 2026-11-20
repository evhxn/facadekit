import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Checklist from "./Checklist";
import Stepper from "./Stepper";

afterEach(cleanup);

const steps = (done: boolean[]) =>
  ["Projector", "Camera", "Scan"].map((title, i) => ({ title, done: done[i] ?? false }));

describe("Stepper", () => {
  it("says where the user is and lets them go back, not forward past unfinished work", () => {
    const onSelect = vi.fn();
    render(<Stepper steps={steps([true, false, false])} current={2} onSelect={onSelect} />);
    expect(screen.getByText("Step 2 of 3: Camera")).toBeTruthy();
    expect(screen.getByText("Camera").closest("[aria-current]")?.getAttribute("aria-current")).toBe(
      "step",
    );
    fireEvent.click(screen.getByRole("button", { name: /Projector/ }));
    expect(onSelect).toHaveBeenCalledWith(1);
    expect(screen.queryByRole("button", { name: /Scan/ })).toBeNull();
  });

  it("lets the user jump to a later step that is already finished", () => {
    const onSelect = vi.fn();
    render(<Stepper steps={steps([true, true, true])} current={1} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /Scan/ }));
    expect(onSelect).toHaveBeenCalledWith(3);
  });
});

describe("Checklist", () => {
  it("lists each item with its state in words for screen readers", () => {
    render(
      <Checklist
        title="Ready to scan?"
        items={[
          { label: "Projector", state: "ok", detail: "1920 × 1080" },
          { label: "Camera check", state: "problem", detail: "The picture is too small." },
          { label: "Scan", state: "todo" },
        ]}
      />,
    );
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items[0]).toContain("Projector (done)1920 × 1080");
    expect(items[1]).toContain("Camera check (needs attention)");
    expect(items[2]).toContain("Scan (not done yet)");
  });
});
