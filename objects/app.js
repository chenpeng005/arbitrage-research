const objects = [
  {
    id: "3.1.1",
    name: "正股价格",
    status: "revision",
    sections: {
      type: `<span class="type-label">基础参数</span>`,
      definition: `<p>正股价格是可转债对应股票在某一时点的市场价格。</p>`,
      standalone: `<p class="standalone-answer">有。</p>`,
      meaning: `<p>在其他条件近似不变时，正股价格上升会提高转股价值，通常推动转债价格上升；正股价格下降则相反。因此，正股价格通过改变转股价值，影响转债未来收益率概率分布。</p>`
    }
  },
  {
    id: "3.1.2",
    name: "转股价格",
    status: "revision",
    sections: {
      type: `<span class="type-label">基础参数</span>`,
      definition: `<p>转股价格是将可转债转换为股票时，每取得一股股票所对应的债券面值金额。</p>`,
      standalone: `<p class="standalone-answer">无。</p>`,
      meaning: `<p class="standalone-answer">无独立意义。</p>`
    }
  },
  {
    id: "3.1.4",
    name: "转股价值 / 平价",
    status: "revision",
    sections: {
      type: `<span class="type-label">派生参数</span>`,
      definition: `<p>转股价值是指一张面值100元的可转债，按照当前转股价格转换成股票后，对应股票当前的市场价值。</p><blockquote>转股价值 = 100 × 正股价格 ÷ 转股价格</blockquote><p>例如，正股价格为8元，转股价格为10元，则转股价值为80元。</p>`,
      standalone: `<p class="standalone-answer">有。</p>`,
      meaning: `<p>在其他条件近似不变时，转股价值越高，转换权越接近价内或处于更深的价内状态，转债的 Delta 通常越高；同时，转股价值上升通常会推动转债价格上升。因此，转股价值会影响转债的股性强弱和未来收益率概率分布。</p>`
    }
  },
  {
    id: "3.1.5",
    name: "转股溢价率",
    status: "revision",
    sections: {
      type: `<span class="type-label">派生参数</span>`,
      definition: `<p>转股溢价率衡量转债价格相对于当前转股价值高出或低出的比例。</p><blockquote>转股溢价率 = 转债价格 ÷ 转股价值 − 1</blockquote><p>它表示投资者按当前价格买入转债并立即转股，相对于取得股票市场价值所支付的溢价。</p>`,
      standalone: `<p class="standalone-answer">有。</p>`,
      meaning: `<p>转股溢价率越低，转债价格与转股价值衔接越紧，正股价格变化通常越容易传导至转债价格；转股溢价率越高，正股需要更大幅度上涨，转股价值才可能对当前转债价格形成支撑。因此，它会影响转债对正股上涨的响应程度和未来收益率概率分布。</p>`
    }
  },
  {
    id: "3.1.7",
    name: "转债 Delta（标准化股性敏感度）",
    status: "clarify",
    sections: {
      type: `<span class="type-label">模型派生参数</span>`,
      definition: `<p>转债原始 Delta 是转债理论价值对正股价格的一阶敏感度；为便于不同转债之间比较，再除以转股比例，得到0至1之间的标准化 Delta。</p><blockquote>原始 Delta = ∂V<sub>转债</sub> ÷ ∂S<sub>正股</sub><br>标准化 Delta = 原始 Delta ÷ 转股比例<br>转股比例 = 100 ÷ 转股价格</blockquote><p>例如，标准化 Delta 为0.8，表示正股发生小幅变化时，转债理论价值大约承接了完全转股状态下80%的价格变化。</p>`,
      standalone: `<p class="standalone-answer">有。</p>`,
      meaning: `<p>Delta 越高，正股价格的小幅变化对转债价格的影响通常越大，转债的股性越强；Delta 越低，转债对正股小幅变化越不敏感。因此，Delta 描述了转债未来收益率概率分布对正股变化的局部敏感程度。</p>`
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

const storageKey = "arbitrage-object-review-3.1-v2";
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
    return parsed && parsed.schemaVersion === 2 ? parsed : { schemaVersion: 2, activeIndex: 0, reviews: {}, updatedAt: "" };
  } catch {
    return { schemaVersion: 2, activeIndex: 0, reviews: {}, updatedAt: "" };
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
  $("#draftBadge").textContent = item.status === "clarify" ? "待澄清稿" : "第二版";
  $("#draftBadge").classList.toggle("approved", item.status === "revision");
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
  $("#fieldEditors").innerHTML = sectionMeta.map(([key, title], index) => `
    <section class="field-editor">
      <div class="field-editor-head">
        <label for="field-${key}">${index + 1}. ${title}</label>
        <span>${review.fieldNotes[key] ? "已填写" : `${index + 1} / 4`}</span>
      </div>
      <textarea id="field-${key}" data-field="${key}" rows="7" placeholder="写下对这一项的修改、补充、反驳或疑问……">${escapeHtml(review.fieldNotes[key] || "")}</textarea>
    </section>
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
    schemaVersion: 2,
    packageType: ids.length === 1 ? "single-object-review" : "batch-review",
    batch: "3.1 转股结构·第二版",
    draftVersion: "v2",
    sourceTemplate: "03 研究对象样例｜转股价值 + 2026-09-10 第一轮反馈",
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
  link.download = `转股结构审查反馈-V2-${fileSuffix}-${new Date().toISOString().slice(0, 10)}.json`;
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
      if (data.schemaVersion !== 2 || data.draftVersion !== "v2" || !Array.isArray(data.objects)) throw new Error("invalid");
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
