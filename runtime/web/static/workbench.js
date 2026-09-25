const state={
  opportunities:null,
  filters:{search:"",path:"",research:""},
  polling:null
};

function pageMode(){
  const p=window.location.pathname.replace(/\/+$/,"")||"/";
  if(p==="/workbench")return "landing";
  if(p==="/run-center")return "run";
  if(p==="/opportunities")return "opportunities";
  if(/^\/opportunities\/\d{6}$/.test(p))return "detail";
  if(/^\/opportunities-v2-preview\/\d{6}$/.test(p))return "detail";
  if(p==="/audit")return "audit";
  return "landing";
}

function detailCodeFromPath(){
  const m=window.location.pathname.match(
    /^\/(?:opportunities|opportunities-v2-preview)\/(\d{6})\/?$/
  );
  return m?m[1]:null;
}

function isV2PreviewPage(){
  return /^\/opportunities-v2-preview\/\d{6}\/?$/.test(
    window.location.pathname
  );
}

function configurePage(){
  const mode=pageMode();
  const ids={landing:"#landing",run:"#runCenter",opportunities:"#opportunities",audit:"#audit"};
  Object.entries(ids).forEach(([key,sel])=>{
    const el=$(sel);
    if(el)el.classList.toggle("hidden",key!==mode);
  });
  const map=$("#marketMap");
  if(map)map.classList.add("hidden");
  if(mode==="detail"){
    document.body.classList.add("detail-page");
    $("#detailOverlay").classList.remove("hidden");
  }
  return mode;
}

const STAGES=[
  ["MARKET_MAP","生成藏宝图"],
  ["MARKET_INGRESS","冻结市场输入"],
  ["ECONOMIC_DISCOVERY","三条路径经济判断"],
  ["RESEARCH_TRIGGER","判断是否需要深研"],
  ["RESEARCH_TASKS","生成研究任务"],
  ["RESEARCH_EVIDENCE","准备研究证据"],
  ["PATH_RESEARCH","AI 路径深研"],
  ["CANDIDATE_POOL","汇总真正机会"],
  ["OPPORTUNITY_RECORD","生成个券研究"],
  ["COMPLETE","本轮完成"]
];

