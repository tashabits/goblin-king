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
  RefreshCw,
  Sparkles,
  Square,
} from "lucide-react";
import type { FormEvent, ReactNode } from "react";

import { quoteFor } from "./adminData";
import { Stat, Table } from "./components";
import type { Goblin, Job } from "./types";

type Counts = {
  active: number;
  failed: number;
  completed: number;
  services: number;
};

function validationBadge(goblin: Goblin) {
  const status = goblin.validation_status ?? {
    state: "unknown",
    message: "No validation proof has been recorded. Validate first, then schedule.",
  };
  return (
    <span className={`status-badge validation-${status.state}`} title={status.message}>
      {status.state}
    </span>
  );
}

export function LoginScreen({
  draftToken,
  onDraftTokenChange,
  onLogin,
}: {
  draftToken: string;
  onDraftTokenChange: (value: string) => void;
  onLogin: (event: FormEvent) => void;
}) {
  return (
    <main className="login-shell">
      <section className="login-card">
        <Crown className="login-crown" aria-hidden />
        <p className="eyebrow">Goblin King Admin Lab</p>
        <h1>Tester console login</h1>
        <p className="quote">"{quoteFor(0)}"</p>
        <form onSubmit={onLogin}>
          <label htmlFor="token">API token</label>
          <input
            id="token"
            value={draftToken}
            onChange={(event) => onDraftTokenChange(event.target.value)}
          />
          <button type="submit">
            <KeyRound size={18} /> Enter the lab
          </button>
        </form>
      </section>
    </main>
  );
}

export function AdminShell({
  error,
  eventCount,
  liveEventCount,
  onLogout,
  onRefresh,
  children,
}: {
  error: string;
  eventCount: number;
  liveEventCount: number;
  onLogout: () => void;
  onRefresh: () => void;
  children: ReactNode;
}) {
  const navItems = [
    "Dashboard",
    "Goblin Lab",
    "Task Board",
    "Schedules",
    "Runs",
    "Fanout",
    "Services",
    "Events",
    "Discovery",
    "Deploy",
    "Admin",
  ];

  return (
    <div className="app-shell">
      <aside>
        <Crown className="brand-mark" aria-hidden />
        <h1>Goblin King</h1>
        <p>Admin Lab Bench</p>
        <nav>
          {navItems.map((item) => (
            <a href={`#${item.toLowerCase().replaceAll(" ", "-")}`} key={item}>
              {item}
            </a>
          ))}
        </nav>
        <button className="ghost" onClick={onLogout}>
          Logout
        </button>
      </aside>
      <main>
        <header className="hero">
          <div>
            <p className="eyebrow">Live tester interface</p>
            <h2>Every path gets a button, a table, or a witness.</h2>
            <p className="quote">"{quoteFor(eventCount + liveEventCount)}"</p>
          </div>
          <button onClick={onRefresh}>
            <RefreshCw size={18} /> Refresh all
          </button>
        </header>

        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}

        {children}
      </main>
    </div>
  );
}

export function DashboardPanel({ counts }: { counts: Counts }) {
  return (
    <section id="dashboard" className="grid four">
      <Stat icon={<Activity />} label="Active tasks" value={counts.active} />
      <Stat icon={<Ban />} label="Failed/cancelled" value={counts.failed} />
      <Stat icon={<ClipboardList />} label="Completed" value={counts.completed} />
      <Stat icon={<HeartPulse />} label="Running services" value={counts.services} />
    </section>
  );
}

export function GoblinLabPanel({
  goblins,
  selectedKind,
  jobInput,
  onKindChange,
  onJobInputChange,
  onSubmitJob,
}: {
  goblins: Goblin[];
  selectedKind: string;
  jobInput: string;
  onKindChange: (value: string) => void;
  onJobInputChange: (value: string) => void;
  onSubmitJob: (kind?: string, inputOverride?: string) => void;
}) {
  return (
    <section id="goblin-lab" className="panel two-column">
      <div>
        <h3>
          <FlaskConical /> Goblin Lab
        </h3>
        <p className="muted">
          Spawn one-shot OCI worker containers from the active registry and image map. The
          King-side kill button cancels work in the queue, not a runtime hard-kill.
        </p>
        <label>Goblin kind</label>
        <select value={selectedKind} onChange={(event) => onKindChange(event.target.value)}>
          {goblins.map((goblin) => (
            <option key={goblin.kind} value={goblin.kind}>
              {goblin.kind}
            </option>
          ))}
        </select>
        <label>Input JSON</label>
        <textarea value={jobInput} onChange={(event) => onJobInputChange(event.target.value)} />
        <div className="button-row">
          <button onClick={() => onSubmitJob()}>
            <Play size={16} /> Submit job
          </button>
          <button onClick={() => onSubmitJob("example.hello", '{"name":"World"}')}>
            <Sparkles size={16} /> Hello proof
          </button>
          <button
            onClick={() =>
              onSubmitJob("example.controlled-failure", '{"reason":"admin lab failure proof"}')
            }
          >
            <Ban size={16} /> Failure proof
          </button>
        </div>
      </div>
      <Table
        title="Registered Container Goblins"
        rows={goblins.map((goblin) => [
          goblin.kind,
          goblin.display_name,
          goblin.source || "registry",
          goblin.worker_mapped ? "OCI worker image mapped" : "missing worker image",
          validationBadge(goblin),
          goblin.worker_image || "none",
        ])}
      />
    </section>
  );
}

export function TaskBoardPanel({
  jobs,
  onCancelJob,
  onHardKillJob,
}: {
  jobs: Job[];
  onCancelJob: (jobId: string) => void;
  onHardKillJob: (jobId: string) => void;
}) {
  return (
    <section id="task-board" className="panel">
      <h3>
        <Boxes /> Task Board
      </h3>
      <div className="cards">
        {jobs.map((job) => (
          <article className={`task ${job.status}`} key={job.id}>
            <span>{job.status}</span>
            <h4>{job.kind}</h4>
            <code>{job.id}</code>
            <p>{job.last_error || quoteFor(job.id.length)}</p>
            {!["completed", "failed", "timed_out", "cancelled"].includes(job.status) && (
              <div className="button-row">
                <button className="danger" onClick={() => onCancelJob(job.id)}>
                  <Ban size={16} /> Kill / cancel
                </button>
                <button className="danger" onClick={() => onHardKillJob(job.id)}>
                  <Square size={16} /> Hard kill runtime
                </button>
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
