(() => {
  const state = {
    rows: [],
    filtered: [],
    sortKey: "display_premium_rate",
    sortDir: "desc",
    snapshot: null,
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

  const qualityText = (row) => {
    if (row.estimated_nav_status === "STALE") return "过期";
    if (row.estimated_nav_status !== "AVAILABLE") return "—";
    return ({ HIGH: "高", MEDIUM: "中", LOW: "低", UNKNOWN: "未知" })[
      row.estimated_nav_quality
    ] || "未知";
  };

  const cell = (tr, text, className = "") => {
    const td = document.createElement("td");
    td.textContent = text;
    if (className) td.className = className;
    tr.appendChild(td);
    return td;
  };

  const estimatedMethodText = (row) => {
    if (row.estimated_nav_status !== "AVAILABLE") return "";
    return estimateMethodLabels[row.estimated_nav_method] || "实时估算";
  };

  const appendEstimatedNavCell = (tr, row) => {
    const td = cell(tr, fmt(row.estimated_nav, 4), "num primary-col");
    const methodText = estimatedMethodText(row);
    if (methodText) {
      const method = document.createElement("div");
      method.className = "estimate-method";
      method.textContent = methodText;
      td.appendChild(method);
    }
    return td;
  };

  const premiumClass = (value) => {
    const n = num(value);
    if (n === null) return "";
    return n > 0 ? "positive" : n < 0 ? "negative" : "";
  };

  const premiumBasisText = (row) => {
    if (row.display_premium_basis === "ESTIMATED_NAV") return "实时估算";
    if (row.display_premium_basis === "OFFICIAL_NAV") {
      const lag = row.official_nav_lag_label || "官方NAV";
      return lag === "官方NAV" ? lag : `${lag}净值`;
    }
    return "";
  };

  const appendPremiumCell = (tr, row) => {
    const value = row.display_premium_rate;
    const td = cell(
      tr,
      fmtPct(value),
      `num primary-col strong ${premiumClass(value)}`
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
    if (key === "resolver_class") return resolverLabels[row.resolver_class] || "";
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
      const matchResolver = !resolver || row.resolver_class === resolver;
      const matchSub = !sub || row.subscription_status === sub;
      const matchEstimate = !estimate || row.estimated_nav_status === estimate;
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

      cell(tr, resolverLabels[row.resolver_class] || "未分类");
      cell(tr, fmt(row.price, 3), "num");
      cell(tr, fmtPct(row.pct_change), `num ${premiumClass(row.pct_change)}`);
      cell(tr, fmtVolume(row.volume), "num");
      cell(tr, fmtAmount(row.amount), "num strong");
      appendEstimatedNavCell(tr, row);
      appendPremiumCell(tr, row);
      cell(tr, qualityText(row), `quality q-${(row.estimated_nav_quality || "unknown").toLowerCase()}`);
      cell(tr, fmtPct(row.static_premium_rate), `num ${premiumClass(row.static_premium_rate)}`);
      cell(tr, fmt(row.official_nav, 4), "num");
      cell(tr, row.official_nav_date || "—");
      cell(tr, statusLabels[row.subscription_status] || row.subscription_status || "未知");
      cell(tr, fmtLimit(row.daily_subscription_limit, row.subscription_status), "num");
      cell(tr, statusLabels[row.redemption_status] || row.redemption_status || "未知");
      cell(tr, row.quote_time ? String(row.quote_time).replace("T", " ").slice(5, 19) : "—", "mono");

      frag.appendChild(tr);
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
    $("subscribableCount").textContent = rows.filter(
      (r) => r.subscription_status === "OPEN" || r.subscription_status === "LIMITED"
    ).length;
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
      state.snapshot = snapshot;
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
