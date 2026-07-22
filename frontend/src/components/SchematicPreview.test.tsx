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
});
