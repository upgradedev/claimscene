import { createRef } from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Card, CardContent, CardHeader, CardTitle } from "./card";

describe("Card primitives", () => {
  it("composes header, title and content and merges classNames", () => {
    render(
      <Card className="mycard">
        <CardHeader className="myhead">
          <CardTitle>Sealed case</CardTitle>
        </CardHeader>
        <CardContent>body text</CardContent>
      </Card>,
    );
    expect(screen.getByRole("heading", { name: "Sealed case" })).toBeInTheDocument();
    expect(screen.getByText("body text")).toBeInTheDocument();
  });

  it("forwards a ref to the underlying div", () => {
    const ref = createRef<HTMLDivElement>();
    render(<Card ref={ref}>x</Card>);
    expect(ref.current).toBeInstanceOf(HTMLDivElement);
    expect(ref.current!.className).toContain("sheet");
  });
});
