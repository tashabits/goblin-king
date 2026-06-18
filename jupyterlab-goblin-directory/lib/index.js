import { JupyterFrontEnd } from '@jupyterlab/application';
import { ICommandPalette, showErrorMessage } from '@jupyterlab/apputils';
import { PageConfig, URLExt } from '@jupyterlab/coreutils';
import { ServerConnection } from '@jupyterlab/services';
import { Widget } from '@lumino/widgets';

const COMMAND_ID = 'goblin-directory:open-picker';
const API_PREFIX = 'goblin-directory/api';

class GoblinDirectoryWidget extends Widget {
  constructor() {
    super();
    this.id = 'goblin-directory-picker';
    this.title.label = 'Goblin Directory';
    this.title.caption = 'Discover and launch approved goblins';
    this.title.closable = true;
    this.addClass('gk-directory-picker');
    this.entries = [];
    this.selected = null;
    this.serverSettings = ServerConnection.makeSettings();
    this.node.innerHTML = template();
    this.bindEvents();
    void this.loadEntries();
  }

  bindEvents() {
    this.select = this.node.querySelector('[data-role="entry-select"]');
    this.summary = this.node.querySelector('[data-role="entry-summary"]');
    this.output = this.node.querySelector('[data-role="output"]');
    this.input = this.node.querySelector('[data-role="function-input"]');
    this.proxyPath = this.node.querySelector('[data-role="proxy-path"]');
    this.runButton = this.node.querySelector('[data-action="run-function"]');
    this.startButton = this.node.querySelector('[data-action="start-service"]');
    this.probeButton = this.node.querySelector('[data-action="probe-service"]');
    this.proxyButton = this.node.querySelector('[data-action="proxy-service"]');
    this.stopButton = this.node.querySelector('[data-action="stop-service"]');
    this.node
      .querySelector('[data-action="refresh"]')
      .addEventListener('click', () => void this.loadEntries());
    this.select.addEventListener('change', () => this.setSelected(this.select.value));
    this.runButton.addEventListener('click', () => void this.runFunction());
    this.startButton.addEventListener('click', () => void this.serviceAction('start'));
    this.probeButton.addEventListener('click', () => void this.serviceAction('probe'));
    this.proxyButton.addEventListener('click', () => void this.proxyService());
    this.stopButton.addEventListener('click', () => void this.serviceAction('stop'));
  }

  async loadEntries() {
    this.setOutput('Loading published goblins...');
    try {
      const payload = await this.request('entries?status=published&limit=100');
      this.entries = Array.isArray(payload.items) ? payload.items : [];
      this.renderEntries();
      this.setOutput(`Loaded ${this.entries.length} published goblin(s).`);
    } catch (error) {
      this.setOutput(errorMessage(error));
      await showErrorMessage('Goblin Directory', error);
    }
  }