function $(s){return document.querySelector(s);}
function esc(x){
  return String(x==null?"":x).replace(/[&<>"']/g,m=>({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  })[m]);
}
function num(x){
  if(x===null||x===undefined||x==="")return "—";
  const n=Number(x);
  if(Number.isNaN(n))return String(x);
  if(Math.abs(n)>=100)return n.toFixed(2).replace(/\.00$/,"");
  if(Math.abs(n)>=10)return n.toFixed(2).replace(/0+$/,"").replace(/\.$/,"");
  return n.toFixed(3).replace(/0+$/,"").replace(/\.$/,"");
}
function fmtMetric(m){
  return num(m.value)+(m.unit||"");
}
function badgeClass(status){
  if(["PASS","COMPLETED","REUSED","SKIPPED"].includes(status))return "pass";
  if(["RUNNING","PENDING","IN_PROGRESS"].includes(status))return "running";
  if(["PARTIAL","HOLD_WAITING_EVIDENCE","NEEDS_REVIEW"].includes(status))return "warn";
  if(status==="FAIL")return "fail";
  return "idle";
}
function statusText(status){
  return ({
    PASS:"已完成",COMPLETED:"已完成",REUSED:"已沿用",SKIPPED:"无需执行",
    RUNNING:"运行中",PENDING:"等待中",IN_PROGRESS:"研究中",
    PARTIAL:"部分完成",FAIL:"失败",NEEDS_REVIEW:"需要处理"
  })[status]||status||"未开始";
}
function latestStageMap(job){
  const events=job.full_stages||job.stages||[];
  const map={};
  for(const e of events)map[e.stage]=e;
  if(job.market_snapshot_id && !map.MARKET_MAP){
    map.MARKET_MAP={stage:"MARKET_MAP",status:"REUSED",market_snapshot_id:job.market_snapshot_id};
  }
  if(job.status==="PASS"){
    map.COMPLETE={stage:"COMPLETE",status:"PASS",...(job.summary||{})};
  }
  return map;
}

function renderRun(job){
  const badge=$("#runBadge");
  badge.textContent=statusText(job.status);
  badge.className="badge "+badgeClass(job.status);
  const snapshot=job.market_snapshot_id||job.summary?.market_snapshot_id||"—";
  const cutoff=job.market_cutoff||"—";
  $("#runMeta").innerHTML=
    "<b>最近一次整机：</b>"+esc(job.job_id||job.run_id||"已完成的 Runtime")
    +"　·　市场截面 "+esc(cutoff)
    +"　·　状态 "+esc(statusText(job.status))
    +(snapshot!=="—"?"　·　快照 "+esc(snapshot):"");

  const map=latestStageMap(job);
  $("#stageGrid").innerHTML=STAGES.map((item,index)=>{
    const [id,label]=item;
    const e=map[id];
    const s=e?.status||"";
    const cls=s==="FAIL"?"fail":s==="RUNNING"?"active":
      s==="PARTIAL"?"warn":e?"done":"";
    let meta="";
    if(e){
      if(e.opportunity_bonds!==undefined)meta="发现 "+e.opportunity_bonds+" 只机会债券";
      else if(e.pending_tasks!==undefined)meta="待研究 "+e.pending_tasks+" 条";
      else if(e.evidence_packs!==undefined)meta="证据包 "+e.evidence_packs+" 个";
      else if(e.bond_count!==undefined)meta=e.bond_count+" 只 / "+(e.keep_path_count??"—")+" 条路径";
      else if(e.rounds!==undefined)meta="研究批次 "+e.rounds+" 轮";
    }
    return '<div class="stage '+cls+'">'
      +'<div class="stage-no">'+String(index+1).padStart(2,"0")+'</div>'
      +'<div class="stage-name">'+esc(label)+'</div>'
      +'<div class="stage-status">'+esc(statusText(s))+'</div>'
      +(meta?'<div class="stage-meta">'+esc(meta)+'</div>':"")
      +'</div>';
  }).join("");
}

async function loadLatestRun(){
  try{
    const r=await fetch("/api/opportunity/full-runs/latest");
    if(!r.ok)throw new Error(await r.text());
    renderRun(await r.json());
  }catch(e){
    $("#runBadge").textContent="尚无记录";
    $("#runBadge").className="badge idle";
    $("#runMeta").textContent="尚未找到完整整机运行记录。可以从最新正式藏宝图继续，或在收盘后从头运行。";
    renderRun({status:"",full_stages:[]});
  }
}
async function startRun(sourceMode){
  const closeBtn=$("#runCloseBtn"), reuseBtn=$("#runReuseBtn");
  closeBtn.disabled=true; reuseBtn.disabled=true;
  try{
    const r=await fetch("/api/opportunity/full-runs",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        source_mode:sourceMode,
        run_research:true,
        research_batch_limit:5,
        max_research_rounds:20
      })
    });
    if(!r.ok)throw new Error(await r.text());
    const out=await r.json();
    pollRun(out.job_id);
  }catch(e){
    alert("启动失败："+e.message);
    closeBtn.disabled=false; reuseBtn.disabled=false;
  }
}

function pollRun(jobId){
  if(state.polling)clearInterval(state.polling);
  const tick=async()=>{
    try{
      const r=await fetch("/api/runs/"+encodeURIComponent(jobId));
      if(!r.ok)throw new Error(await r.text());
      const job=await r.json();
      renderRun(job);
      if(["PASS","FAIL","NEEDS_REVIEW"].includes(job.status)){
        clearInterval(state.polling);
        state.polling=null;
        $("#runCloseBtn").disabled=false;
        $("#runReuseBtn").disabled=false;
        await Promise.all([loadMarketMapMeta(),loadOpportunities()]);
      }
    }catch(e){
      clearInterval(state.polling);
      state.polling=null;
      $("#runCloseBtn").disabled=false;
      $("#runReuseBtn").disabled=false;
    }
  };
  tick();
  state.polling=setInterval(tick,2000);
}

