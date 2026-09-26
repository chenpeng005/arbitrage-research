const state={
  opportunities:null,
  filters:{search:"",path:"",research:"",showWatch:false},
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
    $("#runMeta").textContent="尚未找到完整整机运行记录。正式运行只在交易日收盘后进行。";
    renderRun({status:"",full_stages:[]});
  }
}

async function loadRunPolicy(){
  const btn=$("#runCloseBtn");
  const box=$("#runPolicy");
  try{
    const r=await fetch("/api/market-status");
    if(!r.ok)throw new Error(await r.text());
    const d=await r.json();
    if(d.formal_run_completed){
      btn.disabled=true;
      btn.textContent="今日正式运行已完成";
      box.innerHTML="<b>"+esc(d.china_date)+"：</b> 今日正式收盘 Full Runtime 已完成。同一收盘截面不会重复正式运行。";
    }else if(d.formal_run_active){
      btn.disabled=true;
      btn.textContent="今日正式运行中";
      box.innerHTML="<b>"+esc(d.china_date)+"：</b> 今日正式流程正在运行，请查看下方阶段进度。";
    }else if(!d.is_trade_day){
      btn.disabled=true;
      btn.textContent="等待下一个交易日收盘";
      box.innerHTML="<b>"+esc(d.china_date)+"：</b> 今天不是交易日。运行中心等待下一个正式收盘截面。";
    }else if(!d.after_close_gate){
      btn.disabled=true;
      btn.textContent="15:10 后可运行";
      box.innerHTML="<b>"+esc(d.china_date)+"：</b> 今天是交易日，但正式收盘截面尚未冻结；北京时间 "+esc(d.close_gate_time)+" 后开放一次正式运行。";
    }else{
      btn.disabled=false;
      btn.textContent="启动今日正式全流程";
      box.innerHTML="<b>"+esc(d.china_date)+"：</b> 今日正式收盘截面已可用，尚未运行。现在可以启动一次完整机会发现。";
    }
  }catch(e){
    btn.disabled=true;
    btn.textContent="运行状态读取失败";
    box.textContent="无法确认今日正式运行条件："+e.message;
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
        await Promise.all([loadRunPolicy(),loadMarketMapMeta(),loadOpportunities()]);
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
  const active=(data.opportunities||[]).filter(bondInActiveResearch);
  const watch=(data.opportunities||[]).length-active.length;
  const floor=active.filter(b=>b.floor_class==="保底型").length;
  const nonFloor=active.length-floor;
  $("#opportunitySummary").innerHTML=
    '<b>市场截面 '+esc(data.market_cutoff||"—")+'</b>'
    +'　研究层 '+esc(active.length)+' 只'
    +'　·　保底 '+esc(floor)+' 只'
    +'　·　非保底 '+esc(nonFloor)+' 只'
    +'　·　长期监控 '+esc(watch)+' 只';
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

function bondCurrentPrice(b){
  for(const p of b.paths||[]){
    const m=(p.metrics||[]).find(x=>x.label==="当前价格");
    if(m&&m.value!==null&&m.value!==undefined)return m.value;
  }
  return null;
}

function pathSpaceText(p){
  if(p.ytm_pct!==null&&p.ytm_pct!==undefined){
    return "税前YTM "+Number(p.ytm_pct).toFixed(2)+"%";
  }
  const rel=(p.metrics||[]).find(x=>x.label==="相对当前价空间");
  if(rel&&rel.value!==null&&rel.value!==undefined){
    return (Number(rel.value)>=0?"+":"")+num(rel.value)+"%";
  }
  const gap=(p.metrics||[]).find(x=>x.label==="模型价差");
  if(gap&&gap.value!==null&&gap.value!==undefined){
    return (Number(gap.value)>=0?"+":"")+num(gap.value)+"元";
  }
  return "—";
}

function pathLines(paths,fn){
  return (paths||[]).map(p=>'<div class="path-subline">'+fn(p)+'</div>').join("");
}

function opportunityRowHtml(b,index){
  const hasV2=["110092","127089"].includes(String(b.bond_code||""));
  const name=String(b.bond_name||"").replace(/转债$/,"");
  const preview=hasV2
    ?'<a class="v2-preview-link" href="/opportunities-v2-preview/'+esc(b.bond_code)+'">V2</a>'
    :"";
  const paths=b.paths||[];
  return '<tr class="opportunity-row" data-code="'+esc(b.bond_code)+'">'
    +'<td class="row-no">'+esc(index+1)+'</td>'
    +'<td class="code-cell">'+esc(b.bond_code)+'</td>'
    +'<td><b>'+esc(name)+'</b>'+preview+'</td>'
    +'<td class="num-cell">'+esc(num(bondCurrentPrice(b)))+'</td>'
    +'<td>'+pathLines(paths,p=>esc(p.path_name))+'</td>'
    +'<td>'+pathLines(paths,p=>esc(p.current_event_state_text||"—"))+'</td>'
    +'<td>'+pathLines(paths,p=>esc(p.opportunity_time||"—"))+'</td>'
    +'<td class="num-cell">'+pathLines(paths,p=>esc(pathSpaceText(p)))+'</td>'
    +'<td>'+pathLines(paths,p=>esc(p.research_state_text||"—"))+'</td>'
    +'</tr>';
}

function fillOpportunityGroup(target,rows){
  const el=$(target);
  if(!el)return;
  el.innerHTML=rows.length
    ?rows.map(opportunityRowHtml).join("")
    :'<tr><td colspan="9" class="muted">当前筛选条件下没有对象。</td></tr>';
}

function renderOpportunityList(){
  const data=state.opportunities;
  if(!data)return;
  let filtered=(data.opportunities||[]).filter(bondMatches);
  const active=filtered.filter(bondInActiveResearch);
  const watch=filtered.filter(b=>!bondInActiveResearch(b));
  const rows=state.filters.showWatch?active.concat(watch):active;
  const floor=rows.filter(b=>b.floor_class==="保底型");
  const nonFloor=rows.filter(b=>b.floor_class!=="保底型");

  $("#floorVisibleCount").textContent=floor.length+" 只";
  $("#nonFloorVisibleCount").textContent=nonFloor.length+" 只";
  fillOpportunityGroup("#floorOpportunityTableBody",floor);
  fillOpportunityGroup("#nonFloorOpportunityTableBody",nonFloor);

  document.querySelectorAll(".v2-preview-link").forEach(el=>{
    el.addEventListener("click",e=>e.stopPropagation());
  });
  document.querySelectorAll(".opportunity-row").forEach(el=>{
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
    const msg='<tr><td colspan="9" class="muted">机会结果读取失败：'+esc(e.message)+'</td></tr>';
    $("#floorOpportunityTableBody").innerHTML=msg;
    $("#nonFloorOpportunityTableBody").innerHTML=msg;
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
  return '<section class="research-summary">'
    +'<div class="research-summary-label">先看结论</div>'
    +'<h4>'+esc(obj["核心结论"]||"当前判断")+'</h4>'
    +(obj["经济结果"]?'<div class="summary-economic"><b>经济结果：</b>'+esc(obj["经济结果"])+'</div>':"")
    +(why.length?'<div class="summary-why"><b>为什么：</b><ul>'+why.map(x=>'<li>'+esc(x)+'</li>').join("")+'</ul></div>':"")
    +(obj["首要风险提醒"]?'<div class="summary-risk"><b>最重要的风险：</b>'+esc(obj["首要风险提醒"])+'</div>':"")
    +(obj["下一步关注"]?'<div class="summary-next"><b>下一步关注：</b>'+esc(obj["下一步关注"])+'</div>':"")
    +'</section>';
}

function renderFactTable(table){
  if(!table||!Array.isArray(table.columns)||!Array.isArray(table.rows))return "";
  return '<div class="logic-table-wrap"><table class="logic-table"><thead><tr>'
    +table.columns.map(x=>'<th>'+esc(x)+'</th>').join("")
    +'</tr></thead><tbody>'
    +table.rows.map(row=>'<tr>'+row.map(cell=>'<td>'+esc(cell)+'</td>').join("")+'</tr>').join("")
    +'</tbody></table></div>';
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
        +renderFactTable(x["事实表格"])
        +(facts.length?'<div class="logic-facts"><b>关键事实</b><ul>'+facts.map(f=>'<li>'+esc(f)+'</li>').join("")+'</ul></div>':"")
        +(x["为什么"]?'<div class="logic-reason"><b>为什么：</b>'+esc(x["为什么"])+'</div>':"")
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

function renderRiskMatrix(obj){
  if(!obj||typeof obj!=="object")return "";
  const groups=["当前风险","失效条件","未来天然不确定","当前仍待查证"];
  const visible=groups.filter(key=>Array.isArray(obj[key])&&obj[key].length);
  if(!visible.length)return "";
  return '<section class="research-section risk-section">'
    +'<div class="logic-heading"><div><p class="eyebrow">风险与未决事项</p><h4>哪些东西可能让结论改变</h4></div></div>'
    +'<div class="risk-flow">'+visible.map(key=>
      '<div class="risk-flow-block"><h4>'+esc(key)+'</h4><ul>'
      +obj[key].map(x=>'<li>'+esc(valueText(x))+'</li>').join("")
      +'</ul></div>'
    ).join("")+'</div></section>';
}
function renderUpdates(items){
  if(!Array.isArray(items)||!items.length)return "";
  return '<section class="research-section"><h4>下一更新节点</h4>'
    +'<div class="logic-table-wrap"><table class="logic-table update-table"><thead><tr><th>时间</th><th>更新节点</th></tr></thead><tbody>'
    +items.map(x=>{
      if(typeof x!=="object")return '<tr><td>待定</td><td>'+esc(valueText(x))+'</td></tr>';
      const date=x["日期"]||"待定";
      const event=x["事件"]||valueText(x);
      return '<tr><td>'+esc(date)+'</td><td>'+esc(event)+'</td></tr>';
    }).join("")+'</tbody></table></div></section>';
}

function renderEvidence(items){
  if(!Array.isArray(items)||!items.length)return "";
  return '<details class="research-section"><summary>展开关键证据（'+items.length+' 条）</summary><div class="evidence-list">'
    +items.map(x=>{
      const supports=Array.isArray(x["支持内容"])?x["支持内容"].join("；"):x["支持内容"];
      return '<div class="evidence-item"><div class="evidence-title">'+esc(x["标题"]||"证据")+'</div>'
        +'<div class="evidence-meta">'+esc([x["来源类型"],x["日期"],x["可信度"]?"可信度："+x["可信度"]:""].filter(Boolean).join(" · "))+'</div>'
        +(supports?'<div>'+esc(supports)+'</div>':"")
        +(x["定位"]?'<div class="evidence-meta">'+esc(x["定位"])+'</div>':"")
        +'</div>';
    }).join("")+'</div></details>';
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
    +renderRiskMatrix(r["风险与未决事项"])
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
  $("#researchFilter").addEventListener("change",e=>{
    state.filters.research=e.target.value;
    if(e.target.value==="NOT_TRIGGERED"){
      state.filters.showWatch=true;
      $("#showWatchToggle").checked=true;
    }
    renderOpportunityList();
  });
  $("#showWatchToggle").addEventListener("change",e=>{
    state.filters.showWatch=e.target.checked;
    renderOpportunityList();
  });
  $("#resetFilters").addEventListener("click",()=>{
    state.filters={search:"",path:"",research:"",showWatch:false};
    $("#searchInput").value="";$("#pathFilter").value="";$("#researchFilter").value="";$("#showWatchToggle").checked=false;
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
    await Promise.all([loadLatestRun(),loadRunPolicy()]);
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
