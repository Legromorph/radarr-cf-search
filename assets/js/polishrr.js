// ==============================
//  Polishrr Status Dashboard JS
// ==============================

// --- Element Refs ---
const logOutput = document.getElementById('log-output');
const summaryDiv = document.getElementById('upgrade-summary');
const triggerBtn = document.getElementById('trigger-upgrade');

// --- Token Setup ---
let TOKEN = localStorage.getItem("POLISHRR_TOKEN");
if (!TOKEN) {
  TOKEN = prompt("Enter your POLISHRR_TOKEN:");
  if (TOKEN) localStorage.setItem("POLISHRR_TOKEN", TOKEN);
}
if (!TOKEN) alert("⚠️ No token provided — API calls will fail until you refresh and enter one.");

// --- Utility: Append log lines ---
function logLine(prefix, text) {
  logOutput.textContent += prefix + text + "\n";
  logOutput.scrollTop = logOutput.scrollHeight;
}

// --- Load upgrade summary ---
async function loadSummary() {
  try {
    const res = await fetch('/api/upgrade-summary', {
      headers: { 'Authorization': `Bearer ${TOKEN}` }
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const data = await res.json();
    const radarr = data.radarr || {};
    const sonarr = data.sonarr || {};

    summaryDiv.innerHTML = `
      🎞 <b>Radarr</b>: below cutoff <b>${radarr.total_below_cutoff ?? '-'}</b>,
      eligible <b>${radarr.eligible_for_upgrade ?? '-'}</b><br>
      📺 <b>Sonarr</b>: below cutoff <b>${sonarr.total_below_cutoff ?? '-'}</b>,
      eligible <b>${sonarr.eligible_for_upgrade ?? '-'}</b>
    `;
  } catch (err) {
    summaryDiv.textContent = `⚠️ Failed to load summary (${err.message})`;
  }
}

// --- Load download queue ---
async function loadQueue(tab = "all") {
  let url = "/api/download-queue";
  if (tab === "tagged") url += "?tagged=true";
  if (tab === "eligible") url += "?eligible=true";

  const targetDiv = document.getElementById(`queue-${tab}`);
  if (!targetDiv) return;

  try {
    const res = await fetch(url, {
      headers: { 'Authorization': `Bearer ${TOKEN}` }
    });
    if (!res.ok) throw new Error(res.status + ' ' + res.statusText);
    const q = await res.json();

    let html = "";
    if (q.radarr?.length)
      html += renderQueueTable('🎞 Radarr', q.radarr, ['Title', 'Status', 'Left', 'Time', 'Indexer']);
    if (q.sonarr?.length)
      html += renderQueueTable('📺 Sonarr', q.sonarr, ['Series', 'Ep', 'Status', 'Left', 'Time', 'Indexer']);
    if (!html) html = "✅ No active downloads.";

    targetDiv.innerHTML = html;
  } catch (err) {
    targetDiv.textContent = `⚠️ Failed to load queue (${err.message})`;
  }
}

// --- Render helper ---
function renderQueueTable(title, items, headers) {
  let html = `<div class="queue-table"><h3>${title}</h3>`;
  html += '<table><thead><tr>';

  // Header row
  for (const h of headers) {
    html += `<th>${h}</th>`;
  }
  html += '</tr></thead><tbody>';

  // Rows
  for (const item of items) {
    const status = item.status?.toLowerCase() || '';
    const statusClass =
      status.includes('fail') ? 'status-failed' :
      status.includes('download') ? 'status-downloading' :
      status.includes('complete') ? 'status-completed' : '';

    const cols = [];

    // Flexible mapping based on table type
    if (title.includes('Radarr')) {
      cols.push(item.title ?? '-');
      cols.push(`<span class="${statusClass}">${item.status ?? '-'}</span>`);
      cols.push(`${item.sizeleft?.toFixed?.(2) ?? '-'} GB`);
      cols.push(item.timeleft ?? '-');
      cols.push(item.indexer ?? '-');
    } else {
      cols.push(item.series ?? '-');
      cols.push(item.episode ?? '-');
      cols.push(`<span class="${statusClass}">${item.status ?? '-'}</span>`);
      cols.push(`${item.sizeleft?.toFixed?.(2) ?? '-'} GB`);
      cols.push(item.timeleft ?? '-');
      cols.push(item.indexer ?? '-');
    }

    html += '<tr>' + cols.map(c => `<td>${c}</td>`).join('') + '</tr>';
  }

  html += '</tbody></table></div>';
  return html;
}

// --- Trigger button ---
triggerBtn.onclick = async () => {
  logLine("[*] ", "Triggering upgrade...");
  const res = await fetch('/api/trigger', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${TOKEN}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ target: 'both' })
  });
  if (!res.ok) {
    logLine("[!] ", "Trigger failed: " + res.status);
  } else {
    logLine("[✓] ", "Trigger accepted.");
  }
};

// --- Live Event Stream ---
const es = new EventSource('/api/events');
es.addEventListener('info', e => logLine("[i] ", e.data));
es.addEventListener('error', e => logLine("[!] ", e.data));
es.addEventListener('done', e => {
  logLine("[✓] ", e.data);
  loadSummary();
  loadQueue();
});
es.onmessage = e => logLine("", e.data);
es.onerror = () => logLine("[!] ", "Event stream disconnected.");

// --- Initial Load ---
loadSummary();
loadQueue();
setInterval(loadSummary, 60000);
setInterval(loadQueue, 15000);

// ==============================
//  Tabs & Dynamic Content
// ==============================
const tabBtns = document.querySelectorAll('.tab-btn');
const tabPanels = document.querySelectorAll('.queue-tab');

function activateTab(targetId) {
  // Buttons aktiv
  tabBtns.forEach(b => b.classList.toggle('active', b.dataset.target === targetId));
  // Panels anzeigen/verstecken
  tabPanels.forEach(p => p.classList.toggle('active', p.id === targetId));

  // Daten für das aktive Panel laden
  if (targetId === 'queue-all') {
    loadQueue('all');
  } else if (targetId === 'queue-tagged') {
    loadQueue('tagged');
  } else if (targetId === 'queue-eligible') {
    loadQueue('eligible');
  }
}

// Klick-Handler
tabBtns.forEach(btn => {
  btn.addEventListener('click', () => activateTab(btn.dataset.target));
});

// Initial: "Alle"
activateTab('queue-all');
