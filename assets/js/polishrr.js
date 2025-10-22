// ========================================================
//  Polishrr Dashboard (Refactored to modern best practices)
// ========================================================

(() => {
  "use strict";

  // --- === Global State === ---
  const state = {
    token: null,
    eventSource: null,
    tabs: ["all", "tagged", "eligible"],
    lastTab: "all",
  };

  // --- === DOM References === ---
  const els = {};
  const qs = (sel) => document.querySelector(sel);
  const qsa = (sel) => document.querySelectorAll(sel);

  // --- === Utility: Logging === ---
  const log = (level, msg) => {
    if (!els.logOutput) return;
    const prefixMap = {
      info: "[i] ",
      ok: "[✓] ",
      warn: "[!] ",
      start: "[*] ",
    };
    const prefix = prefixMap[level] ?? "[ ] ";
    els.logOutput.textContent += prefix + msg + "\n";
    els.logOutput.scrollTop = els.logOutput.scrollHeight;
  };

  // --- === Utility: API Fetch with timeout === ---
  async function apiFetch(path, options = {}, timeoutMs = 15000) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    const headers = {
      "Authorization": `Bearer ${state.token}`,
      "Content-Type": "application/json",
      ...(options.headers || {}),
    };
    try {
      const res = await fetch(path, { ...options, headers, signal: ctrl.signal });
      clearTimeout(t);
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(`HTTP ${res.status}: ${txt}`);
      }
      const ctype = res.headers.get("content-type") || "";
      if (ctype.includes("application/json")) return res.json();
      return res.text();
    } catch (err) {
      clearTimeout(t);
      log("warn", `API ${path} failed: ${err.message}`);
      throw err;
    }
  }

  // --- === Token Handling === ---
  function initToken() {
    let t = localStorage.getItem("POLISHRR_TOKEN");
    if (!t) {
      t = prompt("Enter your POLISHRR_TOKEN:");
      if (t) localStorage.setItem("POLISHRR_TOKEN", t);
    }
    if (!t) {
      alert("⚠️ No token provided — refresh and enter one to enable API calls.");
    }
    state.token = t;
  }

  // --- === Load Upgrade Summary === ---
  async function loadSummary() {
    try {
      const data = await apiFetch("/api/upgrade-summary");
      const r = data.radarr || {};
      const s = data.sonarr || {};
      els.summary.innerHTML = `
        🎞 <b>Radarr</b>: below cutoff <b>${r.total_below_cutoff ?? '-'}</b>,
        eligible <b>${r.eligible_for_upgrade ?? '-'}</b><br>
        📺 <b>Sonarr</b>: below cutoff <b>${s.total_below_cutoff ?? '-'}</b>,
        eligible <b>${s.eligible_for_upgrade ?? '-'}</b>
      `;
    } catch (e) {
      els.summary.textContent = `⚠️ Failed to load summary (${e.message})`;
    }
  }

  // --- === Queue Rendering === ---
  function renderQueueTable(title, items, headers, isEligible = false) {
    if (!Array.isArray(items) || !items.length) return "";

    // Optional: Vorinitialisierung sortieren, z. B. alphabetisch
    if (isEligible && state.lastSort) {
      const { index, asc } = state.lastSort;
      items.sort((a, b) => {
        const v1 = Object.values(a)[index] ?? "";
        const v2 = Object.values(b)[index] ?? "";
        return asc ? String(v1).localeCompare(String(v2))
                  : String(v2).localeCompare(String(v1));
      });
    }

    const rows = items.map((it) => {
      const status = (it.status || "").toLowerCase();
      const statusClass =
        status.includes("fail") ? "status-failed" :
        status.includes("download") ? "status-downloading" :
        status.includes("complete") ? "status-completed" : "";

      const cols = [];
      if (isEligible) {
        const id = it.id;
        if (!id) return "";
        const target = it.series ? "sonarr" : "radarr";
        cols.push(it.title ?? it.series ?? "-");
        cols.push(`<span class="${statusClass}">${it.status ?? "-"}</span>`);
        cols.push(`
          <button class="btn-mini upgrade-btn" data-id="${id}" data-target="${target}">Upgrade</button>
          <button class="btn-mini force-btn" data-id="${id}" data-target="${target}">Force</button>
        `);
      } else if (title.includes("Radarr")) {
        cols.push(it.title ?? "-");
        cols.push(`<span class="${statusClass}">${it.status ?? "-"}</span>`);
        cols.push(`${it.sizeleft?.toFixed?.(2) ?? "-"} GB`);
        cols.push(it.timeleft ?? "-");
        cols.push(it.indexer ?? "-");
      } else {
        cols.push(it.series ?? "-");
        cols.push(it.episode ?? "-");
        cols.push(`<span class="${statusClass}">${it.status ?? "-"}</span>`);
        cols.push(`${it.sizeleft?.toFixed?.(2) ?? "-"} GB`);
        cols.push(it.timeleft ?? "-");
        cols.push(it.indexer ?? "-");
      }
      return `<tr>${cols.map(c => `<td>${c}</td>`).join("")}</tr>`;
    }).join("");

    // 🔥 Neu: data-key pro Spalte + Sortieranzeige
    const ths = headers.map((h, i) => 
      `<th data-index="${i}" class="sortable-header">${h} <span class="sort-icon"></span></th>`
    ).join('');

    return `
      <div class="queue-table">
        <h3>${title}</h3>
        <div class="table-body-scroll">
          <table class="${isEligible ? "sortable" : ""}">
            <thead><tr>${ths}</tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>
    `;
  }

  // === Sortier-Event für klickbare Tabellen ===
