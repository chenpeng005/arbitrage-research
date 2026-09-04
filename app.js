const signals = [
  {id:"01",instrument:"南航转债",code:"110075 · 可转债",event:"临近到期",aiDecision:"research",aiLabel:"建议研究",confidence:"中等置信",summary:"页面价格106.35元接近税前赎回价106.50元，表面上是小幅下行、保留正股弹性的结构。",caution:"个人投资者可能需对利息部分缴税，社区测算税后回收约105.20元；“最多亏1.5元”并不严谨。",data:"现价 106.35 · 税前赎回 106.50 · 最后交易日 10-09",source:"https://app.jisilu.cn/question/522483?gopage-true__page-1__item_id=5518045",sourceLabel:"集思录原帖",official:"",tag:"临期转债"},
  {id:"02",instrument:"洽洽转债",code:"128135 · 可转债",event:"临近到期",aiDecision:"watch",aiLabel:"建议观察",confidence:"中等置信",summary:"价格114.785元接近115元税前赎回价，但转股价值仅34.49元，向上期权非常弱。",caution:"需要确认利息税后的实际回收额；如果税后低于买入价，结构可能只是确定小亏且几乎没有上行。",data:"现价 114.785 · 税前赎回 115.00 · 最后交易日 10-14",source:"https://app.jisilu.cn/question/524995",sourceLabel:"9月4日投资提示",official:"",tag:"临期转债"},
  {id:"03",instrument:"晶能转债",code:"118034 · 可转债",event:"下修到底",aiDecision:"watch",aiLabel:"建议观察",confidence:"中等置信",summary:"转股价由6.35元降至4.36元，9月7日生效，属于已经落地的明确事件。",caution:"下修本身不是本金保护。需要补齐新转股价值、复牌价格、溢价率和发行人信用后才能判断。",data:"新转股价 4.36 · 9月7日生效",source:"https://app.jisilu.cn/question/524566",sourceLabel:"集思录讨论",official:"https://static.cninfo.com.cn/finalpage/2026-09-04/1225547209.PDF",tag:"转债下修"},
  {id:"04",instrument:"芳源转债",code:"118020 · 可转债",event:"再次提议下修",aiDecision:"watch",aiLabel:"建议观察",confidence:"较高置信",summary:"转股价此前已由18.63元两次下修至9元，董事会再次提议下修，股东会尚未表决。",caution:"集思录估算下修到底后仍有约29%溢价；即使下修通过，也未自然形成低风险收益。",data:"当前转股价 9.00 · 股东会 09-11 · 估算到底溢价 29.02%",source:"https://app.jisilu.cn/question/524995",sourceLabel:"9月4日投资提示",official:"https://static.cninfo.com.cn/finalpage/2026-09-04/1225545926.PDF",tag:"转债下修"},
  {id:"05",instrument:"优彩资源 / 优彩转债",code:"002998 / 127078",event:"筹划控制权变更",aiDecision:"research",aiLabel:"建议研究",confidence:"中等置信",summary:"这是可能扩展策略边界的新事件：控制权变化或影响正股，并可能传导至关联转债。",caution:"协议尚未签署且可能终止。控制权变更不等于触发要约，需要等待受让方、比例和交易方式。",data:"9月4日起停牌 · 预计不超过2个交易日",source:"https://app.jisilu.cn/question/524995",sourceLabel:"集思录汇总",official:"https://static.cninfo.com.cn/finalpage/2026-09-04/1225547328.PDF",tag:"控制权变更"},
  {id:"06",instrument:"创金合信北京国资公司REIT",code:"公募REIT",event:"上市大幅破发",aiDecision:"watch",aiLabel:"策略层观察",confidence:"中等置信",summary:"社区称认购中签率约80%、开盘跌约25%，暴露高比例中签与高发行定价叠加的非对称风险。",caution:"这不是当前套利机会，更像发行申购策略的失败样本；社区数字仍需以基金公告和行情核实。",data:"社区描述：中签约80% · 开盘跌约25%",source:"https://app.jisilu.cn/question/524995",sourceLabel:"集思录原帖",official:"",tag:"REITs发行"},
  {id:"07",instrument:"公募基金披露规则",code:"ETF / LOF",event:"十大持有人数据减少",aiDecision:"watch",aiLabel:"系统情报",confidence:"较低置信",summary:"帖子称公募基金不再继续披露十大持有人，可能影响ETF、LOF套利的拥挤度与持有人结构判断。",caution:"目前只有社区讨论，尚未核实正式规则和适用范围；它影响研究能力，不是直接交易机会。",data:"最新回复 09-04 14:29",source:"https://app.jisilu.cn/question/524925?show_all_answer-TRUE__item_id-5534477__answer_id-5534477__single-TRUE=",sourceLabel:"具体讨论与回复",official:"",tag:"系统情报"},
  {id:"08",instrument:"AI高股息轮动",code:"A股策略",event:"自动化选股实践",aiDecision:"reject",aiLabel:"暂不纳入",confidence:"较高置信",summary:"低估值、高股息与门票股轮动可能偏低波，但它不是严格的套利或事件驱动。",caution:"是否纳入取决于系统边界：只做套利，还是扩大到所有低风险策略。",data:"最新回复 09-04 15:13",source:"https://app.jisilu.cn/question/524983",sourceLabel:"集思录原帖",official:"",tag:"策略边界"},
  {id:"09",instrument:"可转债调仓记录",code:"可转债组合",event:"高抛低吸与做T",aiDecision:"reject",aiLabel:"建议排除",confidence:"较高置信",summary:"帖子主要记录转债仓位、市场情绪、高抛低吸和做T，没有发现明确事件或退出权。",caution:"日内操作与临时判断还可能触碰“盘中不决策”的交易纪律。",data:"最新回复 09-04 14:44",source:"https://app.jisilu.cn/question/524999",sourceLabel:"集思录原帖",official:"",tag:"日常交易"}
];

