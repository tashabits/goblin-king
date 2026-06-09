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
  ScrollText,
  Shield,
  Sparkles,
  Square,
  Wand2,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

const API_BASE = "/admin-api";
const WS_BASE = `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/admin-ws/runs`;
const TOKEN_KEY = "goblinKingAdminToken";
const QUOTES = [
  "The King counts every task twice: once for courage, once for evidence.",
  "A proper goblin returns receipts.",
  "No mystery enters the queue without a lantern tied to it.",
  "The throne accepts chaos, but only with structured JSON.",
  "If it moves, heartbeat it. If it stops, write it down.",
  "Tiny workers, enormous paperwork.",
];

type Goblin = {
  kind: string;
  display_name: string;
  worker_image?: string | null;
  worker_mapped: boolean;
};

type Job = {
  id: string;
  kind: string;
  status: string;
  input: Record<string, unknown>;
  created_at: string;
  due_at?: string | null;
  last_error?: string | null;
};

type Run = {
  id: string;
  job_id: string;
  kind: string;
  status: string;
  result?: Record<string, unknown> | null;
  error?: string | null;
  started_at: string;
};

type EventRecord = {
  id: string;
  event_type: string;
  source: string;
  created_at: string;
  payload: Record<string, unknown>;
};

type Heartbeat = {
  owner_id: string;
  owner_type: string;
  status: string;
  last_seen_at: string;
};

type LongService = {
  id: string;
  kind: string;
  status: string;
  base_url: string;
  last_probe_json?: Record<string, unknown> | null;
};

type AuditLog = {
  id: string;
  action: string;
  outcome: string;
  created_at: string;
};

type Schedule = {
  id: string;
  kind: string;
  cron: string;
  enabled: boolean;
  next_run_at: string;
};

type FanoutDetail = {
  status: string;
  fanout: { id: string; description?: string | null };
  counts: Record<string, number>;
};

type TrafficEntry = {
  label: string;
  request: unknown;
  response: unknown;
};

function quoteFor(seed: number) {
  return QUOTES[seed % QUOTES.length];
}

async function readJson(response: Response) {
  const text = await response.text();
  if (!text) return null;
  return JSON.parse(text);
}

export function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || "");
  const [draftToken, setDraftToken] = useState(token || "local-dev-token");
  const [goblins, setGoblins] = useState<Goblin[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [events, setEvents] = useState<EventRecord[]>([]);
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
  const [serviceUrl, setServiceUrl] = useState("http://long-hello:8080");
  const [retryJobId, setRetryJobId] = useState("");
  const [artifactRunId, setArtifactRunId] = useState("");
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
      [{ label, request: { path, ...options }, response: { status: response.status, payload } }, ...items].slice(0, 12),
    );
    if (!response.ok) {
      throw new Error(payload?.detail || `${label} failed with ${response.status}`);
    }
    return payload;
  }

  async function refreshAll() {
    if (!token) return;
    setError("");
    try {
      const [
        goblinPayload,
        jobPayload,
        runPayload,
        eventPayload,
        heartbeatPayload,
        servicePayload,
        schedulePayload,
        fanoutPayload,
        auditPayload,
      ] = await Promise.all([
        api("/goblins", {}, "GET /goblins"),
        api("/jobs?limit=100", {}, "GET /jobs"),
        api("/runs?limit=100", {}, "GET /runs"),
        api("/events?limit=50", {}, "GET /events"),
        api("/heartbeats", {}, "GET /heartbeats"),
        api("/services/long-running", {}, "GET /services/long-running"),
        api("/schedules", {}, "GET /schedules"),
        api("/fanouts", {}, "GET /fanouts"),
        api("/audit-logs?limit=20", {}, "GET /audit-logs"),
      ]);
      setGoblins(goblinPayload);
      setJobs(jobPayload.items);
      setRuns(runPayload.items);
      setEvents(eventPayload.items);
      setHeartbeats(heartbeatPayload);
      setServices(servicePayload);
      setSchedules(schedulePayload);
      setFanouts(fanoutPayload);
      setAuditLogs(auditPayload.items);
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
      { method: "POST", body: JSON.stringify({ kind: "example.long-hello", base_url: serviceUrl }) },
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

  async function inspectArtifacts(runId: string) {
    if (!runId) return;
    await api(`/runs/${runId}/artifacts`, {}, "GET /runs/{run_id}/artifacts");
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
          {["Dashboard", "Goblin Lab", "Task Board", "Schedules", "Runs", "Fanout", "Services", "Events", "Admin"].map((item) => (
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
            <p className="muted">Spawn one-shot jobs from the registry. The King-side kill button cancels work in the queue, not a runtime hard-kill.</p>
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
            title="Registered Goblins"
            rows={goblins.map((goblin) => [goblin.kind, goblin.display_name, goblin.worker_mapped ? goblin.worker_image || "mapped" : "unmapped"])}
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
                  <button className="danger" onClick={() => void cancelJob(job.id)}><Ban size={16} /> Kill / cancel</button>
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
          <div className="button-row">
            <input value={serviceUrl} onChange={(event) => setServiceUrl(event.target.value)} />
            <button onClick={() => void registerService()}>Register service</button>
          </div>
          <div className="cards">
            {services.map((service) => (
              <article className={`task ${service.status}`} key={service.id}>
                <span>{service.status}</span>
                <h4>{service.kind}</h4>
                <code>{service.base_url}</code>
                <pre>{JSON.stringify(service.last_probe_json || {}, null, 2)}</pre>
                <button onClick={() => void probeService(service.id)}>Probe</button>
                <button className="danger" onClick={() => void stopService(service.id)}><Square size={16} /> Stop service</button>
              </article>
            ))}
          </div>
        </section>

        <section id="events" className="panel two-column">
          <Table title="Durable Events" rows={events.map((event) => [event.event_type, event.source, event.created_at])} />
          <Table title="Live Event Rail" rows={liveEvents.map((event) => [event.event_type, event.source, JSON.stringify(event.payload)])} />
          <Table title="Heartbeats" rows={heartbeats.map((beat) => [beat.owner_type, beat.owner_id, beat.status, beat.last_seen_at])} />
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

function Stat({ icon, label, value }: { icon: ReactNode; label: string; value: number }) {
  return (
    <article className="stat">
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function Table({ title, rows }: { title: string; rows: ReactNode[][] }) {
  return (
    <div className="table-wrap">
      <h3>{title}</h3>
      {rows.length === 0 ? (
        <p className="empty">"{quoteFor(title.length)}"</p>
      ) : (
        <table>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${title}-${index}`}>
                {row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
