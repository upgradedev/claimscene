import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render as rtlRender, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { ReviewStep } from "./ReviewStep";
import { claimsceneApi } from "@/lib/api";
import { useCaseStore } from "@/store/useCaseStore";
import { addDamage, addVehicle, emptyScene, setImpact, type Scene } from "@/lib/scene";

/** The step now asks the server how long a render takes here (the measured
 *  estimate beside the render CTA), so it needs a query client like every
 *  other data-touching component's test. */
function render(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

function seededScene(): Scene {
  let scene = addVehicle(emptyScene()); // veh_a + veh_b
  scene = setImpact(scene, "veh_a", 6);
  scene = addDamage(scene, "veh_a");
  return scene;
}

describe("ReviewStep (human-in-the-loop centrepiece)", () => {
  beforeEach(() => {
    vi.spyOn(claimsceneApi, "previewSchematic").mockResolvedValue({
      svg: "<svg>x</svg>",
      warnings: [],
      contact_point: null,
      vehicle_count: 2,
      road: { layout: "x_intersection", lanes_per_direction: 1, signal: "none" },
    });
    useCaseStore.setState({
      scene: seededScene(),
      extraction: { extractor: "committed-ground-truth", source: "committed_ground_truth", mode: "offline" },
      step: "review",
    });
  });

  it("renders the trust statement, constrained controls and the render CTA", () => {
    render(<ReviewStep />);
    expect(screen.getByText(/AI proposes, you confirm/i)).toBeInTheDocument();
    expect(screen.getByText(/source: sample ground-truth/i)).toBeInTheDocument();
    // Constrained dropdowns (road + two vehicles) — no free-text coordinate fields.
    expect(screen.getAllByRole("combobox").length).toBeGreaterThanOrEqual(9);
    // 12-position clock pickers → radios (impact for veh_a + one damage zone).
    expect(screen.getAllByRole("radio").length).toBeGreaterThanOrEqual(24);
    expect(screen.getByRole("button", { name: /Confirm & render this case/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add vehicle/i })).toBeInTheDocument();
  });

  it("edits flow through the constrained controls into the shared scene", () => {
    render(<ReviewStep />);
    const layout = screen.getByLabelText("layout") as HTMLSelectElement;
    fireEvent.change(layout, { target: { value: "roundabout" } });
    expect(useCaseStore.getState().scene?.road.layout).toBe("roundabout");
  });

  it("picking a clock position records the impact in the scene", () => {
    render(<ReviewStep />);
    // veh_b starts un-struck; click 3 o'clock in ITS impact clock picker.
    const group = screen.getByRole("radiogroup", { name: /Impact clock position for veh_b/i });
    fireEvent.click(within(group).getByRole("radio", { name: /3 o'clock/i }));
    const impacts = useCaseStore.getState().scene?.impacts ?? [];
    expect(impacts.some((i) => i.vehicle_id === "veh_b" && i.clock_position === 3)).toBe(true);
  });

  it("the direct-manipulation placement panel is wired to the same scene as the dropdowns", () => {
    render(<ReviewStep />);
    // The panel renders alongside the dropdown-based editors, reading the same
    // shared scene: veh_a's approach dropdown starts at "N" (emptyScene's
    // default), and the placement panel's marker agrees.
    const approachSelect = screen.getAllByLabelText("approach")[0] as HTMLSelectElement;
    expect(approachSelect.value).toBe("N");
    expect(screen.getByRole("button", { name: /^veh_a:.*north approach/i })).toBeInTheDocument();

    // Changing the dropdown updates the SAME store the panel reads from — the
    // two controls are two views onto one piece of state, not two copies.
    fireEvent.change(approachSelect, { target: { value: "E" } });
    expect(useCaseStore.getState().scene?.movements.find((m) => m.vehicle_id === "veh_a")?.approach).toBe(
      "E",
    );
    expect(screen.getByRole("button", { name: /^veh_a:.*east approach/i })).toBeInTheDocument();
  });

  it("quotes the measured wait, and only claims a range when there is one", async () => {
    // Measured live on the deployed app the day this shipped: one sample, so
    // the median and the slow end are the same number. "about 2 minutes, and
    // up to about 2 minutes" is not a range.
    vi.spyOn(claimsceneApi, "renderEstimate")
      .mockResolvedValue({ samples: 1, typical_seconds: 92, slow_seconds: 92 });
    render(<ReviewStep />);
    await screen.findByText(/Rendering takes about 2 minutes here\./i);
    expect(screen.getByText(/last 1 case rendered here/i)).toBeInTheDocument();
    expect(screen.queryByText(/at the slow end/i)).not.toBeInTheDocument();
  });

  it("shows the slow end once the recent cases actually differ", async () => {
    vi.spyOn(claimsceneApi, "renderEstimate")
      .mockResolvedValue({ samples: 8, typical_seconds: 240, slow_seconds: 420 });
    render(<ReviewStep />);
    await screen.findByText(/up to about 7 minutes at the slow end/i);
    expect(screen.getByText(/last 8 cases/i)).toBeInTheDocument();
  });

  it("says nothing has been timed rather than quoting a made-up figure", async () => {
    vi.spyOn(claimsceneApi, "renderEstimate")
      .mockResolvedValue({ samples: 0, typical_seconds: null, slow_seconds: null });
    render(<ReviewStep />);
    await screen.findByText(/No case has been timed here yet/i);
  });
});
