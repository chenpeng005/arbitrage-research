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

const sourceLabels = {
  jisilu: "集思录",
  xueqiu: "雪球",
  eastmoney: "东方财富股吧",
  taoguba: "淘股吧"
};

// 9/28–10/4 historical sample: display-only object prototype.
// Intentionally not persisted to SQLite yet.
const objectPrototypeByFindingId = {
  RDF_e3815ffa74c8550eee4b: "康佳主动退市现金选择权",
  RDF_976ad2dd0114f8971135: "康佳主动退市现金选择权",
  RDF_ba35bbf7bcc1a525e6d5: "纳指科技ETF（159509）高溢价",
  RDF_58a0adc1103e5882dd83: "南航转债盘中极端成交",
  RDF_064234506e78f82c8fe8: "圣晖集成配债",
  RDF_0dab7a66fac93e6bdea4: "中金三兄弟吸收合并 / 换股",
  RDF_9ab28e7f0aa7662a60b6: "消费贷资金投资低波资产",
  RDF_6256bce806f8ebd13e23: "消费贷资金投资低波资产",
  RDF_297fe33210ba69ce4ea8: "纳指ETF节前高溢价",
  RDF_6b383a5c2b2c1b55f21c: "岭南转债退市后兑付",
  RDF_eb73b871afc0d37ff137: "券商智能条件单 / 网格",
  RDF_d455d2d0a4c37797dc34: "002667 要约收购",
  RDF_d308e8f1cafba0184583: "江山转债下修博弈",
  RDF_0bd08c00b111915da456: "绿茵转债下修",
  RDF_f2bf8db082bfe924c93d: "渝水转债下修触发数据",
  RDF_315e27c40baf72c8798b: "康佳主动退市现金选择权",
  RDF_a627bdd1741fe91d9f56: "弱者体系 / 规则型资产配置",
  RDF_46dc88fed01532468d34: "康佳主动退市现金选择权",
  RDF_f055f173729bc2918311: "康佳主动退市现金选择权",
  RDF_adcce26d5799ab30d544: "货币ETF（511800）节前异常波动",
  RDF_b0692e5125a40d4939f8: "港股ETF申赎",
  RDF_b2f0d3f08d1134c62f5c: "侨银转债下修前正股博弈",
  RDF_a3505fb4fa6293146488: "跨境QDII ETF申购套利",
  RDF_c37223833c4fccba68f7: "中金三兄弟现金选择权 / 换股套利",
  RDF_7658b1db0e6ba3656ef2: "IM跨期套利"
};

// High-confidence representative evidence locators reconstructed from the sample.
// Missing/uncertain rows deliberately fall back to the thread URL.
const evidencePrototypeByFindingId = {
  RDF_976ad2dd0114f8971135: {answerId:"5550357", author:"红牛Y", time:"2026-09-28 10:45"},
  RDF_e3815ffa74c8550eee4b: {answerId:"5550362", author:"caishendao", time:"2026-09-28 10:47"},
  RDF_58a0adc1103e5882dd83: {answerId:"5551283", author:"viking75", time:"2026-09-29 14:22"},
  RDF_ba35bbf7bcc1a525e6d5: {answerId:"5551284", author:"虞鼠乔鱼", time:"2026-09-29 14:23"},
  RDF_064234506e78f82c8fe8: {answerId:"5552532", author:"稳定变富之路", time:"2026-09-30 21:18"},
  RDF_0bd08c00b111915da456: {answerId:"5551782", author:"心蓝黄", time:"2026-09-30 07:59"},
  RDF_0dab7a66fac93e6bdea4: {answerId:"5552500", author:"hannon", time:"2026-09-30 20:28"},
  RDF_297fe33210ba69ce4ea8: {answerId:"5552250", author:"周8272339899", time:"2026-09-30 15:36"},
  RDF_6256bce806f8ebd13e23: {answerId:"5552300", author:"沐柰", time:"2026-09-30 16:06"},
  RDF_9ab28e7f0aa7662a60b6: {answerId:"5552439", author:"POOL哥", time:"2026-09-30 18:51"},
  RDF_d308e8f1cafba0184583: {answerId:"5552007", author:"枫林随手记", time:"2026-09-30 11:39"},
  RDF_315e27c40baf72c8798b: {answerId:"5552727", author:"rzchen", time:"2026-10-01 09:49"},
  RDF_a627bdd1741fe91d9f56: {answerId:"5552662", author:"zyc田忌赛马", time:"2026-10-01 08:14"},
  RDF_46dc88fed01532468d34: {answerId:"5553121", author:"张集思78", time:"2026-10-02 14:36"},
  RDF_adcce26d5799ab30d544: {answerId:"5553032", author:"集思小子", time:"2026-10-02 09:31"},
  RDF_f055f173729bc2918311: {answerId:"5553117", author:"CZX303304", time:"2026-10-02 14:32"},
  RDF_b0692e5125a40d4939f8: {answerId:"5553411", author:"bi18an", time:"2026-10-03 15:53"},
  RDF_b2f0d3f08d1134c62f5c: {answerId:"5553316", author:"火星兔", time:"2026-10-03 09:28"},
  RDF_7658b1db0e6ba3656ef2: {answerId:"5553677", author:"flyzizai", time:"2026-10-04 13:16"},
  RDF_c37223833c4fccba68f7: {answerId:"5553554", author:"yyj919", time:"2026-10-04 07:29"}
};

