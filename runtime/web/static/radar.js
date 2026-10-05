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
  const parts = String(value || "").split("-").map(Number);
  if (parts.length !== 3 || parts.some(Number.isNaN)) return value;
  const d = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function localToday() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return y + "-" + m + "-" + day;
}

function shortDateTime(value) {
  if (!value) return "";
  return String(value).replace("T", " ").slice(0, 16);
}

function broadCountFromNote(note) {
  const match = String(note || "").match(/宽筛\s*(\d+)\s*条/);
  return match ? Number(match[1]) : null;
}

function compactText(value, maxLength) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= maxLength) return text;
  const sentenceMatch = text.match(/^(.{28,}?[。！？；])/);
  if (sentenceMatch && sentenceMatch[1].length <= maxLength) {
    return sentenceMatch[1];
  }
  return text.slice(0, maxLength).replace(/[，、；：\s]+$/, "") + "…";
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
  const focus = compactText(row.what_happened, 132);
  const why = compactText(row.ai_understanding, 118);
  const evidence = row.evidence_excerpt
    ? '<div class="detail-evidence"><div class="detail-label">原始证据</div><div>' +
        escapeHtml(row.evidence_excerpt) + "</div></div>"
    : "";
  return (
    '<article class="finding">' +
      '<div class="meta"><span class="type">' +
        escapeHtml(typeLabels[row.finding_type] || row.finding_type) +
      "</span><span>" + meta + "</span></div>" +
      '<h2><a href="' + escapeHtml(row.url) + '" target="_blank" rel="noopener">' +
        escapeHtml((index + 1) + ". " + row.title) +
      "</a></h2>" +
      '<div class="focus-block"><div class="focus-label">一句话焦点</div><div class="focus-text">' +
        escapeHtml(focus) + "</div></div>" +
      '<div class="quick-grid">' +
        '<div class="quick-item"><div class="quick-label">为什么值得看</div><div>' +
          escapeHtml(why) + "</div></div>" +
        '<div class="quick-item judgment-card"><div class="quick-label">当前判断</div><div>' +
          escapeHtml(row.current_judgment) + "</div></div>" +
      "</div>" +
      '<details class="finding-details">' +
        '<summary>展开详情</summary>' +
        '<div class="detail-body">' +
          '<div class="detail-row"><div class="detail-label">发生了什么</div><div>' +
            escapeHtml(row.what_happened) + "</div></div>" +
          '<div class="detail-row"><div class="detail-label">AI理解</div><div>' +
            escapeHtml(row.ai_understanding) + "</div></div>" +
          evidence +
          '<div class="source-link"><a href="' + escapeHtml(row.url) + '" target="_blank" rel="noopener">打开原帖 ↗</a></div>' +
        "</div>" +
      "</details>" +
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
  const selected = requested || dates[0] || localToday();
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
