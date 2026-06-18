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

function statusClass(status) {
  return `status-${String(status || "unknown").replace(/[^a-z0-9_-]/g, "")}`;
}

function entryCard(entryDetail, { review = false } = {}) {
  const entry = entryDetail.entry || entryDetail;
  const version = latestVersion(entryDetail);
  const article = document.createElement("article");
  const status = version?.status || entry.status;
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
    <div class="tags">${(entry.tags || []).map((tag) => `<span class="pill">${escapeHtml(tag)}</span>`).join("")}</div>
    <div class="actions"></div>
  `;
  article.querySelector('[data-action="detail"]').addEventListener("click", () => {
    state.selectedEntry = entryDetail;
    $("#entryIdInput").value = entry.id;
    renderEntryDetail(entryDetail);
    switchTab("detail");
  });
  const actions = article.querySelector(".actions");
  if (entry.owner === state.me?.user || state.me?.is_admin) {
    actions.append(
      actionButton("Validate", () => validateEntry(entry.id)),
      actionButton("Request Review", () => transitionEntry(entry.id, "request-review")),
    );
  }
  if (state.me?.is_admin && review) {
    actions.append(
      actionButton("Approve", () => transitionEntry(entry.id, "approve")),
      actionButton("Publish", () => transitionEntry(entry.id, "publish")),
      actionButton("Reject", () => transitionEntry(entry.id, "reject"), "danger"),
      actionButton("Retire", () => transitionEntry(entry.id, "retire"), "danger"),
    );
  }
  if (entry.status === "published") {
    actions.append(
      actionButton("Use", () => {
        $("#runtimeName").value = entry.name;
        $("#runtimeType").value = entry.type;
        switchTab("runtime");
      }, "primary"),
    );
  }
  return article;
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
  const payload = await api(`/repository/entries?${params.toString()}`);
  renderList($("#directoryList"), payload.items || []);
}

async function loadMine() {
  const payload = await api("/repository/entries?status=all&limit=100");
  const mine = (payload.items || []).filter((item) => item.entry.owner === state.me.user);
  renderList($("#mineList"), mine);
}

async function loadReview() {
  $("#reviewHint").textContent = state.me?.is_admin
    ? "Pending entries are ready for approval, rejection, publication, or retirement."
    : "Review actions are available only to repository admins.";
  const payload = await api("/repository/entries?status=pending_review&limit=100");
  renderList($("#reviewList"), payload.items || [], { review: true });
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
  const detail = await api(`/repository/entries/${encodeURIComponent(id)}`);
  state.selectedEntry = detail;
  renderEntryDetail(detail);
}

function renderEntryDetail(detail) {
  const entry = detail.entry;
  $("#entryDetail").innerHTML = `
    <h2>${escapeHtml(entry.display_name || entry.name)}</h2>
    <p>${escapeHtml(entry.description || "")}</p>
    <div class="entry-meta">
      <span>${escapeHtml(entry.name)}</span>
      <span>${escapeHtml(entry.type)}</span>
      <span class="${statusClass(entry.status)}">${escapeHtml(entry.status)}</span>
      <span>${escapeHtml(entry.project_id || "default")}</span>
      <span>${escapeHtml(entry.owner || "unknown owner")}</span>
    </div>
    <h2>Versions</h2>
    <pre>${escapeHtml(JSON.stringify(detail.versions || [], null, 2))}</pre>
  `;
}

async function validateEntry(id) {
  const payload = await jsonPost(`/repository/entries/${encodeURIComponent(id)}/validate`, {
    require_success: true,
  });
  showToast("Validation complete");
  await refreshActiveLists();
  state.selectedEntry = payload;
  renderEntryDetail(payload);
}

async function transitionEntry(id, action) {
  const payload = await jsonPost(`/repository/entries/${encodeURIComponent(id)}/${action}`, {});
  showToast(`${action.replace("-", " ")} complete`);
  await refreshActiveLists();
  state.selectedEntry = payload;
  renderEntryDetail(payload);
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
  if (!name) throw new Error("Repository name is required");
  return encodeURIComponent(name);
}

async function runFunction() {
  const payload = await jsonPost(`/repository/functions/${runtimeName()}/run`, {
    input: runtimeInput(),
  });
  renderJson($("#runtimeOutput"), payload);
  showToast(`Queued job ${payload.job?.id || ""}`.trim());
}

async function startService() {
  const payload = await jsonPost(`/repository/services/${runtimeName()}/start`, {});
  renderJson($("#runtimeOutput"), payload);
  showToast("Service started");
}

async function probeService() {
  const payload = await jsonPost(`/repository/services/${runtimeName()}/probe`, {});
  renderJson($("#runtimeOutput"), payload);
  showToast("Probe complete");
}

async function proxyService() {
  const path = $("#runtimeProxyPath").value.trim().replace(/^\/+/, "");
  const payload = await api(`/repository/services/${runtimeName()}/proxy/${path}`);
  renderJson($("#runtimeOutput"), payload);
  showToast("Proxy call complete");
}

async function stopService() {
  const payload = await jsonPost(`/repository/services/${runtimeName()}/stop`, {});
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
  $("#sessionSummary").textContent = `${state.me.user} · ${state.me.is_admin ? "admin" : "user"}`;
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
  await refreshActiveLists();
}

init().catch(showError);
