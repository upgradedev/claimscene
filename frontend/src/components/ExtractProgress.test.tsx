import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { ExtractProgress, formatElapsed } from "./ExtractProgress";

describe("formatElapsed", () => {
  it("shows seconds under a minute and M:SS at/after a minute", () => {
    expect(formatElapsed(0)).toBe("0s");
    expect(formatElapsed(9)).toBe("9s");
    expect(formatElapsed(59)).toBe("59s");
    expect(formatElapsed(60)).toBe("1:00");
    expect(formatElapsed(75)).toBe("1:15");
    expect(formatElapsed(605)).toBe("10:05");
  });
});

describe("ExtractProgress", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("announces progress with a live status + indeterminate progressbar", () => {
    render(<ExtractProgress isSample={false} />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("uses upload copy (VLM ladder) when not a sample", () => {
    render(<ExtractProgress isSample={false} />);
    expect(screen.getByRole("status")).toHaveTextContent(/VLM extraction ladder/i);
    expect(screen.getByText(/Extracting the scene/i)).toBeInTheDocument();
  });

  it("uses committed-ground-truth copy for a sample", () => {
    render(<ExtractProgress isSample />);
    expect(screen.getByRole("status")).toHaveTextContent(/committed ground-truth scene/i);
    expect(screen.getByText(/usually instant/i)).toBeInTheDocument();
  });

  it("counts the elapsed time up while in flight", () => {
    vi.useFakeTimers();
    render(<ExtractProgress isSample={false} />);
    expect(screen.getByText("0s")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByText("3s")).toBeInTheDocument();
  });
});