const decisionOptions=[["research","重点研究"],["watch","继续观察"],["reject","排除"],["unclear","暂时说不清"]];
const reasonOptions=["未选择原因","没有下行锚","上行空间不足","信用风险太大","时间过长","交易摩擦太大","信息不足","直觉上有意思","不属于套利范围"];
const storageKey="arbitrage-review-2026-09-04-v2";
let reviews=loadReviews();
let activeFilter="all";

function loadReviews(){try{return JSON.parse(localStorage.getItem(storageKey))||{}}catch{return {}}}
function saveReviews(){localStorage.setItem(storageKey,JSON.stringify(reviews));updateDashboard();renderQueue()}
function visibleSignals(){return signals.filter(item=>{const d=reviews[item.id]?.decision;if(activeFilter==="all")return true;if(activeFilter==="pending")return !d;return d===activeFilter})}

function renderSignals(){
  const visible=visibleSignals();
  document.querySelector("#emptyState").hidden=visible.length>0;
  document.querySelector("#signalList").innerHTML=visible.map(signalCard).join("");
  bindCardEvents();
}

function signalCard(item){
  const review=reviews[item.id]||{};const reviewed=Boolean(review.decision);
  return `<article class="signal-card ${reviewed?"reviewed":""}" data-id="${item.id}">
    <div class="signal-index"><span>${item.id}</span><i class="review-mark">${reviewed?"已审":"待审"}</i></div>
    <div class="signal-content">
      <header class="signal-title"><div><span class="tag">${item.tag}</span><h3>${item.instrument}</h3><p>${item.code} · ${item.event}</p></div><div class="ai-badge ${item.aiDecision}"><span>AI建议</span><strong>${item.aiLabel}</strong><small>${item.confidence}</small></div></header>
      <div class="signal-data">${item.data}</div>
      <div class="analysis-grid"><div><span>为什么进入视野</span><p>${item.summary}</p></div><div><span>当前疑点</span><p>${item.caution}</p></div></div>
      <div class="source-row"><a href="${item.source}" target="_blank" rel="noreferrer">${item.sourceLabel} ↗</a>${item.official?`<a href="${item.official}" target="_blank" rel="noreferrer">公司公告 ↗</a>`:""}</div>
      <div class="human-review"><div class="decision-group" role="group" aria-label="你对${item.instrument}的判断">${decisionOptions.map(([value,label])=>`<button type="button" data-decision="${value}" class="decision ${review.decision===value?"selected":""}">${label}</button>`).join("")}</div>
      <div class="review-note"><select aria-label="选择判断原因">${reasonOptions.map(reason=>`<option ${review.reason===reason?"selected":""}>${reason}</option>`).join("")}</select><input type="text" maxlength="100" aria-label="补充一句判断" placeholder="补充一句判断（可选）" value="${escapeHtml(review.note||"")}" /></div></div>
    </div></article>`;
}

