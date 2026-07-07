/* Molnfrontend: schemaformulär + 7-dagars tidslinje (Chart.js floating bars).
   Multi-tenant: allt enhetsdata hämtas under /api/devices/{deviceId}/…;
   401 skickar till inloggningen. */
"use strict";

const VALVES = [1, 2];
const DAYS = 7;
const MS_PER_HOUR = 3600 * 1000;

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/* Backend skickar naiv UTC ("2026-07-05T04:30:00") — tvinga UTC-tolkning. */
function parseUtc(ts) {
  return new Date(/Z|[+-]\d{2}:\d{2}$/.test(ts) ? ts : ts + "Z");
}

function fmtTime(d) {
  return d.toLocaleTimeString("sv-SE", { hour: "2-digit", minute: "2-digit" });
}

function fmtDate(d) {
  return d.toLocaleDateString("sv-SE", { weekday: "short", day: "numeric", month: "numeric" });
}

/* ---------- Enhetsval & API-hjälpare ---------- */

let deviceId = null;
let devices = [];

/* fetch som skickar till inloggningen vid 401 (utgången session). */
async function authFetch(url, opts) {
  const r = await fetch(url, opts);
  if (r.status === 401) {
    location.href = "/login.html";
    throw new Error("inte inloggad");
  }
  return r;
}

/* Alla enhets-endpoints bor under /api/devices/{id}/… */
function api(path, opts) {
  return authFetch(`/api/devices/${deviceId}${path}`, opts);
}

const deviceSelect = document.getElementById("device-select");
const deviceName = document.getElementById("device-name");
const deviceStatus = document.getElementById("device-status");

function fmtLastSeen(ts) {
  const d = parseUtc(ts);
  const today = new Date();
  return d.toDateString() === today.toDateString()
    ? fmtTime(d)
    : `${fmtDate(d)} ${fmtTime(d)}`;
}

function renderDeviceBar() {
  deviceSelect.innerHTML = "";
  for (const d of devices) {
    const opt = document.createElement("option");
    opt.value = d.id;
    opt.textContent = d.name + (d.online ? "" : " (offline)");
    opt.selected = d.id === deviceId;
    deviceSelect.appendChild(opt);
  }
  deviceSelect.hidden = devices.length < 2;
  const current = devices.find((d) => d.id === deviceId);
  deviceName.textContent = devices.length === 1 && current ? current.name : "";
  if (current) {
    deviceStatus.className = "dev-status " + (current.online ? "online" : "offline");
    deviceStatus.textContent = current.online
      ? "online"
      : "offline" + (current.last_seen ? ` — senast ${fmtLastSeen(current.last_seen)}` : "");
  } else {
    deviceStatus.textContent = "";
  }
}

async function refreshDevices() {
  const r = await authFetch("/api/devices");
  devices = await r.json();
  const stored = localStorage.getItem("deviceId");
  if (!devices.some((d) => d.id === deviceId)) {
    deviceId = devices.some((d) => d.id === stored) ? stored : (devices[0]?.id ?? null);
  }
  if (deviceId) localStorage.setItem("deviceId", deviceId);
  renderDeviceBar();
}

function loadAllForDevice() {
  document.querySelectorAll("form[data-valve]").forEach((form) => loadSchedule(form));
  loadHistory();
  loadIrrigation();
  loadSensor();
}

deviceSelect.addEventListener("change", () => {
  deviceId = deviceSelect.value;
  localStorage.setItem("deviceId", deviceId);
  renderDeviceBar();
  loadAllForDevice();
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" });
  location.href = "/login.html";
});

/* ---------- "Lägg till enhet"-modal ---------- */

const addModal = document.getElementById("add-device-modal");
const claimCodeEl = document.getElementById("claim-code");
const claimStatus = document.getElementById("claim-status");
let claimPoll = null;