async function loadMarketMapMeta(){
  try{
    const r=await fetch("/api/market-map/view");
    if(!r.ok)throw new Error(await r.text());
    const d=await r.json();
    $("#mapBadge").textContent="正式截面已就绪";
    $("#mapBadge").className="badge pass";
    $("#mapMeta").textContent=
      "市场截面："+(d.market_cutoff||"—")
      +"；覆盖 "+((d.rows||[]).length)+" 只转债"
      +"；模型版本："+(d.model_version||"—")+"。";
  }catch(e){
    $("#mapBadge").textContent="暂不可用";
    $("#mapBadge").className="badge warn";
    $("#mapMeta").textContent="暂时没有可读取的正式藏宝图。";
  }
}
function researchStateCounts(data){
  const out={COMPLETED:0,HOLD_WAITING_EVIDENCE:0,NOT_TRIGGERED:0,PENDING:0,IN_PROGRESS:0};
  for(const b of data.opportunities||[]){
    for(const p of b.paths||[]){
      if(out[p.research_state]!==undefined)out[p.research_state]++;
    }
  }
  return out;
}

function renderOpportunitySummary(data){
  const c=researchStateCounts(data);
  const items=[
    ["机会转债",data.bond_count||0],
    ["正向经济路径",data.keep_path_count||0],
    ["已完成深研",c.COMPLETED],
    ["等待补充证据",c.HOLD_WAITING_EVIDENCE],
    ["等待事件节点",c.NOT_TRIGGERED]
  ];
  $("#opportunitySummary").innerHTML=items.map(x=>
    '<div class="metric"><span>'+esc(x[0])+'</span><strong>'+esc(x[1])+'</strong></div>'
  ).join("");
}

function bondMatches(b){
  const f=state.filters;
  const q=f.search.trim().toLowerCase();
  if(q){
    const hay=(b.bond_code+" "+b.bond_name).toLowerCase();
    if(!hay.includes(q))return false;
  }
  if(f.path && !(b.paths||[]).some(p=>p.path_id===f.path))return false;
  if(f.research && !(b.paths||[]).some(p=>p.research_state===f.research))return false;
  return true;
}

function renderPathRow(p){
  const metrics=(p.metrics||[]).map(m=>
    '<span class="mini-metric">'+esc(m.label)+' '+esc(fmtMetric(m))+'</span>'
  ).join("");
  return '<div class="path-row">'
    +'<div><div class="path-title">'+esc(p.path_name)+'</div>'
    +'<div class="path-state">'+esc(p.research_state_text)+'</div></div>'
    +'<div><div class="path-metrics">'+metrics+'</div></div>'
    +'<div class="quick">'+esc(p.quick_judgment||"")+'</div>'
    +'</div>';
}

function bondInActiveResearch(b){
  return (b.paths||[]).some(p=>p.research_state!=="NOT_TRIGGERED");
}

function bondCardHtml(b){
  const hasV2=["110092","127089"].includes(String(b.bond_code||""));
  const preview=hasV2
    ?'<a class="v2-preview-link" href="/opportunities-v2-preview/'+esc(b.bond_code)+'">查看 V2 研究样本</a>'
    :"";
  return '<article class="opp-card" data-code="'+esc(b.bond_code)+'">'
    +'<div class="opp-head"><div><span class="bond-code">'+esc(b.bond_code)+'</span>'
    +'<span class="bond-name">'+esc(String(b.bond_name||"").replace(/转债$/,""))+'</span></div>'
    +'<div class="opp-actions"><div class="opp-count">发现 '+esc(b.opportunity_path_count)+' 条机会路径</div>'+preview+'</div></div>'
    +'<div class="path-strip">'+(b.paths||[]).map(renderPathRow).join("")+'</div>'
  +'</article>';
}

function renderOpportunityList(){
  const data=state.opportunities;
  if(!data)return;
  const rows=(data.opportunities||[]).filter(bondMatches);
  const active=rows.filter(bondInActiveResearch);
  const watch=rows.filter(b=>!bondInActiveResearch(b));
  $("#activeVisibleCount").textContent=active.length+" 只";
  $("#watchVisibleCount").textContent=watch.length+" 只";
  $("#activeOpportunityList").innerHTML=active.length
    ?active.map(bondCardHtml).join("")
    :'<div class="empty">当前筛选条件下，没有已经进入研究层的机会。</div>';
  $("#watchOpportunityList").innerHTML=watch.length
    ?watch.map(bondCardHtml).join("")
    :'<div class="empty">当前筛选条件下，没有等待事件节点的长期监控对象。</div>';

  document.querySelectorAll(".v2-preview-link").forEach(el=>{
    el.addEventListener("click",e=>e.stopPropagation());
  });
  document.querySelectorAll(".opp-card").forEach(el=>{
    el.addEventListener("click",()=>{
      window.location.href="/opportunities/"+encodeURIComponent(el.dataset.code);
    });
  });
}
async function loadOpportunities(){
  try{
    const r=await fetch("/api/opportunity/view/latest");
    if(!r.ok)throw new Error(await r.text());
    const data=await r.json();
    state.opportunities=data;
    $("#opportunityBadge").textContent="已加载";
    $("#opportunityBadge").className="badge pass";
    renderOpportunitySummary(data);
    renderOpportunityList();
  }catch(e){
    $("#opportunityBadge").textContent="读取失败";
    $("#opportunityBadge").className="badge fail";
    const msg='<div class="empty">机会结果读取失败：'+esc(e.message)+'</div>';
    $("#activeOpportunityList").innerHTML=msg;
    $("#watchOpportunityList").innerHTML="";
  }
}

