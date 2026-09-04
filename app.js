const opportunities = [
  {
    id: "konka",
    name: "*ST康佳 A / B",
    code: "000016 / 200016 · A股 / B股",
    strategy: "主动退市现金选择权",
    anchor: "A ¥2.48 / B $0.73",
    floor: "公司行动形成的现金权利",
    status: "research",
    statusText: "研究中",
    next: "2026-09-14 股东大会",
    summary: "以现金选择权作为主要退出锚点，重点核查方案通过、持有人资格、申报安排以及A/B股汇率与交易摩擦。",
    condition: "在规定登记日持有且不属于公告列明的除外主体；最终以实施公告为准。",
    risk: "主动退市方案未获通过、实施安排变化，或B股汇兑和流动性成本高于预期。",
    upside: "在现金退出之外，二级市场价格上涨仍可提前退出；B股折价可能提供更厚的安全垫。",
    scenarios: [
      ["顺利实施", "按现金选择权退出", "主要承担时间与交易摩擦"],
      ["进程延期", "继续等待或二级市场退出", "资金占用时间延长"],
      ["方案未通过", "现金锚点消失", "回到基本面和流动性定价"]
    ],
    source: "https://static.cninfo.com.cn/finalpage/2026-09-01/1225537108.PDF",
    sourceText: "公司正式公告"
  },
  {
    id: "gtjai",
    name: "国泰君安国际",
    code: "01788 · 港股通",
    strategy: "协议安排私有化",
    anchor: "HK$3.00",
    floor: "附带前置条件的现金对价",
    status: "wait",
    statusText: "等待条件",
    next: "等待方案文件与审批进展",
    summary: "市场价格与私有化对价之间存在价差，但方案仍需经过前置条件、股东及法院等程序，不能把价差直接视为无风险收益。",
    condition: "方案文件生效、所需批准取得，并满足适用的协议安排条件。",
    risk: "条件未满足、时间显著延长、汇率变化或方案终止后股价回落。",
    upside: "潜在提价或进程推进带来的价差收敛。",
    scenarios: [
      ["方案完成", "取得每股HK$3.00", "收益取决于买入价与完成时间"],
      ["进程延期", "价差继续存在", "年化下降并承担汇率波动"],
      ["方案失败", "现金锚点消失", "重新按公司基本面定价"]
    ],
    source: "https://www.gtjai.com/sc/ir_announcement",
    sourceText: "公司公告页面"
  },
  {
    id: "dashen",
    name: "大参转债",
    code: "113605 · 可转债",
    strategy: "临期到期＋低转股溢价",
    anchor: "到期赎回约 ¥110",
    floor: "信用支持的到期偿付",
    status: "reject",
    statusText: "暂不符合",
    next: "距到期约48日（快照时点）",
    summary: "虽然期限短、转股溢价率不高，但快照价格明显高于到期赎回额，向下并未被债底充分保护，不符合当前核心标准。",
    condition: "发行人正常兑付；若依靠转股价值退出，则需正股价值在剩余期限内维持。",
    risk: "正股下跌导致转股价值下降，同时转债价格向到期赎回额收敛。",
    upside: "正股上涨可通过接近零的转股溢价参与，但并非免费期权。",
    scenarios: [
      ["正股上涨", "跟随转股价值", "保留有限上行"],
      ["正股横盘", "逐步向到期价值收敛", "可能损失当前溢价"],
      ["正股下跌", "依赖到期赎回", "价格到债底仍有明显距离"]
    ],
    source: "https://www.jisilu.cn/data/convert_bond_detail/113605",
    sourceText: "转债资料页"
  }
];

const statusMarkup = item => `<span class="status-pill ${item.status}"><i class="dot ${item.status === "research" ? "green" : item.status === "wait" ? "amber" : "red"}"></i>${item.statusText}</span>`;

