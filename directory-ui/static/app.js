const state = {
  me: null,
  selectedEntry: null,
  selectedBundle: null,
  runtimeEntries: [],
  runtimeEntry: null,
  refreshTimer: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(options.body && !(options.body instanceof Blob) ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
    ...options,
  });
  const text = await response.text();
  let payload = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { text };
    }
  }
  if (!response.ok) {
    const detail = payload.detail || payload.text || `${response.status} ${response.statusText}`;
    throw new Error(detail);
  }
  return payload;
}

function api(path, options = {}) {
  return requestJson(`ui-api${path}`, options);
}

function jsonPost(path, payload = {}) {
  return api(path, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("visible"), 4500);
}

function showError(error) {
  console.error(error);
  showToast(error.message || String(error));
}

function setStatus(name, message, error = false) {
  const node = $(`#${name}Status`);
  if (!node) return;
  node.textContent = message;
  node.classList.toggle("error", error);
}

function markRefreshed(name, count) {
  const suffix = count === undefined ? "" : ` (${count})`;
  setStatus(name, `Updated ${new Date().toLocaleTimeString()}${suffix}`);
}

function renderJson(node, payload) {
  node.textContent = JSON.stringify(payload, null, 2);
}

function switchTab(tab) {
  $$(".tabs button").forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
  $$(".view").forEach((view) => view.classList.toggle("active", view.dataset.view === tab));
}

function entryVersions(entryDetail) {
  return entryDetail.versions || [];
}

function latestVersion(entryDetail) {
  const versions = entryVersions(entryDetail);
  return versions.length ? versions[versions.length - 1] : entryDetail.version || null;
}

function entryStatus(entryDetail) {
  const entry = entryDetail.entry || entryDetail;
  const version = latestVersion(entryDetail);
  return entry.status || version?.status;
}

function statusClass(status) {
  return `status-${String(status || "unknown").replace(/[^a-z0-9_-]/g, "")}`;
}

function statusMessage(entryDetail) {
  const status = entryStatus(entryDetail);
  if (status === "approved") return "Approved and awaiting publication.";
  if (status === "published") return "Published entries are discoverable and runnable by authorized users.";
  if (status === "retired") return "Retired entries are hidden from discovery and ready for permanent deletion.";
  if (status === "pending_review") return "Pending admin review.";
  if (status === "validated") return "Validated and ready to request review.";
  if (status === "draft") return "Draft entries must validate before review.";
  return "";
}

function entryLabel(entryDetail) {
  const entry = entryDetail.entry || entryDetail;
  const version = latestVersion(entryDetail);
  const display = entry.display_name || entry.name;
  const versionText = version?.version ? ` v${version.version}` : "";
  const typeText = entry.type === "notebook_service" ? "service" : "function";
  return `${display}${versionText} (${typeText})`;
}

function entryCard(entryDetail, { review = false } = {}) {
  const entry = entryDetail.entry || entryDetail;
  const article = document.createElement("article");
  const version = latestVersion(entryDetail);
  const status = entryStatus(entryDetail);
  article.innerHTML = `
    <div class="entry-head">
      <div>
        <div class="entry-title">${escapeHtml(entry.display_name || entry.name)}</div>
        <div class="entry-meta">
          <span>${escapeHtml(entry.name)}</span>
          <span>${escapeHtml(entry.type)}</span>
          <span class="${statusClass(status)}">${escapeHtml(status)}</span>
          <span>project ${escapeHtml(entry.project_id || "default")}</span>
          <span>owner ${escapeHtml(entry.owner || "unknown")}</span>
          ${version ? `<span>v${version.version}</span>` : ""}
          ${version?.source_hash ? `<span>${escapeHtml(String(version.source_hash).slice(0, 12))}</span>` : ""}
        </div>
      </div>
      <button type="button" data-action="detail">Detail</button>
    </div>
    <p>${escapeHtml(entry.description || "")}</p>
    <p class="status-note">${escapeHtml(statusMessage(entryDetail))}</p>
    <div class="tags">${(entry.tags || []).map((tag) => `<span class="pill">${escapeHtml(tag)}</span>`).join("")}</div>
    <div class="actions"></div>
  `;
  article.querySelector('[data-action="detail"]').addEventListener("click", () => {
    state.selectedEntry = entryDetail;
    $("#entryIdInput").value = entry.id;
    renderEntryDetail(entryDetail);
    switchTab("detail");
  });
  renderEntryActions(article.querySelector(".actions"), entryDetail, { review });
  return article;
}

