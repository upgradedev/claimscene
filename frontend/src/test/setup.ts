import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// jsdom doesn't implement these — stub so components under test don't throw.
if (!("createObjectURL" in URL)) {
  // @ts-expect-error test shim
  URL.createObjectURL = () => "blob:mock";
}
if (!("revokeObjectURL" in URL)) {
  // @ts-expect-error test shim
  URL.revokeObjectURL = () => {};
}

// jsdom has no layout, so it does not implement scrollIntoView at all. The
// guided tour calls it on the element each step points at.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

// framer-motion's whileInView uses IntersectionObserver, absent in jsdom.
if (!("IntersectionObserver" in globalThis)) {
  class MockIO {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() {
      return [];
    }
  }
  // @ts-expect-error test shim
  globalThis.IntersectionObserver = MockIO;
}

afterEach(() => cleanup());