function renderMetrics(metrics){
  return '<div class="metric-row">'+(metrics||[]).map(m=>
    '<div class="metric-small"><span>'+esc(m.label)+'</span><strong>'
    +esc(fmtMetric(m))+'</strong></div>'
  ).join("")+'</div>';
}

function renderResearchSummary(obj){
  if(!obj||typeof obj!=="object")return "";
  const why=Array.isArray(obj["为什么"])?obj["为什么"]:[];
  const risks=Array.isArray(obj["主要风险"])?obj["主要风险"]:[];
  return '<section class="research-summary">'
    +'<div class="research-summary-label">先看结论</div>'
    +'<h4>'+esc(obj["核心结论"]||"当前判断")+'</h4>'
    +(obj["经济结果"]?'<div class="summary-economic"><b>经济结果：</b>'+esc(obj["经济结果"])+'</div>':"")
    +(why.length?'<div class="summary-why"><b>为什么：</b><ul>'+why.map(x=>'<li>'+esc(x)+'</li>').join("")+'</ul></div>':"")
    +(risks.length?'<div class="summary-risk"><b>主要风险：</b><ul>'+risks.map(x=>'<li>'+esc(x)+'</li>').join("")+'</ul></div>':"")
    +(obj["下一步关注"]?'<div class="summary-next"><b>下一步关注：</b>'+esc(obj["下一步关注"])+'</div>':"")
    +'</section>';
}

function renderLogicChain(items){
  if(!Array.isArray(items)||!items.length)return "";
  return '<section class="research-section logic-section"><div class="logic-heading">'
    +'<div><p class="eyebrow">为什么得出这个结论</p><h4>研究逻辑链</h4></div>'
    +'<span class="muted">'+items.length+' 个节点</span></div>'
    +'<div class="logic-chain">'+items.map((x,i)=>{
      const facts=Array.isArray(x["关键事实"])?x["关键事实"]:[];
      return '<article class="logic-step">'
        +'<div class="logic-step-head"><span class="logic-no">'+esc(x["序号"]||i+1)+'</span>'
        +'<div><h4>'+esc(x["标题"]||x["节点编号"]||("步骤 "+(i+1)))+'</h4>'
        +'<div class="logic-question">'+esc(x["问题"]||"")+'</div></div>'
        +'<span class="logic-state">'+esc(x["状态"]||"")+'</span></div>'
        +(x["先给答案"]?'<div class="logic-answer"><b>答案：</b>'+esc(x["先给答案"])+'</div>':"")
        +(facts.length?'<div class="logic-facts"><b>关键事实</b><ul>'+facts.map(f=>'<li>'+esc(f)+'</li>').join("")+'</ul></div>':"")
        +(x["为什么"]?'<div class="logic-reason"><b>为什么：</b>'+esc(x["为什么"])+'</div>':"")
        +(x["本步结论"]?'<div class="logic-conclusion"><b>本步结论：</b>'+esc(x["本步结论"])+'</div>':"")
        +'</article>';
    }).join("")+'</div></section>';
}

function renderJudgments(obj){
  const entries=Object.entries(obj||{});
  if(!entries.length)return "";
  return '<section class="research-section"><h4>研究判断</h4><div class="judgment-list">'
    +entries.map(([k,v])=>
      '<div class="judgment-item"><b>'+esc(k)+'</b><div>'+esc(valueText(v))+'</div></div>'
    ).join("")+'</div></section>';
}

function valueText(v){
  if(v===null||v===undefined)return "—";
  if(typeof v==="string"||typeof v==="number"||typeof v==="boolean")return String(v);
  return JSON.stringify(v,null,2);
}