function renderEntryActions(node, entryDetail, { review = false } = {}) {
  const entry = entryDetail.entry || entryDetail;
  const status = entryStatus(entryDetail);
  const canEdit = entry.owner === state.me?.user || state.me?.is_admin;
  const buttons = [];

  if (canEdit && status === "draft") {
    buttons.push(actionButton("Validate", () => validateEntry(entry.id)));
  }
  if (canEdit && status === "validated") {
    buttons.push(actionButton("Request Review", () => transitionEntry(entry.id, "request-review")));
  }
  if (state.me?.is_admin && status === "pending_review") {
    buttons.push(
      actionButton("Approve", () => transitionEntry(entry.id, "approve")),
      actionButton("Reject", () => transitionEntry(entry.id, "reject"), "danger"),
    );
  }
  if (state.me?.is_admin && status === "approved") {
    buttons.push(
      actionButton("Publish", () => transitionEntry(entry.id, "publish"), "primary"),
      actionButton("Reject", () => transitionEntry(entry.id, "reject"), "danger"),
    );
  }
  if (status === "published") {
    buttons.push(
      actionButton("Use", () => selectRuntimeEntry(entry.id), "primary"),
      actionButton("Copy Notebook Cell", () => copyNotebookCell(entryDetail)),
    );
    if (state.me?.is_admin) {
      buttons.push(actionButton("Retire from Directory", () => transitionEntry(entry.id, "retire"), "danger"));
    }
  }
  if (state.me?.is_admin && ["draft", "rejected", "retired"].includes(status)) {
    buttons.push(actionButton("Delete Entry Permanently", () => deleteEntry(entry.id), "danger"));
  }
  if (state.me?.is_admin && review && status !== "published" && status !== "retired") {
    buttons.push(actionButton("Retire", () => transitionEntry(entry.id, "retire"), "danger"));
  }

  node.replaceChildren(...buttons);
}

