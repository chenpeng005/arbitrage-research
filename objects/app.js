const objects = [
  {
    id: "3.1.1",
    name: "正股价格",
    status: "draft",
    sections: {
      type: `<span class="type-label">基础参数</span><p>可以从股票市场直接观察，是其他转股结构参数的输入项。</p>`,
      definition: `<p>正股价格是可转债对应上市公司股票在某一时点的市场成交价格。研究时必须同时注明价格时点、价格口径及复权情况。</p><p>对于实时研究，通常采用最新成交价或收盘价；对于历史回溯，应采用当时可获得的价格，避免使用事后复权数据造成信息穿越。</p>`,
      standalone: `<p class="standalone-answer">有方向性信息，但单独不足以判断。</p><p>正股上涨通常有利于转债的股性价值，正股下跌通常不利；但只知道正股价格的绝对数值，不知道转股价格、转债价格和条款状态，无法判断转债未来收益率概率分布。</p>`,
      meaning: `<p>正股价格主要作为以下关系的输入：</p><ul><li>正股价格 ÷ 转股价格 → 转股价值及正股相对转股价的位置；</li><li>正股价格与强赎、下修、回售触发价格比较 → 条款触发状态；</li><li>正股价格变化 + 转债价格变化 → 实际跟涨弹性。</li></ul><p class="research-note">初步判断：应保留为基础研究对象，但其绝对价格本身解释力较弱。</p>`
    }
  },
  {
    id: "3.1.2",
    name: "转股价格",
    status: "draft",
    sections: {
      type: `<span class="type-label">基础参数</span><p>由募集说明书、历次调整公告及当前生效条款明确规定。</p>`,
      definition: `<p>转股价格是投资者将可转债转换为正股时，每取得一股股票所对应的债券面值金额。</p><p>当前有效转股价格可能因分红、送股、配股、增发、下修等事项发生调整，因此研究时不能只读取发行时的初始转股价格。</p>`,
      standalone: `<p class="standalone-answer">单独无意义。</p><p>转股价格是10元还是50元，不能直接说明转债便宜或昂贵，也不能说明转股是否有利。它必须与正股价格、转债价格以及可能的调整路径结合。</p>`,
      meaning: `<p>转股价格决定每张转债能够转换的股票数量，是转股价值、转股溢价率及多项条款触发价格的核心输入。</p><ul><li>与正股价格结合，决定当前转股价值；</li><li>与下修底价规则结合，决定潜在下修空间；</li><li>其调整事件会离散地改变转股结构。</li></ul><p class="research-note">需要区分“当前有效转股价格”与“转股价格调整规则”，后者属于条款对象。</p>`
    }
  },
  {
    id: "3.1.3",
    name: "每张转债可转股数量",
    status: "draft",
    sections: {
      type: `<span class="type-label">派生参数</span><p>通常由转债面值除以当前转股价格计算得到。</p>`,
      definition: `<p>每张转债可转股数量，是一张面值100元的转债按照当前转股价格能够转换成的股票数量。</p><blockquote>每张可转股数量 = 100 ÷ 当前转股价格</blockquote><p>实际转换时还需要服从交易所关于最小转股单位和不足一股资金返还的具体规则。</p>`,
      standalone: `<p class="standalone-answer">单独无意义。</p><p>它只是当前转股价格的另一种表达。可转股数量多，不代表转股价值一定高；还必须乘以正股价格。</p>`,
      meaning: `<p>它可以帮助还原转股价值的计算过程，并在实际转股时核算能够取得的股票数量、零股及尾款。</p><p>但在概率分布研究中，它与“转股价格”高度重复，单独保留可能造成对象冗余。</p><p class="research-note">初步判断：可考虑降级为“转股价格”或“转股价值”定义中的计算项，而不是独立研究对象。</p>`
    }
  },
  {
    id: "3.1.4",
    name: "转股价值 / 平价",
    status: "sample",
    sections: {
      type: `<span class="type-label">派生参数</span><p>由正股价格和转股价格两个基础参数计算得到。</p>`,
      definition: `<p>转股价值是指一张面值100元的可转债，按照当前转股价格转换成股票后，对应股票当前的市场价值。</p><blockquote>转股价值 = 100 × 正股价格 ÷ 转股价格</blockquote><p>例如，正股价格为8元，转股价格为10元，则转股价值为80元。</p>`,
      standalone: `<p class="standalone-answer">无。</p><p>单独知道一只转债的转股价值，不能有效判断其未来收益率概率分布。同样是80元转股价值，转债价格可能是85元、110元或140元，三种情况下风险收益结构完全不同。</p><p>因此，不能因为转股价值高或低，就直接判断转债股性强弱、上涨空间或者下跌风险。</p>`,
      meaning: `<p class="standalone-answer">单独无意义。</p><p>转股价值的价值主要在于作为其他研究对象的组成部分。</p><blockquote>转债价格 + 转股价值 → 转股溢价率</blockquote><p>真正具有较强解释意义的是这些参数之间形成的关系，而不是转股价值的绝对数值本身。这些组合关系放在后续“研究对象组合”阶段统一研究。</p>`
    }
  },
  {
    id: "3.1.5",
    name: "转股溢价率",
    status: "draft",
    sections: {
      type: `<span class="type-label">派生参数</span><p>由转债市场价格和转股价值计算得到。</p>`,
      definition: `<p>转股溢价率衡量转债价格相对于当前转股价值高出或低出的比例。</p><blockquote>转股溢价率 = 转债价格 ÷ 转股价值 − 1</blockquote><p>它表示投资者按当前价格买入转债并立即转股，相对于取得股票市场价值所支付的溢价。</p>`,
      standalone: `<p class="standalone-answer">有较强意义，但仍不能单独完成判断。</p><p>溢价率会直接影响转债对正股涨跌的敏感程度，并反映当前转股兑现需要跨越的价格距离。但低溢价不等于低风险，高溢价也不必然代表没有机会。</p>`,
      meaning: `<p>转股溢价率影响未来收益分布的形状：</p><ul><li>溢价率较低时，转债价格更容易随正股变动；</li><li>溢价率较高时，正股需要更大涨幅才能通过转股价值支撑当前债价；</li><li>其意义会受到剩余期限、波动率、债底、条款事件和转债绝对价格影响。</li></ul><p class="research-note">它本身已经是“转债价格与转股价值”的组合关系，需要讨论它应属于单对象阶段还是组合阶段。</p>`
    }
  },
  {
    id: "3.1.6",
    name: "正股价格相对转股价格的位置",
    status: "draft",
    sections: {
      type: `<span class="type-label">派生参数</span><p>由正股价格除以当前转股价格得到，是一个相对位置指标。</p>`,
      definition: `<p>该对象描述正股价格处于转股价格之上还是之下，以及两者之间的比例距离。</p><blockquote>相对位置 = 正股价格 ÷ 转股价格</blockquote><p>比值为1表示平价转股；高于1表示正股价格高于转股价格；低于1表示正股价格低于转股价格。</p>`,
      standalone: `<p class="standalone-answer">单独无意义。</p><p>它能描述转换权当前处于价内还是价外，却不知道投资者为转债支付了多少，也不知道债底和剩余期限，因而不能独立判断未来收益率分布。</p>`,
      meaning: `<p>它有助于识别转换权的价内、平价或价外状态，并与期限、波动率共同影响期权敏感度。</p><p>但该比值乘以100后，数值上等于面值100元转债的转股价值，因此与“转股价值”高度同构。</p><p class="research-note">初步判断：不宜和转股价值同时作为独立对象，可能应合并为同一对象的两种表达。</p>`
    }
  },
  {
    id: "3.1.7",
    name: "转债 Delta / 股性敏感度",
    status: "draft",
    sections: {
      type: `<span class="type-label">派生参数</span><p>通常由期权定价模型估算，或根据价格变化进行经验估计。</p>`,
      definition: `<p>转债Delta描述在其他条件近似不变时，正股价格发生小幅变化，转债理论价值相应变化的敏感程度。</p><p>不同模型、参数和单位口径可能给出不同Delta，研究时必须说明计算方法，不能把平台展示值视为无条件客观事实。</p>`,
      standalone: `<p class="standalone-answer">有意义，但属于局部、条件化的敏感度。</p><p>较高Delta通常意味着转债短期更接近股票，较低Delta意味着对正股小幅波动不敏感。但Delta会随正股价格、波动率、剩余期限、信用和条款变化，并不是固定属性。</p>`,
      meaning: `<p>Delta主要描述收益分布对正股小幅变动的局部响应，可用于比较股性暴露和估算对冲比例。</p><p>它不能直接告诉我们最大下行、长期上涨空间或离散事件后的跳跃变化；对于存在强赎、下修和信用风险的转债，模型Delta可能明显偏离实际表现。</p><p class="research-note">需讨论是否保留为独立对象，以及采用理论Delta还是经验Delta。</p>`
    }
  },
  {
    id: "3.1.8",
    name: "转债实际跟涨弹性",
    status: "draft",
    sections: {
      type: `<span class="type-label">派生参数</span><p>根据一段观察期内正股与转债的实际价格变化计算或统计得到。</p>`,
      definition: `<p>实际跟涨弹性描述在特定时间窗口内，正股上涨或下跌时转债价格实际跟随的幅度。</p><blockquote>一种简化表达：转债涨跌幅 ÷ 正股涨跌幅</blockquote><p>观察窗口、涨跌方向、起止价格和异常事件都会影响结果，因此不存在唯一固定口径。</p>`,
      standalone: `<p class="standalone-answer">有历史描述意义，但不能直接外推未来。</p><p>过去跟涨较强，可能来自当时较低的溢价率、市场情绪或特殊事件；条件变化后，未来弹性可能完全不同。</p>`,
      meaning: `<p>它可以检验理论股性是否在真实交易中兑现，并暴露模型未覆盖的供需、流动性和情绪因素。</p><p>研究未来收益分布时，应把它视为经验性证据，并与当前转股溢价率、Delta、成交和事件状态结合。</p><p class="research-note">需要先统一观察窗口和方向口径，否则不同个券之间无法比较。</p>`
    }
  },
  {
    id: "3.1.9",
    name: "期权价值",
    status: "draft",
    sections: {
      type: `<span class="type-label">派生参数</span><p>由估值模型或“转债价值减去纯债价值”的残差方法估算。</p>`,
      definition: `<p>期权价值是可转债中与未来转换权及相关选择权有关的价值部分。常见简化口径是：</p><blockquote>期权价值 ≈ 转债价值 − 纯债价值</blockquote><p>但转债同时包含下修、回售、强赎等相互作用的条款，残差不一定等同于单一标准看涨期权价值。</p>`,
      standalone: `<p class="standalone-answer">有估值分解意义，但高度依赖模型。</p><p>较高的估算期权价值可能意味着市场为未来正股上涨和条款权利支付了较多价格，却不能单独判断这部分价格合理与否。</p>`,
      meaning: `<p>期权价值有助于把转债价格拆分为债性保护和股性权利，进而研究投资者实际为不确定上行支付了多少。</p><p>其解释力取决于纯债价值、波动率、信用利差、期限和条款建模是否可靠。不同模型结果可能差异很大。</p><p class="research-note">需要澄清对象名称究竟指模型期权价值、市场隐含期权价值，还是转债价格减纯债价值。</p>`
    }
  }
];

