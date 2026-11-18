const {chromium} = require('/audit/node_modules/playwright');
const fs = require('fs');
const {isDeepStrictEqual} = require('node:util');
const cfg = JSON.parse(fs.readFileSync('/inputs/config.json'));
const token = process.env.GOBLIN_KING_AUDIT_TOKEN;
const output = '/proof';
fs.mkdirSync(output, {recursive: true});
let diagnosticPage;

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--disable-dev-shm-usage']});
  const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
  diagnosticPage = page;
  const base = 'http://delivery-admin:8080';
  async function get(path) {
    const response = await page.request.get(base + '/admin-api' + path,
      {headers: {Authorization: 'Bearer ' + token}});
    if (!response.ok()) throw Error('Inspection failed: ' + path + ' ' + response.status());
    return response.json();
  }
  async function response(path, method, action) {
    const pending = page.waitForResponse(r => new URL(r.url()).pathname === '/admin-api' + path
      && r.request().method() === method, {timeout: 30000});
    await action();
    const result = await pending;
    if (!result.ok()) throw Error(path + ' ' + result.status() + ': ' + await result.text());
    return result.json();
  }
  async function settled(id) {
    const deadline = Date.now() + 900000;
    while (Date.now() < deadline) {
      const row = await get('/pipeline/operations/' + id);
      if (['completed', 'failed', 'held'].includes(row.status)) {
        if (row.status !== 'completed') throw Error('Networking operation ' + id + ': ' + row.status);
        return row;
      }
      await new Promise(resolve => setTimeout(resolve, 3000));
    }
    throw Error('Networking operation timed out: ' + id);
  }
  await page.goto(base + '/admin/');
  await page.getByLabel('API token', {exact: true}).fill(token);
  await page.getByRole('button', {name: 'Enter the lab', exact: true}).click();
  const panel = page.locator('#delivery');
  await panel.getByLabel('Project', {exact: true}).fill(cfg.project);
  const releases = await response('/pipeline/releases', 'GET', async () => {
    await panel.getByRole('button', {name: 'Inspect delivery', exact: true}).click();
  });
  if (releases.items.length !== 4) throw Error('Four releases are required');
  await panel.locator('summary').filter({hasText: 'Networking: ingress and egress'}).click();
  const network = panel.locator('details').filter({hasText: 'Configure approved application routes and outbound connections separately for each environment.'}).first();
  const inspected = {};
  async function inspect(key) {
    await network.getByLabel(/^Network release/).selectOption(key);
    return response('/pipeline/releases/' + key + '/networking', 'GET', () =>
      network.getByRole('button', {name: 'Inspect networking', exact: true}).click());
  }
  for (const release of releases.items) {
    inspected[release.key] = await inspect(release.key);
    await network.screenshot({path: output + '/inspect-' + release.document.environment.namespace + '.png'});
  }
  const key = cfg.release_key;
  const original = await inspect(key);
  const endpoint = '/pipeline/releases/' + key + '/networking';
  const operations = [];
  async function apply(name) {
    const preview = await response('/pipeline/networking/preview', 'POST', () =>
      network.getByRole('button', {name: 'Preview networking changes', exact: true}).click());
    await network.screenshot({path: output + '/' + name + '-preview.png'});
    const started = await response(endpoint, 'POST', () =>
      network.getByRole('button', {name: 'Apply networking changes', exact: true}).click());
    operations.push({name, preview, started, settled: await settled(started.operation_id)});
    const current = await inspect(key);
    if (!isDeepStrictEqual(current.observed.configuration, preview.configuration)) {
      fs.writeFileSync(output + '/configuration-mismatch.json', JSON.stringify({current, preview}, null, 2));
      throw Error('Observed UI configuration differs from its preview');
    }
    await network.screenshot({path: output + '/' + name + '-observed.png'});
    return current;
  }
  await network.getByLabel('Request timeout (seconds)', {exact: true}).fill('28');
  await apply('limits');
  await network.getByRole('button', {name: 'Disable ingress and egress in preview', exact: true}).click();
  const disabled = await apply('disabled');
  if (disabled.observed.configuration.ingress.enabled || disabled.observed.configuration.egress.enabled)
    throw Error('Disable form left networking enabled');
  await network.getByLabel('Enable ingress', {exact: true}).check();
  await network.getByLabel('Enable managed outbound access', {exact: true}).check();
  await network.getByLabel('Request timeout (seconds)', {exact: true}).fill(String(original.desired.ingress.timeout_seconds));
  await apply('restored');
  const probe = await response(endpoint + '/probe', 'POST', () =>
    network.getByRole('button', {name: 'Probe networking health', exact: true}).click());
  operations.push({name: 'probe', started: probe, settled: await settled(probe.operation_id)});
  const final = await inspect(key);
  const diagnostics = await response('/pipeline/networking/diagnostics', 'GET', () =>
    network.getByRole('button', {name: 'Inspect networking diagnostics', exact: true}).click());
  await network.screenshot({path: output + '/diagnostics.png'});
  fs.writeFileSync(output + '/result.json', JSON.stringify({completed: true,
    mode: 'container-headless', ...cfg, inspected, operations, final, diagnostics}, null, 2));
  await browser.close();
})().catch(async error => {
  if (diagnosticPage) {
    fs.writeFileSync(output + '/page.txt', await diagnosticPage.locator('body').innerText());
    await diagnosticPage.screenshot({path: output + '/failure.png'});
  }
  fs.writeFileSync(output + '/error.txt', String(error.stack || error));
  console.error(error);
}).finally(() => { setInterval(() => {}, 1000); });
