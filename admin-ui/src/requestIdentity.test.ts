import { afterEach, expect, it, vi } from "vitest";
import { requestIdentity } from "./requestIdentity";

afterEach(() => vi.unstubAllGlobals());

it("creates distinct UUIDs on HTTP origins without randomUUID", () => {
  const random = crypto.getRandomValues.bind(crypto);
  vi.stubGlobal("crypto", { getRandomValues: random });
  const first = requestIdentity();
  expect(first).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  expect(requestIdentity()).not.toBe(first);
});
