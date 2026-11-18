import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NetworkingPanel } from "./NetworkingPanel";

afterEach(cleanup);

const configuration = {
  ingress: { enabled: false, gateway: "test", hostname: "app.example.test", path: "/", path_type: "PathPrefix",
    exposure: "internal", certificate: "test", https_redirect: true, timeout_seconds: 30,
    max_request_bytes: 1048576, streaming: false },
  egress: { enabled: false, hosts: [], direct: [] },
};
const releases = [{ key: "release", document: { environment: { namespace: "app-test", release: "app" } } }];
const capabilities = {
  enabled: true, ingress_available: true, egress_available: true,
  releases: { "app-test/app": { gateways: ["test"], outbound_hosts: [{ hostname: "api.example.test", port: 443, protocol: "HTTPS" }], direct_destinations: [] } },
  gateways: { test: { mode: "managed", exposure: "internal", hostname_suffixes: ["example.test"], certificates: ["test"] } },
};

function fixture(enabled = true) {
  const api = vi.fn(async (path: string, options?: RequestInit) => {
    if (path.includes("capabilities")) return { ...capabilities, enabled };
    if (path.endsWith("preview")) {
      const input = JSON.parse(options!.body as string);
      return { ...input, expected_version: 7, preview_id: "a".repeat(64), active: false,
        previous: configuration, url: "https://app.example.test/", external_reachability: "unverified", workload_restart: true };
    }
    if (options?.method === "POST") return { operation_id: "network-operation" };
    return { version: 7, network_version: 0, active: false, desired: configuration, observed: null };
  });
  render(<NetworkingPanel api={api} project="fixture" releases={releases} />);
  fireEvent.click(screen.getByText("Networking: ingress and egress"));
  fireEvent.change(screen.getByLabelText("Network release"), { target: { value: "release" } });
  fireEvent.click(screen.getByText("Inspect networking"));
  return api;
}

describe("networking controls", () => {
  it("shows actionable diagnostics as retained evidence without mutating a release", async () => {
    const api = vi.fn(async () => ({
      observation_scope: "Retained observations; request a health probe for a fresh check.",
      execution_available: true, recovery_mode: true,
      items: [{ release_key: "release", namespace: "app-test", environment: "test", status: "attention",
        network_version: 4, external_reachability: "unverified",
        actions: ["Installation policy changed; preview and apply the configuration again."] }],
    }));
    render(<NetworkingPanel api={api} project="fixture" releases={releases} />);
    fireEvent.click(screen.getByText("Networking: ingress and egress"));
    fireEvent.click(screen.getByText("Inspect networking diagnostics"));
    await screen.findByText(/Retained observations/);
    expect(screen.getByText(/Installation policy changed/)).toBeTruthy();
    expect(screen.getByText(/Recovery mode prevents new delivery/)).toBeTruthy();
    expect(api.mock.calls).toEqual([["/pipeline/networking/diagnostics?project_id=fixture"]]);
  });

  it("requires a current preview and sends the version and idempotency key", async () => {
    const api = fixture();
    await screen.findByText(/Release version 7/);
    fireEvent.click(screen.getByLabelText("Enable ingress"));
    fireEvent.click(screen.getByLabelText("Enable managed outbound access"));
    fireEvent.click(screen.getByLabelText("HTTPS api.example.test:443"));
    expect(screen.queryByText("Apply networking changes")).toBeNull();
    fireEvent.click(screen.getByText("Preview networking changes"));
    await screen.findByText("The release will remain inactive.");
    fireEvent.click(screen.getByText("Apply networking changes"));
    await screen.findByText("Networking operation network-operation");
    const apply = api.mock.calls.find(([path, options]) => path.endsWith("/networking") && options?.method === "POST");
    const body = JSON.parse(apply![1]!.body as string);
    expect(body.expected_version).toBe(7);
    expect(body.preview_id).toBe("a".repeat(64));
    expect(body.idempotency_key).toBeTruthy();
    expect(body.configuration.egress.hosts).toEqual(capabilities.releases["app-test/app"].outbound_hosts);
  });

  it("invalidates a preview after editing and disables buffering for streaming", async () => {
    fixture();
    await screen.findByText(/Release version 7/);
    fireEvent.click(screen.getByText("Preview networking changes"));
    await screen.findByText("Apply networking changes");
    fireEvent.click(screen.getByLabelText("Streaming / WebSockets"));
    expect(screen.queryByText("Apply networking changes")).toBeNull();
    expect(screen.queryByLabelText("Maximum request bytes")).toBeNull();
  });

  it("shows missing installation capability without offering exposure", async () => {
    fixture(false);
    await screen.findByText(/Networking is not installed/);
    await waitFor(() => expect(screen.getByLabelText("Enable ingress")).toBeDisabled());
  });
});
