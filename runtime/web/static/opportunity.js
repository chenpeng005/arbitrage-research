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

function renderIngress(result, focusResult = false) {
  ingressData = result;
  const overall = document.querySelector("#ingressOverall");
  overall.className = "overall warning";
  overall.textContent = "第一步完成 · 等待合同数据";
  document.querySelector("#ingressMeta").textContent =
    `本次已完成：${result.audit.market_rows} 只转债的市场数据获取与审计（市场截面 ${result.market_cutoff}）。` +
    "当前停在：到期现金、回售和下修合同事实尚未获取，因此还没有开始逐路径经济机会判断。" +
    "下一步：建设合同数据获取与审计。";
  document.querySelector("#ingressResult").classList.remove("hidden");
  document.querySelector("#ingressCounts").innerHTML = [
    ["市场数据已审计", result.audit.market_rows + " 只"],
    ["到期现金合同", "待获取与审计"],
    ["普通回售条款", "待获取与审计"],
    ["下修条款与硬底价", "待获取与审计"]
  ].map(([label,value]) => `<article><small>${label}</small><strong>${value}</strong></article>`).join("");
  renderIngressRows();
  const button = document.querySelector("#ingressRunBtn");
  button.textContent = "第一步已完成 · 可重新运行";
  if (focusResult) {
    document.querySelector("#ingressResult").scrollIntoView({behavior: "smooth", block: "start"});
  }
}

async function loadIngress(method, url) {
  const button = document.querySelector("#ingressRunBtn");
  button.disabled = true;
  button.textContent = "正在获取并审计市场数据…";
  try {
    const response = await fetch(url, {method});
    if (!response.ok) {
      if (response.status === 404 && method === "GET") {
        document.querySelector("#ingressMeta").textContent = "尚未运行市场数据接入；先运行上游完整藏宝图，再点击接入。";
        return;
      }
      throw new Error(await response.text());
    }
    renderIngress(await response.json(), method === "POST");
  } catch (error) {
    const overall = document.querySelector("#ingressOverall");
    overall.className = "overall fail";
    overall.textContent = "接入失败";
    document.querySelector("#ingressMeta").textContent = "市场数据接入失败：" + error.message;
  } finally {
    button.disabled = false;
    if (!ingressData) button.textContent = "运行市场数据接入";
  }
}

document.querySelector("#ingressRunBtn").addEventListener("click", () =>
  loadIngress("POST", "api/opportunity/market-ingress"));
ingressSearch.addEventListener("input", renderIngressRows);
loadIngress("GET", "api/opportunity/market-ingress/latest");
