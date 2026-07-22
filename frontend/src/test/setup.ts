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