function initTableSorting() {
  document.addEventListener("click", (e) => {
    const th = e.target.closest("th.sortable-header");
    if (!th) return;
    const table = th.closest("table.sortable");
    if (!table) return;

    const idx = parseInt(th.dataset.index, 10);
    const asc = !th.classList.contains("asc");

    // Alle Header-Icons resetten
    table.querySelectorAll("th").forEach(h => h.classList.remove("asc", "desc"));
    th.classList.add(asc ? "asc" : "desc");

    const rows = Array.from(table.querySelectorAll("tbody tr"));
    rows.sort((a, b) => {
      const t1 = a.children[idx].innerText.toLowerCase();
      const t2 = b.children[idx].innerText.toLowerCase();
      return asc ? t1.localeCompare(t2) : t2.localeCompare(t1);
    });

    rows.forEach(r => table.querySelector("tbody").appendChild(r));
    state.lastSort = { index: idx, asc }; // Speichern
  });
}


  // --- === Load Queue Data === ---
  async function loadQueue(tab = "all") {
    const targetDiv = qs(`#queue-${tab}`);
    const lastSort = state.lastSort;
    if (!targetDiv) return;
    try {
      const query = tab === "tagged" ? "?tagged=true" :
                    tab === "eligible" ? "?eligible=true" : "";
      const q = await apiFetch(`/api/download-queue${query}`);

      let html = "";
      if (tab === "eligible") {
        if (q.radarr?.length)
          html += renderQueueTable("🎞 Radarr Eligible", q.radarr, ["Title", "Status", "Actions"], true);
        if (q.sonarr?.length)
          html += renderQueueTable("📺 Sonarr Eligible", q.sonarr, ["Series", "Ep", "Status", "Actions"], true);
        if (!html) html = "✅ No eligible items.";
      } else {
        if (q.radarr?.length)
          html += renderQueueTable("🎞 Radarr", q.radarr, ["Title", "Status", "Left", "Time", "Indexer"]);
        if (q.sonarr?.length)
          html += renderQueueTable("📺 Sonarr", q.sonarr, ["Series", "Ep", "Status", "Left", "Time", "Indexer"]);
        if (!html) html = "✅ No active downloads.";
      }
      targetDiv.innerHTML = html;
      if (tab === "eligible" && lastSort) {
        const table = targetDiv.querySelector("table.sortable");
        if (table) {
          const { index, asc } = lastSort;
          const th = table.querySelectorAll("th")[index];
          if (th) th.classList.add(asc ? "asc" : "desc");

          const rows = Array.from(table.querySelectorAll("tbody tr"));
          rows.sort((a, b) => {
            const t1 = a.children[index].innerText.toLowerCase();
            const t2 = b.children[index].innerText.toLowerCase();
            return asc ? t1.localeCompare(t2) : t2.localeCompare(t1);
          });
          rows.forEach(r => table.querySelector("tbody").appendChild(r));
        }
      }
    } catch (e) {
      targetDiv.textContent = `⚠️ Failed to load queue (${e.message})`;
    }
  }

  // --- === Upgrade/Force Actions === ---
  async function handleUpgrade(target, id, force = false) {
    const endpoint = force ? "/api/force-upgrade-item" : "/api/upgrade-item";
    const action = force ? "Force upgrade" : "Upgrade";
    try {
      log("start", `${action} → ${target} (ID ${id})`);
      const res = await apiFetch(endpoint, {
        method: "POST",
        body: JSON.stringify({ id, target }),
      });
      if (res.ok || res.ok === true) log("ok", `${action} started`);
      else log("warn", `${action} failed`);
    } catch (e) {
      log("warn", `${action} failed: ${e.message}`);
    }
  }

  // --- === EventSource Setup === ---
  function initEventStream() {
    const es = new EventSource("/api/events");
    es.addEventListener("info", (e) => log("info", e.data));
    es.addEventListener("error", (e) => log("warn", e.data));
    es.addEventListener("done", (e) => {
      log("ok", e.data);
      loadSummary();
      loadQueue(state.lastTab);
    });
    es.onerror = () => log("warn", "Event stream disconnected.");
    state.eventSource = es;
  }

  // --- === Trigger Upgrade === ---
  async function triggerUpgrade(target = "both") {
    log("start", `Triggering ${target} upgrade...`);
    try {
      await apiFetch("/api/trigger", {
        method: "POST",
        body: JSON.stringify({ target  }),
      });
      log("ok", `${target} trigger accepted.`);
    } catch (e) {
      log("warn", `${target} trigger failed: ${e.message}`);
    }
  }

  // --- === Settings === ---
  async function loadSettings() {
    try {
      const s = await apiFetch("/api/settings");
      qs("#cron-input").value = s.cron ?? "";
      qs("#chk-radarr").checked = !!s.process_radarr;
      qs("#chk-sonarr").checked = !!s.process_sonarr;
      qs("#num-movies").value = s.num_movies ?? 1;
      qs("#num-episodes").value = s.num_episodes ?? 1;
      qs("#chk-force").checked = !!s.force_enabled;
    } catch {
      log("warn", "Failed to load settings");
    }
  }

  async function saveSettings() {
    const cron = qs("#cron-input").value.trim();
    if (!/^(\S+\s+){4}\S+$/.test(cron)) {
      alert("⚠️ Invalid cron expression (e.g. */5 * * * *).");
      return;
    }
    const settings = {
      cron,
      process_radarr: qs("#chk-radarr").checked,
      process_sonarr: qs("#chk-sonarr").checked,
      num_movies: parseInt(qs("#num-movies").value, 10),
      num_episodes: parseInt(qs("#num-episodes").value, 10),
      force_enabled: qs("#chk-force").checked,
    };
    try {
      await apiFetch("/api/settings", {
        method: "POST",
        body: JSON.stringify(settings),
      });
      log("ok", "Settings saved");
    } catch {
      log("warn", "Settings save failed");
    }
  }

  // --- === Tabs === ---
  function activateTab(targetId) {
    state.lastTab = targetId.replace("queue-", "");
    qsa(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.target === targetId));
    qsa(".queue-tab").forEach(p => p.classList.toggle("active", p.id === targetId));
    loadQueue(state.lastTab);
  }

  // --- === Init Dashboard === ---