const sectionMeta = [
  ["type", "对象类型"],
  ["definition", "定义"],
  ["standalone", "单独对未来收益率概率分布是否有意义"],
  ["meaning", "对未来收益率概率分布的具体意义"]
];

const objectDecisions = [
  ["keep", "保留"],
  ["rename", "改名"],
  ["merge", "合并"],
  ["component", "降为组成项"],
  ["delete", "删除"],
  ["uncertain", "暂不确定"]
];

const verdicts = [
  ["agree", "认可"],
  ["partial", "部分认可"],
  ["rewrite", "退回重写"],
  ["pending", "暂不判断"]
];

const storageKey = "arbitrage-object-review-3.1-v1";
let state = loadState();
let activeIndex = Number.isInteger(state.activeIndex) ? Math.min(state.activeIndex, objects.length - 1) : 0;

const $ = selector => document.querySelector(selector);

function blankReview() {
  return { objectDecision: "", verdict: "", fieldNotes: {}, overallNote: "", followupQuestion: "", completed: false, updatedAt: "" };
}

function reviewFor(id) {
  return { ...blankReview(), ...(state.reviews[id] || {}), fieldNotes: { ...(state.reviews[id]?.fieldNotes || {}) } };
}

function loadState() {
  try {
    const parsed = JSON.parse(localStorage.getItem(storageKey));
    return parsed && parsed.schemaVersion === 1 ? parsed : { schemaVersion: 1, activeIndex: 0, reviews: {}, updatedAt: "" };
  } catch {
    return { schemaVersion: 1, activeIndex: 0, reviews: {}, updatedAt: "" };
  }
}

