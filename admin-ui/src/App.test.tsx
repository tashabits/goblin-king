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
      validation_status: {
        state: "validated",
        message: "Latest validation proof passed for this configured image.",
      },
    },
    {
      kind: "example.long-hello",
      display_name: "Example Long Hello",
      worker_mapped: true,
      worker_image: "goblin-king-example-long-hello:local",
      validation_status: {
        state: "unknown",
        message: "No validation proof has been recorded. Validate first, then schedule.",
      },
    },
    {
      kind: "example.failed-validation",
      display_name: "Example Failed Validation",
      worker_mapped: true,
      worker_image: "goblin-king-example-failed-validation:local",
      validation_status: {
        state: "failed",
        message: "worker did not write result.json",
      },
    },
    {
      kind: "example.stale-validation",
      display_name: "Example Stale Validation",
      worker_mapped: true,
      worker_image: "goblin-king-example-stale-validation:local",
      validation_status: {
        state: "stale",
        message: "Latest validation proof was recorded for a different configured image.",
      },
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
  eventStream: {
    stream: "goblin-king:events:stream",
    ok: true,
    length: 3,
    last_generated_id: "1-0",
    groups: [],
    pending: 0,
    error: null,
  },
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
  artifactStorage: {
    root: ".goblin-king/artifacts",
    exists: true,
    writable: true,
    file_count: 1,
    total_bytes: 12,
    metadata_count: 1,
  },
  artifactCleanup: {
    dry_run: true,
    deleted: false,
    root: ".goblin-king/artifacts",
    files_selected: 1,
    bytes_selected: 12,
    files: ["proof.txt"],
  },
  discoveryStatus: {
    active_goblin_count: 2,
    worker_mapped_count: 2,
    worker_unmapped: [],
    discovery_version: 1,
    last_successful_reload_at: "2026-06-09T00:00:00Z",
    last_failed_reload_at: null,
    last_error: null,
  },
  discoverySources: {
    project_settings: "goblin-king-project.json",
    registry_files: ["examples/goblins.json"],
    entry_points_enabled: true,
    worker_image_map: "goblin-images.json",
    goblin_kinds: ["example.hello", "example.long-hello"],
    worker_mapped_kinds: ["example.hello", "example.long-hello"],
    worker_unmapped_kinds: [],
    rejected_definitions: [],
    duplicate_kind_errors: [],
  },
  promotions: [
    {
      id: "promo-1",
      kind: "example.hello",
      source_image: "goblin-king-example-hello:local",
      target_image: "registry.example/goblin-king-example-hello:promoted",
      status: "planned",
      digest: null,
      created_at: "2026-06-09T00:00:00Z",
      detail: { dry_run: true },
    },
  ],
  deployments: [
    {
      id: "deploy-1",
      name: "goblin-king",
      action: "helm-template",
      status: "planned",
      command: ["helm", "template", "goblin-king", "charts/goblin-king"],
      output: null,
      created_at: "2026-06-09T00:00:00Z",
      detail: { execute: false },
    },
  ],
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
    if (url.includes("/admin/runtime/jobs/job-1/kill")) return jsonResponse({ killed: ["docker:abc"], errors: [] });
    if (url.includes("/jobs/fanout")) return jsonResponse({ status: "queued" });
    if (url.includes("/retry")) return jsonResponse({ id: "job-retry", status: "queued" });
    if (url.includes("/runs?")) return jsonResponse(fixtures.runs);
    if (url.includes("/events/stream/status")) return jsonResponse(fixtures.eventStream);
    if (url.includes("/events")) return jsonResponse(fixtures.events);
    if (url.includes("/heartbeats")) return jsonResponse(fixtures.heartbeats);
    if (url.includes("/services/long-running") && init?.method === "POST" && url.endsWith("/probe")) {
      return jsonResponse({ response: { json: { message: "Hello World from long running service" } } });
    }
    if (url.includes("/services/long-running") && init?.method === "POST" && url.endsWith("/stop")) {
      return jsonResponse({ ...fixtures.services[0], status: "stopped" });
    }
    if (url.includes("/admin/runtime/services/svc-1/kill")) {
      return jsonResponse({ killed: ["registered-service:svc-1"], errors: [] });
    }
    if (url.endsWith("/services/long-running") && init?.method === "POST") {
      return jsonResponse(fixtures.services[0]);
    }
    if (url.includes("/services/long-running")) return jsonResponse(fixtures.services);
    if (url.includes("/schedules") && init?.method === "POST") return jsonResponse({ id: "schedule-1" });
    if (url.includes("/schedules")) return jsonResponse(fixtures.schedules);
    if (url.includes("/fanouts")) return jsonResponse(fixtures.fanouts);
    if (url.includes("/audit-logs")) return jsonResponse(fixtures.audits);
    if (url.includes("/admin/artifacts/storage")) return jsonResponse(fixtures.artifactStorage);
    if (url.includes("/admin/artifacts/cleanup")) {
      return jsonResponse({
        ...fixtures.artifactCleanup,
        dry_run: init?.body ? JSON.parse(String(init.body)).dry_run : true,
        deleted: init?.body ? !JSON.parse(String(init.body)).dry_run : false,
      });
    }
    if (url.includes("/admin/discovery/reload")) {
      return jsonResponse({ ...fixtures.discoveryStatus, discovery_version: 2 });
    }
    if (url.includes("/admin/discovery/status")) return jsonResponse(fixtures.discoveryStatus);
    if (url.includes("/admin/discovery/sources")) return jsonResponse(fixtures.discoverySources);
    if (url.includes("/admin/images/promotions") && url.endsWith("/mark")) {
      return jsonResponse({ ...fixtures.promotions[0], status: "promoted" });
    }
    if (url.includes("/admin/images/promotions") && init?.method === "POST") {
      return jsonResponse(fixtures.promotions[0]);
    }
    if (url.includes("/admin/images/promotions")) return jsonResponse(fixtures.promotions);
    if (url.includes("/admin/deployments/helm-template")) return jsonResponse(fixtures.deployments[0]);
    if (url.includes("/admin/deployments/reload-discovery")) return jsonResponse({
      ...fixtures.deployments[0],
      action: "discovery-reload",
      status: "applied",
    });
    if (url.includes("/admin/deployments")) return jsonResponse(fixtures.deployments);
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
    expect(screen.getAllByText("Registered Container Goblins").length).toBeGreaterThan(0);
    expect(screen.getAllByText("OCI worker image mapped").length).toBeGreaterThan(0);
    expect(screen.getAllByText("validated").length).toBeGreaterThan(0);
    expect(screen.getAllByText("unknown").length).toBeGreaterThan(0);
    expect(screen.getAllByText("failed").length).toBeGreaterThan(0);
    expect(screen.getAllByText("stale").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Task Board").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Schedules").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Runs & Results").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Artifact Volume").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Fanout & Retry").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Long Services").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Durable Events").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Redis Stream Delivery").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Discovery").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Image Promotion & Deploy").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Admin & Auth").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Cleanup").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /kill \/ cancel/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /hard kill runtime/i }).length).toBeGreaterThan(0);
  });

  it("calls job submit, cancel, service probe, and stop paths", async () => {
    const fetchMock = mockFetch();
    localStorage.setItem("goblinKingAdminToken", "test-token");
    render(<App />);

    await screen.findAllByText("example.hello");
    await userEvent.click(screen.getAllByRole("button", { name: /^submit job/i })[0]);
    await userEvent.click(screen.getAllByRole("button", { name: /kill \/ cancel/i })[0]);
    await userEvent.click(screen.getAllByRole("button", { name: /hard kill runtime/i })[0]);
    await userEvent.click(screen.getAllByRole("button", { name: /^probe/i })[0]);
    await userEvent.click(screen.getAllByRole("button", { name: /stop service/i })[0]);
    await userEvent.click(screen.getAllByRole("button", { name: /hard stop runtime/i })[0]);

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(urls).toContain("/admin-api/jobs");
      expect(urls).toContain("/admin-api/jobs/job-1/cancel");
      expect(urls).toContain("/admin-api/admin/runtime/jobs/job-1/kill");
      expect(urls).toContain("/admin-api/services/long-running/svc-1/probe");
      expect(urls).toContain("/admin-api/services/long-running/svc-1/stop");
      expect(urls).toContain("/admin-api/admin/runtime/services/svc-1/kill");
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

  it("reloads deploy-time discovery from the admin panel", async () => {
    const fetchMock = mockFetch();
    localStorage.setItem("goblinKingAdminToken", "test-token");
    render(<App />);

    await screen.findAllByText("Discovery");
    await userEvent.click(screen.getByRole("button", { name: /reload discovery/i }));

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(urls).toContain("/admin-api/admin/discovery/reload");
    });
    expect(screen.getAllByText(/example.long-hello/i).length).toBeGreaterThan(0);
  });

  it("records image promotion and deployment proof paths", async () => {
    const fetchMock = mockFetch();
    localStorage.setItem("goblinKingAdminToken", "test-token");
    render(<App />);

    await screen.findAllByText("Image Promotion & Deploy");
    await userEvent.click(screen.getByRole("button", { name: /plan image promotion/i }));
    await userEvent.click(screen.getByRole("button", { name: /record helm render/i }));
    await userEvent.click(screen.getByRole("button", { name: /reload after deploy/i }));
    await userEvent.click(screen.getAllByRole("button", { name: /mark promoted/i })[0]);

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(urls).toContain("/admin-api/admin/images/promotions");
      expect(urls).toContain("/admin-api/admin/deployments/helm-template");
      expect(urls).toContain("/admin-api/admin/deployments/reload-discovery");
      expect(urls).toContain("/admin-api/admin/images/promotions/promo-1/mark");
    });
  });
});
