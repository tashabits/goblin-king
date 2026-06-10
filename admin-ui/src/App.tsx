import {
  Activity,
  Ban,
  Boxes,
  ClipboardList,
  Crown,
  FlaskConical,
  HeartPulse,
  KeyRound,
  Play,
  Radio,
  RefreshCw,
  Rocket,
  ScrollText,
  Shield,
  Sparkles,
  Square,
  Wand2,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  compactTrafficPayload,
  latestFirst,
  quoteFor,
  readJson,
  usefulServicesFirst,
} from "./adminData";
import { Stat, Table } from "./components";
import type {
  AdminConfig,
  ArtifactCleanupResponse,
  ArtifactStorageStatus,
  AuditLog,
  CleanupResponse,
  DeploymentRecord,
  DiscoverySources,
  DiscoveryStatus,
  EventRecord,
  EventStreamStatus,
  FanoutDetail,
  Goblin,
  Heartbeat,
  ImagePromotion,
  Job,
  LongService,
  Run,
  Schedule,
  TrafficEntry,
} from "./types";

const API_BASE = "/admin-api";
const WS_BASE = `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/admin-ws/runs`;
const TOKEN_KEY = "goblinKingAdminToken";
const DEFAULT_LONG_HELLO_URL = "http://long-hello:8080";

