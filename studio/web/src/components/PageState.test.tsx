import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import EditorHeader from "./EditorHeader";
import PageState from "./PageState";

afterEach(cleanup);

describe("PageState", () => {
  it("announces loading politely and errors as alerts, with diagnostics on errors", () => {
    const { rerender } = render(<PageState kind="loading" title="Loading the project" />);
    expect(screen.getByRole("status").getAttribute("aria-busy")).toBe("true");
    rerender(<PageState kind="error" title="Could not load" />);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy diagnostic info" })).toBeTruthy();
  });
});

describe("EditorHeader", () => {
  it("shows the next step, and why it is not ready yet", () => {
    render(
      <MemoryRouter>
        <EditorHeader
          title="Surfaces"
          help="Mark the parts of the house to project onto."
          next={{
            to: "/p/x/arrange",
            label: "Arrange",
            ready: false,
            hint: "Add a surface first.",
          }}
        />
      </MemoryRouter>,
    );
    const link = screen.getByRole("link", { name: "Arrange →" });
    expect(link.getAttribute("href")).toBe("/p/x/arrange");
    expect(link.getAttribute("aria-describedby")).toBe("next-hint");
    expect(screen.getByText("Add a surface first.")).toBeTruthy();
  });
});