async function openAddDevice() {
  claimCodeEl.textContent = "……";
  claimStatus.className = "muted";
  claimStatus.textContent = "Hämtar kod …";
  addModal.showModal();
  try {
    const r = await authFetch("/api/claim-codes", { method: "POST" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const claim = await r.json();
    claimCodeEl.textContent = claim.code;
    claimStatus.textContent = "Väntar på enheten …";
  } catch (e) {
    claimStatus.className = "save-status err";
    claimStatus.textContent = `Kunde inte hämta kod: ${e.message}`;
    return;
  }
  const known = new Set(devices.map((d) => d.id));
  claimPoll = setInterval(async () => {
    try {
      const r = await authFetch("/api/devices");
      const list = await r.json();
      const fresh = list.find((d) => !known.has(d.id));
      if (fresh) {
        clearInterval(claimPoll);
        claimPoll = null;
        devices = list;
        deviceId = fresh.id;
        localStorage.setItem("deviceId", deviceId);
        renderDeviceBar();
        loadAllForDevice();
        claimStatus.className = "save-status ok";
        claimStatus.textContent = "Enheten är ansluten ✓";
      }
    } catch {
      /* nätverksglapp — försök igen nästa varv */
    }
  }, 3000);
}

document.getElementById("add-device-btn").addEventListener("click", openAddDevice);
document.getElementById("claim-close").addEventListener("click", () => {
  if (claimPoll) clearInterval(claimPoll);
  claimPoll = null;
  addModal.close();
});

/* ---------- Schema (lista av bevattningar per ventil, max 6) ---------- */

const MAX_ENTRIES = 6;

function entryRow(entry) {
  const row = document.createElement("div");
  row.className = "entry-row";
  row.innerHTML =
    '<input type="time" name="start" required>' +
    '<input type="number" name="duration_min" min="1" max="180" required>' +
    '<input type="checkbox" name="enabled">' +
    '<button type="button" class="remove" title="Ta bort" aria-label="Ta bort">✕</button>';
  row.querySelector('[name="start"]').value = entry.start;
  row.querySelector('[name="duration_min"]').value = entry.duration_min;
  row.querySelector('[name="enabled"]').checked = entry.enabled;
  row.querySelector(".remove").addEventListener("click", () => row.remove());
  return row;
}

function renderEntries(form, entries) {
  const box = form.querySelector(".entries");
  box.innerHTML = "";
  for (const entry of entries) box.appendChild(entryRow(entry));
}

async function loadSchedule(form) {
  if (!deviceId) return null;
  const id = form.dataset.valve;
  try {
    const r = await api(`/valves/${id}/schedule`);
    if (!r.ok) return null;
    const s = await r.json();
    renderEntries(form, s);
    return s;
  } catch {
    return null;
  }
}

/* Som loadSchedule men rör inte formuläret (används vid eko-pollning). */
async function fetchSchedule(id) {
  try {
    const r = await api(`/valves/${id}/schedule`);
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

function scheduleFromForm(form) {
  return [...form.querySelectorAll(".entry-row")].map((row) => ({
    start: row.querySelector('[name="start"]').value,
    duration_min: parseInt(row.querySelector('[name="duration_min"]').value, 10),
    enabled: row.querySelector('[name="enabled"]').checked,
  }));
}

function sameSchedule(a, b) {
  return a && b && JSON.stringify(a) === JSON.stringify(b);
}

async function saveSchedule(form) {
  const id = form.dataset.valve;
  const status = form.querySelector(".save-status");
  const button = form.querySelector('button[type="submit"]');
  const wanted = scheduleFromForm(form);

  button.disabled = true;
  status.className = "save-status muted";
  status.textContent = "Skickar …";
  try {
    const r = await api(`/valves/${id}/schedule`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(wanted),
    });
    if (!r.ok) {
      const detail = (await r.json().catch(() => ({}))).detail;
      throw new Error(detail || `HTTP ${r.status}`);
    }
    /* Vänta på enhetens eko via /schedule/status → GET tills det matchar. */
    status.textContent = "Skickat — väntar på enheten …";
    for (let i = 0; i < 8; i++) {
      await new Promise((res) => setTimeout(res, 1000));
      const echoed = await fetchSchedule(id);
      if (sameSchedule(echoed, wanted)) {
        renderEntries(form, echoed);
        status.className = "save-status ok";
        status.textContent = "Sparat på enheten ✓";
        button.disabled = false;
        return;
      }
    }
    status.textContent = "Skickat, men enheten har inte bekräftat ännu.";
  } catch (e) {
    status.className = "save-status err";
    status.textContent = `Fel: ${e.message}`;
  }
  button.disabled = false;
}

/* ---------- Historik -> intervall ---------- */

/* Para ON->OFF till [start, slut]-intervall; oparad ON stängs vid nu. */
function pairEvents(events, now) {
  const intervals = [];
  let open = null;
  for (const ev of events) {
    const t = parseUtc(ev.ts);
    if (ev.state === "ON") {
      open = t; // dubbel-ON: behåll den senaste
    } else if (ev.state === "OFF" && open) {
      if (t > open) intervals.push([open, t]);
      open = null;
    }
  }
  if (open && now > open) intervals.push([open, now]);
  return intervals;
}

/* Dela intervall vid lokal midnatt till {dayKey, startH, endH}-segment. */
function splitByDay(intervals, dayStarts) {
  const segments = [];
  for (const [start, end] of intervals) {
    for (const dayStart of dayStarts) {
      const dayEnd = new Date(dayStart.getTime() + 24 * MS_PER_HOUR);
      const s = Math.max(start, dayStart);
      const e = Math.min(end, dayEnd);
      if (e <= s) continue;
      segments.push({
        dayKey: dayStart.getTime(),
        startH: (s - dayStart) / MS_PER_HOUR,
        endH: (e - dayStart) / MS_PER_HOUR,
        start: new Date(s),
        end: new Date(e),
      });
    }
  }
  return segments;
}

function lastDays(n) {
  const days = [];
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  for (let i = 0; i < n; i++) {
    days.push(new Date(today.getTime() - i * 24 * MS_PER_HOUR)); // nyast först (överst)
  }
  return days;
}

/* ---------- Diagram ---------- */

let chart = null;

function seriesColors() {
  return { 1: cssVar("--series-1"), 2: cssVar("--series-2") };
}

function renderChart(segmentsByValve, dayStarts) {
  const labels = dayStarts.map(fmtDate);
  const dayIndex = new Map(dayStarts.map((d, i) => [d.getTime(), i]));
  const colors = seriesColors();

  const datasets = VALVES.map((id) => ({
    label: `Ventil ${id}`,
    backgroundColor: colors[id],
    borderRadius: 4,
    borderSkipped: false,
    barThickness: 9,
    /* minst ~4 min bred stapel så korta körningar syns; tooltip visar exakt tid */
    data: segmentsByValve[id].map((seg) => ({
      x: [seg.startH, Math.max(seg.endH, seg.startH + 0.07)],
      y: labels[dayIndex.get(seg.dayKey)],
      seg,
    })),
  }));

  const ink = { primary: cssVar("--text-primary"), muted: cssVar("--text-muted") };
  const grid = cssVar("--gridline");

  if (chart) chart.destroy();
  chart = new Chart(document.getElementById("history-chart"), {
    type: "bar",
    data: { labels, datasets },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          min: 0,
          max: 24,
          ticks: {
            stepSize: 3,
            color: ink.muted,
            callback: (v) => String(v).padStart(2, "0") + ":00",
          },
          grid: { color: grid, drawTicks: false },
          border: { color: cssVar("--baseline") },
        },
        y: {
          ticks: { color: ink.primary },
          grid: { display: false },
          border: { display: false },
        },
      },
      plugins: {
        legend: {
          position: "top",
          align: "end",
          labels: { color: ink.primary, usePointStyle: true, pointStyle: "rectRounded", boxHeight: 8 },
        },
        tooltip: {
          callbacks: {
            title: (items) => items[0]?.raw.y ?? "",
            label: (item) => {
              const { seg } = item.raw;
              const min = Math.round((seg.end - seg.start) / 60000);
              return `${item.dataset.label}: ${fmtTime(seg.start)}–${fmtTime(seg.end)} (${min} min)`;
            },
          },
        },
      },
    },
  });
}

