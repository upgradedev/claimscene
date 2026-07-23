import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { SchematicPreview } from "./SchematicPreview";
import { claimsceneApi, type PreviewResponse } from "@/lib/api";
import { emptyScene, setRoad } from "@/lib/scene";

function previewOk(svg: string, warnings: string[] = []): PreviewResponse {
  return {
    svg,
    warnings,
    contact_point: null,
    vehicle_count: 1,
    road: { layout: "x_intersection", lanes_per_direction: 1, signal: "none" },
  };
}

describe("SchematicPreview wiring (review-adjust live update)", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("posts the scene and renders the returned schematic + warnings", async () => {
    const spy = vi
      .spyOn(claimsceneApi, "previewSchematic")
      .mockResolvedValue(previewOk("<svg><text>A</text></svg>", ["a north-arm warning"]));

    const scene = emptyScene();
    render(<SchematicPreview scene={scene} debounceMs={0} />);

    await waitFor(() => expect(spy).toHaveBeenCalledWith(scene));
    const img = await screen.findByRole("img");
    expect(img.getAttribute("src")).toMatch(/^data:image\/svg\+xml;base64,/);
    expect(screen.getByText("a north-arm warning")).toBeInTheDocument();
  });

  it("re-requests a preview when the scene is edited", async () => {
    const spy = vi
      .spyOn(claimsceneApi, "previewSchematic")
      .mockResolvedValue(previewOk("<svg>x</svg>"));

    const { rerender } = render(<SchematicPreview scene={emptyScene()} debounceMs={0} />);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    const edited = setRoad(emptyScene(), { signal: "stop_sign" });
    rerender(<SchematicPreview scene={edited} debounceMs={0} />);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(spy).toHaveBeenLastCalledWith(edited);
  });

  it("surfaces an honest error when the preview endpoint fails", async () => {
    vi.spyOn(claimsceneApi, "previewSchematic").mockRejectedValue(new Error("boom"));
    render(<SchematicPreview scene={emptyScene()} debounceMs={0} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/boom/);
  });

  it("ghosts the AI proposal as a dashed overlay once the scene is edited", async () => {
    const spy = vi
      .spyOn(claimsceneApi, "previewSchematic")
      .mockResolvedValue(previewOk("<svg><rect/></svg>"));
    const proposed = emptyScene();
    const edited = setRoad(emptyScene(), { signal: "stop_sign" });
    const { container } = render(
      <SchematicPreview scene={edited} proposedScene={proposed} debounceMs={0} />,
    );
    // Both the confirmed scene AND the frozen proposal are previewed.
    await waitFor(() => expect(spy).toHaveBeenCalledWith(edited));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(proposed));
    // The correction legend + dashed/solid caption make the edit visible.
    expect(await screen.findByText(/dashed ghost is what the AI proposed/i)).toBeInTheDocument();
    expect(screen.getByText(/dashed = AI-proposed/i)).toBeInTheDocument();
    // Two <img> layers: the solid confirmed schematic + the ghosted proposal.
    await waitFor(() => expect(container.querySelectorAll("img")).toHaveLength(2));
  });

  it("does not ghost when the confirmed scene equals the proposal (no edits)", async () => {
    vi.spyOn(claimsceneApi, "previewSchematic").mockResolvedValue(previewOk("<svg><rect/></svg>"));
    const { container } = render(
      <SchematicPreview scene={emptyScene()} proposedScene={emptyScene()} debounceMs={0} />,
    );
    await screen.findByRole("img"); // the confirmed schematic rendered
    expect(screen.queryByText(/dashed ghost/i)).not.toBeInTheDocument();
    expect(container.querySelectorAll("img")).toHaveLength(1);
  });
});
