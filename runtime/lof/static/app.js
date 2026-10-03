(() => {
  const state = {
    rows: [],
    filtered: [],
    sortKey: "_estimate_premium_display",
    sortDir: "desc",
    snapshot: null,
    r2cProfiles: {},
    shadowRegistry: {},
    shadowSummary: {},
    estimateValidation: {},
    estimateValidationSummary: {},
    expandedCode: null,
    timer: null,
  };

  const $ = (id) => document.getElementById(id);
  const tbody = document.querySelector("#lofTable tbody");
  const headers = [...document.querySelectorAll("#lofTable th[data-key]")];

  const resolverLabels = {
    R1_DOMESTIC_INDEX: "国内指数",
    R2_DOMESTIC_OTHER: "国内其他",
    R3_QDII_INDEX: "海外QDII指数",
    R4_QDII_OTHER: "海外QDII其他",
    R5_SPECIAL: "商品/跨境特殊",
  };
  const r2SubclassLabels = {
    EQUITY: "R2-A 主动股票",
    MIXED: "R2-B 混合型",
    BOND: "R2-C 债券型",
    FOF: "R2-D FOF",
  };
  const resolverDisplayLabel = (row) => {
    if (row.resolver_class === "R2_DOMESTIC_OTHER") {
      return r2SubclassLabels[row.lof_type] || "R2-待归类";
    }
    return resolverLabels[row.resolver_class] || "未分类";
  };

  const matchResolverFilter = (row, value) => {
    if (!value) return true;
    if (value === "R2A_EQUITY") {
      return row.resolver_class === "R2_DOMESTIC_OTHER" && row.lof_type === "EQUITY";
    }
    if (value === "R2B_MIXED") {
      return row.resolver_class === "R2_DOMESTIC_OTHER" && row.lof_type === "MIXED";
    }
    if (value === "R2C_BOND") {
      return row.resolver_class === "R2_DOMESTIC_OTHER" && row.lof_type === "BOND";
    }
    if (value === "R2D_FOF") {
      return row.resolver_class === "R2_DOMESTIC_OTHER" && row.lof_type === "FOF";
    }
    return row.resolver_class === value;
  };

  const r2cT1Profile = (row) => {
    const rawType = String(row.fund_type_raw || "");
    const isBondLike = row.lof_type === "BOND" || rawType.includes("固收");
    if (!isBondLike) return null;
    return state.r2cProfiles[row.code] || null;
  };

  const statusLabels = {
    OPEN: "开放",
    LIMITED: "限额",
    SUSPENDED: "暂停",
    UNKNOWN: "未知",
  };
  const estimateMethodLabels = {
    INDEX_PROXY_PREV_CLOSE: "指数直连",
    CSI_COMPONENT_WEIGHT_PREV_CLOSE: "成分重建",
    TARGET_ETF_PREV_CLOSE: "目标ETF代理",
    MULTIDAY_PROXY_FX_BRIDGE: "跨日指数+汇率",
    HK_LIVE_INDEX_FX_BRIDGE: "香港指数实时",
    US_FUTURES_FX_BRIDGE: "美股期货桥接",
    US_LAST_CLOSE_FX_BRIDGE: "美股隔夜收盘",
    COMMODITY_FX_BRIDGE: "商品+汇率",
    DOMESTIC_FUTURES_PREV_SETTLEMENT: "国内期货主连",
    DISCLOSED_HOLDINGS_BASKET: "披露持仓篮子",
    R2B2_CASH_HEAVY_HOLDINGS_BASKET: "现金型持仓篮子",
    RISK_ASSET_OVERLAY: "风险资产覆盖",
  };

  const num = (v) => {
    if (v === null || v === undefined || v === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };

  const fmt = (v, digits = 3) => {
    const n = num(v);
    return n === null ? "—" : n.toFixed(digits);
  };

  const fmtPct = (v) => {
    const n = num(v);
    if (n === null) return "—";
    return `${n > 0 ? "+" : ""}${n.toFixed(2)}%`;
  };

  const fmtAmount = (v) => {
    const n = num(v);
    if (n === null) return "—";
    if (Math.abs(n) >= 1e8) return `${(n / 1e8).toFixed(2)}亿`;
    if (Math.abs(n) >= 1e4) return `${(n / 1e4).toFixed(1)}万`;
    return n.toFixed(0);
  };

  const fmtVolume = (v) => {
    const n = num(v);
    if (n === null) return "—";
    if (Math.abs(n) >= 1e8) return `${(n / 1e8).toFixed(2)}亿`;
    if (Math.abs(n) >= 1e4) return `${(n / 1e4).toFixed(1)}万`;
    return n.toFixed(0);
  };

  const fmtLimit = (v, subscriptionStatus) => {
    if (subscriptionStatus === "SUSPENDED") return "—";
    const n = num(v);
    if (n === null) return "—";
    if (n >= 10000) return `${(n / 10000).toFixed(n % 10000 === 0 ? 0 : 1)}万`;
    return `${n.toFixed(n % 1 === 0 ? 0 : 2)}元`;
  };

  const fmtRatioPct = (v) => {
    const n = num(v);
    return n === null ? "—" : fmtPct(n * 100);
  };

  const executionText = (row) => {
    const sell = num(row.subscription_to_sell_days);
    const referenceSell = num(row.onsite_subscription_to_sell_days_reference);
    const confirm = num(row.subscription_confirmation_days);
    if (sell !== null) return `T申购 → T+${sell}可卖`;
    if (referenceSell !== null) {
      return `场内参考 T+${referenceSell}可卖`;
    }
    if (confirm !== null) return `T+${confirm}确认 · 可卖待核`;
    return "可卖待核";
  };

  const depthLevelText = (label, price, volume) => {
    const p = num(price);
    const v = num(volume);
    if (p === null) return `${label} —`;
    return `${label} ${p.toFixed(3)}${v === null ? "" : `×${v.toFixed(0)}手`}`;
  };

  const appendDepthCell = (tr, row) => {
    const td = document.createElement("td");
    td.className = row.quote_status === "FRESH" ? "depth" : "depth depth-stale";
    const bid = document.createElement("div");
    bid.textContent = `${depthLevelText("买1", row.bid1_price, row.bid1_volume)} · ${depthLevelText("买2", row.bid2_price, row.bid2_volume)}`;
    const ask = document.createElement("div");
    ask.textContent = `${depthLevelText("卖1", row.ask1_price, row.ask1_volume)} · ${depthLevelText("卖2", row.ask2_price, row.ask2_volume)}`;
    td.append(bid, ask);
    const bidPremium = num(row.bid1_estimated_premium_rate);
    if (bidPremium !== null) {
      const premium = document.createElement("div");
      premium.className = `depth-premium ${premiumClass(bidPremium)}`;
      premium.textContent = `买1可卖溢价 ${fmtPct(bidPremium)}`;
      td.appendChild(premium);
    }
    if (row.quote_status !== "FRESH") {
      td.title = "盘口不是当前新鲜行情，仅保留最近行情参考";
    }
    tr.appendChild(td);
    return td;
  };

  const validationProfile = (row) => state.estimateValidation[row.code] || null;

  const currentValidation = (validation) =>
    validation?.current_version || null;

  const qualityText = (row) => {
    const validation = validationProfile(row);
    const current = currentValidation(validation);
    const samples = current ? Number(current.sample_count || 0) : 0;
    const mae = current ? num(current.mae_pct) : null;
    if (samples > 0 && mae !== null) {
      return `当前版 MAE ${mae.toFixed(2)}% · ${samples}天`;
    }
    if (validation?.current_model_version) {
      const historyDays = Number(validation.sample_count || 0);
      return historyDays > 0
        ? `当前版待验证 · 历史${historyDays}天`
        : "当前版待验证";
    }
    const profile = r2cT1Profile(row);
    const t1Mae = profile ? num(profile.mae_abs_return) : null;
    if (t1Mae !== null) {
      return `T-1参考 ${t1Mae.toFixed(2)}%`;
    }
    if (
      currentEstimateAvailable(row)
      || num(row.last_estimated_nav) !== null
    ) {
      return "待验证";
    }
    return "—";
  };

  const researchProfile = (row) => state.shadowRegistry[row.code] || null;

  const researchStateText = (row) => {
    const profile = researchProfile(row);
    if (!profile) return "—";
    if (profile.main_estimate_covered) {
      return profile.main_estimate_available_now
        ? "主估值·可用"
        : "主估值覆盖";
    }
    if (profile.shadow_active) {
      const suffix = profile.t1_profile_available ? " + T-1" : "";
      return `Shadow ${profile.shadow_model_count || 1}路${suffix}`;
    }
    if (profile.t1_profile_available) return "T-1质量";
    return "—";
  };

  const researchStateTitle = (row) => {
    const profile = researchProfile(row);
    if (!profile) return "";
    const parts = [];
    (profile.models || []).forEach((model) => {
      const status = model.status || "UNKNOWN";
      const method = model.method || model.source || "Shadow";
      parts.push(`${method}: ${status}`);
    });
    if (profile.validation_evaluated_count) {
      const mae = num(profile.validation_best_mae_pct);
      parts.push(
        mae === null
          ? `已验证 ${profile.validation_evaluated_count} 个样本`
          : `已验证 ${profile.validation_evaluated_count} 个样本，最佳MAE ${mae.toFixed(2)}%`
      );
    }
    return parts.join("；");
  };

  const qualityTitle = (row) => {
    const validation = validationProfile(row);
    if (validation?.current_model_version) {
      const current = currentValidation(validation) || {};
      const parts = [
        `当前模型 ${validation.current_model_version}`,
        `当前版本 ${Number(current.sample_count || 0)} 天`,
      ];
      if (num(current.mae_pct) !== null) parts.push(`累计MAE ${num(current.mae_pct).toFixed(3)}%`);
      ["1", "3", "5", "10"].forEach((days) => {
        const window = validation.windows?.[days];
        const count = Number(window?.sample_count || 0);
        const mae = num(window?.mae_pct);
        parts.push(
          count >= Number(days) && mae !== null
            ? `${days}日 MAE ${mae.toFixed(3)}%`
            : `${days}日 样本不足(${count}/${days})`
        );
      });
      if (num(current.p90_abs_error_pct) !== null) parts.push(`P90 ${num(current.p90_abs_error_pct).toFixed(3)}%`);
      if (num(current.bias_pct) !== null) parts.push(`Bias ${num(current.bias_pct).toFixed(3)}%`);
      if (num(current.max_abs_error_pct) !== null) parts.push(`最大误差 ${num(current.max_abs_error_pct).toFixed(3)}%`);
      if (Number(validation.sample_count || 0) > Number(current.sample_count || 0)) {
        parts.push(`历史累计 ${validation.sample_count} 天`);
      }
      return parts.join("；");
    }
    const profile = r2cT1Profile(row);
    if (!profile) return "";
    const mae = num(profile.mae_abs_return);
    const up95 = num(profile.up95);
    const samples = Number(profile.return_sample_count || 0);
    const endDate = profile.history_end_date || "未知";
    const parts = [];
    if (mae !== null) parts.push(`T-1近似MAE ${mae.toFixed(2)}%`);
    if (up95 !== null) parts.push(`95%上行带 ${up95.toFixed(2)}%`);
    if (samples) parts.push(`样本 ${samples}`);
    parts.push(`截至 ${endDate}`);
    return `${parts.join("；")}。这是T-1历史波动参考，不是实时估值评级。`;
  };

  const cell = (tr, text, className = "") => {
    const td = document.createElement("td");
    td.textContent = text;
    if (className) td.className = className;
    tr.appendChild(td);
    return td;
  };

  const currentEstimateAvailable = (row) =>
    row.estimated_nav_status === "AVAILABLE"
    && num(row.estimated_nav) !== null;

  const displayedEstimatedNav = (row) =>
    currentEstimateAvailable(row)
      ? row.estimated_nav
      : row.last_estimated_nav;

  const displayedEstimatedPremium = (row) =>
    currentEstimateAvailable(row)
      ? row.estimated_premium_rate
      : row.last_estimated_premium_rate;

  const estimateTimeText = (value) => {
    if (!value) return "";
    return String(value).replace("T", " ").slice(5, 16);
  };

  const estimatedMethodText = (row) => {
    if (currentEstimateAvailable(row)) {
      const method = estimateMethodLabels[row.estimated_nav_method] || "实时估算";
      return `${method} · 实时`;
    }
    if (num(row.last_estimated_nav) !== null) {
      const time = estimateTimeText(row.last_estimated_nav_time);
      return time ? `最后可靠 ${time}` : "最后可靠估值";
    }
    return "";
  };

  const appendEstimatedNavCell = (tr, row) => {
    const current = currentEstimateAvailable(row);
    const value = displayedEstimatedNav(row);
    const td = cell(
      tr,
      fmt(value, 4),
      `num primary-col ${current ? "" : "last-estimate"}`
    );
    const methodText = estimatedMethodText(row);
    if (methodText) {
      const method = document.createElement("div");
      method.className = "estimate-method";
      method.textContent = methodText;
      td.appendChild(method);
    }
    if (!current && num(row.last_estimated_nav) !== null) {
      const method =
        estimateMethodLabels[row.last_estimated_nav_method]
        || row.last_estimated_nav_method
        || "历史估值";
      td.title = `${method}；最后可靠估值时间 ${row.last_estimated_nav_time || "未知"}；行情时间 ${row.last_estimated_quote_time || "未知"}`;
    }
    if (num(value) !== null) {
      td.classList.add("estimate-clickable");
      const hint = document.createElement("div");
      hint.className = "estimate-detail-hint";
      hint.textContent = "点击看方法";
      td.appendChild(hint);
      td.addEventListener("click", (event) => {
        event.stopPropagation();
        state.expandedCode = state.expandedCode === row.code ? null : row.code;
        renderTable();
      });
    }
    return td;
  };

  const detailValue = (row, currentKey, lastKey) =>
    currentEstimateAvailable(row)
      ? row[currentKey]
      : row[lastKey];

  const buildEstimateDetailRow = (row) => {
    const detail = document.createElement("tr");
    detail.className = "estimate-detail-row";
    const td = document.createElement("td");
    td.colSpan = headers.length;
    const methodCode = detailValue(
      row,
      "estimated_nav_method",
      "last_estimated_nav_method"
    );
    const method = estimateMethodLabels[methodCode] || methodCode || "未知";
    const proxy = detailValue(
      row,
      "estimated_nav_proxy",
      "last_estimated_nav_proxy"
    );
    const proxyTime = detailValue(
      row,
      "estimated_nav_proxy_time",
      "last_estimated_nav_proxy_time"
    );
    const proxyReturn = detailValue(
      row,
      "estimated_nav_proxy_return",
      "last_estimated_nav_proxy_return"
    );
    const fxReturn = detailValue(
      row,
      "estimated_nav_fx_return",
      "last_estimated_nav_fx_return"
    );
    const exposure = detailValue(
      row,
      "estimated_nav_exposure_ratio",
      "last_estimated_nav_exposure_ratio"
    );
    const adjustment = detailValue(
      row,
      "estimated_nav_tracking_adjustment",
      "last_estimated_nav_tracking_adjustment"
    );
    const estimateTime = currentEstimateAvailable(row)
      ? row.estimated_nav_time
      : row.last_estimated_nav_time;
    const validation = validationProfile(row);
    const anchorNav = currentEstimateAvailable(row)
      ? row.official_nav
      : row.last_estimated_anchor_nav;
    const anchorDate = currentEstimateAvailable(row)
      ? row.official_nav_date
      : row.last_estimated_anchor_nav_date;

    const title = document.createElement("strong");
    title.textContent = `估值方法：${method}`;
    const facts = document.createElement("div");
    facts.className = "estimate-detail-facts";
    const items = [
      `基准NAV ${fmt(anchorNav, 4)}（${anchorDate || "未知"}）`,
      proxy ? `代理 ${proxy}` : "",
      proxyReturn === null || proxyReturn === undefined ? "" : `代理变动 ${fmtRatioPct(proxyReturn)}`,
      fxReturn === null || fxReturn === undefined ? "" : `汇率变动 ${fmtRatioPct(fxReturn)}`,
      exposure === null || exposure === undefined ? "" : `暴露 ${(Number(exposure) * 100).toFixed(1)}%`,
      adjustment === null || adjustment === undefined ? "" : `跟踪调整 ×${Number(adjustment).toFixed(4)}`,
      estimateTime ? `估值时间 ${String(estimateTime).replace("T", " ").slice(0, 19)}` : "",
      proxyTime ? `底层时间 ${String(proxyTime).replace("T", " ").slice(0, 19)}` : "",
    ].filter(Boolean);
    items.forEach((text) => {
      const span = document.createElement("span");
      span.textContent = text;
      facts.appendChild(span);
    });

    const validationBox = document.createElement("div");
    validationBox.className = "estimate-detail-validation";
    if (validation?.current_model_version) {
      const current = currentValidation(validation) || {};
      const windowParts = ["1", "3", "5", "10"].map((days) => {
        const window = validation.windows?.[days] || {};
        const count = Number(window.sample_count || 0);
        const mae = num(window.mae_pct);
        return count >= Number(days) && mae !== null
          ? `${days}日 MAE ${mae.toFixed(3)}%`
          : `${days}日 样本不足 ${count}/${days}`;
      });
      const metrics = [];
      if (num(current.mae_pct) !== null) metrics.push(`累计MAE ${num(current.mae_pct).toFixed(3)}%`);
      if (num(current.p90_abs_error_pct) !== null) metrics.push(`P90 ${num(current.p90_abs_error_pct).toFixed(3)}%`);
      if (num(current.bias_pct) !== null) metrics.push(`Bias ${num(current.bias_pct).toFixed(3)}%`);
      if (num(current.max_abs_error_pct) !== null) metrics.push(`最大误差 ${num(current.max_abs_error_pct).toFixed(3)}%`);
      validationBox.textContent =
        `真实NAV对账：当前模型 ${validation.current_model_version}；当前版本 ${Number(current.sample_count || 0)}天${metrics.length ? "；" + metrics.join("；") : ""}；${windowParts.join("；")}；历史累计 ${Number(validation.sample_count || 0)}天。`;
    } else {
      validationBox.textContent = "真实NAV对账：当前版本尚无样本，先保留结构质量与时间状态，不给主观高/中/低结论。";
    }
    td.append(title, facts, validationBox);
    detail.appendChild(td);
    return detail;
  };

  const premiumClass = (value) => {
    const n = num(value);
    if (n === null) return "";
    return n > 0 ? "positive" : n < 0 ? "negative" : "";
  };

  const premiumBasisText = (row) => {
    if (currentEstimateAvailable(row)) return "实时估算";
    if (num(row.last_estimated_nav) !== null) {
      const time = estimateTimeText(row.last_estimated_nav_time);
      return time ? `最后可靠 ${time}` : "最后可靠";
    }
    return "";
  };

  const appendPremiumCell = (tr, row) => {
    const value = displayedEstimatedPremium(row);
    const td = cell(
      tr,
      fmtPct(value),
      `num primary-col strong ${premiumClass(value)} ${currentEstimateAvailable(row) ? "" : "last-estimate"}`
    );
    const basisText = premiumBasisText(row);
    if (basisText) {
      const basis = document.createElement("div");
      basis.className = "premium-basis";
      basis.textContent = basisText;
      td.appendChild(basis);
    }
    return td;
  };

  function rowSortValue(row, key) {
    if (key === "_index") return row._index;
    if (key === "resolver_class") return resolverDisplayLabel(row);
    if (key === "_estimate_nav_display") {
      return displayedEstimatedNav(row);
    }
    if (key === "_estimate_premium_display") {
      return displayedEstimatedPremium(row);
    }
    if (key === "_validation") {
      return validationProfile(row)?.current_version?.mae_pct ?? null;
    }
    if (key === "_execution") {
      return row.subscription_to_sell_days
        ?? row.onsite_subscription_to_sell_days_reference
        ?? row.subscription_confirmation_days
        ?? null;
    }
    if (key === "_depth") {
      return row.bid1_price;
    }
    const value = row[key];
    const n = num(value);
    return n !== null ? n : (value ?? "");
  }

  function compareRows(a, b) {
    const av = rowSortValue(a, state.sortKey);
    const bv = rowSortValue(b, state.sortKey);
    const aMissing = av === "" || av === null || av === undefined;
    const bMissing = bv === "" || bv === null || bv === undefined;
    if (aMissing !== bMissing) return aMissing ? 1 : -1;
    let result = 0;
    if (typeof av === "number" && typeof bv === "number") result = av - bv;
    else result = String(av).localeCompare(String(bv), "zh-CN");
    return state.sortDir === "asc" ? result : -result;
  }

  function applyFilters() {
    const q = $("searchInput").value.trim().toLowerCase();
    const resolver = $("resolverFilter").value;
    const sub = $("subscriptionFilter").value;
    const estimate = $("estimateFilter").value;

    state.filtered = state.rows.filter((row) => {
      const matchQ = !q || `${row.code || ""} ${row.name || ""}`.toLowerCase().includes(q);
      const matchResolver = matchResolverFilter(row, resolver);
      const matchSub = !sub || row.subscription_status === sub;
      const matchEstimate = !estimate
        || (estimate === "LAST"
          ? num(row.last_estimated_nav) !== null
          : row.estimated_nav_status === estimate);
      return matchQ && matchResolver && matchSub && matchEstimate;
    });
    state.filtered.sort(compareRows);
    renderTable();
  }

  function renderTable() {
    tbody.replaceChildren();
    const frag = document.createDocumentFragment();

    state.filtered.forEach((row, idx) => {
      const tr = document.createElement("tr");
      cell(tr, String(idx + 1), "num muted");

      const identity = document.createElement("td");
      const code = document.createElement("div");
      code.className = "code";
      code.textContent = row.code || "—";
      const name = document.createElement("div");
      name.className = "name";
      name.textContent = row.name || "—";
      identity.append(code, name);
      tr.appendChild(identity);

      cell(tr, resolverDisplayLabel(row), row.resolver_class === "R2_DOMESTIC_OTHER" ? "resolver-r2" : "");
      cell(tr, fmt(row.price, 3), "num");
      cell(tr, fmtPct(row.pct_change), `num ${premiumClass(row.pct_change)}`);
      cell(tr, fmtVolume(row.volume), "num");
      cell(tr, fmtAmount(row.amount), "num strong");
      appendDepthCell(tr, row);
      appendEstimatedNavCell(tr, row);
      appendPremiumCell(tr, row);
      const qualityCell = cell(tr, qualityText(row), `quality q-${(row.estimated_nav_quality || "unknown").toLowerCase()}`);
      const qTitle = qualityTitle(row);
      if (qTitle) qualityCell.title = qTitle;
      const researchCell = cell(tr, researchStateText(row), "research-state");
      const researchTitle = researchStateTitle(row);
      if (researchTitle) researchCell.title = researchTitle;
      cell(tr, fmtPct(row.static_premium_rate), `num ${premiumClass(row.static_premium_rate)}`);
      cell(tr, fmt(row.official_nav, 4), "num");
      const navLag = row.official_nav_lag_label || "";
      const navDateText = row.official_nav_date
        ? `${row.official_nav_date}${navLag ? ` · ${navLag}` : ""}`
        : "—";
      const navDateCell = cell(
        tr,
        navDateText,
        navLag === "T-1" ? "nav-fresh" : "nav-lagging"
      );
      navDateCell.title = [
        row.official_nav_source ? `来源 ${row.official_nav_source}` : "",
        navLag ? `新鲜度 ${navLag}` : "",
      ].filter(Boolean).join("；");
      cell(tr, statusLabels[row.subscription_status] || row.subscription_status || "未知");
      cell(tr, fmtLimit(row.daily_subscription_limit, row.subscription_status), "num");
      const executionCell = cell(tr, executionText(row), "execution");
      if (num(row.subscription_to_sell_days) === null) {
        const confirm = num(row.subscription_confirmation_days);
        const refSell = num(row.onsite_subscription_to_sell_days_reference);
        executionCell.title = refSell === null
          ? "确认日不等于真正可卖日；尚需按场内登记、转托管和券商执行链核实。"
          : `场内申购规则参考：T+${confirm ?? "?"}确认，确认日次日起份额可卖，因此参考 T+${refSell}。这是场内申购路径参考，不代表所有券商/场外转托管路径均已逐只核实。`;
      }
      cell(tr, statusLabels[row.redemption_status] || row.redemption_status || "未知");
      cell(tr, row.quote_time ? String(row.quote_time).replace("T", " ").slice(5, 19) : "—", "mono");

      frag.appendChild(tr);
      if (state.expandedCode === row.code) {
        frag.appendChild(buildEstimateDetailRow(row));
      }
    });

    tbody.appendChild(frag);
    $("emptyState").classList.toggle("hidden", state.filtered.length !== 0);
    headers.forEach((th) => {
      th.classList.toggle("sorted", th.dataset.key === state.sortKey);
      th.dataset.dir = th.dataset.key === state.sortKey ? state.sortDir : "";
    });
  }

  function renderSummary(snapshot) {
    const rows = state.rows;
    $("totalCount").textContent = snapshot.universe_count ?? rows.length;
    $("estimatedCount").textContent = rows.filter(
      (r) => r.estimated_nav_status === "AVAILABLE"
    ).length;
    $("lastEstimatedCount").textContent = rows.filter(
      (r) => num(r.last_estimated_nav) !== null
    ).length;
    $("subscribableCount").textContent = rows.filter(
      (r) => r.subscription_status === "OPEN" || r.subscription_status === "LIMITED"
    ).length;
    $("shadowCount").textContent =
      state.shadowSummary.active_shadow_fund_count ?? "—";
    $("validationCount").textContent =
      state.estimateValidationSummary.fund_count ?? "—";
    const navFreshness = snapshot.nav_freshness || {};
    const navTotal = Number(navFreshness.r1_total || 0);
    const navT1 = Number(navFreshness.r1_t1_count || 0);
    $("navFreshness").textContent =
      navTotal > 0 ? `${navT1}/${navTotal}` : "—";
    $("navFreshness").className =
      navFreshness.status === "PASS" ? "ok" : "warn";
    $("navFreshness").title = navFreshness.expected_nav_date
      ? `应有NAV日期：${navFreshness.expected_nav_date}；滞后：${navFreshness.r1_lagging_count || 0}只`
      : "尚未识别应有NAV日期";
    $("collectorStatus").textContent = snapshot.collector_status || "—";
    $("collectorStatus").className =
      snapshot.collector_status === "PASS" ? "ok" : "warn";
    const time = snapshot.generated_at || snapshot.market_cutoff || "";
    $("updateText").textContent = time
      ? `快照：${String(time).replace("T", " ").slice(0, 19)}`
      : "快照时间未知";
  }

  async function loadSnapshot() {
    $("refreshBtn").disabled = true;
    try {
      const response = await fetch("/api/lof/snapshot", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const snapshot = await response.json();
      let r2cProfiles = {};
      let shadowRegistry = {};
      let shadowSummary = {};
      let estimateValidation = {};
      let estimateValidationSummary = {};
      try {
        const profileResponse = await fetch("/api/lof/r2c-t1-profile", { cache: "no-store" });
        if (profileResponse.ok) {
          const profilePayload = await profileResponse.json();
          r2cProfiles = profilePayload.rows || {};
        }
      } catch (_) {
        r2cProfiles = {};
      }
      try {
        const registryResponse = await fetch("/api/lof/shadow-registry", { cache: "no-store" });
        if (registryResponse.ok) {
          const registryPayload = await registryResponse.json();
          shadowRegistry = registryPayload.rows || {};
          shadowSummary = registryPayload.summary || {};
        }
      } catch (_) {
        shadowRegistry = {};
        shadowSummary = {};
      }
      try {
        const validationResponse = await fetch("/api/lof/estimate-validation", { cache: "no-store" });
        if (validationResponse.ok) {
          const validationPayload = await validationResponse.json();
          estimateValidation = validationPayload.rows || {};
          estimateValidationSummary = validationPayload.summary || {};
        }
      } catch (_) {
        estimateValidation = {};
        estimateValidationSummary = {};
      }
      state.snapshot = snapshot;
      state.r2cProfiles = r2cProfiles;
      state.shadowRegistry = shadowRegistry;
      state.shadowSummary = shadowSummary;
      state.estimateValidation = estimateValidation;
      state.estimateValidationSummary = estimateValidationSummary;
      state.rows = (snapshot.rows || []).map((r, i) => ({ ...r, _index: i + 1 }));
      renderSummary(snapshot);
      applyFilters();
    } catch (error) {
      $("updateText").textContent = `加载失败：${error.message}`;
      $("collectorStatus").textContent = "不可用";
      $("collectorStatus").className = "warn";
    } finally {
      $("refreshBtn").disabled = false;
    }
  }

  function updateAutoRefresh() {
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
    if ($("autoRefresh").checked) {
      state.timer = setInterval(loadSnapshot, 30000);
    }
  }

  headers.forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (state.sortKey === key) state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
      else {
        state.sortKey = key;
        state.sortDir = ["code", "resolver_class", "subscription_status", "official_nav_date", "quote_time"].includes(key)
          ? "asc"
          : "desc";
      }
      applyFilters();
    });
  });

  ["searchInput", "resolverFilter", "subscriptionFilter", "estimateFilter"].forEach((id) => {
    $(id).addEventListener(id === "searchInput" ? "input" : "change", applyFilters);
  });
  $("refreshBtn").addEventListener("click", loadSnapshot);
  $("autoRefresh").addEventListener("change", updateAutoRefresh);

  updateAutoRefresh();
  loadSnapshot();
})();
