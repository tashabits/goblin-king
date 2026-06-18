const state = {
  me: null,
  selectedEntry: null,
  selectedBundle: null,
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
  return versions.length ? versions[versions.length - 1] : null;
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
          <span>owner ${escapeHtml(entry.owner || "unknown")}</span>
          ${version ? `<span>v${version.version}</span>` : ""}
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
      actionButton("Use", () => {
        $("#runtimeName").value = entry.name;
        $("#runtimeType").value = entry.type;
        switchTab("runtime");
      }, "primary"),
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
  const params = new URLSearchParams({ status: "published", limit: "50" });
  const q = $("#directoryQuery").value.trim();
  const type = $("#directoryType").value;
  if (q) params.set("q", q);
  if (type) params.set("type", type);
  const published = await api(`/directory/entries?${params.toString()}`);
  const items = [...(published.items || [])];
  if (state.me?.is_admin) {
    const approvedParams = new URLSearchParams(params);
    approvedParams.set("status", "approved");
    const approved = await api(`/directory/entries?${approvedParams.toString()}`);
    items.push(...(approved.items || []));
  }
  renderList($("#directoryList"), mergeItems(items));
}

async function loadMine() {
  const payload = await api("/directory/entries?status=all&limit=100");
  const mine = (payload.items || []).filter((item) => item.entry.owner === state.me.user);
  renderList($("#mineList"), mine);
}

async function loadReview() {
  $("#reviewHint").textContent = state.me?.is_admin
    ? "Pending entries are ready for approval; approved entries are ready for publication; retired entries can be permanently deleted."
    : "Review actions are available only to directory admins.";
  const [pending, approved, rejected, retired] = await Promise.all([
    api("/directory/entries?status=pending_review&limit=100"),
    api("/directory/entries?status=approved&limit=100"),
    api("/directory/entries?status=rejected&limit=100"),
    api("/directory/entries?status=retired&limit=100"),
  ]);
  renderList(
    $("#reviewList"),
    mergeItems([
      ...(pending.items || []),
      ...(approved.items || []),
      ...(rejected.items || []),
      ...(retired.items || []),
    ]),
    { review: true },
  );
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
  const detail = await api(`/directory/entries/${encodeURIComponent(id)}`);
  state.selectedEntry = detail;
  renderEntryDetail(detail);
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
  await loadMine();
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

function runtimeName() {
  const name = $("#runtimeName").value.trim();
  if (!name) throw new Error("Goblin name is required");
  return encodeURIComponent(name);
}

async function runFunction() {
  const payload = await jsonPost(`/directory/functions/${runtimeName()}/run`, {
    input: runtimeInput(),
  });
  renderJson($("#runtimeOutput"), payload);
  showToast(`Queued job ${payload.job?.id || ""}`.trim());
}

async function startService() {
  const payload = await jsonPost(`/directory/services/${runtimeName()}/start`, {});
  renderJson($("#runtimeOutput"), payload);
  showToast("Service started");
}

async function probeService() {
  const payload = await jsonPost(`/directory/services/${runtimeName()}/probe`, {});
  renderJson($("#runtimeOutput"), payload);
  showToast("Probe complete");
}

async function proxyService() {
  const path = $("#runtimeProxyPath").value.trim().replace(/^\/+/, "");
  const payload = await api(`/directory/services/${runtimeName()}/proxy/${path}`);
  renderJson($("#runtimeOutput"), payload);
  showToast("Proxy call complete");
}

async function stopService() {
  const payload = await jsonPost(`/directory/services/${runtimeName()}/stop`, {});
  renderJson($("#runtimeOutput"), payload);
  showToast("Service stopped");
}

async function refreshActiveLists() {
  await Promise.allSettled([loadDirectory(), loadMine(), loadReview()]);
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
  $("#runFunction").addEventListener("click", () => runFunction().catch(showError));
  $("#startService").addEventListener("click", () => startService().catch(showError));
  $("#probeService").addEventListener("click", () => probeService().catch(showError));
  $("#proxyService").addEventListener("click", () => proxyService().catch(showError));
  $("#stopService").addEventListener("click", () => stopService().catch(showError));
  $("#closeSnippet").addEventListener("click", closeSnippet);
  $("#copySnippetAgain").addEventListener("click", () => copySnippetAgain().catch(showError));
  await refreshActiveLists();
}

init().catch(showError);
