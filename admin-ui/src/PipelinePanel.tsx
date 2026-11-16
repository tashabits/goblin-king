import { useState } from "react";

type Row = { key?: string; id?: string; version: number; status?: string; enabled?: boolean; document: Record<string, any> };
type Api = (path: string, options?: RequestInit) => Promise<any>;

export function PipelinePanel({ api }: { api: Api }) {
  const [project, setProject] = useState("");
  const [registration, setRegistration] = useState('{"repository_id":1,"registration":{}}');
  const [preview, setPreview] = useState<any>(null);
  const [previewInput, setPreviewInput] = useState("");
  const [collections, setCollections] = useState<Record<string, Row[]>>({});
  const [status, setStatus] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  async function load() {
    const state = await api("/pipeline/status");
    setStatus(state);
    if (!state.enabled || !project) return;
    const entries = await Promise.all(["repositories", "builds", "releases", "operations"].map(async name =>
      [name, (await api(`/pipeline/${name}?project_id=${encodeURIComponent(project)}`)).items]));
    setCollections(Object.fromEntries(entries));
  }
  async function perform(action: () => Promise<void>) {
    setBusy(true); setError("");
    try { await action(); } catch (failure) { setError(String(failure)); }
    finally { setBusy(false); }
  }
  async function mutate(path: string, row: Row, body: object) {
    const result = await api(path, { method: "POST", body: JSON.stringify({ ...body,
      expected_version: row.version, idempotency_key: crypto.randomUUID() }) });
    setNotice(result.operation_id ? `Operation ${result.operation_id}` : "Configuration saved");
    await load();
  }
  return <section id="delivery" className="panel">
    <h2>Repositories / Delivery</h2>
    <p>Discover a repository contract and review its routes before enabling delivery.</p>
    <label>Project <input value={project} onChange={event => setProject(event.target.value)} /></label>
    <button disabled={busy} onClick={() => void perform(load)}>Inspect delivery</button>
    {status && <p>{status.enabled ? "Management configured" : "Management disabled"}
      {status.simulated_provider && " · Local provider simulation; review evidence is synthetic"}
      {status.recovery_mode && " · Recovery hold"}</p>}
    {status?.last_error && <p role="alert">{status.last_error}</p>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <details><summary>Attach or update a repository</summary>
      <label>Repository configuration
        <textarea rows={12} value={registration} onChange={event => { setRegistration(event.target.value); setPreview(null); }} />
      </label>
      <button disabled={busy} onClick={() => void perform(async () => {
        setPreview(await api("/pipeline/repositories/discover", { method: "POST", body: registration }));
        setPreviewInput(registration);
      })}>Discover contract and routes</button>
      {preview && <><pre>{JSON.stringify(preview, null, 2)}</pre>
        <button disabled={busy || previewInput !== registration} onClick={() => void perform(async () => {
          const input = JSON.parse(registration);
          const existing = (collections.repositories || []).find(row =>
            row.document.registration.service_id === input.registration.service_id &&
            row.document.registration.project_id === input.registration.project_id);
          await api("/pipeline/repositories", { method: "POST", body: JSON.stringify({ ...input,
            expected_version: existing?.version || 0, idempotency_key: crypto.randomUUID() }) });
          setNotice("Repository attached with management disabled. Enabling starts with future eligible merges.");
          await load();
        })}>Attach reviewed configuration</button></>}
    </details>
    {(collections.repositories || []).map(row => <article key={row.key}>
      <h3>{row.document.registration.source_repository}</h3>
      <p>Version {row.version} · {row.enabled ? "Enabled" : "Disabled"}</p>
      <button disabled={busy} onClick={() => void perform(() => mutate(`/pipeline/repositories/${row.key}/management`, row, { enabled: !row.enabled }))}>
        {row.enabled ? "Disable management" : "Enable management"}</button>
    </article>)}
    {(collections.releases || []).map(row => <article key={row.key}>
      <h3>{row.document.environment.namespace} / {row.document.environment.release}</h3>
      <p>{row.document.active ? "Active" : "Inactive"} · Generation {row.document.generation}</p>
      <p>Desired: {row.document.desired_commit || "No admitted revision"}</p>
      <p>Observed: {row.document.observed?.image || "No healthy release"}</p>
      {row.document.hold && <p role="alert">Hold: {row.document.hold.reason}</p>}
      {row.document.operation_id && <p>In progress: {row.document.operation_id}</p>}
      {["emergency rollback", "recovered release awaiting explicit hold clearance"].includes(row.document.hold?.reason) && <button disabled={busy || !!row.document.operation_id}
        onClick={() => void perform(() => mutate(`/pipeline/releases/${row.key}/operations`, row, { action: "clear-hold" }))}>Clear release hold</button>}
      {row.document.environment.name === "test" && <button disabled={busy || !!row.document.operation_id}
        onClick={() => void perform(() => mutate(`/pipeline/releases/${row.key}/operations`, row,
          { action: row.document.active ? "deactivate" : "activate" }))}>
        {row.document.active ? "Deactivate test" : "Activate newest verified build"}</button>}
      <details><summary>Release evidence</summary><pre>{JSON.stringify(row.document, null, 2)}</pre></details>
      <details><summary>Emergency rollback</summary>
        {(row.document.history || []).filter((item: any, index: number, items: any[]) =>
          items.findIndex(candidate => candidate.build_id === item.build_id) === index).map((item: any) =>
          <button key={item.build_id} disabled={busy || !!row.document.operation_id}
            onClick={() => void perform(() => mutate(`/pipeline/releases/${row.key}/operations`, row,
              { action: "rollback", payload: { build_id: item.build_id } }))}>Restore {item.build_id} and hold delivery</button>)}
      </details>
    </article>)}
    {["builds", "operations"].map(name => <details key={name}><summary>{name} ({collections[name]?.length || 0})</summary>
      {(collections[name] || []).map(row => <article key={row.id}><h3>{row.id} · {row.status}</h3>
        {row.document.dispatch_error && <p role="alert">{row.document.dispatch_error}</p>}
        {row.document.record_error && <p role="alert">Recording: {row.document.record_error}</p>}
        {name === "operations" && row.status === "held" && row.document.job_id &&
          <button disabled={busy} onClick={() => void perform(() => mutate(`/pipeline/operations/${row.id}/recover`, row, {}))}>Verify termination and reconcile release</button>}
        {name === "builds" && ["failed", "published", "superseded"].includes(row.status || "") &&
          <button disabled={busy} onClick={() => void perform(() => mutate(`/pipeline/builds/${row.id}/rebuild`, row, {}))}>Rebuild as a new attempt</button>}
        <pre>{JSON.stringify(row.document, null, 2)}</pre>
      </article>)}</details>)}
  </section>;
}
