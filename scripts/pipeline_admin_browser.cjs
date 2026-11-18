const { chromium } = require('/audit/node_modules/playwright');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const config = JSON.parse(fs.readFileSync(process.env.AUDIT_CONFIG || '/config.json'));
const output = process.env.AUDIT_OUTPUT || '/proof';
fs.mkdirSync(output, {recursive:true});
const checksum = p => crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
function retained(name) { return {path:name,sha256:checksum(path.join(output,name))}; }
function write(name,value) {
  fs.writeFileSync(path.join(output,name),typeof value==='string'?value:JSON.stringify(value,null,2));
  return retained(name);
}
const recordPath = path.join(output,'browser-proof.json');
const record = fs.existsSync(recordPath) ? JSON.parse(fs.readFileSync(recordPath)) : {
  runtime:config.runtime,revision:config.revision,source_sha256:config.source_sha256,
  acceptance_run_id:config.acceptance_run_id,api_base_url:config.api_base_url,
  browser_base_url:config.browser_base_url,mode:'container-headless',jobs:{},screenshots:{},service_probes:[]
};
for (const key of ['runtime','revision','source_sha256','acceptance_run_id']) {
  if (record[key] !== config[key]) throw Error('Use a fresh audit directory for a different ' + key);
}
if (fs.existsSync(path.join(output,'browser-error.txt'))) {
  fs.renameSync(path.join(output,'browser-error.txt'), path.join(output,'browser-error-'+Date.now()+'.txt'));
}
const save = () => write('browser-proof.json',record);
const expectedFailures = new Set(['example.controlled-failure','example.behavior-shell-failure']);
let browser;
(async()=>{
  browser = await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});
  const context = await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  await page.goto(config.browser_base_url+'/admin/');
  await page.getByLabel('API token',{exact:true}).fill(process.env.GOBLIN_KING_AUDIT_TOKEN);
  await page.getByRole('button',{name:'Enter the lab',exact:true}).click();
  const lab = page.locator('#goblin-lab');
  await lab.locator('select option').first().waitFor({state:'attached'});
  const kinds = await lab.locator('select option').allTextContents();
  write('registered-kinds.json',kinds);
  await lab.scrollIntoViewIfNeeded();
  await lab.locator('select').click();
  await page.screenshot({path:path.join(output,'kinds.png')});
  await page.keyboard.press('Escape');
  record.screenshots.kinds=retained('kinds.png');
  write('kinds.txt',await lab.ariaSnapshot());
  for (const kind of kinds.filter(x=>x!=='example.long-hello')) {
    if(record.jobs[kind])continue;
    await lab.locator('select').selectOption(kind);
    await lab.locator('textarea').fill(JSON.stringify({message:'admin runtime audit',name:'Audit',target:'Audit',value:'Audit'}));
    const responsePromise=page.waitForResponse(r=>new URL(r.url()).pathname==='/admin-api/jobs' && r.request().method()==='POST');
    await lab.getByRole('button',{name:'Submit job',exact:true}).click();
    const response=await responsePromise;
    const job=await response.json();
    if(!response.ok()||!job.id)throw Error('UI submission failed: '+kind+' '+JSON.stringify(job));
    await page.locator('#task-board code').filter({hasText:job.id}).waitFor();
    record.jobs[kind]={job_id:job.id,downloads:{},browser_submission:write('submission-'+kind+'.txt',await page.locator('body').innerText())};
    save();
    console.log(JSON.stringify({kind,job_id:job.id,submitted_by:'browser button'}));
  }
  let runs=[];
  const deadline=Date.now()+20*60*1000;
  while(Date.now()<deadline) {
    const responsePromise=page.waitForResponse(r=>new URL(r.url()).pathname==='/admin-api/runs' && r.request().method()==='GET');
    await page.getByRole('button',{name:'Refresh all',exact:true}).click();
    runs=(await (await responsePromise).json()).items;
    const terminal=Object.values(record.jobs).every(entry=>runs.some(run=>run.job_id===entry.job_id && ['completed','failed','timed_out','cancelled'].includes(run.status)));
    if(terminal)break;
    await page.waitForTimeout(3000);
  }
  for(const [kind,entry] of Object.entries(record.jobs)) {
    const run=runs.find(r=>r.job_id===entry.job_id && ['completed','failed','timed_out','cancelled'].includes(r.status));
    const expected=expectedFailures.has(kind)?'failed':'completed';
    if(!run||run.status!==expected)throw Error('Unexpected runtime result '+kind+' '+JSON.stringify(run));
    if(expected==='failed'&&!String(run.result?.error).toLowerCase().includes('controlled failure'))throw Error('Missing controlled-failure diagnostic');
    entry.run_id=run.id;
    await page.locator('#runs').getByText(run.id,{exact:true}).waitFor();
    write('result-'+kind+'.json',run);
    entry.browser_result=write('result-'+kind+'.txt',await page.locator('#runs').innerText());
    for(const artifact of run.result?.artifacts||[]) {
      await page.locator('#runs input[placeholder="run id"]').fill(run.id);
      await page.getByRole('button',{name:'Inspect artifacts',exact:true}).click();
      const button=page.getByRole('button',{name:'Download '+artifact.name,exact:true});
      await button.waitFor();
      const downloadPromise=page.waitForEvent('download');
      await button.click();
      const download=await downloadPromise;
      const name='download-'+kind+'-'+artifact.name.replace(/[^A-Za-z0-9_.-]/g,'_');
      await download.saveAs(path.join(output,name));
      if(await download.failure())throw Error('Browser download failed');
      entry.downloads[artifact.name]=retained(name);
      await button.scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(output,'artifacts.png')});
      record.screenshots.artifacts=retained('artifacts.png');
    }
    save();
  }
  await page.locator('#runs h3').first().scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'runs.png')});
  record.screenshots.runs=retained('runs.png');
  await page.locator('#events').getByRole('heading',{name:'Durable Events',exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'events.png')});
  record.screenshots.events=retained('events.png');
  await page.getByRole('heading',{name:'Heartbeats',exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'heartbeats.png')});
  record.screenshots.heartbeats=retained('heartbeats.png');
  await page.getByLabel('Long service URL',{exact:true}).fill(config.long_service_url);
  const registerPromise=page.waitForResponse(r=>new URL(r.url()).pathname==='/admin-api/services/long-running' && r.request().method()==='POST');
  await page.getByRole('button',{name:'Register service',exact:true}).click();
  const registered=await registerPromise;
  if(!registered.ok())throw Error('Service registration failed');
  record.service_probes=[];
  for(let index=0;index<2;index++) {
    const probePromise=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/probe') && r.request().method()==='POST');
    await page.locator('#services').getByRole('button',{name:'Probe',exact:true}).first().click();
    const response=await probePromise;
    const probe=await response.json();
    const status=probe.response?.status??probe.response?.status_code;
    const timestamp=probe.response?.json?.timestamp;
    if(!response.ok()||status!==200||!timestamp)throw Error('Service probe failed: '+JSON.stringify(probe));
    record.service_probes.push(write('service-probe-'+index+'.json',{status,timestamp,response:probe}));
    await page.waitForTimeout(1100);
  }
  await page.locator('#services').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'services.png')});
  record.screenshots.services=retained('services.png');
  write('services.txt',await page.locator('#services').innerText());
  write('events-and-heartbeats.txt',await page.locator('#events').innerText());
  record.completed=true;
  save();
  console.log(JSON.stringify({runtime:config.runtime,acceptance_run_id:config.acceptance_run_id,browser_jobs:Object.keys(record.jobs).length,completed:true}));
  await browser.close();
})().catch(async error=>{
  write('browser-error.txt',String(error.stack||error));
  console.error(String(error.stack||error));
  if(browser)await browser.close();
  process.exitCode=1;
});