function saveState(showMessage = false) {
  state.activeIndex = activeIndex;
  state.updatedAt = new Date().toISOString();
  localStorage.setItem(storageKey, JSON.stringify(state));
  updateProgress();
  updateSaveLabel();
  if (showMessage) showToast("已保存在当前浏览器");
}

function updateReview(patch) {
  const id = objects[activeIndex].id;
  state.reviews[id] = { ...reviewFor(id), ...patch, updatedAt: new Date().toISOString() };
  saveState();
  renderNavigation();
  renderStatus();
}

function render() {
  const item = objects[activeIndex];
  $("#objectCode").textContent = `对象 ${item.id}`;
  $("#objectName").textContent = item.name;
  $("#draftBadge").textContent = item.status === "sample" ? "成熟样例" : "AI初稿";
  $("#draftBadge").classList.toggle("approved", item.status === "sample");
  $("#positionText").textContent = `${activeIndex + 1} / ${objects.length}`;
  $("#previousObject").disabled = activeIndex === 0;
  $("#nextObject").disabled = activeIndex === objects.length - 1;
  renderResearch(item);
  renderNavigation();
  renderReview();
  updateProgress();
  updateSaveLabel();
}

function renderResearch(item) {
  $("#researchContent").innerHTML = sectionMeta.map(([key, title], index) => `
    <section class="research-block">
      <header><span>${index + 1}</span><h3>${title}</h3></header>
      ${item.sections[key]}
    </section>
  `).join("");
}

