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
const securityObjectNames = new Set([
  "*ST康佳A", "岭南转债", "罗博特科", "002667", "Sigma Lithium",
  "Summit Therapeutics", "会稽山", "侨银转债", "兴业银行可转债", "兴业银锡",
  "南航转债", "圣晖集成", "宁德时代（300750.SZ）", "山西焦煤", "康佳B",
  "彤程新材", "新城吾悦REIT(SH508610)", "普冉股份", "江山转债", "浪潮信息",
  "渝水转债", "纳指科技ETF（159509）", "纳斯达克100场外A类基金", "绿茵转债",
  "美光科技", "老铺黄金", "英伟达", "蔚来能源", "货币ETF（511800）",
  "金川国际", "飞鹤（06186.HK）"
]);

const strategyObjectNames = new Set([
  "中金三兄弟换股套利", "消费贷资金投资低波资产", "IM跨期套利", "内地身份港股账户",
  "券商网格条件单", "券商返佣", "打新分账户策略", "港股ETF申赎",
  "港股通与A股休市日历错配", "短融ETF季末融资套利", "纳指ETF溢价交易",
  "纳指ETF溢价套利", "纳斯达克100场外A类基金申购限额", "茅台申购黄牛生态",
  "规则型资产配置", "跨境QDII ETF申购套利", "跨境QDII ETF盘前申购套利",
  "跨境QDII ETF高溢价", "面值退市股"
]);

const dateInput = document.getElementById("dateInput");
const findingsEl = document.getElementById("findings");
const emptyEl = document.getElementById("empty");
const runlineEl = document.getElementById("runline");
const historyHint = document.getElementById("historyHint");
const prevDayButton = document.getElementById("prevDay");
const nextDayButton = document.getElementById("nextDay");
let navigationLocked = false;
let activeDate = "";

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

function sourceLabel(value) {
  if (value === "jisilu") return "集思录";
  if (value === "xueqiu") return "雪球";
  return value || "未知来源";
}

function broadCountFromNote(note) {
  const match = String(note || "").match(/宽筛\s*(\d+)\s*条/);
  return match ? Number(match[1]) : null;
}

function formatInteger(value) {
  const number = Number(value || 0);
  return Number.isFinite(number)
    ? Math.max(0, Math.trunc(number)).toLocaleString("zh-CN")
    : "0";
}