function actionButton(label, handler, className = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  if (className) button.classList.add(className);
  button.addEventListener("click", async () => {
    try {
      button.disabled = true;
      await handler();
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

async function loadDirectory() {
  setStatus("directory", "Loading...");
  const params = new URLSearchParams({ status: "published", limit: "100" });
  const q = $("#directoryQuery").value.trim();
  const type = $("#directoryType").value;
  if (q) params.set("q", q);
  if (type) params.set("type", type);
  try {
    const published = await api(`/directory/entries?${params.toString()}`);
    const items = [...(published.items || [])];
    if (state.me?.is_admin) {
      const approvedParams = new URLSearchParams(params);
      approvedParams.set("status", "approved");
      const approved = await api(`/directory/entries?${approvedParams.toString()}`);
      items.push(...(approved.items || []));
    }
    const merged = mergeItems(items);
    renderList($("#directoryList"), merged);
    markRefreshed("directory", merged.length);
  } catch (error) {
    setStatus("directory", error.message || String(error), true);
    throw error;
  }
}

async function loadMine() {
  setStatus("mine", "Loading...");
  try {
    const payload = await api("/directory/entries?status=all&limit=500");
    const mine = (payload.items || []).filter((item) => item.entry.owner === state.me.user);
    renderList($("#mineList"), mine);
    markRefreshed("mine", mine.length);
  } catch (error) {
    setStatus("mine", error.message || String(error), true);
    throw error;
  }
}

async function loadReview() {
  $("#reviewHint").textContent = state.me?.is_admin
    ? "Pending entries are ready for approval; approved entries are ready for publication; retired entries can be permanently deleted."
    : "Review actions are available only to directory admins.";
  setStatus("review", "Loading...");
  try {
    const [pending, approved, rejected, retired] = await Promise.all([
      api("/directory/entries?status=pending_review&limit=100"),
      api("/directory/entries?status=approved&limit=100"),
      api("/directory/entries?status=rejected&limit=100"),
      api("/directory/entries?status=retired&limit=100"),
    ]);
    const items = mergeItems([
      ...(pending.items || []),
      ...(approved.items || []),
      ...(rejected.items || []),
      ...(retired.items || []),
    ]);
    renderList($("#reviewList"), items, { review: true });
    markRefreshed("review", items.length);
  } catch (error) {
    setStatus("review", error.message || String(error), true);
    throw error;
  }
}

function mergeItems(items) {
  const byId = new Map();
  for (const item of items) {
    byId.set(item.entry.id, item);
  }
  return [...byId.values()].sort((left, right) =>
    String(right.entry.updated_at || "").localeCompare(String(left.entry.updated_at || "")),
  );
}

async function loadRuntimeEntries({ preserveSelection = true } = {}) {
  setStatus("runtime", "Loading published entries...");
  try {
    const payload = await api("/directory/entries?status=published&limit=500");
    state.runtimeEntries = payload.items || [];
    renderRuntimeSelect(preserveSelection);
    markRefreshed("runtime", state.runtimeEntries.length);
  } catch (error) {
    setStatus("runtime", error.message || String(error), true);
    throw error;
  }
}

function renderList(node, items, options = {}) {
  node.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.textContent = "No entries found.";
    node.append(empty);
    return;
  }
  for (const item of items) {
    node.append(entryCard(item, options));
  }
}

async function loadEntry() {
  const id = $("#entryIdInput").value.trim();
  if (!id) throw new Error("Entry ID is required");
  setStatus("detail", "Loading...");
  const detail = await api(`/directory/entries/${encodeURIComponent(id)}`);
  state.selectedEntry = detail;
  renderEntryDetail(detail);
  markRefreshed("detail");
}

function renderEntryDetail(detail) {
  const entry = detail.entry;
  const status = entryStatus(detail);
  $("#entryDetail").innerHTML = `
    <h2>${escapeHtml(entry.display_name || entry.name)}</h2>
    <p>${escapeHtml(entry.description || "")}</p>
    <div class="entry-meta">
      <span>${escapeHtml(entry.name)}</span>
      <span>${escapeHtml(entry.type)}</span>
      <span class="${statusClass(status)}">${escapeHtml(status)}</span>
      <span>${escapeHtml(entry.project_id || "default")}</span>
      <span>${escapeHtml(entry.owner || "unknown owner")}</span>
    </div>
    <p class="status-note">${escapeHtml(statusMessage(detail))}</p>
    <div id="entryDetailActions" class="actions"></div>
    <h2>Versions</h2>
    <pre>${escapeHtml(JSON.stringify(detail.versions || [], null, 2))}</pre>
  `;
  renderEntryActions($("#entryDetailActions"), detail);
}

async function validateEntry(id) {
  const payload = await jsonPost(`/directory/entries/${encodeURIComponent(id)}/validate`, {
    require_success: true,
    timeout_seconds: 180,
  });
  showToast("Validation complete");
  await refreshActiveLists();
  state.selectedEntry = payload;
  renderEntryDetail(payload);
}

async function transitionEntry(id, action) {
  const payload = await jsonPost(`/directory/entries/${encodeURIComponent(id)}/${action}`, {});
  showToast(`${action.replace("-", " ")} complete`);
  await refreshActiveLists();
  state.selectedEntry = payload;
  renderEntryDetail(payload);
}

async function deleteEntry(id) {
  const confirmed = window.confirm(
    "Delete this goblin entry permanently? Published entries must be retired first. This does not delete the Goblin Directory or any user files.",
  );
  if (!confirmed) return;
  const payload = await api(`/directory/entries/${encodeURIComponent(id)}`, { method: "DELETE" });
  showToast(`Deleted ${payload.name}`);
  state.selectedEntry = null;
  $("#entryIdInput").value = "";
  $("#entryDetail").textContent = "Entry deleted.";
  await refreshActiveLists();
}

async function copyNotebookCell(entryDetail) {
  const snippet = notebookCellSnippet(entryDetail);
  showSnippet(snippet);
  try {
    if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
    await navigator.clipboard.writeText(snippet);
    showToast("Notebook cell copied");
  } catch {
    showToast("Notebook cell ready to copy");
  }
}

function showSnippet(snippet) {
  $("#snippetText").value = snippet;
  $("#snippetModal").hidden = false;
}

function closeSnippet() {
  $("#snippetModal").hidden = true;
}

async function copySnippetAgain() {
  const snippet = $("#snippetText").value;
  if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
  await navigator.clipboard.writeText(snippet);
  showToast("Notebook cell copied");
}

function notebookCellSnippet(entryDetail) {
  const entry = entryDetail.entry || entryDetail;
  const version = entry.published_version || latestVersion(entryDetail)?.version;
  const projectLine = entry.project_id ? `    project_id=${JSON.stringify(entry.project_id)},\n` : "";
  const versionLine = version ? `    version=${JSON.stringify(version)},\n` : "";
  const bootstrap = [
    "import importlib",
    "import os",
    "import site",
    "import subprocess",
    "import sys",
    "",
    "try:",
    "    from goblin_king.notebooks import GoblinKingNotebookClient",
    "except Exception:",
    "    package = os.environ.get(",
    "        \"GOBLIN_KING_NOTEBOOK_PACKAGE\",",
    "        \"git+https://github.com/tashabits/goblin-king.git@develop\",",
    "    )",
    "    subprocess.check_call([",
    "        sys.executable,",
    "        \"-m\",",
    "        \"pip\",",
    "        \"install\",",
    "        \"--disable-pip-version-check\",",
    "        \"--quiet\",",
    "        \"--user\",",
    "        \"--force-reinstall\",",
    "        \"--no-deps\",",
    "        package,",
    "    ])",
    "    user_site = site.getusersitepackages()",
    "    if user_site not in sys.path:",
    "        sys.path.insert(0, user_site)",
    "    importlib.invalidate_caches()",
    "    from goblin_king.notebooks import GoblinKingNotebookClient",
    "",
    "if \"JUPYTERHUB_API_TOKEN\" not in os.environ:",
    "    raise RuntimeError(\"Run this cell inside a JupyterHub user server\")",
    "",
    "client = GoblinKingNotebookClient(",
    "    api_url=os.environ.get(",
    "        \"GOBLIN_KING_API_URL\",",
    "        \"http://goblin-king-api.default.svc.cluster.local:8000\",",
    "    ),",
    "    token=os.environ[\"JUPYTERHUB_API_TOKEN\"],",
    "    request_timeout_seconds=120,",
    ")",
  ].join("\n");

  if (entry.type === "notebook_service") {
    return `${bootstrap}

service = client.directory_service(
    ${JSON.stringify(entry.name)},
${projectLine}${versionLine})
start = service.start(progress=True)
probe = service.probe()
proxied = service.proxy("/hello")
# stop = service.stop()
{"start": start, "probe": probe, "proxied": proxied}
`;
  }
  return `${bootstrap}

result = client.run_directory_function(
    ${JSON.stringify(entry.name)},
    input={"name": "Directory"},
${projectLine}${versionLine}    progress=True,
)
result
`;
}

async function previewBundle() {
  const file = $("#bundleFile").files[0];
  if (!file) throw new Error("Choose a zip bundle first");
  state.selectedBundle = file;
  const payload = await requestJson("ui-api/bundles/preview", {
    method: "POST",
    body: file,
    headers: { "Content-Type": "application/zip" },
  });
  renderJson($("#bundlePreview"), payload);
}

async function submitBundle() {
  const file = state.selectedBundle || $("#bundleFile").files[0];
  if (!file) throw new Error("Choose a zip bundle first");
  const payload = await requestJson("ui-api/bundles/submit", {
    method: "POST",
    body: file,
    headers: { "Content-Type": "application/zip" },
  });
  renderJson($("#bundlePreview"), payload);
  state.selectedEntry = payload;
  $("#entryIdInput").value = payload.entry.id;
  showToast("Draft submitted");
  await refreshActiveLists();
}

function renderRuntimeSelect(preserveSelection) {
  const select = $("#runtimeEntrySelect");
  const previous = preserveSelection ? select.value : "";
  select.replaceChildren();
  if (!state.runtimeEntries.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No published goblins available";
    select.append(option);
    state.runtimeEntry = null;
    renderRuntimeSelection();
    return;
  }
  for (const item of state.runtimeEntries) {
    const entry = item.entry;
    const option = document.createElement("option");
    option.value = entry.id;
    option.textContent = entryLabel(item);
    select.append(option);
  }
  const nextValue = state.runtimeEntries.some((item) => item.entry.id === previous)
    ? previous
    : state.runtimeEntries[0].entry.id;
  select.value = nextValue;
  selectRuntimeEntry(nextValue, { switchToRuntime: false });
}

function selectRuntimeEntry(entryId, { switchToRuntime = true } = {}) {
  const detail = state.runtimeEntries.find((item) => item.entry.id === entryId) || null;
  state.runtimeEntry = detail;
  $("#runtimeEntrySelect").value = detail?.entry.id || "";
  renderRuntimeSelection();
  if (switchToRuntime) switchTab("runtime");
}

function renderRuntimeSelection() {
  const detail = state.runtimeEntry;
  const summary = $("#runtimeSelectedSummary");
  const functionButton = $("#runFunction");
  const serviceButtons = [$("#startService"), $("#probeService"), $("#proxyService"), $("#stopService")];
  if (!detail) {
    summary.textContent = "No published goblin selected.";
    functionButton.disabled = true;
    serviceButtons.forEach((button) => {
      button.disabled = true;
    });
    return;
  }
  const entry = detail.entry;
  const version = latestVersion(detail);
  summary.innerHTML = `
    <strong>${escapeHtml(entry.display_name || entry.name)}</strong>
    <span>${escapeHtml(entry.name)} - ${escapeHtml(entry.type)} - project ${escapeHtml(entry.project_id || "default")}${version ? ` - v${version.version}` : ""}</span>
    <span>${(entry.tags || []).map((tag) => escapeHtml(tag)).join(", ")}</span>
  `;
  const isFunction = entry.type === "notebook_function";
  functionButton.disabled = !isFunction;
  serviceButtons.forEach((button) => {
    button.disabled = isFunction;
  });
}

function runtimeInput() {
  const text = $("#runtimeInput").value.trim();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(`JSON input is invalid: ${error.message}`);
  }
}

function requireRuntimeEntry(expectedType) {
  const detail = state.runtimeEntry;
  if (!detail) throw new Error("Choose a published goblin first");
  if (expectedType && detail.entry.type !== expectedType) {
    throw new Error(`Selected goblin is ${detail.entry.type}, not ${expectedType}`);
  }
  return detail.entry;
}

async function runFunction() {
  const entry = requireRuntimeEntry("notebook_function");
  const payload = await jsonPost(`/directory/functions/${encodeURIComponent(entry.name)}/run`, {
    input: runtimeInput(),
  });
  renderJson($("#runtimeOutput"), payload);
  showToast(`Queued job ${payload.job?.id || ""}`.trim());
  await refreshActiveLists();
}

async function startService() {
  const entry = requireRuntimeEntry("notebook_service");
  const payload = await jsonPost(`/directory/services/${encodeURIComponent(entry.name)}/start`, {});
  renderJson($("#runtimeOutput"), payload);
  showToast("Service started");
  await refreshActiveLists();
}

async function probeService() {
  const entry = requireRuntimeEntry("notebook_service");
  const payload = await jsonPost(`/directory/services/${encodeURIComponent(entry.name)}/probe`, {});
  renderJson($("#runtimeOutput"), payload);
  showToast("Probe complete");
}

async function proxyService() {
  const entry = requireRuntimeEntry("notebook_service");
  const path = $("#runtimeProxyPath").value.trim().replace(/^\/+/, "");
  const payload = await api(`/directory/services/${encodeURIComponent(entry.name)}/proxy/${path}`);
  renderJson($("#runtimeOutput"), payload);
  showToast("Proxy call complete");
}

async function stopService() {
  const entry = requireRuntimeEntry("notebook_service");
  const payload = await jsonPost(`/directory/services/${encodeURIComponent(entry.name)}/stop`, {});
  renderJson($("#runtimeOutput"), payload);
  showToast("Service stopped");
  await refreshActiveLists();
}

async function refreshActiveLists() {
  await Promise.allSettled([loadDirectory(), loadMine(), loadReview(), loadRuntimeEntries()]);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function init() {
  state.me = await requestJson("ui-api/me");
  $("#sessionSummary").textContent = `${state.me.user} - ${state.me.is_admin ? "admin" : "user"}`;
  $$(".tabs button").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });
  $("#logoutButton").addEventListener("click", async () => {
    await requestJson("ui-api/logout", { method: "POST" });
    window.location.reload();
  });
  $("#refreshDirectory").addEventListener("click", () => loadDirectory().catch(showError));
  $("#directoryQuery").addEventListener("input", () => loadDirectory().catch(showError));
  $("#directoryType").addEventListener("change", () => loadDirectory().catch(showError));
  $("#previewBundle").addEventListener("click", () => previewBundle().catch(showError));
  $("#submitBundle").addEventListener("click", () => submitBundle().catch(showError));
  $("#refreshMine").addEventListener("click", () => loadMine().catch(showError));
  $("#refreshReview").addEventListener("click", () => loadReview().catch(showError));
  $("#loadEntry").addEventListener("click", () => loadEntry().catch(showError));
  $("#runtimeEntrySelect").addEventListener("change", (event) => selectRuntimeEntry(event.target.value));
  $("#runFunction").addEventListener("click", () => runFunction().catch(showError));
  $("#startService").addEventListener("click", () => startService().catch(showError));
  $("#probeService").addEventListener("click", () => probeService().catch(showError));
  $("#proxyService").addEventListener("click", () => proxyService().catch(showError));
  $("#stopService").addEventListener("click", () => stopService().catch(showError));
  $("#closeSnippet").addEventListener("click", closeSnippet);
  $("#copySnippetAgain").addEventListener("click", () => copySnippetAgain().catch(showError));
  await refreshActiveLists();
  state.refreshTimer = window.setInterval(() => {
    refreshActiveLists().catch(showError);
  }, 15000);
}

init().catch(showError);
