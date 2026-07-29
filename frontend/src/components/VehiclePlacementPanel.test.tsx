import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { VehiclePlacementPanel } from "./VehiclePlacementPanel";
import {
  APPROACHES,
  ROAD_LAYOUTS,
  addVehicle,
  emptyScene,
  impactClock,
  patchMovement,
  setImpact,
  setRoad,
  type Scene,
} from "@/lib/scene";

// jsdom has no native PointerEvent constructor (confirmed: 'PointerEvent' in
// window is false on jsdom 24), and @testing-library/dom's fireEvent.pointerX
// helpers silently fall back to the bare `Event` constructor in that case —
// which drops clientX/clientY/pointerType, since only MouseEvent defines
// those. So tests build a real MouseEvent (jsdom implements it fully) typed
// as the pointer event name: DOM dispatch matches listeners by `.type`
// string, not by constructor, and React's synthetic PointerEvent reads
// clientX/clientY straight off the native event — so this reaches
// onPointerDown/Move/Up exactly like a real pointer event would.
function firePointer(
  target: Element | Window,
  type: "pointerdown" | "pointermove" | "pointerup" | "pointercancel",
  init: { clientX: number; clientY: number; pointerType?: string; button?: number },
) {
  const event = new MouseEvent(type, {
    clientX: init.clientX,
    clientY: init.clientY,
    button: init.button ?? 0,
    bubbles: true,
    cancelable: true,
  });
  Object.defineProperty(event, "pointerType", {
    value: init.pointerType ?? "mouse",
    configurable: true,
  });
  fireEvent(target, event);
}

function mockCenter(el: Element, cx: number, cy: number, half = 22) {
  vi.spyOn(el, "getBoundingClientRect").mockReturnValue({
    left: cx - half,
    top: cy - half,
    width: half * 2,
    height: half * 2,
    right: cx + half,
    bottom: cy + half,
    x: cx - half,
    y: cy - half,
    toJSON() {
      return {};
    },
  } as DOMRect);
}

function marker(name: string) {
  return screen.getByRole("button", { name: new RegExp(`^${name}:`) });
}
function rotateHandle(name: string) {
  return screen.getByRole("button", { name: new RegExp(`^Rotate ${name}:`) });
}
function canvas() {
  return screen.getByRole("group", { name: /vehicle placement canvas/i });
}

function twoVehicleScene(): Scene {
  return addVehicle(emptyScene()); // veh_a (approach N) + veh_b (approach S)
}

