import { describe, expect, it } from "vitest";
import type { Pt } from "../../api/types";
import {
  insertVertex,
  isSimple,
  nearestEdge,
  pointInPolygon,
  removeVertex,
  translate,
} from "./polygon";

const square: Pt[] = [
  [0, 0],
  [10, 0],
  [10, 10],
  [0, 10],
];

describe("polygon helpers", () => {
  it("inserts a vertex after the given edge index", () => {
    const out = insertVertex(square, 0, [5, 0]);
    expect(out).toEqual([
      [0, 0],
      [5, 0],
      [10, 0],
      [10, 10],
      [0, 10],
    ]);
    expect(insertVertex(square, 3, [0, 5])[4]).toEqual([0, 5]);
    expect(square).toHaveLength(4);
  });

  it("removes a vertex but never below 3", () => {
    expect(removeVertex(square, 1)).toEqual([
      [0, 0],
      [10, 10],
      [0, 10],
    ]);
    const tri = removeVertex(square, 1);
    expect(removeVertex(tri, 0)).toBe(tri);
  });

  it("finds the nearest edge, including the closing edge", () => {
    expect(nearestEdge(square, [5, -2])).toMatchObject({ index: 0, distance: 2, point: [5, 0] });
    expect(nearestEdge(square, [-1, 5])).toMatchObject({ index: 3, distance: 1, point: [0, 5] });
    expect(nearestEdge(square, [12, 12]).distance).toBeCloseTo(Math.hypot(2, 2));
  });

  it("tests point in polygon", () => {
    expect(pointInPolygon([5, 5], square)).toBe(true);
    expect(pointInPolygon([15, 5], square)).toBe(false);
  });

  it("translates", () => {
    expect(translate(square, 2, -1)[2]).toEqual([12, 9]);
  });

  it("detects a bow-tie as not simple", () => {
    expect(isSimple(square)).toBe(true);
    expect(
      isSimple([
        [0, 0],
        [10, 10],
        [10, 0],
        [0, 10],
      ]),
    ).toBe(false);
    expect(
      isSimple([
        [0, 0],
        [10, 0],
        [5, 8],
      ]),
    ).toBe(true);
  });
});