const dateInput = document.getElementById("dateInput");
const findingsEl = document.getElementById("findings");
const emptyEl = document.getElementById("empty");
const runlineEl = document.getElementById("runline");
const historyHint = document.getElementById("historyHint");
const prevDayButton = document.getElementById("prevDay");
const nextDayButton = document.getElementById("nextDay");
let navigationLocked = false;

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

function sourceLabel(source) {
  return sourceLabels[source] || source || "来源";
}

function renderRunStatus(dateValue, runs) {
  const blocks = runs.map(function(run) {
    const ok = run.scan_status === "OK";
    const broad = broadCountFromNote(run.note);
    const metrics = [
      '<span class="metric"><span>原始候选</span><strong>' + escapeHtml(run.candidate_count) + "</strong></span>",
      broad == null ? "" : '<span class="metric"><span>宽筛</span><strong>' + escapeHtml(broad) + "</strong></span>",
      '<span class="metric"><span>今日发现</span><strong>' + escapeHtml(run.finding_count) + "</strong></span>"
    ].filter(Boolean).join("");
    const note = run.note ? '<div class="run-note">' + escapeHtml(run.note) + "</div>" : "";
    return (
      '<div class="run-status">' +
        '<div class="run-status-top">' +
          '<div class="run-status-title"><span class="status-dot ' + (ok ? "ok" : "failed") + '"></span>' +
            escapeHtml(sourceLabel(run.source) + " · " + (ok ? "扫描成功" : "扫描异常")) +
          "</div>" +
          '<div class="run-time">完成于 ' + escapeHtml(shortDateTime(run.completed_at)) + "</div>" +
        "</div>" +
        '<div class="run-metrics">' + metrics + "</div>" + note +
      "</div>"
    );
  });
  runlineEl.innerHTML = '<div class="run-date">' + escapeHtml(dateValue) + "</div>" + blocks.join("");
}

function objectForFinding(row) {
  const label = objectPrototypeByFindingId[row.finding_id];
  if (label) return {key: label, label: label, mapped: true};
  return {key: "unmapped:" + row.finding_id, label: "待归类", mapped: false};
}

function groupFindingsByObject(findings) {
  const groups = [];
  const index = new Map();
  findings.forEach(function(row) {
    const object = objectForFinding(row);
    if (!index.has(object.key)) {
      const group = {object: object, rows: []};
      index.set(object.key, group);
      groups.push(group);
    }
    index.get(object.key).rows.push(row);
  });
  return groups;
}

function evidenceForFinding(row) {
  const evidence = evidencePrototypeByFindingId[row.finding_id];
  if (!evidence) {
    return {url: row.url, author: row.author || null, time: row.observed_at || null, precise: false};
  }
  return {
    url: row.url + "#answer_list_" + evidence.answerId,
    author: evidence.author || row.author || null,
    time: evidence.time || row.observed_at || null,
    precise: true
  };
}

function renderNode(row) {
  const evidence = evidenceForFinding(row);
  const author = evidence.author ? '<span>' + escapeHtml(evidence.author) + "</span>" : "";
  const time = evidence.time ? '<time>' + escapeHtml(evidence.time) + "</time>" : "";
  const locatorHint = evidence.precise ? '<span class="locator-hint">定位回复</span>' : "";
  return (
    '<div class="object-node">' +
      '<div class="node-type"><span class="type">' + escapeHtml(typeLabels[row.finding_type] || row.finding_type) + "</span></div>" +
      '<div class="node-row"><div class="node-label">今天新增</div><div class="node-fact">' + escapeHtml(row.what_happened) + "</div></div>" +
      '<div class="node-row node-judgment"><div class="node-label">当前判断</div><div>' + escapeHtml(row.current_judgment) + "</div></div>" +
      '<div class="node-source">' +
        '<span>' + escapeHtml(sourceLabel(row.source)) + "</span>" +
        (author ? '<span class="sep">·</span>' + author : "") +
        '<span class="sep">·</span><a href="' + escapeHtml(evidence.url) + '" target="_blank" rel="noopener">《' + escapeHtml(row.title) + '》 ↗</a>' +
        (time ? '<span class="sep">·</span>' + time : "") + locatorHint +
      "</div>" +
    "</div>"
  );
}

function renderObjectGroup(group, index) {
  const mappingLabel = group.object.mapped ? "对象" : "对象 · 待归类";
  return (
    '<article class="object-card">' +
      '<div class="object-head"><div><div class="object-kicker">' + mappingLabel + '</div>' +
      '<h2>' + escapeHtml((index + 1) + ". " + group.object.label) + "</h2></div></div>" +
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
  findingsEl.innerHTML = "";
  emptyEl.hidden = true;
  runlineEl.textContent = "读取中…";
  const response = await fetch("/api/radar/daily?date=" + encodeURIComponent(dateValue), {cache: "no-store"});
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
    findingsEl.innerHTML = groupFindingsByObject(findings).map(renderObjectGroup).join("");
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
  url.searchParams.set("ui", "object-v2-source-v1-nav-v2");
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
    ? "已保存 " + dates.length + " 个日期。9/28–10/4 的对象与精确来源定位为展示原型，尚未写入底层模型。"
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
