import { describe, expect, it } from "vitest";
import { APPROACHES, type Approach } from "./scene";
import {
  angleToApproach,
  angleToClock,
  clampPct,
  clockHandleOffset,
  clockStep,
  describeClock,
  handlePosition,
  markerPosition,
} from "./manipulate";

describe("clampPct", () => {
  it("passes values already inside the safe band through untouched", () => {
    expect(clampPct(50)).toBe(50);
    expect(clampPct(9)).toBe(9);
    expect(clampPct(91)).toBe(91);
  });

  it("clamps below the floor and above the ceiling", () => {
    expect(clampPct(-500)).toBe(9);
    expect(clampPct(0)).toBe(9);
    expect(clampPct(1000)).toBe(91);
  });

  it("never returns non-finite output, even for non-finite input", () => {
    for (const bad of [NaN, Infinity, -Infinity]) {
      const out = clampPct(bad);
      expect(Number.isFinite(out)).toBe(true);
      expect(out).toBeGreaterThanOrEqual(9);
      expect(out).toBeLessThanOrEqual(91);
    }
  });
});

describe("markerPosition", () => {
  it("places a lone vehicle at its approach's compass slot", () => {
    expect(markerPosition("N", 0, 1)).toEqual({ left: 50, top: 26 });
    expect(markerPosition("E", 0, 1)).toEqual({ left: 74, top: 50 });
    expect(markerPosition("S", 0, 1)).toEqual({ left: 50, top: 74 });
    expect(markerPosition("W", 0, 1)).toEqual({ left: 26, top: 50 });
  });

  it("fans out multiple vehicles at the same approach without collapsing to one point", () => {
    const a = markerPosition("N", 0, 2);
    const b = markerPosition("N", 1, 2);
    expect(a).not.toEqual(b);
    // N/S stagger along `left`; the shared `top` stays on the compass slot.
    expect(a.top).toBe(26);
    expect(b.top).toBe(26);
    expect(a.left).not.toBe(b.left);
  });

  it("staggers E/W along `top` (the perpendicular axis) instead of `left`", () => {
    const a = markerPosition("E", 0, 2);
    const b = markerPosition("E", 1, 2);
    expect(a.left).toBe(74);
    expect(b.left).toBe(74);
    expect(a.top).not.toBe(b.top);
  });

  it("keeps every fanned-out position within the clamp band", () => {
    for (const approach of APPROACHES) {
      for (let count = 1; count <= 6; count += 1) {
        for (let i = 0; i < count; i += 1) {
          const p = markerPosition(approach, i, count);
          expect(p.left).toBeGreaterThanOrEqual(9);
          expect(p.left).toBeLessThanOrEqual(91);
          expect(p.top).toBeGreaterThanOrEqual(9);
          expect(p.top).toBeLessThanOrEqual(91);
        }
      }
    }
  });
});

describe("angleToApproach (move snapping)", () => {
  it.each<[string, number, number, Approach]>([
    ["straight up -> N", 0, -100, "N"],
    ["straight right -> E", 100, 0, "E"],
    ["straight down -> S", 0, 100, "S"],
    ["straight left -> W", -100, 0, "W"],
    ["up-and-slightly-right stays N", 10, -100, "N"],
    ["far up-right (45deg) rounds to E (tie breaks up)", 100, -100, "E"],
  ])("%s", (_label, dx, dy, expected) => {
    expect(angleToApproach(dx, dy)).toBe(expected);
  });

  it("is a total function: every legal drag vector snaps to a legal Approach", () => {
    const samples = [0, 1, -1, 6, -6, 50, -50, 1e6, -1e6, 0.0001, -0.0001];
    for (const dx of samples) {
      for (const dy of samples) {
        expect(APPROACHES).toContain(angleToApproach(dx, dy));
      }
    }
  });

  it("never throws or produces an illegal value for non-finite or degenerate input", () => {
    const cases: Array<[number, number]> = [
      [NaN, 5],
      [5, NaN],
      [NaN, NaN],
      [Infinity, 1],
      [1, -Infinity],
      [0, 0],
    ];
    for (const [dx, dy] of cases) {
      const out = angleToApproach(dx, dy);
      expect(APPROACHES).toContain(out);
    }
  });

  it("covers the full circle: every 5deg step lands on one of the 4 legal values", () => {
    for (let deg = 0; deg < 360; deg += 5) {
      const rad = (deg * Math.PI) / 180;
      // Construct (dx,dy) so the resulting angleDeg() matches `deg` under the
      // module's own 0=up/clockwise convention (dx = sin, dy = -cos).
      const dx = Math.sin(rad) * 100;
      const dy = -Math.cos(rad) * 100;
      expect(APPROACHES).toContain(angleToApproach(dx, dy));
    }
  });
});