/* ---------- Tabellvy ---------- */

function renderTable(intervalsByValve) {
  const tbody = document.querySelector("#history-table tbody");
  const rows = [];
  for (const id of VALVES) {
    for (const [start, end] of intervalsByValve[id]) {
      rows.push({ id, start, end });
    }
  }
  rows.sort((a, b) => b.start - a.start);
  tbody.innerHTML = "";
  for (const { id, start, end } of rows) {
    const tr = document.createElement("tr");
    const min = Math.round((end - start) / 60000);
    tr.innerHTML =
      `<td>Ventil ${id}</td><td>${fmtDate(start)}</td>` +
      `<td class="num">${fmtTime(start)}</td><td class="num">${fmtTime(end)}</td>` +
      `<td class="num">${min} min</td>`;
    tbody.appendChild(tr);
  }
  document.getElementById("table-empty").hidden = rows.length > 0;
}

/* ---------- Huvudbrytare (bevattning på/av) ---------- */

const irrToggle = document.getElementById("irrigation-toggle");
const irrStatus = document.getElementById("irrigation-status");

function showIrrigation(enabled) {
  irrToggle.checked = enabled;
  irrToggle.disabled = false;
  irrStatus.className = enabled ? "muted" : "off";
  irrStatus.textContent = enabled ? "på" : "AVSTÄNGD";
}

async function loadIrrigation() {
  if (!deviceId) return null;
  try {
    const r = await api("/irrigation");
    if (!r.ok) {
      irrStatus.textContent = "enheten har inte rapporterat ännu";
      return null;
    }
    const s = await r.json();
    showIrrigation(s.enabled);
    return s.enabled;
  } catch {
    return null;
  }
}