function renderRows() {
  document.querySelector("#overviewRows").innerHTML = opportunities.map(item => `
    <tr data-case="${item.id}" tabindex="0" aria-label="查看${item.name}研究详情">
      <td class="instrument"><strong>${item.name}</strong><span>${item.code}</span></td>
      <td>${item.strategy}</td>
      <td class="anchor">${item.anchor}</td>
      <td>${item.floor}</td>
      <td>${statusMarkup(item)}</td>
      <td>${item.next}</td>
    </tr>`).join("");
}

function renderResearch() {
  document.querySelector("#researchCards").innerHTML = opportunities.map(item => `
    <article class="research-card" data-case="${item.id}" tabindex="0">
      <div><p class="eyebrow">${item.strategy}</p><h3>${item.name}</h3></div>
      <p>${item.summary}</p>
      <button type="button">查看研究</button>
    </article>`).join("");
}

function renderTimeline() {
  document.querySelector("#timelineList").innerHTML = [
    ["2026-09-14", "*ST康佳", "第二次临时股东大会，核对表决结果及后续实施安排。"],
    ["待公告", "国泰君安国际", "关注方案文件、监管审批和法院程序更新。"],
    ["临近到期", "大参转债", "跟踪最后交易、最后转股及到期赎回安排。"]
  ].map(item => `<article class="timeline-item"><time>${item[0]}</time><div><strong>${item[1]}</strong><span>${item[2]}</span></div></article>`).join("");
}

function openCase(id) {
  const item = opportunities.find(x => x.id === id);
  if (!item) return;
  document.querySelector("#dialogContent").innerHTML = `
    <div class="dialog-body">
      <p class="eyebrow">${item.strategy}</p>
      <h2 id="dialogTitle">${item.name}</h2>
      <p class="dialog-sub">${item.code}</p>
      <div class="case-summary">${item.summary}</div>
      <div class="fact-grid">
        <div><span>当前状态</span><strong>${item.statusText}</strong></div>
        <div><span>退出锚点</span><strong>${item.anchor}</strong></div>
        <div><span>下一节点</span><strong>${item.next}</strong></div>
      </div>
      <section class="dialog-section"><h3>资格与成立条件</h3><p>${item.condition}</p></section>
      <section class="dialog-section"><h3>主要风险</h3><p>${item.risk}</p></section>
      <section class="dialog-section"><h3>向上空间</h3><p>${item.upside}</p></section>
      <section class="dialog-section">
        <h3>情景树</h3>
        <table class="scenario-table"><thead><tr><th>情景</th><th>退出路径</th><th>影响</th></tr></thead><tbody>
          ${item.scenarios.map(row => `<tr><td>${row[0]}</td><td>${row[1]}</td><td>${row[2]}</td></tr>`).join("")}
        </tbody></table>
      </section>
      <section class="dialog-section"><h3>原始资料</h3><a class="source-link" href="${item.source}" target="_blank" rel="noreferrer">${item.sourceText} ↗</a></section>
      <p class="data-note">本页是系统首版研究快照，用于验证研究结构；价格、条件和日期在形成实际决策前必须重新核验。</p>
    </div>`;
  document.querySelector("#caseDialog").showModal();
}

document.querySelectorAll(".nav-item").forEach(button => {
  button.addEventListener("click", () => {
    const target = button.dataset.target;
    document.querySelectorAll(".nav-item").forEach(x => x.classList.toggle("active", x === button));
    document.querySelectorAll(".view").forEach(x => x.classList.toggle("active-view", x.id === target));
    document.querySelector(".topbar h1").textContent = button.textContent.replace(/^\d+/, "").trim();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
});

document.addEventListener("click", event => {
  const target = event.target.closest("[data-case]");
  if (target) openCase(target.dataset.case);
});
document.addEventListener("keydown", event => {
  const target = event.target.closest?.("[data-case]");
  if (target && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); openCase(target.dataset.case); }
  if (event.key === "Escape") document.querySelector("#caseDialog").close();
});
document.querySelector(".dialog-close").addEventListener("click", () => document.querySelector("#caseDialog").close());
document.querySelector("#caseDialog").addEventListener("click", event => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});

renderRows();
renderResearch();
renderTimeline();