function initDashboard() {
  els.logOutput = qs("#log-output");
  els.summary = qs("#upgrade-summary");
  els.triggerBtn = qs("#trigger-upgrade");
  els.triggerRadarr  = qs("#trigger-radarr");
  els.triggerSonarr  = qs("#trigger-sonarr");
  initToken();
  initEventStream();

  // Bind Events
  els.triggerUpgrade.addEventListener("click", () => triggerUpgrade("both"));
  els.triggerRadarr.addEventListener("click", () => triggerUpgrade("radarr"));
  els.triggerSonarr.addEventListener("click", () => triggerUpgrade("sonarr"));
  qsa(".tab-btn").forEach(btn =>
    btn.addEventListener("click", () => activateTab(btn.dataset.target))
  );
  qs("#save-settings").addEventListener("click", saveSettings);
  qs("#test-cron").addEventListener("click", () => {
    const cron = qs("#cron-input").value.trim();
    alert(/^(\S+\s+){4}\S+$/.test(cron)
      ? "✅ Cron expression looks valid!"
      : "⚠️ Invalid cron expression.");
  });

  // Global upgrade buttons (delegated)
  document.addEventListener("click", (e) => {
    if (e.target.classList.contains("upgrade-btn")) {
      handleUpgrade(e.target.dataset.target, e.target.dataset.id, false);
    } else if (e.target.classList.contains("force-btn")) {
      if (confirm("⚠️ Force upgrade will delete existing file. Continue?"))
        handleUpgrade(e.target.dataset.target, e.target.dataset.id, true);
    }
  });

  // ✅ Tabellen-Sortierung einmalig initialisieren
  initTableSorting();

  // Initial loads
  loadSummary();
  loadQueue("all");
  loadSettings();
  activateTab("queue-all");

  // Refresh loops
  setInterval(loadSummary, 60000);
  setInterval(() => loadQueue(state.lastTab), 15000);
}


  // Initialize after DOM ready
  document.addEventListener("DOMContentLoaded", initDashboard);
})();
