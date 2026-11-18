import { useState } from "react";

type Api = (path: string, options?: RequestInit) => Promise<any>;
type Host = { hostname: string; port: number; protocol: "HTTP" | "HTTPS" };
type Direct = { cidr: string; port: number; protocol: "TCP" | "UDP" };
type Configuration = {
  ingress: { enabled: boolean; gateway: string; hostname: string; path: string;
    path_type: "Exact" | "PathPrefix"; exposure: "internal" | "external";
    certificate: string; https_redirect: boolean; timeout_seconds: number;
    max_request_bytes: number | null; streaming: boolean };
  egress: { enabled: boolean; hosts: Host[]; direct: Direct[] };
};
type Release = { key?: string; document: Record<string, any> };
const empty: Configuration = {
  ingress: { enabled: false, gateway: "", hostname: "", path: "/", path_type: "PathPrefix",
    exposure: "internal", certificate: "", https_redirect: true, timeout_seconds: 30,
    max_request_bytes: 1048576, streaming: false },
  egress: { enabled: false, hosts: [], direct: [] },
};
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export function NetworkingPanel({ api, project, releases }: {
  api: Api; project: string; releases: Release[];
}) {
  const [releaseKey, setReleaseKey] = useState("");
  const [capabilities, setCapabilities] = useState<any>(null);
  const [state, setState] = useState<any>(null);
  const [configuration, setConfiguration] = useState<Configuration>(empty);
  const [preview, setPreview] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [diagnostics, setDiagnostics] = useState<any>(null);
  const release = releases.find(row => row.key === releaseKey);
  const environment = release?.document.environment;
  const allowed = capabilities?.releases?.[`${environment?.namespace}/${environment?.release}`];
  const gateway = capabilities?.gateways?.[configuration.ingress.gateway];
  async function perform(action: () => Promise<void>) {
    setBusy(true); setError("");
    try { await action(); } catch (failure) { setError(String(failure)); }
    finally { setBusy(false); }
  }
  function edit(next: Configuration) { setConfiguration(next); setPreview(null); setNotice(""); }
  function ingress(changes: Partial<Configuration["ingress"]>) {
    edit({ ...configuration, ingress: { ...configuration.ingress, ...changes } });
  }
  async function load() {
    const [caps, current] = await Promise.all([
      api(`/pipeline/networking/capabilities?project_id=${encodeURIComponent(project)}`),
      api(`/pipeline/releases/${releaseKey}/networking`),
    ]);
    setCapabilities(caps); setState(current); setConfiguration(current.desired); setPreview(null);
  }
  return <details className="panel">
    <summary>Networking: ingress and egress</summary>
    <p>Configure approved application routes and outbound connections separately for each environment.</p>
    <button disabled={busy || !project} onClick={() => void perform(async () => {
      setDiagnostics(await api(`/pipeline/networking/diagnostics?project_id=${encodeURIComponent(project)}`));
    })}>Inspect networking diagnostics</button>
    {diagnostics && <article aria-label="Networking diagnostics">
      <p>{diagnostics.observation_scope}</p>
      {!diagnostics.execution_available && <p role="alert">Install and validate networking execution before applying changes.</p>}
      {diagnostics.recovery_mode && <p role="alert">Recovery mode prevents new delivery until reconciliation is complete.</p>}
      {diagnostics.items.map((item: any) => <div key={item.release_key}>
        <h4>{item.namespace} · {item.environment}: {item.status}</h4>
        <p>Network version {item.network_version} · External reachability: {item.external_reachability}</p>
        {item.certificate && <p>Certificate expires: {item.certificate.expires_at}</p>}
        {item.actions.length > 0 && <ul>{item.actions.map((action: string) => <li key={action}>{action}</li>)}</ul>}
      </div>)}
    </article>}
    <label>Network release <select value={releaseKey} onChange={event => {
      setReleaseKey(event.target.value); setState(null); setPreview(null); setCapabilities(null);
    }}><option value="">Select a release</option>
      {releases.map(row => <option key={row.key} value={row.key}>
        {row.document.environment.namespace} / {row.document.environment.release}
      </option>)}
    </select></label>
    <button disabled={busy || !project || !releaseKey} onClick={() => void perform(load)}>Inspect networking</button>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {state && <>
      <p>Release version {state.version} · Network version {state.network_version} · {state.active ? "Active" : "Inactive"}</p>
      {state.operation_id && <p>Operation: {state.operation_id}</p>}
      {state.hold && <p role="alert">Recovery hold: {state.hold.reason}</p>}
      <button disabled={busy || !state.network_version || !capabilities?.execution_available || !!state.operation_id || !!state.hold}
        onClick={() => void perform(async () => {
          const result = await api(`/pipeline/releases/${releaseKey}/networking/probe`, { method: "POST", body: JSON.stringify({
            expected_version: state.version, idempotency_key: crypto.randomUUID(),
          }) });
          setNotice(`Networking health operation ${result.operation_id}`); await load();
        })}>Probe networking health</button>
      {!capabilities?.enabled && <p>Networking is not installed. Enable the optional networking package before configuring routes or outbound access.</p>}
      {capabilities?.execution_available === false && <p>Networking execution is unavailable. Complete installation diagnostics before applying changes.</p>}
      {capabilities?.enabled && !allowed && <p>This release has no installed networking permissions.</p>}
      <fieldset disabled={busy || !allowed || !capabilities?.enabled}>
        <legend>Ingress</legend>
        <label><input type="checkbox" checked={configuration.ingress.enabled}
          disabled={!capabilities?.ingress_available}
          onChange={event => ingress({ enabled: event.target.checked })} />Enable ingress</label>
        <label>Approved gateway <select value={configuration.ingress.gateway} onChange={event => {
          const binding = capabilities.gateways[event.target.value];
          ingress({ gateway: event.target.value, exposure: binding?.exposure || "internal", certificate: "" });
        }}><option value="">Select a gateway</option>
          {(allowed?.gateways || []).map((name: string) => <option key={name} value={name}>{name} ({capabilities.gateways[name].exposure})</option>)}
        </select></label>
        <label>Hostname <input value={configuration.ingress.hostname} onChange={event => ingress({ hostname: event.target.value })} /></label>
        {gateway && <p>Approved domains: {gateway.hostname_suffixes.join(", ")} · Exposure: {gateway.exposure}</p>}
        <label>Certificate <select value={configuration.ingress.certificate} onChange={event => ingress({ certificate: event.target.value })}>
          <option value="">Select a certificate</option>{(gateway?.certificates || []).map((name: string) => <option key={name}>{name}</option>)}
        </select></label>
        <label>Path <input value={configuration.ingress.path} onChange={event => ingress({ path: event.target.value })} /></label>
        <label>Path matching <select value={configuration.ingress.path_type} onChange={event => ingress({ path_type: event.target.value as "Exact" | "PathPrefix" })}>
          <option value="PathPrefix">Path prefix</option><option value="Exact">Exact path</option>
        </select></label>
        <label><input type="checkbox" checked={configuration.ingress.https_redirect} onChange={event => ingress({ https_redirect: event.target.checked })} />Redirect HTTP to HTTPS</label>
        <label><input type="checkbox" checked={configuration.ingress.streaming} onChange={event => ingress({ streaming: event.target.checked,
          max_request_bytes: event.target.checked ? null : 1048576 })} />Streaming / WebSockets</label>
        {configuration.ingress.streaming ? <p>Streaming disables request buffering and its body-size limit.</p> :
          <label>Maximum request bytes <input type="number" min={1024} max={10485760} value={configuration.ingress.max_request_bytes || 1048576}
            onChange={event => ingress({ max_request_bytes: Number(event.target.value) })} /></label>}
        <label>Request timeout (seconds) <input type="number" min={1} max={300} value={configuration.ingress.timeout_seconds}
          onChange={event => ingress({ timeout_seconds: Number(event.target.value) })} /></label>
      </fieldset>
      <fieldset disabled={busy || !allowed || !capabilities?.egress_available}>
        <legend>Egress</legend>
        <label><input type="checkbox" checked={configuration.egress.enabled} onChange={event => edit({ ...configuration,
          egress: { ...configuration.egress, enabled: event.target.checked } })} />Enable managed outbound access</label>
        <p>HTTP clients must honor the configured proxy. Disabling managed egress restores DNS-only outbound access.</p>
        {(allowed?.outbound_hosts || []).map((host: Host) => <label key={`${host.protocol}/${host.hostname}/${host.port}`}>
          <input type="checkbox" checked={configuration.egress.hosts.some(entry => same(entry, host))} onChange={event => edit({ ...configuration,
            egress: { ...configuration.egress, hosts: event.target.checked ? [...configuration.egress.hosts, host] : configuration.egress.hosts.filter(entry => !same(entry, host)) } })} />
          {host.protocol} {host.hostname}:{host.port}
        </label>)}
        {(allowed?.direct_destinations || []).map((destination: Direct) => <label key={`${destination.protocol}/${destination.cidr}/${destination.port}`}>
          <input type="checkbox" checked={configuration.egress.direct.some(entry => same(entry, destination))} onChange={event => edit({ ...configuration,
            egress: { ...configuration.egress, direct: event.target.checked ? [...configuration.egress.direct, destination] : configuration.egress.direct.filter(entry => !same(entry, destination)) } })} />
          Direct {destination.protocol} {destination.cidr}:{destination.port}
        </label>)}
      </fieldset>
      <button disabled={busy || !allowed || !!state.operation_id || !!state.hold} onClick={() => edit({
        ...configuration, ingress: { ...configuration.ingress, enabled: false },
        egress: { ...configuration.egress, enabled: false },
      })}>Disable ingress and egress in preview</button>
      <button disabled={busy || !capabilities?.enabled || !allowed || (!!state.operation_id && !state.hold)} onClick={() => void perform(async () => {
        setPreview(await api("/pipeline/networking/preview", { method: "POST", body: JSON.stringify({ release_key: releaseKey, configuration }) }));
      })}>Preview networking changes</button>
      {preview && <article><h4>Networking preview</h4>
        <p>URL: {preview.url || "Ingress disabled"} · External reachability: {preview.external_reachability}</p>
        <p>{preview.workload_restart ? "Applying this change may restart workloads and close existing connections." : "No proxy configuration restart required."}</p>
        <p>{preview.active ? "The release is active." : "The release will remain inactive."}</p>
        <pre>{JSON.stringify({ before: preview.previous, after: preview.configuration }, null, 2)}</pre>
        {state.hold && state.operation_id ? <button disabled={busy || !same(preview.configuration, state.desired)} onClick={() => void perform(async () => {
          const operation = await api(`/pipeline/operations/${state.operation_id}`);
          const result = await api(`/pipeline/operations/${state.operation_id}/recover`, { method: "POST", body: JSON.stringify({
            expected_version: operation.version, networking_preview_id: preview.preview_id,
            idempotency_key: crypto.randomUUID(),
          }) });
          setNotice(`Networking recovery operation ${result.operation_id}`); await load();
        })}>Verify termination and recover with this policy preview</button> : <button disabled={busy || capabilities?.execution_available === false} onClick={() => void perform(async () => {
          const result = await api(`/pipeline/releases/${releaseKey}/networking`, { method: "POST", body: JSON.stringify({
            configuration: preview.configuration, expected_version: preview.expected_version,
            preview_id: preview.preview_id, idempotency_key: crypto.randomUUID(),
          }) });
          setNotice(`Networking operation ${result.operation_id}`); await load();
        })}>Apply networking changes</button>}
      </article>}
      <details><summary>Observed networking</summary><pre>{JSON.stringify(state.observed, null, 2)}</pre></details>
    </>}
  </details>;
}
