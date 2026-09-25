let ingressData = null;
const ingressSearch = document.querySelector("#ingressSearch");

function ingressEsc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, ch =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[ch]);
}

function renderIngressRows() {
  if (!ingressData) return;
  const term = ingressSearch.value.trim().toLowerCase();
  const rows = ingressData.rows.filter(row =>
    row.bond_code.includes(term) || row.bond_name.toLowerCase().includes(term));
  document.querySelector("#ingressTable").innerHTML = rows.length ? rows.map(row => {
    const path = row.path_inputs;
    const state = key => `<span class="ingress-wait" title="${ingressEsc(path[key].missing.join("、"))}">待补合同数据</span>`;
    return `<tr><td>${ingressEsc(row.bond_code)}</td><td>${ingressEsc(row.bond_name.replace(/转债$/, ""))}</td>` +
      `<td>${row.current_bond_price.toFixed(2)}</td><td>${row.current_conversion_value.toFixed(2)}</td>` +
      `<td>${row.remaining_months.toFixed(1)}</td><td>${row.remaining_size.toFixed(2)}</td>` +
      `<td>${state("MATURITY_CASH")}</td><td>${state("PUT")}</td><td>${state("DOWNWARD_REVISION")}</td></tr>`;
  }).join("") : `<tr><td colspan="9" class="muted">没有匹配的转债。</td></tr>`;
}

function renderIngress(result) {
  ingressData = result;
  const overall = document.querySelector("#ingressOverall");
  overall.className = "overall warning";
  overall.textContent = "市场输入已审计 · 合同待补";
  document.querySelector("#ingressMeta").textContent =
    `运行 ${result.run_id}｜市场截面 ${result.market_cutoff}｜快照 ${result.market_snapshot_id}｜` +
    `数据审计 ${result.audit.status}｜经济判断 ${result.discovery_status}`;
  document.querySelector("#ingressResult").classList.remove("hidden");
  document.querySelector("#ingressCounts").innerHTML = [
    ["市场数据已审计", result.audit.market_rows + " 只"],
    ["到期现金合同", "待获取与审计"],
    ["普通回售条款", "待获取与审计"],
    ["下修条款与硬底价", "待获取与审计"]
  ].map(([label,value]) => `<article><small>${label}</small><strong>${value}</strong></article>`).join("");
  renderIngressRows();
}

async function loadIngress(method, url) {
  const button = document.querySelector("#ingressRunBtn");
  button.disabled = true;
  try {
    const response = await fetch(url, {method});
    if (!response.ok) {
      if (response.status === 404 && method === "GET") {
        document.querySelector("#ingressMeta").textContent = "尚未运行市场数据接入；先运行上游完整藏宝图，再点击接入。";
        return;
      }
      throw new Error(await response.text());
    }
    renderIngress(await response.json());
  } catch (error) {
    const overall = document.querySelector("#ingressOverall");
    overall.className = "overall fail";
    overall.textContent = "接入失败";
    document.querySelector("#ingressMeta").textContent = "市场数据接入失败：" + error.message;
  } finally {
    button.disabled = false;
  }
}

document.querySelector("#ingressRunBtn").addEventListener("click", () =>
  loadIngress("POST", "api/opportunity/market-ingress"));
ingressSearch.addEventListener("input", renderIngressRows);
loadIngress("GET", "api/opportunity/market-ingress/latest");