irrToggle.addEventListener("change", async () => {
  const wanted = irrToggle.checked;
  irrToggle.disabled = true;
  irrStatus.className = "muted";
  irrStatus.textContent = "skickar …";
  try {
    const r = await api("/irrigation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: wanted }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    /* Vänta på enhetens eko via irrigation/status. */
    irrStatus.textContent = "väntar på enheten …";
    for (let i = 0; i < 8; i++) {
      await new Promise((res) => setTimeout(res, 1000));
      const echoed = await loadIrrigation();
      if (echoed === wanted) return;
    }
    irrStatus.textContent = "enheten har inte bekräftat ännu";
    irrToggle.disabled = false;
  } catch (e) {
    irrStatus.className = "off";
    irrStatus.textContent = `fel: ${e.message}`;
    irrToggle.checked = !wanted;
    irrToggle.disabled = false;
  }
});

/* ---------- Vattensensor (read-only, ägs av enheten) ---------- */

const sensorStatus = document.getElementById("sensor-status");

async function loadSensor() {
  if (!deviceId) return;
  try {
    const r = await api("/sensor");
    if (!r.ok) {
      sensorStatus.className = "muted sensor";
      sensorStatus.textContent = "";
      return;
    }
    const s = await r.json();
    sensorStatus.className = s.wet ? "sensor wet" : "muted sensor";
    sensorStatus.textContent = s.wet ? "Sensor: VÅT — bevattning stoppad" : "Sensor: torr";
  } catch {
    /* backend nere — lämna som det är */
  }
}

/* ---------- Laddning ---------- */

let lastData = null;

async function loadHistory() {
  if (!deviceId) return;
  const now = new Date();
  const dayStarts = lastDays(DAYS);
  const intervalsByValve = {};
  const segmentsByValve = {};
  for (const id of VALVES) {
    let events = [];
    try {
      const r = await api(`/valves/${id}/history?days=${DAYS}`);
      if (r.ok) events = await r.json();
    } catch {
      /* backend nere — visa tomt */
    }
    intervalsByValve[id] = pairEvents(events, now);
    segmentsByValve[id] = splitByDay(intervalsByValve[id], dayStarts);
  }
  lastData = { segmentsByValve, dayStarts, intervalsByValve };
  renderChart(segmentsByValve, dayStarts);
  renderTable(intervalsByValve);
}

async function updateHealth() {
  const el = document.getElementById("mqtt-status");
  try {
    const r = await fetch("/api/health");
    const h = await r.json();
    el.textContent = h.mqtt ? "Ansluten till MQTT-brokern" : "MQTT-brokern är inte ansluten";
  } catch {
    el.textContent = "Backend svarar inte";
  }
}

document.querySelectorAll("form[data-valve]").forEach((form) => {
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    saveSchedule(form);
  });
  form.querySelector(".add-entry").addEventListener("click", () => {
    const box = form.querySelector(".entries");
    if (box.children.length >= MAX_ENTRIES) {
      const status = form.querySelector(".save-status");
      status.className = "save-status err";
      status.textContent = `Max ${MAX_ENTRIES} bevattningar per dygn.`;
      return;
    }
    box.appendChild(entryRow({ start: "06:00", duration_min: 15, enabled: true }));
  });
});

/* Rita om med rätt färger när systemtemat växlar. */
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (lastData) renderChart(lastData.segmentsByValve, lastData.dayStarts);
});

/* ---------- Start: kräver inloggning + minst en enhet ---------- */

(async () => {
  try {
    const me = await (await authFetch("/api/auth/me")).json();
    document.getElementById("admin-link").hidden = !me.is_admin;
    await refreshDevices();
  } catch {
    return; // 401 → redirect till login.html är redan gjord
  }
  updateHealth();
  if (deviceId) {
    loadAllForDevice();
  } else {
    openAddDevice(); // nytt konto utan enheter — visa kopplingsflödet direkt
  }

  setInterval(loadSensor, 10 * 1000);
  setInterval(loadHistory, 60 * 1000);
  setInterval(updateHealth, 30 * 1000);
  setInterval(() => refreshDevices().catch(() => {}), 30 * 1000);
  setInterval(() => {
    /* Uppdatera inte mitt i en pågående eko-pollning (toggeln är då disabled
       med "skickar/väntar"-status som inte får skrivas över). */
    if (!irrToggle.disabled || irrStatus.textContent.includes("rapporterat")) {
      loadIrrigation();
    }
  }, 30 * 1000);
})();
