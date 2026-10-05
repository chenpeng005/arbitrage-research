const typeLabels = {
  NEW_MECHANISM: "新机制",
  NEW_CASE: "新案例",
  REAL_TEST: "真实实测",
  COUNTEREVIDENCE: "反例/证伪",
  RULE_CHANGE: "规则变化",
  EXECUTION_ISSUE: "执行差异",
  DATA_ISSUE: "数据问题",
  ANOMALY: "异常观察",
  OTHER: "其他"
};

const dateInput = document.getElementById("dateInput");
const findingsEl = document.getElementById("findings");
const emptyEl = document.getElementById("empty");
const runlineEl = document.getElementById("runline");
const historyHint = document.getElementById("historyHint");

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function shiftDate(value, days) {
  const d = new Date(value + "T00:00:00");
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function shortDateTime(value) {
  if (!value) return "";
  return String(value).replace("T", " ").slice(0, 16);
}

function broadCountFromNote(note) {
  const match = String(note || "").match(/宽筛\s*(\d+)\s*条/);
  return match ? Number(match[1]) : null;
}

function renderRunStatus(dateValue, runs) {
  const blocks = runs.map(function(run) {
    const source = run.source === "jisilu" ? "集思录" : run.source;
    const ok = run.scan_status === "OK";
    const broad = broadCountFromNote(run.note);
    const metrics = [
      '<span class="metric"><span>原始候选</span><strong>' +
        escapeHtml(run.candidate_count) + "</strong></span>",
      broad == null ? "" :
        '<span class="metric"><span>宽筛</span><strong>' +
          escapeHtml(broad) + "</strong></span>",
      '<span class="metric"><span>今日发现</span><strong>' +
        escapeHtml(run.finding_count) + "</strong></span>"
    ].filter(Boolean).join("");
    const note = run.note
      ? '<div class="run-note">' + escapeHtml(run.note) + "</div>"
      : "";
    return (
      '<div class="run-status">' +
        '<div class="run-status-top">' +
          '<div class="run-status-title"><span class="status-dot ' +
            (ok ? "ok" : "failed") + '"></span>' +
            escapeHtml(source + " · " + (ok ? "扫描成功" : "扫描异常")) +
          "</div>" +
          '<div class="run-time">完成于 ' + escapeHtml(shortDateTime(run.completed_at)) + "</div>" +
        "</div>" +
        '<div class="run-metrics">' + metrics + "</div>" +
        note +
      "</div>"
    );
  });
  runlineEl.innerHTML =
    '<div class="run-date">' + escapeHtml(dateValue) + "</div>" + blocks.join("");
}

function renderFinding(row, index) {
  const meta = [
    row.source === "jisilu" ? "集思录" : row.source,
    row.author || null,
    row.observed_at || null
  ].filter(Boolean).map(escapeHtml).join(" · ");
  const evidence = row.evidence_excerpt
    ? '<div class="evidence">' + escapeHtml(row.evidence_excerpt) + "</div>"
    : "";
  return (
    '<article class="finding">' +
      '<div class="meta"><span class="type">' +
        escapeHtml(typeLabels[row.finding_type] || row.finding_type) +
      "</span><span>" + meta + "</span></div>" +
      '<h2><a href="' + escapeHtml(row.url) + '" target="_blank" rel="noopener">' +
        escapeHtml((index + 1) + ". " + row.title) +
      "</a></h2>" +
      "<dl>" +
        '<div class="row"><dt>发生了什么</dt><dd>' +
          escapeHtml(row.what_happened) + "</dd></div>" +
        '<div class="row"><dt>AI理解</dt><dd>' +
          escapeHtml(row.ai_understanding) + "</dd></div>" +
      "</dl>" +
      '<div class="judgment">' + escapeHtml(row.current_judgment) + "</div>" +
      evidence +
    "</article>"
  );
}

async function loadDates() {
  const response = await fetch("/api/radar/dates?limit=120");
  if (!response.ok) return [];
  const data = await response.json();
  return data.dates || [];
}

async function loadDay(dateValue) {
  findingsEl.innerHTML = "";
  emptyEl.hidden = true;
  runlineEl.textContent = "读取中…";
  const response = await fetch(
    "/api/radar/daily?date=" + encodeURIComponent(dateValue)
  );
  if (!response.ok) {
    runlineEl.textContent = "读取失败";
    return;
  }
  const data = await response.json();
  const runs = data.runs || [];
  if (!runs.length) {
    runlineEl.textContent = dateValue + " · 尚无扫描记录";
    emptyEl.hidden = false;
    emptyEl.textContent = "这一天没有保存的扫描记录。";
    return;
  }
  renderRunStatus(dateValue, runs);
  const findings = data.findings || [];
  if (!findings.length) {
    emptyEl.hidden = false;
    emptyEl.textContent = "今日已完成扫描，没有值得保留的新发现。";
  } else {
    findingsEl.innerHTML = findings.map(renderFinding).join("");
  }
}

async function init() {
  const dates = await loadDates();
  const params = new URLSearchParams(location.search);
  const requested = params.get("date");
  const selected = requested || dates[0] || new Date().toISOString().slice(0, 10);
  dateInput.value = selected;
  historyHint.textContent = dates.length
    ? "已保存 " + dates.length + " 个日期，可直接切换日期回看。"
    : "尚无历史记录。";
  await loadDay(selected);
}

dateInput.addEventListener("change", function() {
  const value = dateInput.value;
  history.replaceState(null, "", "/radar?date=" + encodeURIComponent(value));
  loadDay(value);
});
document.getElementById("prevDay").addEventListener("click", function() {
  dateInput.value = shiftDate(dateInput.value, -1);
  dateInput.dispatchEvent(new Event("change"));
});
document.getElementById("nextDay").addEventListener("click", function() {
  dateInput.value = shiftDate(dateInput.value, 1);
  dateInput.dispatchEvent(new Event("change"));
});

init();