function renderListSection(title,items){
  if(!Array.isArray(items)||!items.length)return "";
  return '<section class="research-section"><h4>'+esc(title)+'</h4><ul>'
    +items.map(x=>'<li>'+esc(valueText(x))+'</li>').join("")
    +'</ul></section>';
}
function renderUpdates(items){
  if(!Array.isArray(items)||!items.length)return "";
  return '<section class="research-section"><h4>下一更新节点</h4><div class="update-list">'
    +items.map(x=>{
      if(typeof x!=="object")return '<div class="update-item">'+esc(valueText(x))+'</div>';
      const date=x["日期"]||"时间待定";
      const event=x["事件"]||valueText(x);
      return '<div class="update-item"><b>'+esc(date)+'</b><div>'+esc(event)+'</div></div>';
    }).join("")+'</div></section>';
}

function renderEvidence(items){
  if(!Array.isArray(items)||!items.length)return "";
  return '<section class="research-section"><h4>关键证据</h4><div class="evidence-list">'
    +items.map(x=>{
      const supports=Array.isArray(x["支持内容"])?x["支持内容"].join("；"):x["支持内容"];
      return '<div class="evidence-item"><div class="evidence-title">'+esc(x["标题"]||"证据")+'</div>'
        +'<div class="evidence-meta">'+esc([x["来源类型"],x["日期"],x["可信度"]?"可信度："+x["可信度"]:""].filter(Boolean).join(" · "))+'</div>'
        +(supports?'<div>'+esc(supports)+'</div>':"")
        +(x["定位"]?'<div class="evidence-meta">'+esc(x["定位"])+'</div>':"")
        +'</div>';
    }).join("")+'</div></section>';
}

function renderObject(value,depth=0){
  if(value===null||value===undefined)return '<span class="muted">—</span>';
  if(typeof value!=="object")return '<span>'+esc(valueText(value))+'</span>';
  if(Array.isArray(value)){
    return '<div class="object-view">'+value.map((x,i)=>
      '<div class="object-row">'+renderObject(x,depth+1)+'</div>'
    ).join("")+'</div>';
  }
  return '<div class="object-view">'+Object.entries(value).map(([k,v])=>
    '<div class="object-row"><div class="object-key">'+esc(k)+'</div>'
      +'<div class="object-value">'+renderObject(v,depth+1)+'</div></div>'
  ).join("")+'</div>';
}

function renderResearch(path){
  const r=path.research;
  if(!r){
    return '<div class="empty">当前还没有完整深研结果。'+esc(path.status_explanation||"")+'</div>';
  }
  const hasV2=!!(r["总判断"] && Array.isArray(r["研究逻辑链"]) && r["研究逻辑链"].length);
  const main=hasV2
    ?renderResearchSummary(r["总判断"])+renderLogicChain(r["研究逻辑链"])
    :renderJudgments(r["研究判断"]);
  const secondary=hasV2
    ?'<details class="research-section"><summary>展开补充研究判断</summary>'+renderJudgments(r["研究判断"])+'</details>'
    :"";
  return main
    +renderListSection("关键风险",r["关键风险"])
    +renderListSection("失效条件",r["失效条件"])
    +renderListSection("未来天然不确定事项",r["未来天然不确定事项"])
    +renderListSection("当前仍待查证事项",r["当前仍待查证事项"])
    +renderUpdates(r["下一更新节点"])
    +renderEvidence(r["关键证据"])
    +secondary
    +'<details class="research-section"><summary>展开关键事实链（原始结构）</summary>'
      +renderObject(r["关键事实链"])+'</details>';
}
function renderDetail(data){
  $("#detailTitle").textContent=(data.bond_code||"")+" "+String(data.bond_name||"").replace(/转债$/,"");
  $("#detailMeta").textContent="市场截面 "+(data.market_cutoff||"—")+" · 发现 "+data.opportunity_path_count+" 条机会路径";
  const preview=data.preview
    ?'<div class="preview-banner"><b>Path Result V2 Golden Sample 预览</b><div>'
      +esc(data.preview.message||"")+'</div></div>'
    :"";
  $("#detailBody").innerHTML=preview+(data.paths||[]).map(path=>
    '<article class="detail-path">'
      +'<div class="detail-path-head"><div><h3>'+esc(path.path_name)+'</h3>'
      +'<div class="muted">'+esc(path.opportunity_status)+' · '+esc(path.research_state_text)+'</div></div>'
      +'<span class="badge '+badgeClass(path.research_state)+'">'+esc(path.research_state_text)+'</span></div>'
      +renderMetrics(path.metrics)
      +'<div class="path-status-box">'+esc(path.status_explanation||"")+'</div>'
      +renderResearch(path)
      +'<details class="research-section"><summary>工程审计信息</summary><pre class="audit-json">'
      +esc(JSON.stringify(path.audit||{},null,2))+'</pre></details>'
    +'</article>'
  ).join("");
}