function renderNavigation() {
  $("#objectList").innerHTML = objects.map((item, index) => {
    const review = reviewFor(item.id);
    return `<button type="button" class="object-item ${index === activeIndex ? "active" : ""} ${review.completed ? "reviewed" : ""}" data-index="${index}">
      <span class="number">${String(index + 1).padStart(2, "0")}</span>
      <span class="name">${item.name}</span>
      <i class="state-dot" aria-hidden="true"></i>
    </button>`;
  }).join("");
  document.querySelectorAll(".object-item").forEach(button => {
    button.addEventListener("click", () => switchObject(Number(button.dataset.index)));
  });
}

function renderReview() {
  const item = objects[activeIndex];
  const review = reviewFor(item.id);
  $("#objectDecision").innerHTML = choiceButtons(objectDecisions, review.objectDecision, "object-decision");
  $("#contentVerdict").innerHTML = choiceButtons(verdicts, review.verdict, "content-verdict");
  $("#fieldEditors").innerHTML = sectionMeta.map(([key, title]) => `
    <details class="field-editor" ${review.fieldNotes[key] ? "open" : ""}>
      <summary>${title}<span>${review.fieldNotes[key] ? "已填写" : "可选"}</span></summary>
      <textarea data-field="${key}" placeholder="我的修改稿、补充或反驳……">${escapeHtml(review.fieldNotes[key] || "")}</textarea>
    </details>
  `).join("");
  $("#overallNote").value = review.overallNote;
  $("#followupQuestion").value = review.followupQuestion;
  bindReviewEvents();
  renderStatus();
}

function choiceButtons(options, selected, attribute) {
  return options.map(([value, label]) => `<button type="button" class="choice-button ${selected === value ? "selected" : ""}" data-${attribute}="${value}">${label}</button>`).join("");
}

