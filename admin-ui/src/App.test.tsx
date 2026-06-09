import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const fixtures = {
  goblins: [
    {
      kind: "example.hello",
      display_name: "Example Hello",
      worker_mapped: true,
      worker_image: "goblin-king-example-hello:local",
    },
    {
      kind: "example.long-hello",
      display_name: "Example Long Hello",
      worker_mapped: true,
      worker_image: "goblin-king-example-long-hello:local",
    },
  ],
  jobs: {
    items: [
      {
        id: "job-1",
        kind: "example.hello",
        status: "queued",
        input: {},
        created_at: "2026-06-09T00:00:00Z",
      },
    ],
    meta: { limit: 100, offset: 0, count: 1 },
  },
  runs: { items: [], meta: { limit: 100, offset: 0, count: 0 } },
  events: { items: [], meta: { limit: 50, offset: 0, count: 0 } },
  heartbeats: [],
  services: [
    {
      id: "svc-1",
      kind: "example.long-hello",
      status: "running",
      base_url: "http://long-hello:8080",
      last_probe_json: {},
    },
  ],
  schedules: [],
  fanouts: [],
  audits: { items: [], meta: { limit: 20, offset: 0, count: 0 } },
};

function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

function mockFetch() {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/admin/config.json")) {
      return jsonResponse({ deploymentScope: "test", longHelloUrl: "http://test-long-hello" });
    }
    if (url.includes("/goblins")) return jsonResponse(fixtures.goblins);
    if (url.includes("/jobs?")) return jsonResponse(fixtures.jobs);
    if (url.endsWith("/jobs")) {
      return jsonResponse({ id: "job-2", kind: "example.hello", status: "queued" });
    }
    if (url.includes("/jobs/job-1/cancel")) return jsonResponse({ ...fixtures.jobs.items[0], status: "cancelled" });
    if (url.includes("/jobs/fanout")) return jsonResponse({ status: "queued" });
    if (url.includes("/retry")) return jsonResponse({ id: "job-retry", status: "queued" });
    if (url.includes("/runs?")) return jsonResponse(fixtures.runs);
    if (url.includes("/events")) return jsonResponse(fixtures.events);
    if (url.includes("/heartbeats")) return jsonResponse(fixtures.heartbeats);
    if (url.includes("/services/long-running") && init?.method === "POST" && url.endsWith("/probe")) {
      return jsonResponse({ response: { json: { message: "Hello World from long running service" } } });
    }
    if (url.includes("/services/long-running") && init?.method === "POST" && url.endsWith("/stop")) {
      return jsonResponse({ ...fixtures.services[0], status: "stopped" });
    }
    if (url.endsWith("/services/long-running") && init?.method === "POST") {
      return jsonResponse(fixtures.services[0]);
    }
    if (url.includes("/services/long-running")) return jsonResponse(fixtures.services);
    if (url.includes("/schedules") && init?.method === "POST") return jsonResponse({ id: "schedule-1" });
    if (url.includes("/schedules")) return jsonResponse(fixtures.schedules);
    if (url.includes("/fanouts")) return jsonResponse(fixtures.fanouts);
    if (url.includes("/audit-logs")) return jsonResponse(fixtures.audits);
    if (url.includes("/admin/cleanup/runtime")) {
      return jsonResponse({
        dry_run: init?.body ? JSON.parse(String(init.body)).dry_run : true,
        deleted: init?.body ? !JSON.parse(String(init.body)).dry_run : false,
        counts: { jobs: 1, runs: 1, long_services: 1, events: 2 },
      });
    }
    if (url.includes("/admin/users")) return jsonResponse({ id: "user-1" });
    if (url.includes("/admin/projects")) return jsonResponse({ id: "project-1" });
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

class MockWebSocket {
  onmessage: ((message: MessageEvent<string>) => void) | null = null;
  constructor(public url: string) {}
  close() {}
}

describe("App", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  it("stores token login and loads goblins", async () => {
    const fetchMock = mockFetch();
    render(<App />);

    await userEvent.clear(screen.getByLabelText(/api token/i));
    await userEvent.type(screen.getByLabelText(/api token/i), "test-token");
    await userEvent.click(screen.getByRole("button", { name: /enter the lab/i }));

    expect(localStorage.getItem("goblinKingAdminToken")).toBe("test-token");
    expect((await screen.findAllByText("example.hello")).length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/admin-api/goblins"),
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer test-token" }),
      }),
    );
  });

  it("represents major control-plane paths with tester controls", async () => {
    mockFetch();
    localStorage.setItem("goblinKingAdminToken", "test-token");
    render(<App />);

    expect((await screen.findAllByText("Goblin Lab")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Task Board").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Schedules").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Runs & Results").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Fanout & Retry").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Long Services").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Durable Events").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Admin & Auth").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Cleanup").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /kill \/ cancel/i }).length).toBeGreaterThan(0);
  });

  it("calls job submit, cancel, service probe, and stop paths", async () => {
    const fetchMock = mockFetch();
    localStorage.setItem("goblinKingAdminToken", "test-token");
    render(<App />);

    await screen.findAllByText("example.hello");
    await userEvent.click(screen.getAllByRole("button", { name: /^submit job/i })[0]);
    await userEvent.click(screen.getAllByRole("button", { name: /kill \/ cancel/i })[0]);
    await userEvent.click(screen.getAllByRole("button", { name: /^probe/i })[0]);
    await userEvent.click(screen.getAllByRole("button", { name: /stop service/i })[0]);

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(urls).toContain("/admin-api/jobs");
      expect(urls).toContain("/admin-api/jobs/job-1/cancel");
      expect(urls).toContain("/admin-api/services/long-running/svc-1/probe");
      expect(urls).toContain("/admin-api/services/long-running/svc-1/stop");
    });
  });

  it("loads the deployment long-service URL from runtime config", async () => {
    mockFetch();
    localStorage.setItem("goblinKingAdminToken", "test-token");
    render(<App />);

    expect(await screen.findByDisplayValue("http://test-long-hello")).toBeInTheDocument();
    expect(screen.getByText(/Deployment default: test uses/i)).toBeInTheDocument();
  });

  it("previews and removes historical runtime rows", async () => {
    const fetchMock = mockFetch();
    localStorage.setItem("goblinKingAdminToken", "test-token");
    render(<App />);

    await screen.findAllByText("Cleanup");
    await userEvent.click(screen.getByRole("button", { name: /preview old rows/i }));
    await waitFor(() => {
      expect(screen.getAllByText(/\"jobs\": 1/i).length).toBeGreaterThan(0);
    });
    await userEvent.click(screen.getByRole("button", { name: /remove previewed rows/i }));

    await waitFor(() => {
      const cleanupCalls = fetchMock.mock.calls.filter((call) =>
        String(call[0]).includes("/admin-api/admin/cleanup/runtime"),
      );
      expect(cleanupCalls).toHaveLength(2);
      expect(cleanupCalls[0][1]).toEqual(
        expect.objectContaining({ body: expect.stringContaining("\"dry_run\":true") }),
      );
      expect(cleanupCalls[1][1]).toEqual(
        expect.objectContaining({ body: expect.stringContaining("\"dry_run\":false") }),
      );
    });
  });
});