function renderRunStatus(dateValue, runs) {
  const blocks = runs.map(function(run) {
    const source = sourceLabel(run.source);
    const ok = run.scan_status === "OK";
    const broad = broadCountFromNote(run.note);
    const mode = run.run_mode === "BACKFILL"
      ? '<span class="run-mode backfill">历史回溯</span>'
      : '<span class="run-mode live">当日扫描</span>';
    const metrics = [
      '<span class="metric"><span>原始候选</span><strong>' +
        escapeHtml(run.candidate_count) + "</strong></span>",
      broad == null ? "" :
        '<span class="metric"><span>宽筛</span><strong>' +
          escapeHtml(broad) + "</strong></span>",
      '<span class="metric"><span>今日发现</span><strong>' +
        escapeHtml(run.finding_count) + "</strong></span>",
      run.ai_metered
        ? '<span class="metric"><span>API请求</span><strong>' +
            escapeHtml(formatInteger(run.ai_request_count)) + "</strong></span>"
        : "",
      run.ai_metered
        ? '<span class="metric"><span>AI Token</span><strong>' +
            escapeHtml(formatInteger(run.ai_total_tokens)) + "</strong></span>"
        : ""
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
            mode +
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

function groupFindingsByObject(findings) {
  const groups = [];
  const index = new Map();
  findings.forEach(function(row) {
    const key = row.object_id || ("unmapped:" + row.finding_id);
    const label = row.object_name || "待归类";
    if (!index.has(key)) {
      const group = {key: key, label: label, rows: []};
      index.set(key, group);
      groups.push(group);
    }
    index.get(key).rows.push(row);
  });
  return groups;
}

function objectKindLabel(name) {
  if (securityObjectNames.has(name)) return {label: "标的", cls: "security"};
  if (strategyObjectNames.has(name)) return {label: "策略", cls: "strategy"};
  return {label: "待整理", cls: "pending"};
}

function nodeDateLabel(row) {
  const value = String(row.run_date || activeDate || "");
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value.slice(5).replace("-", "/") : "";
}

function primaryEvidence(row) {
  const evidence = Array.isArray(row.evidence) ? row.evidence : [];
  return evidence.find(function(item) { return item.is_primary; }) || evidence[0] || null;
}

function renderSource(row) {
  const evidence = primaryEvidence(row);
  const source = sourceLabel(evidence ? evidence.source : row.source);
  const author = evidence && evidence.author ? evidence.author : row.author;
  const time = evidence && evidence.published_at
    ? evidence.published_at
    : row.observed_at;
  const sourceTitle = evidence && evidence.source_title
    ? evidence.source_title
    : row.title;
  const url = evidence && evidence.locator_url
    ? evidence.locator_url
    : row.url;
  const precise = Boolean(
    evidence && evidence.locator_kind && evidence.locator_kind !== "QUESTION"
  );
  const pieces = [
    '<span class="source-platform">' + escapeHtml(source) + "</span>",
    author ? '<span>' + escapeHtml(author) + "</span>" : "",
    sourceTitle
      ? '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener">《' +
          escapeHtml(sourceTitle) + '》</a>'
      : "",
    time ? '<span>' + escapeHtml(time) + "</span>" : "",
    precise
      ? '<a class="locator-link" href="' + escapeHtml(url) + '" target="_blank" rel="noopener">定位回复 ↗</a>'
      : '<a class="locator-link" href="' + escapeHtml(url) + '" target="_blank" rel="noopener">原文 ↗</a>'
  ].filter(Boolean);
  return '<div class="node-source">' + pieces.join('<span class="source-sep">·</span>') + "</div>";
}

function renderNode(row) {
  const nodeTitle = row.node_title || row.title || "未命名节点";
  const dateLabel = nodeDateLabel(row);
  const typeLabel = typeLabels[row.finding_type] || row.finding_type;
  const meta = [dateLabel, typeLabel].filter(Boolean).join(" · ");
  return (
    '<section class="object-node">' +
      '<div class="node-meta">' + escapeHtml(meta) + "</div>" +
      '<h3 class="node-title">' + escapeHtml(nodeTitle) + "</h3>" +
      '<div class="node-fact"><span class="node-label">今天新增</span><div>' +
        escapeHtml(row.what_happened) + "</div></div>" +
      '<div class="node-judgment"><span class="node-label">当前判断</span><div>' +
        escapeHtml(row.current_judgment) + "</div></div>" +
      renderSource(row) +
    "</section>"
  );
}

function renderObjectGroup(group) {
  const kind = objectKindLabel(group.label);
  return (
    '<article class="object-card">' +
      '<div class="object-head">' +
        '<div class="object-identity">' +
          '<span class="object-kind ' + kind.cls + '">' + escapeHtml(kind.label) + "</span>" +
          '<h2>' + escapeHtml(group.label) + "</h2>" +
        "</div>" +
      "</div>" +
      '<div class="object-nodes">' + group.rows.map(renderNode).join("") + "</div>" +
    "</article>"
  );
}

async function loadDates() {
  const response = await fetch("/api/radar/dates?limit=120", {cache: "no-store"});
  if (!response.ok) return [];
  const data = await response.json();
  return data.dates || [];
}

async function loadDay(dateValue) {
  activeDate = dateValue;
  findingsEl.innerHTML = "";
  emptyEl.hidden = true;
  runlineEl.textContent = "读取中…";
  const response = await fetch(
    "/api/radar/daily?date=" + encodeURIComponent(dateValue),
    {cache: "no-store"}
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
    const groups = groupFindingsByObject(findings);
    findingsEl.innerHTML = groups.map(renderObjectGroup).join("");
  }
}

function navigateToDate(value) {
  if (navigationLocked || !value) return;
  navigationLocked = true;
  prevDayButton.disabled = true;
  nextDayButton.disabled = true;
  dateInput.disabled = true;
  const url = new URL(window.location.href);
  url.pathname = "/radar";
  url.search = "";
  url.searchParams.set("date", value);
  url.searchParams.set("ui", "attention-object-v1");
  window.location.assign(url.toString());
}

async function init() {
  const params = new URLSearchParams(location.search);
  const requested = params.get("date");
  if (requested) dateInput.value = requested;
  const dates = await loadDates();
  const selected = requested || dates[0] || localToday();
  dateInput.value = selected;
  historyHint.textContent = dates.length
    ? "已保存 " + dates.length + " 个日期；观察对象、节点与来源证据由 Runtime 持久化。"
    : "尚无历史记录。";
  await loadDay(selected);
}

dateInput.addEventListener("change", function() {
  navigateToDate(dateInput.value);
});
prevDayButton.addEventListener("click", function(event) {
  event.preventDefault();
  event.stopImmediatePropagation();
  navigateToDate(shiftDate(dateInput.value, -1));
});
nextDayButton.addEventListener("click", function(event) {
  event.preventDefault();
  event.stopImmediatePropagation();
  navigateToDate(shiftDate(dateInput.value, 1));
});

init();
