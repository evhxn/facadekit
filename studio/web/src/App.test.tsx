import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import App from "./App";

afterEach(cleanup);

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App", () => {
  it("renders the FacadeKit header", () => {
    renderAt("/");
    expect(screen.getByText("FacadeKit")).toBeTruthy();
  });

  it("shows project nav links on project routes", () => {
    renderAt("/p/demo/setup");
    expect(screen.getByText("Surfaces")).toBeTruthy();
    expect(screen.getByText("Publish")).toBeTruthy();
  });

  it("renders /projector without the header", () => {
    renderAt("/projector");
    expect(screen.queryByText("FacadeKit")).toBeNull();
    expect(screen.getByText("Projector")).toBeTruthy();
  });
});
