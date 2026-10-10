(() => {
  const $ = (id) => document.getElementById(id);

  const statusLabels = {
    OPEN: "开放申购",
    LIMITED: "限额申购",
    SUSPENDED: "暂停申购",
    UNKNOWN: "未知",
  };

  const methodLabels = {
    INDEX_PROXY_PREV_CLOSE: "指数直连",
    CSI_COMPONENT_WEIGHT_PREV_CLOSE: "成分重建",
    TARGET_ETF_PREV_CLOSE: "目标ETF代理",
    MULTIDAY_PROXY_FX_BRIDGE: "跨日指数+汇率",
    HK_LIVE_INDEX_FX_BRIDGE: "香港指数实时",
    US_FUTURES_FX_BRIDGE: "美股期货桥接",
    US_LAST_CLOSE_FX_BRIDGE: "美股隔夜收盘",
    COMMODITY_FX_BRIDGE: "商品+汇率",
    COMMODITY_CNH_FALLBACK_BRIDGE: "商品+离岸人民币回退",
    COMMODITY_BASKET_FX_BRIDGE: "商品组合+汇率",
    COMMODITY_BASKET_CNH_FALLBACK_BRIDGE: "商品组合+离岸人民币回退",
    DISCLOSED_GOLD_EXPOSURE_FX_BRIDGE: "披露黄金暴露+汇率",
    DISCLOSED_GOLD_EXPOSURE_CNH_FALLBACK_BRIDGE: "披露黄金暴露+离岸人民币回退",
    DOMESTIC_FUTURES_PREV_SETTLEMENT: "国内期货主连",
    DISCLOSED_HOLDINGS_BASKET: "披露持仓篮子",
    R2B2_CASH_HEAVY_HOLDINGS_BASKET: "现金型持仓篮子",
    RISK_ASSET_OVERLAY: "风险资产覆盖",
  };

  const num = (value) => {
    const n = Number(value);
    return value === null || value === undefined || value === "" || !Number.isFinite(n) ? null : n;
  };

  const fmt = (value, digits = 4) => {
    const n = num(value);
    return n === null ? "—" : n.toFixed(digits);
  };

  const fmtPct = (value) => {
    const n = num(value);
    return n === null ? "—" : `${n > 0 ? "+" : ""}${n.toFixed(2)}%`;
  };

  const fmtTime = (value) => {
    if (!value) return "—";
    return String(value).replace("T", " ").slice(0, 19);
  };

  const fmtLimitAmount = (value) => {
    const n = num(value);
    if (n === null) return "—";
    if (Math.abs(n) > 1e8) return `${(n / 1e8).toFixed(2).replace(/\.00$/, "")}亿`;
    if (Math.abs(n) >= 1e4) return `${(n / 1e4).toFixed(1).replace(/\.0$/, "")}万`;
    return `${n.toFixed(Number.isInteger(n) ? 0 : 2)}元`;
  };

  const fmtLimit = (type, amount) => {
    if (type === "UNLIMITED") return "不限额";
    if (type === "NOT_APPLICABLE") return "不适用";
    if (type === "UNKNOWN") return "未知";
    return fmtLimitAmount(amount);
  };

  const identityCell = (row) => {
    const td = document.createElement("td");
    td.className = "identity";
    const code = document.createElement("div");
    code.className = "code";
    code.textContent = row.code || "—";
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = row.name || "—";
    td.append(code, name);
    return td;
  };

  const cell = (tr, text, className = "") => {
    const td = document.createElement("td");
    td.textContent = text;
    if (className) td.className = className;
    tr.appendChild(td);
    return td;
  };

  const setEmpty = (tbodyId, emptyId, count) => {
    $(emptyId).classList.toggle("hidden", count !== 0);
    $(tbodyId).closest("table").classList.toggle("hidden", count === 0);
  };

  function renderStatusChanges(rows) {
    const tbody = $("statusChanges");
    tbody.replaceChildren();
    rows.forEach((row, index) => {
      const tr = document.createElement("tr");
      cell(tr, String(index + 1));
      tr.appendChild(identityCell(row));
      cell(tr, statusLabels[row.old_status] || row.old_status || "—");
      cell(tr, statusLabels[row.new_status] || row.new_status || "—", "transition");
      cell(tr, fmtTime(row.first_observed_time));
      cell(tr, row.state_source || "—");
      tbody.appendChild(tr);
    });
    setEmpty("statusChanges", "statusEmpty", rows.length);
    $("statusChangeCount").textContent = String(rows.length);
    $("statusChangeMeta").textContent = rows.length ? `${rows.length} 条` : "无变化";
  }

  function renderLimitChanges(rows) {
    const tbody = $("limitChanges");
    tbody.replaceChildren();
    rows.forEach((row, index) => {
      const tr = document.createElement("tr");
      cell(tr, String(index + 1));
      tr.appendChild(identityCell(row));
      cell(tr, fmtLimit(row.old_limit_type, row.old_limit_amount));
      cell(tr, fmtLimit(row.new_limit_type, row.new_limit_amount), "transition");
      cell(tr, fmtTime(row.first_observed_time));
      cell(tr, row.state_source || "—");
      tbody.appendChild(tr);
    });
    setEmpty("limitChanges", "limitEmpty", rows.length);
    $("limitChangeCount").textContent = String(rows.length);
    $("limitChangeMeta").textContent = rows.length ? `${rows.length} 条` : "无变化";
  }

  function renderOfficial(rows, tbodyId, emptyId) {
    const tbody = $(tbodyId);
    tbody.replaceChildren();
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      cell(tr, String(row.rank || "—"));
      tr.appendChild(identityCell(row));
      cell(tr, fmt(row.price, 3));
      cell(tr, fmt(row.official_nav, 4));
      const premium = num(row.static_premium_rate);
      cell(tr, fmtPct(premium), premium !== null && premium >= 0 ? "positive" : "negative");
      cell(tr, `${row.official_nav_date || "—"}${row.official_nav_lag_label ? ` · ${row.official_nav_lag_label}` : ""}`);
      tbody.appendChild(tr);
    });
    setEmpty(tbodyId, emptyId, rows.length);
  }

  function renderEstimated(rows, tbodyId, emptyId) {
    const tbody = $(tbodyId);
    tbody.replaceChildren();
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      cell(tr, String(row.rank || "—"));
      tr.appendChild(identityCell(row));
      cell(tr, fmt(row.price, 3));
      cell(tr, fmt(row.estimated_nav, 4));
      const premium = num(row.estimated_premium_rate);
      cell(tr, fmtPct(premium), premium !== null && premium >= 0 ? "positive" : "negative");
      const method = methodLabels[row.estimated_nav_method] || row.estimated_nav_method || "—";
      cell(tr, `${row.estimated_nav_quality || "UNKNOWN"} · ${method}`);
      tbody.appendChild(tr);
    });
    setEmpty(tbodyId, emptyId, rows.length);
  }

  function renderPayload(payload) {
    const reminder = payload.reminder;
    const banner = $("statusBanner");
    if (!reminder) {
      banner.className = "banner warn";
      banner.textContent = `今日（${payload.requested_date || "—"}）未生成提醒，且暂无历史日报。`;
      $("tradeDate").textContent = "—";
      $("statusChangeCount").textContent = "—";
      $("limitChangeCount").textContent = "—";
      $("snapshotTime").textContent = "—";
      renderStatusChanges([]);
      renderLimitChanges([]);
      renderOfficial([], "officialPremium", "officialPremiumEmpty");
      renderOfficial([], "officialDiscount", "officialDiscountEmpty");
      renderEstimated([], "estimatedPremium", "estimatedPremiumEmpty");
      renderEstimated([], "estimatedDiscount", "estimatedDiscountEmpty");
      $("snapshotMeta").textContent = "暂无可展示日报。";
      return;
    }

    if (payload.status === "TODAY") {
      banner.className = "banner ok";
      banner.textContent = `今日提醒已生成：${reminder.trade_date}，20:55 定时任务已收口。`;
    } else {
      banner.className = "banner info";
      banner.textContent = `今日（${payload.requested_date}）未生成日报；当前显示最近一份真实交易日日报：${reminder.trade_date}。`;
    }

    $("tradeDate").textContent = reminder.trade_date || "—";
    $("snapshotTime").textContent = reminder.snapshot_market_cutoff ? fmtTime(reminder.snapshot_market_cutoff).slice(11, 16) : "—";

    const a = reminder.A_subscription_status_changes || [];
    const b = reminder.B_subscription_limit_changes || [];
    renderStatusChanges(a);
    renderLimitChanges(b);
    renderOfficial(reminder.C_official_premium_top5 || [], "officialPremium", "officialPremiumEmpty");
    renderOfficial(reminder.C_official_discount_top5 || [], "officialDiscount", "officialDiscountEmpty");
    renderEstimated(reminder.D_estimated_premium_top5 || [], "estimatedPremium", "estimatedPremiumEmpty");
    renderEstimated(reminder.D_estimated_discount_top5 || [], "estimatedDiscount", "estimatedDiscountEmpty");
    $("snapshotMeta").textContent = `Snapshot ${reminder.snapshot_id || "—"} · market_cutoff ${fmtTime(reminder.snapshot_market_cutoff)}`;
  }

  async function loadReminder() {
    $("refreshBtn").disabled = true;
    try {
      const response = await fetch("/api/lof/daily-reminder", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      renderPayload(await response.json());
    } catch (error) {
      const banner = $("statusBanner");
      banner.className = "banner warn";
      banner.textContent = `提醒加载失败：${error.message}`;
    } finally {
      $("refreshBtn").disabled = false;
    }
  }

  $("refreshBtn").addEventListener("click", loadReminder);
  loadReminder();
})();
