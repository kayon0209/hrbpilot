import "@testing-library/jest-dom/vitest";

// jsdom provides its own AbortController/AbortSignal, while `Request` stays
// Node's undici implementation (jsdom has no fetch). undici validates
// `init.signal` against the native AbortSignal class captured at Node startup,
// so every signal constructible inside the jsdom environment is rejected —
// a clash that cannot happen in a real browser with a single implementation.
// react-router v7 builds `new Request(url, { signal })` on every navigation,
// so wrap `Request` for tests: detach the jsdom signal for construction and
// re-attach it on the instance (fetch is mocked here, so undici's internal
// abort wiring is never needed).
const NativeRequest = globalThis.Request;

class TestRequest extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    if (init?.signal) {
      const { signal, ...rest } = init;
      super(input, rest);
      Object.defineProperty(this, "signal", { value: signal, configurable: true });
    } else {
      super(input, init);
    }
  }
}

globalThis.Request = TestRequest;