describe("angleToClock (rotate snapping)", () => {
  it.each<[string, number, number, number]>([
    ["up -> 12", 0, -100, 12],
    ["right -> 3", 100, 0, 3],
    ["down -> 6", 0, 100, 6],
    ["left -> 9", -100, 0, 9],
  ])("%s", (_label, dx, dy, expected) => {
    expect(angleToClock(dx, dy)).toBe(expected);
  });

  it("round-trips with clockHandleOffset for every clock position", () => {
    for (let clock = 1; clock <= 12; clock += 1) {
      const off = clockHandleOffset(clock, 100);
      expect(angleToClock(off.left, off.top)).toBe(clock);
    }
  });

  it("is a total function: every legal drag vector snaps into 1..12", () => {
    const samples = [0, 1, -1, 6, -6, 50, -50, 1e6, -1e6, 0.0001, -0.0001];
    for (const dx of samples) {
      for (const dy of samples) {
        const clock = angleToClock(dx, dy);
        expect(Number.isInteger(clock)).toBe(true);
        expect(clock).toBeGreaterThanOrEqual(1);
        expect(clock).toBeLessThanOrEqual(12);
      }
    }
  });

  it("never throws, returns 0, or produces an illegal value for degenerate input", () => {
    const cases: Array<[number, number]> = [
      [NaN, 5],
      [5, NaN],
      [NaN, NaN],
      [Infinity, 1],
      [1, -Infinity],
      [0, 0],
    ];
    for (const [dx, dy] of cases) {
      const out = angleToClock(dx, dy);
      expect(Number.isInteger(out)).toBe(true);
      expect(out).toBeGreaterThanOrEqual(1);
      expect(out).toBeLessThanOrEqual(12);
    }
  });
});

describe("clockHandleOffset / handlePosition", () => {
  it("matches ClockPicker's own convention: 12 points up, 3 right, 6 down, 9 left", () => {
    expect(clockHandleOffset(12, 10).left).toBeCloseTo(0);
    expect(clockHandleOffset(12, 10).top).toBeCloseTo(-10);
    expect(clockHandleOffset(3, 10).left).toBeCloseTo(10);
    expect(clockHandleOffset(3, 10).top).toBeCloseTo(0);
    expect(clockHandleOffset(6, 10).left).toBeCloseTo(0);
    expect(clockHandleOffset(6, 10).top).toBeCloseTo(10);
    expect(clockHandleOffset(9, 10).left).toBeCloseTo(-10);
    expect(clockHandleOffset(9, 10).top).toBeCloseTo(0);
  });

  it("handlePosition never escapes the clamp band even from an edge marker", () => {
    for (const approach of APPROACHES) {
      const marker = markerPosition(approach, 0, 1);
      for (let clock = 1; clock <= 12; clock += 1) {
        const p = handlePosition(marker, clock);
        expect(p.left).toBeGreaterThanOrEqual(9);
        expect(p.left).toBeLessThanOrEqual(91);
        expect(p.top).toBeGreaterThanOrEqual(9);
        expect(p.top).toBeLessThanOrEqual(91);
      }
    }
  });
});

describe("clockStep", () => {
  it("turns the impact on at 12 first, from not-struck, in either direction", () => {
    expect(clockStep(null, 1)).toBe(12);
    expect(clockStep(null, -1)).toBe(12);
  });

  it("wraps forward past 12 back to 1", () => {
    expect(clockStep(12, 1)).toBe(1);
  });

  it("wraps backward past 1 back to 12", () => {
    expect(clockStep(1, -1)).toBe(12);
  });

  it("steps by exactly one otherwise", () => {
    expect(clockStep(3, 1)).toBe(4);
    expect(clockStep(3, -1)).toBe(2);
  });

  it("stays within 1..12 for a full lap in both directions", () => {
    let clock: number | null = null;
    for (let i = 0; i < 30; i += 1) {
      clock = clockStep(clock, 1);
      expect(clock).toBeGreaterThanOrEqual(1);
      expect(clock).toBeLessThanOrEqual(12);
    }
    clock = null;
    for (let i = 0; i < 30; i += 1) {
      clock = clockStep(clock, -1);
      expect(clock).toBeGreaterThanOrEqual(1);
      expect(clock).toBeLessThanOrEqual(12);
    }
  });
});

describe("describeClock", () => {
  it("labels not-struck", () => {
    expect(describeClock(null)).toBe("not struck");
  });

  it("adds the body-position meaning for the 4 cardinal clocks", () => {
    expect(describeClock(12)).toBe("12 o'clock (front)");
    expect(describeClock(3)).toBe("3 o'clock (right side)");
    expect(describeClock(6)).toBe("6 o'clock (rear)");
    expect(describeClock(9)).toBe("9 o'clock (left side)");
  });

  it("has no meaning suffix for the other 8 positions", () => {
    expect(describeClock(1)).toBe("1 o'clock");
    expect(describeClock(7)).toBe("7 o'clock");
  });
});