function bindReviewEvents() {
  document.querySelectorAll("[data-object-decision]").forEach(button => {
    button.addEventListener("click", () => {
      updateReview({ objectDecision: button.dataset.objectDecision, completed: false });
      renderReview();
    });
  });
  document.querySelectorAll("[data-content-verdict]").forEach(button => {
    button.addEventListener("click", () => {
      updateReview({ verdict: button.dataset.contentVerdict, completed: false });
      renderReview();
    });
  });
  document.querySelectorAll("[data-field]").forEach(textarea => {
    textarea.addEventListener("input", event => {
      const review = reviewFor(objects[activeIndex].id);
      review.fieldNotes[event.target.dataset.field] = event.target.value;
      updateReview({ fieldNotes: review.fieldNotes, completed: false });
    });
  });
  $("#overallNote").addEventListener("input", event => updateReview({ overallNote: event.target.value, completed: false }));
  $("#followupQuestion").addEventListener("input", event => updateReview({ followupQuestion: event.target.value, completed: false }));
}

function renderStatus() {
  const review = reviewFor(objects[activeIndex].id);
  const hasInput = review.objectDecision || review.verdict || review.overallNote || review.followupQuestion || Object.values(review.fieldNotes).some(Boolean);
  const status = $("#reviewStatus");
  status.className = "review-status";
  if (review.completed) {
    status.textContent = "已完成";
    status.classList.add("complete");
  } else if (hasInput) {
    status.textContent = "编辑中";
    status.classList.add("editing");
  } else {
    status.textContent = "未审查";
  }
}

function updateProgress() {
  const complete = objects.filter(item => reviewFor(item.id).completed).length;
  $("#progressText").textContent = `${complete} / ${objects.length}`;
  $("#progressBar").style.width = `${complete / objects.length * 100}%`;
}

function updateSaveLabel() {
  if (!state.updatedAt) return;
  const time = new Date(state.updatedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  $("#saveState").textContent = `已在 ${time} 保存到本机`;
  $("#localHint").textContent = `已自动保存到当前浏览器 · ${time}`;
}

function switchObject(index) {
  activeIndex = Math.max(0, Math.min(index, objects.length - 1));
  saveState();
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function completeCurrent() {
  const review = reviewFor(objects[activeIndex].id);
  if (!review.objectDecision || !review.verdict) {
    showToast("请先选择对象处理方式和总体判断");
    return;
  }
  updateReview({ completed: true, completedAt: new Date().toISOString() });
  render();
  showToast("这个对象已标记为完成");
}

function feedbackPackage(ids) {
  const selected = objects.filter(item => ids.includes(item.id));
  return {
    schemaVersion: 1,
    packageType: ids.length === 1 ? "single-object-review" : "batch-review",
    batch: "3.1 转股结构",
    sourceTemplate: "03 研究对象样例｜转股价值",
    exportedAt: new Date().toISOString(),
    objects: selected.map(item => ({
      id: item.id,
      name: item.name,
      draftStatus: item.status,
      review: reviewFor(item.id)
    }))
  };
}

function exportFeedback(ids, fileSuffix) {
  const data = feedbackPackage(ids);
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `转股结构审查反馈-${fileSuffix}-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
  showToast("反馈包已导出，可直接上传给我");
}

function importFeedback(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result);
      if (data.schemaVersion !== 1 || !Array.isArray(data.objects)) throw new Error("invalid");
      data.objects.forEach(item => {
        if (objects.some(object => object.id === item.id) && item.review) state.reviews[item.id] = item.review;
      });
      saveState();
      render();
      showToast("反馈包已导入并保存到本机");
    } catch {
      showToast("无法识别这个反馈包");
    }
  };
  reader.readAsText(file, "utf-8");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

let toastTimer;
function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2200);
}

$("#previousObject").addEventListener("click", () => switchObject(activeIndex - 1));
$("#nextObject").addEventListener("click", () => switchObject(activeIndex + 1));
$("#markComplete").addEventListener("click", completeCurrent);
$("#exportCurrent").addEventListener("click", () => exportFeedback([objects[activeIndex].id], objects[activeIndex].id));
$("#exportAll").addEventListener("click", () => exportFeedback(objects.map(item => item.id), "全部"));
$("#importFile").addEventListener("change", event => {
  const [file] = event.target.files;
  if (file) importFeedback(file);
  event.target.value = "";
});

render();