async function openDetail(code){
  $("#detailOverlay").classList.remove("hidden");
  $("#detailTitle").textContent="正在读取 "+code;
  $("#detailBody").innerHTML='<div class="empty">正在读取完整个券研究……</div>';
  if(pageMode()!=="detail")document.body.style.overflow="hidden";
  try{
    const endpoint=isV2PreviewPage()
      ?"/api/opportunity/preview-v2/"+encodeURIComponent(code)
      :"/api/opportunity/view/"+encodeURIComponent(code);
    const r=await fetch(endpoint);
    if(!r.ok)throw new Error(await r.text());
    const data=await r.json();
    renderDetail(data);
    if(pageMode()==="detail")document.title=(data.bond_name||code)+"｜个券完整研究";
  }catch(e){
    $("#detailBody").innerHTML='<div class="empty">读取失败：'+esc(e.message)+'</div>';
  }
}

function closeDetail(){
  if(pageMode()==="detail"){
    window.location.href="/opportunities";
    return;
  }
  $("#detailOverlay").classList.add("hidden");
  document.body.style.overflow="";
}

function bind(){
  $("#runCloseBtn").addEventListener("click",()=>startRun("CLOSE"));
  $("#runReuseBtn").addEventListener("click",()=>startRun("LATEST_FORMAL"));
  $("#searchInput").addEventListener("input",e=>{state.filters.search=e.target.value;renderOpportunityList();});
  $("#pathFilter").addEventListener("change",e=>{state.filters.path=e.target.value;renderOpportunityList();});
  $("#researchFilter").addEventListener("change",e=>{state.filters.research=e.target.value;renderOpportunityList();});
  $("#resetFilters").addEventListener("click",()=>{
    state.filters={search:"",path:"",research:""};
    $("#searchInput").value="";$("#pathFilter").value="";$("#researchFilter").value="";
    renderOpportunityList();
  });
  $("#detailClose").addEventListener("click",closeDetail);
  $("#detailOverlay").addEventListener("click",e=>{if(e.target===$("#detailOverlay"))closeDetail();});
  document.addEventListener("keydown",e=>{if(e.key==="Escape")closeDetail();});
}

async function loadLandingSummary(){
  const el=$("#landingSummary");
  if(!el)return;
  try{
    const [rr,or]=await Promise.all([
      fetch("/api/opportunity/full-runs/latest"),
      fetch("/api/opportunity/view/latest")
    ]);
    const run=rr.ok?await rr.json():{};
    const opp=or.ok?await or.json():{};
    const active=(opp.opportunities||[]).filter(bondInActiveResearch).length;
    const watch=(opp.opportunities||[]).length-active;
    el.innerHTML="<b>最近正式结果：</b> 市场截面 "+esc(run.market_cutoff||opp.market_cutoff||"—")
      +"　·　机会转债 "+esc(opp.bond_count||0)+" 只"
      +"　·　已进入研究层 "+esc(active)+" 只"
      +"　·　等待事件节点 "+esc(watch)+" 只"
      +"　·　整机状态 "+esc(statusText(run.status));
  }catch(e){
    el.textContent="暂时无法读取最新运行摘要。";
  }
}

async function init(){
  bind();
  const mode=configurePage();
  if(mode==="landing"){
    document.title="机会发现工作台";
    await loadLandingSummary();
  }else if(mode==="run"){
    document.title="运行中心｜机会发现";
    await loadLatestRun();
  }else if(mode==="opportunities"){
    document.title="机会结果｜机会发现";
    await loadOpportunities();
  }else if(mode==="audit"){
    document.title="工程审计｜机会发现";
  }else if(mode==="detail"){
    const code=detailCodeFromPath();
    if(code)await openDetail(code);
  }
}
init();