export function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || "");
  const [draftToken, setDraftToken] = useState(token || "local-dev-token");
  const [goblins, setGoblins] = useState<Goblin[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [eventStream, setEventStream] = useState<EventStreamStatus | null>(null);
  const [heartbeats, setHeartbeats] = useState<Heartbeat[]>([]);
  const [services, setServices] = useState<LongService[]>([]);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [fanouts, setFanouts] = useState<FanoutDetail[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [selectedKind, setSelectedKind] = useState("example.hello");
  const [jobInput, setJobInput] = useState('{"name":"World"}');
  const [fanoutInput, setFanoutInput] = useState(
    '{"description":"admin lab fanout","items":[{"kind":"example.hello","input":{"name":"One"}},{"kind":"example.progress","input":{"steps":3}}]}',
  );
  const [scheduleCron, setScheduleCron] = useState("* * * * *");
  const [adminConfig, setAdminConfig] = useState<AdminConfig>({
    deploymentScope: "docker",
    longHelloUrl: DEFAULT_LONG_HELLO_URL,
  });
  const [serviceUrl, setServiceUrl] = useState(DEFAULT_LONG_HELLO_URL);
  const [retryJobId, setRetryJobId] = useState("");
  const [artifactRunId, setArtifactRunId] = useState("");
  const [artifactStorage, setArtifactStorage] = useState<ArtifactStorageStatus | null>(null);
  const [artifactCleanup, setArtifactCleanup] = useState<ArtifactCleanupResponse | null>(null);
  const [cleanupPreview, setCleanupPreview] = useState<CleanupResponse | null>(null);
  const [discoveryStatus, setDiscoveryStatus] = useState<DiscoveryStatus | null>(null);
  const [discoverySources, setDiscoverySources] = useState<DiscoverySources | null>(null);
  const [promotions, setPromotions] = useState<ImagePromotion[]>([]);
  const [deployments, setDeployments] = useState<DeploymentRecord[]>([]);
  const [promotionTargetImage, setPromotionTargetImage] = useState("goblin-king-example:promoted");
  const [includeUnprobedServices, setIncludeUnprobedServices] = useState(true);
  const [traffic, setTraffic] = useState<TrafficEntry[]>([]);
  const [error, setError] = useState("");
  const [liveEvents, setLiveEvents] = useState<EventRecord[]>([]);

  const headers = useMemo(
    () => ({
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    }),
    [token],
  );

  async function api(path: string, options: RequestInit = {}, label = path) {
    const request = {
      ...options,
      headers: { ...headers, ...(options.headers || {}) },
    };
    const response = await fetch(`${API_BASE}${path}`, request);
    const payload = await readJson(response);
    setTraffic((items) =>
      [
        {
          label,
          request: { path, method: options.method || "GET", body: options.body },
          response: { status: response.status, payload: compactTrafficPayload(payload) },
        },
        ...items,
      ].slice(0, 12),
    );
    if (!response.ok) {
      throw new Error(payload?.detail || `${label} failed with ${response.status}`);
    }
    return payload;
  }

  useEffect(() => {
    async function loadConfig() {
      try {
        const response = await fetch("/admin/config.json", { cache: "no-store" });
        if (!response.ok) return;
        const payload = (await response.json()) as Partial<AdminConfig>;
        const nextUrl = payload.longHelloUrl || DEFAULT_LONG_HELLO_URL;
        setAdminConfig({
          deploymentScope: payload.deploymentScope || "docker",
          longHelloUrl: nextUrl,
        });
        setServiceUrl((current) => (current === DEFAULT_LONG_HELLO_URL || !current ? nextUrl : current));
      } catch {
        return;
      }
    }
    void loadConfig();
  }, []);

  async function refreshAll() {
    if (!token) return;
    setError("");
    try {
      const [
        goblinPayload,
        jobPayload,
        runPayload,
        eventPayload,
        eventStreamPayload,
        heartbeatPayload,
        servicePayload,
        schedulePayload,
        fanoutPayload,
        auditPayload,
        artifactStoragePayload,
        discoveryStatusPayload,
        discoverySourcesPayload,
        promotionPayload,
        deploymentPayload,
      ] = await Promise.all([
        api("/goblins", {}, "GET /goblins"),
        api("/jobs?limit=100", {}, "GET /jobs"),
        api("/runs?limit=100", {}, "GET /runs"),
        api("/events?limit=50", {}, "GET /events"),
        api("/events/stream/status", {}, "GET /events/stream/status"),
        api("/heartbeats", {}, "GET /heartbeats"),
        api("/services/long-running", {}, "GET /services/long-running"),
        api("/schedules", {}, "GET /schedules"),
        api("/fanouts", {}, "GET /fanouts"),
        api("/audit-logs?limit=20", {}, "GET /audit-logs"),
        api("/admin/artifacts/storage", {}, "GET /admin/artifacts/storage"),
        api("/admin/discovery/status", {}, "GET /admin/discovery/status"),
        api("/admin/discovery/sources", {}, "GET /admin/discovery/sources"),
        api("/admin/images/promotions", {}, "GET /admin/images/promotions"),
        api("/admin/deployments", {}, "GET /admin/deployments"),
      ]);
      setGoblins(goblinPayload);
      setJobs(latestFirst(jobPayload.items));
      setRuns(latestFirst(runPayload.items));
      setEvents(latestFirst(eventPayload.items));
      setEventStream(eventStreamPayload);
      setHeartbeats(heartbeatPayload);
      setServices(usefulServicesFirst(servicePayload));
      setSchedules(schedulePayload);
      setFanouts(fanoutPayload);
      setAuditLogs(latestFirst(auditPayload.items));
      setArtifactStorage(artifactStoragePayload);
      setDiscoveryStatus(discoveryStatusPayload);
      setDiscoverySources(discoverySourcesPayload);
      setPromotions(latestFirst(promotionPayload));
      setDeployments(latestFirst(deploymentPayload));
      setSelectedKind((current) => goblinPayload.find((item: Goblin) => item.kind === current)?.kind || goblinPayload[0]?.kind || "example.hello");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  useEffect(() => {
    void refreshAll();
  }, [token]);

  useEffect(() => {
    if (!token) return;
    const socket = new WebSocket(`${WS_BASE}?token=${encodeURIComponent(token)}`);
    socket.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as EventRecord;
        setLiveEvents((items) => [event, ...items].slice(0, 10));
      } catch {
        return;
      }
    };
    return () => socket.close();
  }, [token]);

  function login(event: FormEvent) {
    event.preventDefault();
    localStorage.setItem(TOKEN_KEY, draftToken);
    setToken(draftToken);
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    setToken("");
  }

  async function submitJob(kind = selectedKind, input = jobInput) {
    const payload = JSON.parse(input || "{}");
    await api(
      "/jobs",
      { method: "POST", body: JSON.stringify({ kind, input: payload }) },
      "POST /jobs",
    );
    await refreshAll();
  }

  async function cancelJob(jobId: string) {
    await api(`/jobs/${jobId}/cancel`, { method: "POST" }, "POST /jobs/{job_id}/cancel");
    await refreshAll();
  }

  async function hardKillJob(jobId: string) {
    await api(
      `/admin/runtime/jobs/${jobId}/kill`,
      { method: "POST", body: JSON.stringify({ runtime: "both" }) },
      "POST /admin/runtime/jobs/{job_id}/kill",
    );
    await refreshAll();
  }

  async function createFanout() {
    await api("/jobs/fanout", { method: "POST", body: fanoutInput }, "POST /jobs/fanout");
    await refreshAll();
  }

  async function retryJob() {
    if (!retryJobId) return;
    await api(
      `/jobs/${retryJobId}/retry`,
      { method: "POST", body: JSON.stringify({ reason: "admin lab retry" }) },
      "POST /jobs/{job_id}/retry",
    );
    await refreshAll();
  }

  async function createSchedule() {
    await api(
      "/schedules",
      {
        method: "POST",
        body: JSON.stringify({
          kind: selectedKind,
          cron: scheduleCron,
          input: JSON.parse(jobInput || "{}"),
          due_now: true,
        }),
      },
      "POST /schedules",
    );
    await refreshAll();
  }

  async function toggleSchedule(schedule: Schedule) {
    await api(
      `/schedules/${schedule.id}`,
      { method: "PATCH", body: JSON.stringify({ enabled: !schedule.enabled }) },
      "PATCH /schedules/{schedule_id}",
    );
    await refreshAll();
  }

  async function registerService() {
    await api(
      "/services/long-running",
      { method: "POST", body: JSON.stringify({ kind: "example.long-hello", base_url: serviceUrl.trim() }) },
      "POST /services/long-running",
    );
    await refreshAll();
  }

  async function probeService(serviceId: string) {
    await api(
      `/services/long-running/${serviceId}/probe`,
      { method: "POST" },
      "POST /services/long-running/{service_id}/probe",
    );
    await refreshAll();
  }

  async function stopService(serviceId: string) {
    await api(
      `/services/long-running/${serviceId}/stop`,
      { method: "POST" },
      "POST /services/long-running/{service_id}/stop",
    );
    await refreshAll();
  }

  async function hardKillService(serviceId: string) {
    await api(
      `/admin/runtime/services/${serviceId}/kill`,
      { method: "POST", body: JSON.stringify({ runtime: "both" }) },
      "POST /admin/runtime/services/{service_id}/kill",
    );
    await refreshAll();
  }

  async function inspectArtifacts(runId: string) {
    if (!runId) return;
    await api(`/runs/${runId}/artifacts`, {}, "GET /runs/{run_id}/artifacts");
  }

  async function cleanupArtifacts(dryRun: boolean) {
    const payload = await api(
      "/admin/artifacts/cleanup",
      {
        method: "POST",
        body: JSON.stringify({ dry_run: dryRun, max_total_bytes: 0 }),
      },
      dryRun ? "POST /admin/artifacts/cleanup dry-run" : "POST /admin/artifacts/cleanup delete",
    );
    setArtifactCleanup(payload);
    await refreshAll();
  }

  async function createPrincipal(type: "user" | "project" | "token") {
    if (type === "user") {
      await api(
        "/admin/users",
        { method: "POST", body: JSON.stringify({ email: `tester-${Date.now()}@example.test`, display_name: "Admin Lab Tester" }) },
        "POST /admin/users",
      );
    }
    if (type === "project") {
      await api(
        "/admin/projects",
        { method: "POST", body: JSON.stringify({ name: `Admin Lab ${Date.now()}` }) },
        "POST /admin/projects",
      );
    }
    if (type === "token") {
      setError("Create a user/project token through API once you choose a user id; panel path is represented.");
    }
    await refreshAll();
  }

  async function cleanupRuntimeRows(dryRun: boolean) {
    const response = await api(
      "/admin/cleanup/runtime",
      {
        method: "POST",
        body: JSON.stringify({
          dry_run: dryRun,
          include_unprobed_services: includeUnprobedServices,
        }),
      },
      dryRun ? "POST /admin/cleanup/runtime dry-run" : "POST /admin/cleanup/runtime delete",
    ) as CleanupResponse;
    setCleanupPreview(response);
    await refreshAll();
  }

  async function reloadDiscovery() {
    const status = await api(
      "/admin/discovery/reload",
      { method: "POST" },
      "POST /admin/discovery/reload",
    ) as DiscoveryStatus;
    setDiscoveryStatus(status);
    await refreshAll();
  }

  async function planPromotion() {
    await api(
      "/admin/images/promotions",
      {
        method: "POST",
        body: JSON.stringify({
          kind: selectedKind,
          target_image: promotionTargetImage,
          build: true,
          push: true,
          dry_run: true,
        }),
      },
      "POST /admin/images/promotions",
    );
    await refreshAll();
  }

  async function markPromotion(promotionId: string) {
    await api(
      `/admin/images/promotions/${promotionId}/mark`,
      {
        method: "POST",
        body: JSON.stringify({ status: "promoted", detail: { marked_from: "admin lab" } }),
      },
      "POST /admin/images/promotions/{promotion_id}/mark",
    );
    await refreshAll();
  }

  async function recordHelmTemplate() {
    await api(
      "/admin/deployments/helm-template",
      {
        method: "POST",
        body: JSON.stringify({ name: "admin-lab", release: "goblin-king", execute: false }),
      },
      "POST /admin/deployments/helm-template",
    );
    await refreshAll();
  }

  async function recordDeploymentReload() {
    await api(
      "/admin/deployments/reload-discovery",
      { method: "POST" },
      "POST /admin/deployments/reload-discovery",
    );
    await refreshAll();
  }

  const cleanupTotal = cleanupPreview
    ? Object.values(cleanupPreview.counts).reduce((total, value) => total + value, 0)
    : 0;

  const counts = {
    active: jobs.filter((job) => ["queued", "leased", "running", "retrying"].includes(job.status)).length,
    failed: jobs.filter((job) => ["failed", "timed_out", "cancelled"].includes(job.status)).length,
    completed: jobs.filter((job) => job.status === "completed").length,
    services: services.filter((service) => service.status === "running").length,
  };

  if (!token) {
    return (
      <main className="login-shell">
        <section className="login-card">
          <Crown className="login-crown" aria-hidden />
          <p className="eyebrow">Goblin King Admin Lab</p>
          <h1>Tester console login</h1>
          <p className="quote">"{quoteFor(0)}"</p>
          <form onSubmit={login}>
            <label htmlFor="token">API token</label>
            <input id="token" value={draftToken} onChange={(event) => setDraftToken(event.target.value)} />
            <button type="submit"><KeyRound size={18} /> Enter the lab</button>
          </form>
        </section>
      </main>
    );
  }

  return (
    <div className="app-shell">
      <aside>
        <Crown className="brand-mark" aria-hidden />
        <h1>Goblin King</h1>
        <p>Admin Lab Bench</p>
        <nav>
          {["Dashboard", "Goblin Lab", "Task Board", "Schedules", "Runs", "Fanout", "Services", "Events", "Discovery", "Deploy", "Admin"].map((item) => (
            <a href={`#${item.toLowerCase().replaceAll(" ", "-")}`} key={item}>{item}</a>
          ))}
        </nav>
        <button className="ghost" onClick={logout}>Logout</button>
      </aside>
      <main>
        <header className="hero">
          <div>
            <p className="eyebrow">Live tester interface</p>
            <h2>Every path gets a button, a table, or a witness.</h2>
            <p className="quote">"{quoteFor(events.length + liveEvents.length)}"</p>
          </div>
          <button onClick={refreshAll}><RefreshCw size={18} /> Refresh all</button>
        </header>

        {error && <div className="error" role="alert">{error}</div>}

        <section id="dashboard" className="grid four">
          <Stat icon={<Activity />} label="Active tasks" value={counts.active} />
          <Stat icon={<Ban />} label="Failed/cancelled" value={counts.failed} />
          <Stat icon={<ClipboardList />} label="Completed" value={counts.completed} />
          <Stat icon={<HeartPulse />} label="Running services" value={counts.services} />
        </section>

        <section id="goblin-lab" className="panel two-column">
          <div>
            <h3><FlaskConical /> Goblin Lab</h3>
            <p className="muted">
              Spawn one-shot OCI worker containers from the active registry and image map. The King-side kill button cancels work in the queue, not a runtime hard-kill.
            </p>
            <label>Goblin kind</label>
            <select value={selectedKind} onChange={(event) => setSelectedKind(event.target.value)}>
              {goblins.map((goblin) => <option key={goblin.kind} value={goblin.kind}>{goblin.kind}</option>)}
            </select>
            <label>Input JSON</label>
            <textarea value={jobInput} onChange={(event) => setJobInput(event.target.value)} />
            <div className="button-row">
              <button onClick={() => void submitJob()}><Play size={16} /> Submit job</button>
              <button onClick={() => void submitJob("example.hello", '{"name":"World"}')}><Sparkles size={16} /> Hello proof</button>
              <button onClick={() => void submitJob("example.controlled-failure", '{"reason":"admin lab failure proof"}')}><Ban size={16} /> Failure proof</button>
            </div>
          </div>
          <Table
            title="Registered Container Goblins"
            rows={goblins.map((goblin) => [
              goblin.kind,
              goblin.display_name,
              goblin.worker_mapped ? "OCI worker image mapped" : "missing worker image",
              goblin.worker_image || "none",
            ])}
          />
        </section>

        <section id="task-board" className="panel">
          <h3><Boxes /> Task Board</h3>
          <div className="cards">
            {jobs.map((job) => (
              <article className={`task ${job.status}`} key={job.id}>
                <span>{job.status}</span>
                <h4>{job.kind}</h4>
                <code>{job.id}</code>
                <p>{job.last_error || quoteFor(job.id.length)}</p>
                {!["completed", "failed", "timed_out", "cancelled"].includes(job.status) && (
                  <div className="button-row">
                    <button className="danger" onClick={() => void cancelJob(job.id)}><Ban size={16} /> Kill / cancel</button>
                    <button className="danger" onClick={() => void hardKillJob(job.id)}><Square size={16} /> Hard kill runtime</button>
                  </div>
                )}
              </article>
            ))}
          </div>
        </section>

        <section id="schedules" className="panel two-column">
          <div>
            <h3><ScrollText /> Schedules</h3>
            <label>Cron</label>
            <input value={scheduleCron} onChange={(event) => setScheduleCron(event.target.value)} />
            <button onClick={() => void createSchedule()}><Play size={16} /> Create due schedule</button>
          </div>
          <Table
            title="Schedule list"
            rows={schedules.map((schedule) => [
              schedule.kind,
              schedule.cron,
              schedule.enabled ? "enabled" : "disabled",
              <button key={schedule.id} onClick={() => void toggleSchedule(schedule)}>{schedule.enabled ? "Disable" : "Enable"}</button>,
            ])}
          />
        </section>

        <section id="runs" className="panel two-column">
          <Table
            title="Runs & Results"
            rows={runs.map((run) => [run.kind, run.status, run.id, run.error || JSON.stringify(run.result || {})])}
          />
          <div>
            <h3><ClipboardList /> Artifacts</h3>
            <label>Run ID</label>
            <input value={artifactRunId} onChange={(event) => setArtifactRunId(event.target.value)} placeholder="run id" />
            <button onClick={() => void inspectArtifacts(artifactRunId)}>Inspect artifacts</button>
            <p className="muted">Download links are returned by `GET /runs/{"{run_id}"}/artifacts`.</p>
            <h3><Boxes /> Artifact Volume</h3>
            <p className="muted">Volume/PVC storage health and cleanup proof for filesystem-backed artifacts.</p>
            <Table
              title="Storage"
              rows={[
                ["Root", artifactStorage?.root || "unknown"],
                ["Exists", artifactStorage?.exists ? "yes" : "no"],
                ["Writable", artifactStorage?.writable ? "yes" : "no"],
                ["Files", artifactStorage?.file_count ?? 0],
                ["Bytes", artifactStorage?.total_bytes ?? 0],
                ["Metadata rows", artifactStorage?.metadata_count ?? 0],
              ]}
            />
            <div className="button-row">
              <button onClick={() => void cleanupArtifacts(true)}>Preview artifact cleanup</button>
              <button
                className="danger"
                disabled={!artifactCleanup || artifactCleanup.deleted || artifactCleanup.files_selected === 0}
                onClick={() => void cleanupArtifacts(false)}
              >
                Delete previewed artifacts
              </button>
            </div>
            {artifactCleanup && <pre className="traffic">{JSON.stringify(artifactCleanup, null, 2)}</pre>}
          </div>
        </section>

        <section id="fanout" className="panel two-column">
          <div>
            <h3><Wand2 /> Fanout & Retry</h3>
            <label>Fanout JSON</label>
            <textarea value={fanoutInput} onChange={(event) => setFanoutInput(event.target.value)} />
            <button onClick={() => void createFanout()}>Create fanout</button>
            <label>Retry terminal job ID</label>
            <input value={retryJobId} onChange={(event) => setRetryJobId(event.target.value)} />
            <button onClick={() => void retryJob()}>Retry job</button>
          </div>
          <Table title="Fanouts" rows={fanouts.map((fanout) => [fanout.fanout.id, fanout.status, JSON.stringify(fanout.counts)])} />
        </section>

        <section id="services" className="panel">
          <h3><Radio /> Long Services</h3>
          <p className="muted">
            Deployment default: {adminConfig.deploymentScope} uses <code>{adminConfig.longHelloUrl}</code>.
          </p>
          <div className="button-row">
            <label className="sr-only" htmlFor="service-url">Long service URL</label>
            <input id="service-url" value={serviceUrl} onChange={(event) => setServiceUrl(event.target.value)} />
            <button className="ghost" onClick={() => setServiceUrl(adminConfig.longHelloUrl)}>Use deployment URL</button>
            <button onClick={() => void registerService()}>Register service</button>
          </div>
          <div className="cards">
            {services.map((service) => (
              <article className={`task ${service.status}`} key={service.id}>
                <span>{service.status}</span>
                <h4>{service.kind}</h4>
                <code>{service.base_url}</code>
                <pre>{JSON.stringify(service.last_probe_json || {}, null, 2)}</pre>
                <button
                  disabled={service.status === "stopped"}
                  onClick={() => void probeService(service.id)}
                >
                  {service.status === "stopped" ? "Stopped" : "Probe"}
                </button>
                <button
                  className="danger"
                  disabled={service.status === "stopped"}
                  onClick={() => void stopService(service.id)}
                >
                  <Square size={16} /> {service.status === "stopped" ? "Stopped" : "Stop service"}
                </button>
                <button
                  className="danger"
                  disabled={service.status === "stopped"}
                  onClick={() => void hardKillService(service.id)}
                >
                  <Square size={16} /> Hard stop runtime
                </button>
              </article>
            ))}
          </div>
        </section>

        <section id="events" className="panel two-column">
          <Table title="Durable Events" rows={events.map((event) => [event.event_type, event.source, event.created_at])} />
          <Table title="Live Event Rail" rows={liveEvents.map((event) => [event.event_type, event.source, JSON.stringify(event.payload)])} />
          <Table
            title="Redis Stream Delivery"
            rows={[
              ["Stream", eventStream?.stream || "goblin-king:events:stream"],
              ["Status", eventStream?.ok ? "healthy" : "unavailable"],
              ["Length", eventStream?.length ?? 0],
              ["Pending", eventStream?.pending ?? 0],
              ["Last ID", eventStream?.last_generated_id || "none"],
              ["Groups", eventStream?.groups?.length ?? 0],
              ["Error", eventStream?.error || "none"],
            ]}
          />
          <Table title="Heartbeats" rows={heartbeats.map((beat) => [beat.owner_type, beat.owner_id, beat.status, beat.last_seen_at])} />
        </section>

        <section id="discovery" className="panel two-column">
          <div>
            <h3><RefreshCw /> Discovery</h3>
            <p className="muted">
              Reload registry files, package entry points, and container worker image maps after a deployment. A failed reload keeps the previous good list active.
            </p>
            <div className="button-row">
              <button onClick={() => void reloadDiscovery()}><RefreshCw size={16} /> Reload discovery</button>
            </div>
            <pre className="traffic">{JSON.stringify(discoveryStatus || {}, null, 2)}</pre>
          </div>
          <div>
            <Table
              title="Discovery Sources"
              rows={[
                ["Project settings", discoverySources?.project_settings || "direct settings"],
                ["Registry files", discoverySources?.registry_files.join(", ") || "none"],
                ["Entry points", discoverySources?.entry_points_enabled ? "enabled" : "disabled"],
                ["Worker image map", discoverySources?.worker_image_map || "unknown"],
                ["Unmapped workers", discoverySources?.worker_unmapped_kinds.join(", ") || "none"],
                ["Rejected definitions", discoverySources?.rejected_definitions.join("; ") || "none"],
              ]}
            />
            <Table
              title="Active Goblin Kinds"
              rows={(discoverySources?.goblin_kinds || []).map((kind) => [
                kind,
                discoverySources?.worker_mapped_kinds.includes(kind) ? "worker mapped" : "needs mapping",
              ])}
            />
          </div>
        </section>

        <section id="deploy" className="panel two-column">
          <div>
            <h3><Rocket /> Image Promotion & Deploy</h3>
            <p className="muted">
              Plan worker image promotion, record Helm render intent, and reload discovery after deploy. Pushes are dry-run proof unless an operator runs the recorded command outside the lab.
            </p>
            <label>Promoted image tag</label>
            <input value={promotionTargetImage} onChange={(event) => setPromotionTargetImage(event.target.value)} />
            <div className="button-row">
              <button onClick={() => void planPromotion()}><Rocket size={16} /> Plan image promotion</button>
              <button onClick={() => void recordHelmTemplate()}>Record Helm render</button>
              <button onClick={() => void recordDeploymentReload()}><RefreshCw size={16} /> Reload after deploy</button>
            </div>
            <Table
              title="Worker Coverage"
              rows={goblins.map((goblin) => [
                goblin.kind,
                goblin.worker_mapped ? "image mapped" : "missing image map",
                goblin.worker_image || "none",
              ])}
            />
          </div>
          <div>
            <Table
              title="Image Promotions"
              rows={promotions.map((promotion) => [
                promotion.kind,
                promotion.status,
                `${promotion.source_image} -> ${promotion.target_image}`,
                <button key={promotion.id} onClick={() => void markPromotion(promotion.id)}>Mark promoted</button>,
              ])}
            />
            <Table
              title="Deployment Proof Trail"
              rows={deployments.map((record) => [
                record.action,
                record.status,
                record.command.join(" "),
                record.output || JSON.stringify(record.detail),
              ])}
            />
          </div>
        </section>

        <section id="admin" className="panel two-column">
          <div>
            <h3><Shield /> Admin & Auth</h3>
            <p className="muted">Admin paths are represented here for local RBAC, audit, token, and rate-limit proof.</p>
            <div className="button-row">
              <button onClick={() => void createPrincipal("user")}>Create test user</button>
              <button onClick={() => void createPrincipal("project")}>Create test project</button>
              <button onClick={() => void createPrincipal("token")}>Token path note</button>
            </div>
            <h3><Ban /> Cleanup</h3>
            <p className="muted">
              Preview and remove historical terminal jobs, runs, fanouts, events, old service registrations, and worker heartbeats.
              Running work, schedules, users, projects, and tokens are preserved.
            </p>
            <label className="check-row">
              <input
                type="checkbox"
                checked={includeUnprobedServices}
                onChange={(event) => setIncludeUnprobedServices(event.target.checked)}
              />
              Include unprobed registered services
            </label>
            <div className="button-row">
              <button onClick={() => void cleanupRuntimeRows(true)}>Preview old rows</button>
              <button
                className="danger"
                disabled={!cleanupPreview || cleanupPreview.deleted || cleanupTotal === 0}
                onClick={() => void cleanupRuntimeRows(false)}
              >
                Remove previewed rows
              </button>
            </div>
            {cleanupPreview && (
              <pre className="traffic">{JSON.stringify(cleanupPreview, null, 2)}</pre>
            )}
          </div>
          <Table title="Audit Logs" rows={auditLogs.map((log) => [log.action, log.outcome, log.created_at])} />
        </section>

        <section className="panel">
          <h3><Activity /> Captured Traffic</h3>
          <pre className="traffic">{JSON.stringify(traffic, null, 2)}</pre>
        </section>
      </main>
    </div>
  );
}