describe("VehiclePlacementPanel", () => {
  it("renders one marker per vehicle with an aria-label reflecting approach and struck state", () => {
    render(<VehiclePlacementPanel scene={emptyScene()} onChange={vi.fn()} />);
    expect(marker("veh_a")).toHaveAccessibleName(/north approach, not struck/i);
  });

  it("reflects a clock position already set via the dropdown/ClockPicker path", () => {
    const scene = setImpact(emptyScene(), "veh_a", 6);
    render(<VehiclePlacementPanel scene={scene} onChange={vi.fn()} />);
    expect(marker("veh_a")).toHaveAccessibleName(/6 o'clock \(rear\)/i);
  });

  it("reflects an approach already set via patchMovement (the dropdown path)", () => {
    const scene = patchMovement(emptyScene(), "veh_a", { approach: "W" });
    render(<VehiclePlacementPanel scene={scene} onChange={vi.fn()} />);
    expect(marker("veh_a")).toHaveAccessibleName(/west approach/i);
  });

  it("selects a vehicle on pointerdown (aria-pressed) and reveals its rotate handle", () => {
    render(<VehiclePlacementPanel scene={emptyScene()} onChange={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /^Rotate/i })).not.toBeInTheDocument();
    firePointer(marker("veh_a"), "pointerdown", { clientX: 10, clientY: 10 });
    firePointer(window, "pointerup", { clientX: 10, clientY: 10 });
    expect(marker("veh_a")).toHaveAttribute("aria-pressed", "true");
    expect(rotateHandle("veh_a")).toBeInTheDocument();
  });

  it("deselects when Escape is pressed on the marker", () => {
    render(<VehiclePlacementPanel scene={emptyScene()} onChange={vi.fn()} />);
    firePointer(marker("veh_a"), "pointerdown", { clientX: 10, clientY: 10 });
    firePointer(window, "pointerup", { clientX: 10, clientY: 10 });
    expect(rotateHandle("veh_a")).toBeInTheDocument();
    fireEvent.keyDown(marker("veh_a"), { key: "Escape" });
    expect(marker("veh_a")).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("button", { name: /^Rotate/i })).not.toBeInTheDocument();
  });

  it("deselects when the canvas background (not a vehicle) is clicked", () => {
    render(<VehiclePlacementPanel scene={emptyScene()} onChange={vi.fn()} />);
    firePointer(marker("veh_a"), "pointerdown", { clientX: 10, clientY: 10 });
    firePointer(window, "pointerup", { clientX: 10, clientY: 10 });
    expect(marker("veh_a")).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(canvas());
    expect(marker("veh_a")).toHaveAttribute("aria-pressed", "false");
  });

  it("Enter/Space on an unselected marker selects it", () => {
    render(<VehiclePlacementPanel scene={emptyScene()} onChange={vi.fn()} />);
    fireEvent.keyDown(marker("veh_a"), { key: "Enter" });
    expect(marker("veh_a")).toHaveAttribute("aria-pressed", "true");
  });

  it("an unrecognised key does nothing (no crash, no scene change)", () => {
    const onChange = vi.fn();
    render(<VehiclePlacementPanel scene={emptyScene()} onChange={onChange} />);
    fireEvent.keyDown(marker("veh_a"), { key: "q" });
    expect(onChange).not.toHaveBeenCalled();
  });

  describe("move: keyboard", () => {
    it.each(APPROACHES.map((a) => [a] as const))(
      "arrow key sets approach to %s, matching patchMovement exactly",
      (approach) => {
        const onChange = vi.fn();
        const scene = emptyScene();
        render(<VehiclePlacementPanel scene={scene} onChange={onChange} />);
        const key = { N: "ArrowUp", E: "ArrowRight", S: "ArrowDown", W: "ArrowLeft" }[approach];
        fireEvent.keyDown(marker("veh_a"), { key });
        expect(onChange).toHaveBeenCalledWith(patchMovement(scene, "veh_a", { approach }));
      },
    );

    it("shift+arrow does not move (reserved for rotate)", () => {
      const onChange = vi.fn();
      render(<VehiclePlacementPanel scene={emptyScene()} onChange={onChange} />);
      fireEvent.keyDown(marker("veh_a"), { key: "ArrowUp", shiftKey: true });
      // ArrowUp+shift is not bound to anything for the marker; nothing commits.
      expect(onChange).not.toHaveBeenCalled();
    });
  });

  describe("move: pointer drag", () => {
    it("drags past the threshold and commits the nearest approach on release", () => {
      const onChange = vi.fn();
      const scene = emptyScene(); // veh_a starts at approach N
      render(<VehiclePlacementPanel scene={scene} onChange={onChange} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 100, clientY: 100 });
      firePointer(window, "pointermove", { clientX: 100, clientY: 160 }); // dy=+60 -> S
      firePointer(window, "pointerup", { clientX: 100, clientY: 160 });
      expect(onChange).toHaveBeenCalledWith(patchMovement(scene, "veh_a", { approach: "S" }));
    });

    it("does not deselect on the native click that a real browser synthesises right after a drag", () => {
      // Regression test: a real browser fires a synthetic "click" after
      // mousedown/mouseup land on different elements (the marker relocates
      // to its candidate slot mid-drag), targeting their nearest common
      // ancestor — this canvas. Confirmed against a real browser (Edge) that
      // this used to immediately undo the selection right after a successful
      // move. Exactly one such click must be swallowed; a later, genuine
      // background click must still deselect normally.
      render(<VehiclePlacementPanel scene={emptyScene()} onChange={vi.fn()} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 100, clientY: 100 });
      firePointer(window, "pointermove", { clientX: 100, clientY: 160 }); // -> S, past threshold
      firePointer(window, "pointerup", { clientX: 100, clientY: 160 });
      expect(marker("veh_a")).toHaveAttribute("aria-pressed", "true");

      fireEvent.click(canvas()); // the browser's post-drag synthetic click
      expect(marker("veh_a")).toHaveAttribute("aria-pressed", "true");

      fireEvent.click(canvas()); // a later, genuine background click
      expect(marker("veh_a")).toHaveAttribute("aria-pressed", "false");
    });

    it("the suppression never leaks into an unrelated later click when no synthetic click ever arrives", () => {
      // If a drag's pointerup is released outside the panel entirely, no
      // click ever reaches the canvas to consume the flag. It must still not
      // survive into the NEXT gesture and swallow a later, real deselect.
      render(<VehiclePlacementPanel scene={emptyScene()} onChange={vi.fn()} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 100, clientY: 100 });
      firePointer(window, "pointermove", { clientX: 100, clientY: 160 }); // -> S, past threshold
      firePointer(window, "pointerup", { clientX: 100, clientY: 160 }); // no click ever follows

      // A fresh, ordinary click-to-select (no movement) on the same marker.
      firePointer(marker("veh_a"), "pointerdown", { clientX: 5, clientY: 5 });
      firePointer(window, "pointerup", { clientX: 5, clientY: 5 });
      expect(marker("veh_a")).toHaveAttribute("aria-pressed", "true");

      // A single genuine background click must deselect immediately, not be
      // swallowed by a stale flag left over from the earlier drag.
      fireEvent.click(canvas());
      expect(marker("veh_a")).toHaveAttribute("aria-pressed", "false");
    });

    it("shows the live candidate snap target while dragging, before release", () => {
      render(<VehiclePlacementPanel scene={emptyScene()} onChange={vi.fn()} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 100, clientY: 100 });
      firePointer(window, "pointermove", { clientX: 160, clientY: 100 }); // dx=+60 -> E
      expect(marker("veh_a")).toHaveAccessibleName(/east approach/i);
      firePointer(window, "pointerup", { clientX: 160, clientY: 100 });
    });

    it("a drag that never clears the threshold commits nothing (a click, not a move)", () => {
      const onChange = vi.fn();
      render(<VehiclePlacementPanel scene={emptyScene()} onChange={onChange} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 100, clientY: 100 });
      firePointer(window, "pointermove", { clientX: 102, clientY: 101 }); // well under threshold
      firePointer(window, "pointerup", { clientX: 102, clientY: 101 });
      expect(onChange).not.toHaveBeenCalled();
      // The click still selected the vehicle.
      expect(marker("veh_a")).toHaveAttribute("aria-pressed", "true");
    });

    it("pointercancel aborts the drag without committing", () => {
      const onChange = vi.fn();
      render(<VehiclePlacementPanel scene={emptyScene()} onChange={onChange} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 100, clientY: 100 });
      firePointer(window, "pointermove", { clientX: 100, clientY: 200 });
      firePointer(window, "pointercancel", { clientX: 100, clientY: 200 });
      expect(onChange).not.toHaveBeenCalled();
    });

    it("ignores a non-primary mouse button (right-click does not drag or select)", () => {
      const onChange = vi.fn();
      render(<VehiclePlacementPanel scene={emptyScene()} onChange={onChange} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 100, clientY: 100, pointerType: "mouse", button: 2 });
      firePointer(window, "pointermove", { clientX: 100, clientY: 200 });
      firePointer(window, "pointerup", { clientX: 100, clientY: 200 });
      expect(onChange).not.toHaveBeenCalled();
      expect(marker("veh_a")).toHaveAttribute("aria-pressed", "false");
    });

    it("works identically for a touch pointer", () => {
      const onChange = vi.fn();
      const scene = emptyScene();
      render(<VehiclePlacementPanel scene={scene} onChange={onChange} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 100, clientY: 100, pointerType: "touch" });
      firePointer(window, "pointermove", { clientX: 160, clientY: 100, pointerType: "touch" });
      firePointer(window, "pointerup", { clientX: 160, clientY: 100, pointerType: "touch" });
      expect(onChange).toHaveBeenCalledWith(patchMovement(scene, "veh_a", { approach: "E" }));
    });

    it("an absurd drag delta still commits one of the 4 legal approaches, never a raw pixel", () => {
      const onChange = vi.fn();
      render(<VehiclePlacementPanel scene={emptyScene()} onChange={onChange} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 0, clientY: 0 });
      firePointer(window, "pointermove", { clientX: 1e7, clientY: 0.0003 });
      firePointer(window, "pointerup", { clientX: 1e7, clientY: 0.0003 });
      expect(onChange).toHaveBeenCalledTimes(1);
      const next = onChange.mock.calls[0]?.[0] as Scene;
      const approach = next.movements.find((m) => m.vehicle_id === "veh_a")?.approach;
      expect(APPROACHES).toContain(approach);
    });
  });

  describe("rotate: keyboard (on the marker itself, once selected)", () => {
    it("[ steps the impact clock down (or turns it on at 12 first)", () => {
      const onChange = vi.fn();
      const scene = emptyScene();
      render(<VehiclePlacementPanel scene={scene} onChange={onChange} />);
      fireEvent.keyDown(marker("veh_a"), { key: "[" });
      expect(onChange).toHaveBeenCalledWith(setImpact(scene, "veh_a", 12));
    });

    it("] steps the impact clock up", () => {
      const onChange = vi.fn();
      const scene = setImpact(emptyScene(), "veh_a", 6);
      render(<VehiclePlacementPanel scene={scene} onChange={onChange} />);
      fireEvent.keyDown(marker("veh_a"), { key: "]" });
      expect(onChange).toHaveBeenCalledWith(setImpact(scene, "veh_a", 7));
    });

    it("shift+ArrowLeft / shift+ArrowRight are documented equivalents of [ and ]", () => {
      const onChange = vi.fn();
      const scene = setImpact(emptyScene(), "veh_a", 6);
      render(<VehiclePlacementPanel scene={scene} onChange={onChange} />);
      fireEvent.keyDown(marker("veh_a"), { key: "ArrowLeft", shiftKey: true });
      expect(onChange).toHaveBeenCalledWith(setImpact(scene, "veh_a", 5));
      fireEvent.keyDown(marker("veh_a"), { key: "ArrowRight", shiftKey: true });
      expect(onChange).toHaveBeenCalledWith(setImpact(scene, "veh_a", 7));
    });
  });

  describe("rotate: keyboard (on the dedicated rotate handle)", () => {
    it("arrow keys and brackets both rotate the handle", () => {
      const onChange = vi.fn();
      const scene = setImpact(emptyScene(), "veh_a", 6);
      render(<VehiclePlacementPanel scene={scene} onChange={onChange} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 10, clientY: 10 });
      firePointer(window, "pointerup", { clientX: 10, clientY: 10 });

      fireEvent.keyDown(rotateHandle("veh_a"), { key: "ArrowUp" });
      expect(onChange).toHaveBeenLastCalledWith(setImpact(scene, "veh_a", 5));

      fireEvent.keyDown(rotateHandle("veh_a"), { key: "]" });
      expect(onChange).toHaveBeenLastCalledWith(setImpact(scene, "veh_a", 7));
    });

    it("Backspace clears the impact back to not-struck", () => {
      const onChange = vi.fn();
      const scene = setImpact(emptyScene(), "veh_a", 6);
      render(<VehiclePlacementPanel scene={scene} onChange={onChange} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 10, clientY: 10 });
      firePointer(window, "pointerup", { clientX: 10, clientY: 10 });
      fireEvent.keyDown(rotateHandle("veh_a"), { key: "Backspace" });
      expect(onChange).toHaveBeenCalledWith(setImpact(scene, "veh_a", null));
    });

    it("Escape on the handle deselects too", () => {
      const onChange = vi.fn();
      render(<VehiclePlacementPanel scene={emptyScene()} onChange={onChange} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 10, clientY: 10 });
      firePointer(window, "pointerup", { clientX: 10, clientY: 10 });
      fireEvent.keyDown(rotateHandle("veh_a"), { key: "Escape" });
      expect(marker("veh_a")).toHaveAttribute("aria-pressed", "false");
    });

    it("an unrecognised key on the handle does nothing", () => {
      const onChange = vi.fn();
      render(<VehiclePlacementPanel scene={emptyScene()} onChange={onChange} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 10, clientY: 10 });
      firePointer(window, "pointerup", { clientX: 10, clientY: 10 });
      fireEvent.keyDown(rotateHandle("veh_a"), { key: "q" });
      expect(onChange).not.toHaveBeenCalled();
    });
  });

  describe("rotate: pointer drag", () => {
    it("drags the handle around the marker's real screen center and commits the nearest clock", () => {
      const onChange = vi.fn();
      const scene = emptyScene();
      render(<VehiclePlacementPanel scene={scene} onChange={onChange} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 10, clientY: 10 });
      firePointer(window, "pointerup", { clientX: 10, clientY: 10 });

      mockCenter(marker("veh_a"), 200, 200);
      firePointer(rotateHandle("veh_a"), "pointerdown", { clientX: 200, clientY: 184 });
      firePointer(window, "pointermove", { clientX: 260, clientY: 200 }); // dx=60,dy=0 from center -> 3
      firePointer(window, "pointerup", { clientX: 260, clientY: 200 });

      expect(onChange).toHaveBeenCalledWith(setImpact(scene, "veh_a", 3));
    });

    it("does not deselect on the native click that follows a real rotate drag either", () => {
      render(<VehiclePlacementPanel scene={emptyScene()} onChange={vi.fn()} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 10, clientY: 10 });
      firePointer(window, "pointerup", { clientX: 10, clientY: 10 });
      mockCenter(marker("veh_a"), 200, 200);
      firePointer(rotateHandle("veh_a"), "pointerdown", { clientX: 200, clientY: 184 });
      firePointer(window, "pointermove", { clientX: 260, clientY: 200 }); // 12 -> 3, a real rotation
      firePointer(window, "pointerup", { clientX: 260, clientY: 200 });

      fireEvent.click(canvas());
      expect(marker("veh_a")).toHaveAttribute("aria-pressed", "true");
    });

    it("shows a live clock readout while rotating, before release", () => {
      render(<VehiclePlacementPanel scene={emptyScene()} onChange={vi.fn()} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 10, clientY: 10 });
      firePointer(window, "pointerup", { clientX: 10, clientY: 10 });
      mockCenter(marker("veh_a"), 200, 200);
      firePointer(rotateHandle("veh_a"), "pointerdown", { clientX: 200, clientY: 184 });
      firePointer(window, "pointermove", { clientX: 200, clientY: 260 }); // straight down -> 6
      expect(screen.getByText(/veh_a: north approach, 6 o'clock \(rear\)/i)).toBeInTheDocument();
      firePointer(window, "pointerup", { clientX: 200, clientY: 260 });
    });

    it("pointercancel aborts the rotate without committing", () => {
      const onChange = vi.fn();
      render(<VehiclePlacementPanel scene={emptyScene()} onChange={onChange} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 10, clientY: 10 });
      firePointer(window, "pointerup", { clientX: 10, clientY: 10 });
      mockCenter(marker("veh_a"), 200, 200);
      firePointer(rotateHandle("veh_a"), "pointerdown", { clientX: 200, clientY: 184 });
      firePointer(window, "pointermove", { clientX: 260, clientY: 200 });
      firePointer(window, "pointercancel", { clientX: 260, clientY: 200 });
      expect(onChange).not.toHaveBeenCalled();
    });

    it("still commits a legal clock when nothing mocks the marker's rect (jsdom's zero-sized default)", () => {
      const onChange = vi.fn();
      render(<VehiclePlacementPanel scene={emptyScene()} onChange={onChange} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 10, clientY: 10 });
      firePointer(window, "pointerup", { clientX: 10, clientY: 10 });
      firePointer(rotateHandle("veh_a"), "pointerdown", { clientX: 0, clientY: 0 });
      firePointer(window, "pointermove", { clientX: 100, clientY: 0 });
      firePointer(window, "pointerup", { clientX: 100, clientY: 0 });
      expect(onChange).toHaveBeenCalledTimes(1);
      const next = onChange.mock.calls[0]?.[0] as Scene;
      expect(impactClock(next, "veh_a")).toBe(3);
    });

    it("an absurd drag delta still commits a clock in 1..12, never a raw pixel", () => {
      const onChange = vi.fn();
      render(<VehiclePlacementPanel scene={emptyScene()} onChange={onChange} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 10, clientY: 10 });
      firePointer(window, "pointerup", { clientX: 10, clientY: 10 });
      mockCenter(marker("veh_a"), 200, 200);
      firePointer(rotateHandle("veh_a"), "pointerdown", { clientX: 200, clientY: 184 });
      firePointer(window, "pointermove", { clientX: -1e9, clientY: 4.5 });
      firePointer(window, "pointerup", { clientX: -1e9, clientY: 4.5 });
      expect(onChange).toHaveBeenCalledTimes(1);
      const next = onChange.mock.calls[0]?.[0] as Scene;
      const clock = impactClock(next, "veh_a");
      expect(clock).not.toBeNull();
      expect(Number.isInteger(clock)).toBe(true);
      expect(clock as number).toBeGreaterThanOrEqual(1);
      expect(clock as number).toBeLessThanOrEqual(12);
    });
  });

  describe("keyboard/pointer parity", () => {
    it("keyboard move and pointer-drag move produce byte-identical scenes for the same target", () => {
      const scene = emptyScene();
      const viaKeyboard = vi.fn();
      const { unmount } = render(<VehiclePlacementPanel scene={scene} onChange={viaKeyboard} />);
      fireEvent.keyDown(marker("veh_a"), { key: "ArrowRight" });
      unmount();

      const viaPointer = vi.fn();
      render(<VehiclePlacementPanel scene={scene} onChange={viaPointer} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 0, clientY: 0 });
      firePointer(window, "pointermove", { clientX: 60, clientY: 0 });
      firePointer(window, "pointerup", { clientX: 60, clientY: 0 });

      expect(viaKeyboard).toHaveBeenCalledWith(patchMovement(scene, "veh_a", { approach: "E" }));
      expect(viaPointer).toHaveBeenCalledWith(patchMovement(scene, "veh_a", { approach: "E" }));
    });

    it("keyboard rotate and pointer-drag rotate produce byte-identical scenes for the same target", () => {
      const scene = emptyScene();
      const viaKeyboard = vi.fn();
      const { unmount } = render(<VehiclePlacementPanel scene={scene} onChange={viaKeyboard} />);
      fireEvent.keyDown(marker("veh_a"), { key: "]" }); // null -> 12
      unmount();

      const viaPointer = vi.fn();
      render(<VehiclePlacementPanel scene={scene} onChange={viaPointer} />);
      firePointer(marker("veh_a"), "pointerdown", { clientX: 10, clientY: 10 });
      firePointer(window, "pointerup", { clientX: 10, clientY: 10 });
      mockCenter(marker("veh_a"), 200, 200);
      firePointer(rotateHandle("veh_a"), "pointerdown", { clientX: 200, clientY: 184 });
      firePointer(window, "pointermove", { clientX: 200, clientY: 184 }); // straight up -> 12
      firePointer(window, "pointerup", { clientX: 200, clientY: 184 });

      expect(viaKeyboard).toHaveBeenCalledWith(setImpact(scene, "veh_a", 12));
      expect(viaPointer).toHaveBeenCalledWith(setImpact(scene, "veh_a", 12));
    });
  });

  describe("AI-proposed vs human-confirmed ghosting", () => {
    it("shows a dashed ghost marker when the proposal's approach differs from the confirmed one", () => {
      const proposed = emptyScene(); // veh_a approach N
      const confirmed = patchMovement(emptyScene(), "veh_a", { approach: "S" });
      const { container } = render(
        <VehiclePlacementPanel scene={confirmed} proposedScene={proposed} onChange={vi.fn()} />,
      );
      const ghosts = Array.from(container.querySelectorAll('[aria-hidden="true"]')).filter(
        (el) => el.textContent === "veh_a",
      );
      expect(ghosts).toHaveLength(1);
    });

    it("shows no ghost marker when the proposal already agrees with the confirmed scene", () => {
      const scene = emptyScene();
      const { container } = render(
        <VehiclePlacementPanel scene={scene} proposedScene={scene} onChange={vi.fn()} />,
      );
      const ghosts = Array.from(container.querySelectorAll('[aria-hidden="true"]')).filter(
        (el) => el.textContent === "veh_a",
      );
      expect(ghosts).toHaveLength(0);
    });

    it("shows a dashed ghost rotate-handle when the proposal's impact clock differs, once selected", () => {
      const proposed = setImpact(emptyScene(), "veh_a", 6);
      const confirmed = emptyScene(); // not struck
      const { container } = render(
        <VehiclePlacementPanel scene={confirmed} proposedScene={proposed} onChange={vi.fn()} />,
      );
      firePointer(marker("veh_a"), "pointerdown", { clientX: 10, clientY: 10 });
      firePointer(window, "pointerup", { clientX: 10, clientY: 10 });
      const ghostHandle = Array.from(container.querySelectorAll('[aria-hidden="true"]')).find((el) =>
        el.className.toString().includes("border-cyan-300/60"),
      );
      expect(ghostHandle).toBeTruthy();
    });

    it("shows no ghost when there is no proposed scene at all (e.g. a scenario with no AI baseline)", () => {
      const { container } = render(<VehiclePlacementPanel scene={emptyScene()} onChange={vi.fn()} />);
      const ghosts = Array.from(container.querySelectorAll('[aria-hidden="true"]')).filter(
        (el) => el.textContent === "veh_a",
      );
      expect(ghosts).toHaveLength(0);
    });
  });

  it("fans out multiple vehicles sharing an approach instead of stacking them", () => {
    render(<VehiclePlacementPanel scene={twoVehicleScene()} onChange={vi.fn()} />);
    expect(marker("veh_a")).toHaveAccessibleName(/north approach/i);
    expect(marker("veh_b")).toHaveAccessibleName(/south approach/i);
  });

  it("does not crash and drops the rotate handle when the selected vehicle is removed", () => {
    const scene = twoVehicleScene();
    const { rerender } = render(<VehiclePlacementPanel scene={scene} onChange={vi.fn()} />);
    firePointer(marker("veh_a"), "pointerdown", { clientX: 10, clientY: 10 });
    firePointer(window, "pointerup", { clientX: 10, clientY: 10 });
    expect(rotateHandle("veh_a")).toBeInTheDocument();

    const withoutA: Scene = { ...scene, vehicles: scene.vehicles.filter((v) => v.id !== "veh_a"),
      movements: scene.movements.filter((m) => m.vehicle_id !== "veh_a") };
    rerender(<VehiclePlacementPanel scene={withoutA} onChange={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /^Rotate/i })).not.toBeInTheDocument();
  });

  it.each(ROAD_LAYOUTS.map((l) => [l] as const))("renders a road template for layout %s without crashing", (layout) => {
    const { container } = render(
      <VehiclePlacementPanel scene={setRoad(emptyScene(), { layout })} onChange={vi.fn()} />,
    );
    expect(container.querySelector("svg")).toBeInTheDocument();
  });
});