  renderEntries() {
    this.select.innerHTML = '';
    if (this.entries.length === 0) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = 'No published goblins found';
      this.select.appendChild(option);
      this.setSelected('');
      return;
    }
    for (const item of this.entries) {
      const entry = item.entry || item;
      const option = document.createElement('option');
      option.value = entry.id || entry.name;
      option.textContent = optionLabel(item);
      this.select.appendChild(option);
    }
    this.setSelected(this.select.value);
  }

  setSelected(identifier) {
    this.selected =
      this.entries.find(item => {
        const entry = item.entry || item;
        return (entry.id || entry.name) === identifier;
      }) || null;
    this.renderSelected();
  }

  renderSelected() {
    const item = this.selected;
    const entry = item?.entry || item;
    if (!entry) {
      this.summary.textContent = 'Publish goblins in the Directory to launch them here.';
      this.setButtons(null);
      return;
    }
    const version = publishedVersion(item);
    const tags = Array.isArray(entry.tags) ? entry.tags.join(', ') : '';
    this.summary.textContent = [
      `Display: ${entry.display_name || entry.name}`,
      `Name: ${entry.name}`,
      `Type: ${entry.type}`,
      `Project: ${entry.project_id || 'default'}`,
      `Version: ${version || entry.published_version || 'latest'}`,
      `Tags: ${tags || 'none'}`
    ].join('\n');
    this.setButtons(entry.type);
  }

  setButtons(type) {
    const isFunction = type === 'notebook_function';
    const isService = type === 'notebook_service';
    this.runButton.disabled = !isFunction;
    this.startButton.disabled = !isService;
    this.probeButton.disabled = !isService;
    this.proxyButton.disabled = !isService;
    this.stopButton.disabled = !isService;
  }

  async runFunction() {
    const item = this.requireSelected('notebook_function');
    if (!item) {
      return;
    }
    const entry = item.entry || item;
    let input;
    try {
      input = JSON.parse(this.input.value || '{}');
    } catch (error) {
      this.setOutput(`Invalid JSON input: ${errorMessage(error)}`);
      return;
    }
    await this.invoke(
      `functions/${encodeURIComponent(entry.name)}/run`,
      {
        input,
        project_id: entry.project_id || null,
        version: publishedVersion(item)
      },
      'Running function goblin...'
    );
  }

  async serviceAction(action) {
    const item = this.requireSelected('notebook_service');
    if (!item) {
      return;
    }
    const entry = item.entry || item;
    await this.invoke(
      `services/${encodeURIComponent(entry.name)}/${action}`,
      {
        project_id: entry.project_id || null,
        version: publishedVersion(item)
      },
      `${action[0].toUpperCase()}${action.slice(1)}ing service goblin...`
    );
  }

  async proxyService() {
    const item = this.requireSelected('notebook_service');
    if (!item) {
      return;
    }
    const entry = item.entry || item;
    const cleanPath = (this.proxyPath.value || '/hello').replace(/^\/+/, '');
    const params = new URLSearchParams();
    if (entry.project_id) {
      params.set('project_id', entry.project_id);
    }
    const version = publishedVersion(item);
    if (version) {
      params.set('version', String(version));
    }
    const suffix = params.toString() ? `?${params.toString()}` : '';
    this.setOutput('Proxying service request...');
    try {
      const payload = await this.request(
        `services/${encodeURIComponent(entry.name)}/proxy/${pathEncode(cleanPath)}${suffix}`
      );
      this.setJsonOutput(payload);
    } catch (error) {
      this.setOutput(errorMessage(error));
      await showErrorMessage('Goblin Directory', error);
    }
  }

  async invoke(path, body, message) {
    this.setOutput(message);
    try {
      const payload = await this.request(path, {
        method: 'POST',
        body
      });
      this.setJsonOutput(payload);
    } catch (error) {
      this.setOutput(errorMessage(error));
      await showErrorMessage('Goblin Directory', error);
    }
  }

  requireSelected(type) {
    const item = this.selected;
    const entry = item?.entry || item;
    if (!entry) {
      this.setOutput('Select a published goblin first.');
      return null;
    }
    if (entry.type !== type) {
      this.setOutput(`Selected goblin is ${entry.type}, not ${type}.`);
      return null;
    }
    return item;
  }

  async request(path, options = {}) {
    const url = URLExt.join(PageConfig.getBaseUrl(), API_PREFIX, path);
    const init = {
      method: options.method || 'GET',
      headers: {
        Accept: 'application/json'
      },
      credentials: 'same-origin'
    };
    if (options.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(options.body);
    }
    const response = await ServerConnection.makeRequest(url, init, this.serverSettings);
    const text = await response.text();
    let payload = {};
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (_error) {
        payload = { detail: text };
      }
    }
    if (!response.ok) {
      throw new Error(payload.detail || `Directory request failed with ${response.status}`);
    }
    return payload;
  }

  setOutput(message) {
    this.output.textContent = message;
  }

  setJsonOutput(payload) {
    this.output.textContent = JSON.stringify(payload, null, 2);
  }
}

function template() {
  return `
    <section class="gk-directory-picker__toolbar">
      <button type="button" data-action="refresh">Refresh</button>
    </section>
    <label class="gk-directory-picker__label">
      Published goblin
      <select data-role="entry-select"></select>
    </label>
    <pre class="gk-directory-picker__summary" data-role="entry-summary"></pre>
    <label class="gk-directory-picker__label">
      Function input JSON
      <textarea data-role="function-input" spellcheck="false">{"name": "Directory"}</textarea>
    </label>
    <div class="gk-directory-picker__actions">
      <button type="button" data-action="run-function">Run Function</button>
      <button type="button" data-action="start-service">Start Service</button>
      <button type="button" data-action="probe-service">Probe</button>
      <label>
        Proxy path
        <input data-role="proxy-path" value="/hello" />
      </label>
      <button type="button" data-action="proxy-service">Proxy</button>
      <button type="button" data-action="stop-service">Stop</button>
    </div>
    <pre class="gk-directory-picker__output" data-role="output"></pre>
  `;
}

function optionLabel(item) {
  const entry = item.entry || item;
  const display = entry.display_name || entry.name;
  const version = publishedVersion(item) || entry.published_version || 'latest';
  const project = entry.project_id || 'default';
  const tags = Array.isArray(entry.tags) && entry.tags.length ? ` [${entry.tags.join(', ')}]` : '';
  return `${display} - ${entry.name} - ${entry.type} - ${project} - v${version}${tags}`;
}

function publishedVersion(item) {
  const entry = item?.entry || item;
  if (entry?.published_version) {
    return entry.published_version;
  }
  const versions = Array.isArray(item?.versions) ? item.versions : [];
  const published = versions.find(version => version.status === 'published');
  return published?.version || null;
}

function pathEncode(path) {
  return path
    .split('/')
    .filter(Boolean)
    .map(part => encodeURIComponent(part))
    .join('/');
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

const plugin = {
  id: 'goblin-king-jupyterlab:directory-picker',
  autoStart: true,
  requires: [ICommandPalette],
  activate: (app, palette) => {
    let widget = null;
    const openWidget = () => {
      if (!widget || widget.isDisposed) {
        widget = new GoblinDirectoryWidget();
        app.shell.add(widget, 'left', { rank: 601 });
      }
      app.shell.activateById(widget.id);
    };
    app.commands.addCommand(COMMAND_ID, {
      label: 'Goblin Directory',
      caption: 'Open the Goblin Directory picker',
      execute: openWidget
    });
    palette.addItem({ command: COMMAND_ID, category: 'Goblin Directory' });
    openWidget();
  }
};

export default plugin;
export { GoblinDirectoryWidget };