function bindCardEvents(){
  document.querySelectorAll(".signal-card").forEach(card=>{const id=card.dataset.id;
    card.querySelectorAll("[data-decision]").forEach(button=>button.addEventListener("click",()=>{reviews[id]={...(reviews[id]||{}),decision:button.dataset.decision};saveReviews();renderSignals();showToast("判断已保存在当前浏览器")}));
    card.querySelector("select").addEventListener("change",event=>{reviews[id]={...(reviews[id]||{}),reason:event.target.value};saveReviews()});
    card.querySelector("input").addEventListener("change",event=>{reviews[id]={...(reviews[id]||{}),note:event.target.value.trim()};saveReviews()});
  });
}

function updateDashboard(){
  const decisions=signals.map(x=>reviews[x.id]?.decision).filter(Boolean);const count=key=>decisions.filter(x=>x===key).length;const reviewed=decisions.length;
  document.querySelector("#progressText").textContent=`${reviewed} / ${signals.length}`;document.querySelector("#progressBar").style.width=`${reviewed/signals.length*100}%`;
  document.querySelector("#metricTotal").textContent=signals.length;document.querySelector("#metricPending").textContent=signals.length-reviewed;document.querySelector("#metricResearch").textContent=count("research");document.querySelector("#metricWatch").textContent=count("watch")+count("unclear");document.querySelector("#metricReject").textContent=count("reject");
}

function renderQueue(){
  const items=signals.filter(item=>reviews[item.id]?.decision==="research");
  document.querySelector("#researchQueue").innerHTML=items.length?items.map(item=>{const review=reviews[item.id]||{};return `<article><span>${item.id} · ${item.tag}</span><div><h3>${item.instrument}</h3><p>${review.note||((review.reason&&review.reason!=="未选择原因")?review.reason:"等待进一步研究")}</p></div><a href="${item.source}" target="_blank" rel="noreferrer">返回原文 ↗</a></article>`}).join(""):`<div class="empty-state">尚未选择重点研究线索。完成今日审阅后，它们会自动出现在这里。</div>`;
}

function feedbackText(){return `套利研究系统｜2026-09-04 第2轮反馈\n${signals.map(item=>{const review=reviews[item.id];if(!review?.decision)return `${item.id} ${item.instrument}：未审阅`;const label=decisionOptions.find(x=>x[0]===review.decision)?.[1]||review.decision;const extras=[review.reason&&review.reason!=="未选择原因"?review.reason:"",review.note||""].filter(Boolean).join("；");return `${item.id} ${item.instrument}：${label}${extras?`（${extras}）`:""}`}).join("\n")}`}

async function copyFeedback(){const text=feedbackText();try{await navigator.clipboard.writeText(text);showToast("本轮反馈已复制，可以粘贴到对话中")}catch{const area=document.createElement("textarea");area.value=text;document.body.appendChild(area);area.select();document.execCommand("copy");area.remove();showToast("本轮反馈已复制")}}
function escapeHtml(value){return value.replace(/[&<>'"]/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[ch]))}
let toastTimer;function showToast(message){const toast=document.querySelector("#toast");toast.textContent=message;toast.classList.add("show");clearTimeout(toastTimer);toastTimer=setTimeout(()=>toast.classList.remove("show"),2200)}

document.querySelectorAll(".nav-item").forEach(button=>button.addEventListener("click",()=>{document.querySelectorAll(".nav-item").forEach(x=>x.classList.toggle("active",x===button));document.querySelectorAll(".view").forEach(x=>x.classList.toggle("active-view",x.id===button.dataset.target));document.querySelector("#pageTitle").textContent=button.textContent.replace(/^\d+/,"").trim();if(button.dataset.target==="queue")renderQueue();window.scrollTo({top:0,behavior:"smooth"})}));
document.querySelectorAll(".filter").forEach(button=>button.addEventListener("click",()=>{activeFilter=button.dataset.filter;document.querySelectorAll(".filter").forEach(x=>x.classList.toggle("active",x===button));renderSignals()}));
document.querySelector("#copyFeedback").addEventListener("click",copyFeedback);
updateDashboard();renderSignals();renderQueue();
