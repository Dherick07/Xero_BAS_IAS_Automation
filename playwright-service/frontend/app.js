// frontend/app.js — Unified IAS/BAS Xero Reports Automation
const API = {
  key: null,

  async init() {
    const cfg = await this.get('/api/ui-config');
    this.key = cfg.api_key;
  },

  headers() {
    return { 'Content-Type': 'application/json', 'X-API-Key': this.key };
  },

  async get(path) {
    const r = await fetch(path, { headers: this.headers() });
    if (!r.ok) throw new Error(`GET ${path} failed: ${r.status}`);
    return r.json();
  },

  async post(path, body) {
    const r = await fetch(path, { method: 'POST', headers: this.headers(), body: JSON.stringify(body) });
    if (!r.ok) throw new Error(`POST ${path} failed: ${r.status}`);
    return r.json();
  },
};

// DOM helpers
const $ = id => document.getElementById(id);

// --- State ---
let currentJobId = null;
let pollInterval = null;
let clientMap = {};  // tenant_name → full client object
let isLoggedIn = false;
let reportType = 'ias';  // 'ias' or 'bas'

// --- Initialization ---
document.addEventListener('DOMContentLoaded', async () => {
  await API.init();
  loadAuthStatus();
  loadClients();
  loadRecentRuns();
  populateYears();
  $('run-btn').addEventListener('click', handleRun);
  $('login-btn').onclick = handleLogin;

  // Report type toggle
  $('btn-ias').addEventListener('click', () => setReportType('ias'));
  $('btn-bas').addEventListener('click', () => setReportType('bas'));

  // Show client info when org is selected (useful for BAS)
  $('org-input').addEventListener('change', showClientInfo);
  $('org-input').addEventListener('input', showClientInfo);
});

// --- Report Type Toggle ---
function setReportType(type) {
  reportType = type;

  // Update toggle buttons
  $('btn-ias').className = type === 'ias' ? 'toggle-btn active' : 'toggle-btn';
  $('btn-bas').className = type === 'bas' ? 'toggle-btn active' : 'toggle-btn';

  // Show/hide period selectors
  $('month-group').style.display = type === 'ias' ? '' : 'none';
  $('quarter-group').style.display = type === 'bas' ? '' : 'none';

  // Reload clients filtered by report type
  loadClients();

  // Update client info display
  showClientInfo();
}

function showClientInfo() {
  const infoEl = $('client-info');
  const orgName = $('org-input').value.trim();
  const client = clientMap[orgName];

  if (reportType === 'bas' && client) {
    const gst = client.gst_accounting_method || 'Not set';
    const paygi = client.paygi_frequency || 'Not set';
    infoEl.innerHTML = `<strong>GST Method:</strong> ${gst} &nbsp;|&nbsp; <strong>PAYGI:</strong> ${paygi}`;
    infoEl.style.display = 'block';
  } else {
    infoEl.style.display = 'none';
  }
}

// --- Auth ---
async function loadAuthStatus() {
  try {
    const status = await API.get('/api/auth/status');
    isLoggedIn = status.logged_in;
    const badge = $('auth-badge');
    if (status.logged_in) {
      badge.className = 'status-badge ok';
      badge.innerHTML = '<span class="dot"></span> Logged In';
      $('login-btn').style.display = 'none';
      $('run-btn').disabled = false;
    } else {
      badge.className = 'status-badge error';
      badge.innerHTML = '<span class="dot"></span> Not Logged In';
      $('login-btn').style.display = '';
      $('run-btn').disabled = true;
    }
  } catch {
    isLoggedIn = false;
    $('auth-badge').className = 'status-badge error';
    $('auth-badge').innerHTML = '<span class="dot"></span> Unknown';
    $('login-btn').style.display = '';
    $('run-btn').disabled = true;
  }
}

async function handleLogin() {
  const btn = $('login-btn');
  btn.disabled = true;
  btn.textContent = 'Opening Xero...';
  hideLoginInstruction();
  try {
    await API.post('/api/auth/setup', {});
    btn.textContent = "I've Logged In";
    btn.onclick = handleCompleteLogin;
    btn.disabled = false;
    showLoginInstruction('A Xero login page has been opened in Chrome. Log in, then click "I\'ve Logged In".');
  } catch (e) {
    console.error('Setup failed:', e);
    btn.textContent = 'Login';
    btn.disabled = false;
    showLoginInstruction('Failed to open Xero login. Check the server is running and try again.', true);
  }
}

async function handleCompleteLogin() {
  const btn = $('login-btn');
  btn.disabled = true;
  btn.textContent = 'Verifying...';
  try {
    const result = await API.post('/api/auth/complete', {});
    if (result.success) {
      hideLoginInstruction();
      await loadAuthStatus();
      btn.onclick = handleLogin;
    } else {
      btn.textContent = "I've Logged In";
      btn.disabled = false;
      showLoginInstruction('Login not detected yet — please finish logging in and try again.', true);
    }
  } catch (e) {
    console.error('Complete login failed:', e);
    btn.textContent = "I've Logged In";
    btn.disabled = false;
    showLoginInstruction('Error verifying login. Please try again.', true);
  }
}

function showLoginInstruction(msg, isError = false) {
  const el = $('login-instruction');
  el.textContent = msg;
  el.className = isError ? 'login-hint error' : 'login-hint';
  el.style.display = 'block';
}

function hideLoginInstruction() {
  const el = $('login-instruction');
  el.style.display = 'none';
  el.textContent = '';
}

// --- Clients ---
async function loadClients() {
  try {
    const data = await API.get(`/api/clients/?report_type=${reportType}`);
    const datalist = $('clients-list');
    datalist.innerHTML = '';
    clientMap = {};
    data.clients.forEach(c => {
      clientMap[c.tenant_name] = c;  // Store full client object
      const opt = document.createElement('option');
      opt.value = c.tenant_name;
      datalist.appendChild(opt);
    });
  } catch (e) {
    console.error('Failed to load clients:', e);
  }
}

function populateYears() {
  const sel = $('year-select');
  const current = new Date().getFullYear();
  for (let y = current - 2; y <= current + 1; y++) {
    const opt = document.createElement('option');
    opt.value = y;
    opt.textContent = y;
    if (y === current) opt.selected = true;
    sel.appendChild(opt);
  }
}

// --- Recent Runs ---
async function loadRecentRuns() {
  try {
    const data = await API.get('/api/reports/logs?limit=10');
    const tbody = $('runs-tbody');
    if (!data.logs || data.logs.length === 0) {
      tbody.innerHTML = '<tr class="empty-row"><td colspan="4">No runs yet</td></tr>';
      return;
    }
    tbody.innerHTML = data.logs.map(log => {
      const when = log.started_at ? formatCompactDate(new Date(log.started_at)) : '—';
      const statusClass = log.status === 'success' ? 'success' : log.status === 'failed' ? 'failed' : 'running';
      const name = log.file_name || '—';
      const mode = (log.report_mode || '—').toUpperCase();
      return `<tr>
        <td title="${name}">${name}</td>
        <td><span class="badge type-${(log.report_mode || 'ias').toLowerCase()}">${mode}</span></td>
        <td><span class="badge ${statusClass}">${log.status}</span></td>
        <td>${when}</td>
      </tr>`;
    }).join('');
  } catch (e) {
    console.error('Failed to load recent runs:', e);
  }
}

// --- Run handler ---
async function handleRun() {
  const orgInput = $('org-input').value.trim();
  const year = parseInt($('year-select').value);

  // Get month based on report type
  const month = reportType === 'bas'
    ? parseInt($('quarter-select').value)
    : parseInt($('month-select').value);

  if (!orgInput) { alert('Please select an organisation.'); return; }
  if (!clientMap[orgInput]) { alert('Organisation not found in the list. Please select from the dropdown.'); return; }

  const client = clientMap[orgInput];

  $('run-btn').disabled = true;
  $('run-btn').textContent = 'Running...';
  showProgress();
  clearSteps();

  const modeLabel = reportType.toUpperCase();
  addStep(`Starting ${modeLabel} report for ${orgInput}...`);

  try {
    const job = await API.post('/api/reports/run', {
      tenant_id: client.tenant_id,
      tenant_name: orgInput,
      tenant_shortcode: client.tenant_shortcode,
      month: month,
      year: year,
      find_unfiled: false,
      report_type: reportType,
    });
    currentJobId = job.job_id;
    startPolling();
  } catch (e) {
    addStep(`Error: ${e.message}`, 'error');
    finishRun(false, null);
  }
}

function startPolling() {
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(async () => {
    try {
      const job = await API.get(`/api/reports/job/${currentJobId}`);
      syncSteps(job.steps, job.status);

      if (job.status !== 'running') {
        clearInterval(pollInterval);
        pollInterval = null;
        const success = job.status === 'success';
        const fileName = job.result?.consolidated_file?.file_name || null;
        finishRun(success, fileName, job.result?.errors);
        loadRecentRuns();
      }
    } catch (e) {
      clearInterval(pollInterval);
      addStep('Could not contact server. Check if the app is still running.', 'error');
      finishRun(false, null);
    }
  }, 3000);
}

// --- UI helpers ---
function formatCompactDate(date) {
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const month = months[date.getMonth()];
  const day = date.getDate();
  let hours = date.getHours();
  const minutes = date.getMinutes().toString().padStart(2, '0');
  const ampm = hours >= 12 ? 'PM' : 'AM';
  hours = hours % 12 || 12;
  return `${month} ${day}, ${hours}:${minutes} ${ampm}`;
}

let renderedSteps = 0;

function showProgress() {
  $('progress-section').style.display = 'block';
  $('result-box').style.display = 'none';
  renderedSteps = 0;
}

function clearSteps() {
  $('step-log').innerHTML = '';
  renderedSteps = 0;
}

function syncSteps(steps, status) {
  for (let i = renderedSteps; i < steps.length; i++) {
    const isDone = status !== 'running' && i === steps.length - 1;
    addStep(steps[i], isDone ? (status === 'success' ? 'done' : 'error') : '');
  }
  renderedSteps = steps.length;
}

function addStep(msg, cls = '') {
  const li = document.createElement('li');
  if (cls) li.className = cls;
  li.textContent = msg;
  $('step-log').appendChild(li);
}

function finishRun(success, fileName, errors) {
  $('run-btn').disabled = !isLoggedIn;
  $('run-btn').textContent = 'Run Report';
  const box = $('result-box');
  box.style.display = 'block';
  if (success) {
    box.className = 'result-box success';
    box.textContent = fileName ? `Done! File saved: ${fileName}` : 'Done!';
  } else {
    box.className = 'result-box failure';
    const errMsg = errors && errors.length ? errors.join('; ') : 'The report job failed. Check the step log above.';
    box.textContent = `Failed: ${errMsg}`;
  }
}
