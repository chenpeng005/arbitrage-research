const acquisitionSteps=[
  ["S0","冻结本次运行"],
  ["S1","获取主行情"],
  ["S2","可转债范围筛选"],
  ["S3","补充到期日"],
  ["S4","补充剩余规模"],
  ["S5","多来源交叉审计"],
  ["S6","数据准备完成"]
];

const calculationSteps=[
  ["C0","读取已审计输入"],
  ["C1","划分支持区间与核心区间"],
  ["C2","拟合基础价值曲线"],
  ["C3","基础模型硬审计"],
  ["C4","拟合剩余期限调整"],
  ["C5","拟合剩余规模候选调整"],
  ["C6","计算残差分布并冻结快照"]
];

const statusLabels={
  IDLE:"尚未运行",
  PENDING:"等待执行",
  RUNNING:"运行中",
  RUNNING_WARNING:"运行中 · 有警告",
  PASS:"通过",
  WARNING:"有警告",
  FAIL:"失败",
  BLOCKED:"已阻塞",
  NEEDS_REVIEW:"需要人工处理"
};

const metricLabels={
  run_id:"运行编号",
  snapshot_mode:"运行模式",
  market_cutoff:"市场截面",
  market_source:"行情来源",
  attempt:"取数尝试",
  data_mode:"数据方式",
  candidate_count:"候选对象",
  unique_codes:"唯一代码",
  price_covered:"有价格对象",
  candidate:"候选对象",
  base_sample:"基础样本",
  excluded:"排除对象",
  maturity_covered:"到期日覆盖",
  maturity_missing:"到期日缺失",
  duration_sample:"期限样本",
  scale_sample:"规模样本",
  size_invalid_or_missing:"规模缺失/异常",
  aux_source_extra:"辅助源额外对象",
  cv_diff_ratio_median:"CV差异中位数",
  cv_diff_ratio_max:"CV最大差异",
  k_conflicts:"转股价冲突",
  maturity_mismatch:"到期日口径差异",
  source_candidate:"主源候选",
  support_zone:"支持区间样本",
  core_zone:"核心区间样本",
  input_rows:"输入样本",
  model_ready_rows:"完整模型样本",
  invalid_rows:"无效样本",
  support_sample:"支持区间样本",
  core_sample:"核心区间样本",
  support_min_cv:"支持区间下限",
  support_max_cv:"支持区间上限",
  core_min_cv:"核心区间下限",
  core_max_cv:"核心区间上限",
  beta0:"β0",
  beta1:"β1",
  beta2:"β2",
  core_mae:"核心区间 MAE",
  huber_t:"Huber 参数",
  fit_parameters_finite:"参数有限",
  shape_monotonic:"核心区单调不下降",
  min_grid_increment:"最小网格增量",
  fit_iterations:"拟合迭代次数",
  intercept:"截距",
  slope:"期限斜率",
  base_core_mae:"仅基础曲线 MAE",
  base_duration_core_mae:"基础+期限 MAE",
  mae_improvement:"MAE 改善",
  component_status:"组件状态",
  ln_size_slope:"ln(规模)斜率",
  with_scale_core_mae:"加入规模后 MAE",
  conservative_k:"保守规模斜率 k",
  q25:"残差 Q25",
  q50:"残差 Q50",
  q75:"残差 Q75",
  base_mae:"仅基础曲线 MAE",
  base_duration_mae:"基础+期限 MAE",
  base_duration_scale_mae:"基础+期限+规模 MAE"
};

const acquisitionSummaryLabels={
  source_candidate:"主源候选",
  base_sample:"基础样本",
  duration_sample:"期限样本",
  scale_sample:"规模样本",
  support_zone:"支持区间",
  core_zone:"核心区间"
};

const calculationSummaryLabels={
  base_mae:"仅基础曲线 MAE",
  base_duration_mae:"加入期限后",
  base_duration_scale_mae:"加入规模后",
  q25:"残差 Q25",
  q50:"残差 Q50",
  q75:"残差 Q75"
};

const $=function(s){return document.querySelector(s);};
let currentJob=null;
let timer=null;
let calcJob=null;
let calcTimer=null;

function localDateString(){
  const d=new Date();
  const local=new Date(d.getTime()-d.getTimezoneOffset()*60000);
  return local.toISOString().slice(0,10);
}

$("#cutoff").value=localDateString();
$("#calcCutoff").value=localDateString();

function esc(x){
  return String(x==null?"":x).replace(/[&<>"']/g,function(m){
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m];
  });
}

function statusClass(x){
  return (x||"idle").toLowerCase();
}

function statusText(x){
  return statusLabels[x]||x||"尚未运行";
}

function prettyNumber(value,digits){
  const n=Number(value);
  if(!Number.isFinite(n))return value;
  return n.toFixed(digits);
}

function metricText(key,value){
  if(["cv_diff_ratio_median","cv_diff_ratio_max"].includes(key)){
    return (Number(value)*100).toFixed(4)+"%";
  }
  if(key==="snapshot_mode"){
    if(value==="LIVE_TEST")return "盘中测试";
    if(value==="REPLAY_TEST")return "历史回放测试";
    return "正式收盘截面";
  }
  if(key==="data_mode" && value==="fixture_replay")return "固定历史样本";
  if(key==="market_source"){
    if(value==="EASTMONEY_PUSH2")return "东方财富主行情";
    if(value==="FALLBACK_EM_DATACENTER_PLUS_JSL")return "备用：东方财富数据中心 + 集思录";
    if(value==="REPLAY_FIXTURE")return "固定历史样本";
  }
  if(key==="component_status" && value==="CANDIDATE")return "候选";
  if(["fit_parameters_finite","shape_monotonic"].includes(key))return value?"是":"否";
  if([
    "beta0","beta1","beta2","core_mae","intercept","slope",
    "base_core_mae","base_duration_core_mae","mae_improvement",
    "ln_size_slope","with_scale_core_mae","conservative_k",
    "q25","q50","q75","base_mae","base_duration_mae","base_duration_scale_mae"
  ].includes(key)){
    return prettyNumber(value,2);
  }
  if(["huber_t","min_grid_increment"].includes(key))return prettyNumber(value,4);
  return value;
}

function renderSkeleton(containerSelector,defs,prefix){
  const container=$(containerSelector);
  container.innerHTML=defs.map(function(item){
    const id=item[0], name=item[1];
    return '<article class="step pending" id="'+prefix+id+'">'
      +'<div class="step-head"><div class="step-title"><span class="sid">'+id+'</span><strong>'+name+'</strong></div>'
      +'<span class="badge">等待执行</span></div>'
      +'<p class="conclusion">等待程序进入本步骤。</p>'
      +'<div class="metrics"></div><div class="detail-zone"></div></article>';
  }).join("");
}

function renderNotes(notes){
  if(!notes || !notes.length)return "";
  return '<div class="detail-notes">'+notes.map(function(n){
    return '<p>'+esc(n)+'</p>';
  }).join("")+'</div>';
}

function renderReasonCounts(counts){
  const entries=Object.entries(counts||{});
  if(!entries.length)return "";
  return '<div class="reason-grid">'+entries.map(function(entry){
    return '<div><span>'+esc(entry[0])+'</span><strong>'+esc(entry[1])+' 只</strong></div>';
  }).join("")+'</div>';
}

function formatCell(key,value){
  if(value===null || value===undefined || value==="")return "—";
  if(key==="cv_diff_pct")return Number(value).toFixed(4)+"%";
  if([
    "calc_CV","source_CV","P","S","K","K_aux","K_aux_diff",
    "remaining_size","issue_size","anchor","adjustment",
    "anchor_neutral","residual_final","value","months","size"
  ].includes(key)){
    const n=Number(value);
    return Number.isFinite(n)?n.toFixed(3).replace(/\.000$/,""):value;
  }
  if(String(key).includes("maturity") && typeof value==="string")return value.slice(0,10);
  return value;
}

function renderTable(t){
  const rows=t.rows||[];
  if(!rows.length)return "";
  const columns=t.columns||[];
  const head=columns.map(function(c){return '<th>'+esc(c.label)+'</th>';}).join("");
  const body=rows.map(function(row){
    return '<tr>'+columns.map(function(c){
      return '<td>'+esc(formatCell(c.key,row[c.key]))+'</td>';
    }).join("")+'</tr>';
  }).join("");
  return '<div class="audit-table-wrap"><h4>'+esc(t.title||"审计明细")+'</h4>'
    +'<div class="table-scroll"><table class="audit-table"><thead><tr>'+head+'</tr></thead><tbody>'+body+'</tbody></table></div></div>';
}

function renderDetails(details){
  if(!details)return "";
  const notes=renderNotes(details.notes);
  const reasons=renderReasonCounts(details.reason_counts);
  const tables=(details.tables||[]).map(renderTable).join("");
  if(!notes && !reasons && !tables)return "";
  return '<details class="audit-detail"><summary>展开本步审计明细</summary>'+notes+reasons+tables+'</details>';
}

function renderStep(event,prefix){
  if(!event)return;
  const el=$("#"+prefix+event.id);
  if(!el)return;
  el.classList.remove("pending");
  const badge=el.querySelector(".badge");
  badge.textContent=statusText(event.status);
  badge.className="badge "+statusClass(event.status);
  el.querySelector(".conclusion").textContent=event.conclusion||"正在执行…";

  const metrics=event.metrics||{};
  el.querySelector(".metrics").innerHTML=Object.entries(metrics).map(function(entry){
    const key=entry[0], value=entry[1];
    return '<span class="metric"><b>'+esc(metricLabels[key]||key)+'</b> · '+esc(metricText(key,value))+'</span>';
  }).join("");

  el.querySelectorAll(".warning-box").forEach(function(x){x.remove();});
  const zone=el.querySelector(".detail-zone");
  (event.warnings||[]).forEach(function(w){
    zone.insertAdjacentHTML("beforebegin",'<div class="warning-box">'+esc(w)+'</div>');
  });
  zone.innerHTML=renderDetails(event.details);
}

function setOverall(selector,status){
  const el=$(selector);
  el.textContent=statusText(status);
  el.className="overall "+statusClass(status);
}

function updateProgress(data,textSelector,barSelector,total){
  const done=Object.values(data.steps||{}).filter(function(x){
    return ["PASS","WARNING","FAIL","NEEDS_REVIEW"].includes(x.status);
  }).length;
  $(textSelector).textContent=done+" / "+total;
  $(barSelector).style.width=Math.min(done/total*100,100)+"%";
}

function renderSummary(stepEvent,sectionSelector,metricsSelector,labels){
  if(!stepEvent || !stepEvent.metrics)return;
  $(sectionSelector).classList.remove("hidden");
  $(metricsSelector).innerHTML=Object.entries(labels).map(function(entry){
    const key=entry[0], label=entry[1], value=stepEvent.metrics[key];
    return '<article><span>'+esc(label)+'</span><strong>'+esc(value==null?"—":metricText(key,value))+'</strong></article>';
  }).join("");
}

async function pollAcquisition(){
  if(!currentJob)return;
  const r=await fetch("api/runs/"+currentJob);
  const data=await r.json();
  setOverall("#overall",data.status);

  let mode="正式收盘截面";
  if(data.snapshot_mode==="LIVE_TEST")mode="盘中测试";
  if(data.snapshot_mode==="REPLAY_TEST")mode="历史回放测试";

  $("#runMeta").innerHTML='<div class="meta-grid">'
    +'<span><b>运行任务</b> '+esc(data.job_id)+'</span>'
    +'<span><b>模式</b> '+esc(mode)+'</span>'
    +'<span><b>市场截面</b> '+esc(data.market_cutoff)+'</span>'
    +'</div>';

  Object.values(data.steps||{}).forEach(function(event){renderStep(event,"step-");});
  updateProgress(data,"#progressText","#bar",7);
  renderSummary((data.steps||{}).S6,"#summary","#summaryMetrics",acquisitionSummaryLabels);

  if(["PASS","WARNING","FAIL","NEEDS_REVIEW"].includes(data.status)){
    clearInterval(timer);
    timer=null;
    $("#runBtn").disabled=false;
  }
}

async function pollCalculation(){
  if(!calcJob)return;
  const r=await fetch("api/runs/"+calcJob);
  const data=await r.json();
  setOverall("#calcOverall",data.status);

  const inputMode=data.input_mode==="REPLAY_TEST"?"固定历史审计结果":"最近一次成功的数据获取结果";
  $("#calcRunMeta").innerHTML='<div class="meta-grid">'
    +'<span><b>运行任务</b> '+esc(data.job_id)+'</span>'
    +'<span><b>输入</b> '+esc(inputMode)+'</span>'
    +'<span><b>市场截面</b> '+esc(data.market_cutoff)+'</span>'
    +(data.source_job_id?'<span><b>来源任务</b> '+esc(data.source_job_id)+'</span>':"")
    +'</div>';

  Object.values(data.steps||{}).forEach(function(event){renderStep(event,"calc-step-");});
  updateProgress(data,"#calcProgressText","#calcBar",7);
  renderSummary((data.steps||{}).C6,"#calcSummary","#calcSummaryMetrics",calculationSummaryLabels);

  if(["PASS","WARNING","FAIL","NEEDS_REVIEW"].includes(data.status)){
    clearInterval(calcTimer);
    calcTimer=null;
    $("#calcRunBtn").disabled=false;
  }
}

$("#runBtn").addEventListener("click",async function(){
  renderSkeleton("#steps",acquisitionSteps,"step-");
  $("#summary").classList.add("hidden");
  setOverall("#overall","PENDING");
  $("#runBtn").disabled=true;
  const payload={snapshot_mode:$("#mode").value,market_cutoff:$("#cutoff").value};

  try{
    const r=await fetch("api/runs",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(payload)
    });
    if(!r.ok)throw new Error(await r.text());
    const data=await r.json();
    currentJob=data.job_id;
    await pollAcquisition();
    timer=setInterval(pollAcquisition,1000);
  }catch(err){
    setOverall("#overall","FAIL");
    $("#runMeta").textContent="无法启动本次运行："+err.message;
    $("#runBtn").disabled=false;
  }
});

$("#calcRunBtn").addEventListener("click",async function(){
  renderSkeleton("#calcSteps",calculationSteps,"calc-step-");
  $("#calcSummary").classList.add("hidden");
  setOverall("#calcOverall","PENDING");
  $("#calcRunBtn").disabled=true;

  const payload={
    input_mode:$("#calcInputMode").value,
    market_cutoff:$("#calcCutoff").value
  };

  try{
    const r=await fetch("api/calculation-runs",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(payload)
    });
    if(!r.ok)throw new Error(await r.text());
    const data=await r.json();
    calcJob=data.job_id;
    await pollCalculation();
    calcTimer=setInterval(pollCalculation,1000);
  }catch(err){
    setOverall("#calcOverall","FAIL");
    $("#calcRunMeta").textContent="无法启动计算："+err.message;
    $("#calcRunBtn").disabled=false;
  }
});

renderSkeleton("#steps",acquisitionSteps,"step-");
renderSkeleton("#calcSteps",calculationSteps,"calc-step-");


let marketMapData=null;
let marketMapMode="raw";
let marketMapSelectedCode=null;

function qtile(values,q){
  const xs=values.filter(Number.isFinite).slice().sort(function(a,b){return a-b;});
  if(!xs.length)return NaN;
  const pos=(xs.length-1)*q;
  const lo=Math.floor(pos), hi=Math.ceil(pos);
  if(lo===hi)return xs[lo];
  return xs[lo]+(xs[hi]-xs[lo])*(pos-lo);
}

function baseAnchorAt(cv){
  const base=((marketMapData||{}).components||{}).base||{};
  const p=base.params||{};
  const z=(cv-90)/20;
  return Number(p.beta0)+Number(p.beta1)*z+Number(p.beta2)*z*z;
}

function mapY(row){
  let y=Number(row.P);
  if(marketMapMode==="standard"){
    if($("#mapDuration").checked)y-=Number(row.duration_adjustment||0);
    if($("#mapScale").checked)y-=Number(row.scale_neutral||0);
  }
  return y;
}

function mapModeTitle(){
  if(marketMapMode==="raw"){
    return {
      title:"市场原貌",
      subtitle:"横轴：转股价值 CV｜纵轴：实际转债价格｜红线：BaseAnchor"
    };
  }
  const parts=[];
  if($("#mapDuration").checked)parts.push("期限");
  if($("#mapScale").checked)parts.push("规模");
  return {
    title:"标准化藏宝图",
    subtitle:"横轴：转股价值 CV｜纵轴：实际价格扣除"+(parts.length?parts.join("、"):"未扣除额外因素")+"｜红线：BaseAnchor"
  };
}

function updateMapControlState(){
  const raw=marketMapMode==="raw";
  $("#mapRawBtn").classList.toggle("active",raw);
  $("#mapStdBtn").classList.toggle("active",!raw);
  $("#mapDuration").disabled=raw;
  $("#mapScale").disabled=raw;
}

function renderMapSummary(){
  if(!marketMapData)return;
  const d=marketMapData.diagnostics||{};
  const q=marketMapData.residual_core_after_scale||{};
  $("#mapSummary").classList.remove("hidden");
  const items=[
    ["仅基础曲线 MAE",d.base_core_mae],
    ["加入期限后",d.base_duration_core_mae],
    ["加入规模后",d.base_duration_scale_core_mae],
    ["最终残差 Q50",q.q50]
  ];
  $("#mapSummaryMetrics").innerHTML=items.map(function(item){
    return '<article><span>'+esc(item[0])+'</span><strong>'+esc(prettyNumber(item[1],2))+'</strong></article>';
  }).join("");
}

function renderMapMeta(){
  if(!marketMapData)return;
  const z=marketMapData.zones||{};
  const source=marketMapData.source_type==="CLOSE"?"正式收盘结果":"最近一次计算结果";
  const scaleStatus=((((marketMapData||{}).components||{}).scale_neutral||{}).status||"CANDIDATE")==="CANDIDATE"?"候选":"正式";
  $("#mapMeta").innerHTML='<div class="meta-grid">'
    +'<span><b>来源</b> '+esc(source)+'</span>'
    +'<span><b>市场截面</b> '+esc(marketMapData.market_cutoff)+'</span>'
    +'<span><b>Support</b> '+esc((z.support||[]).join("–"))+'</span>'
    +'<span><b>Core</b> '+esc((z.core||[]).join("–"))+'</span>'
    +'<span><b>规模调整</b> '+esc(scaleStatus)+'</span>'
    +'</div>';
}

function renderMapSelected(row){
  const el=$("#mapSelected");
  if(!row){
    el.className="map-selected muted";
    el.textContent="鼠标移到散点上查看单券；点击散点可以固定查看。";
    return;
  }
  const diff=Number(row.diff_candidate);
  el.className="map-selected";
  el.innerHTML='<div class="selected-title"><strong>'+esc(row.bond_name)+'</strong><span>'+esc(row.bond_code)+'</span></div>'
    +'<div class="selected-grid">'
    +'<span>实际价格 <b>'+prettyNumber(row.P,2)+'</b></span>'
    +'<span>转股价值 <b>'+prettyNumber(row.source_CV,2)+'</b></span>'
    +'<span>剩余期限 <b>'+prettyNumber(row.remaining_months,1)+' 月</b></span>'
    +'<span>剩余规模 <b>'+prettyNumber(row.remaining_size,2)+' 亿</b></span>'
    +'<span>Base <b>'+prettyNumber(row.base_anchor,2)+'</b></span>'
    +'<span>期限调整 <b>'+prettyNumber(row.duration_adjustment,2)+'</b></span>'
    +'<span>规模调整（候选） <b>'+prettyNumber(row.scale_neutral,2)+'</b></span>'
    +'<span>藏宝图参考（候选） <b>'+prettyNumber(row.discovery_reference_candidate,2)+'</b></span>'
    +'<span>实际 - 参考 <b class="'+(diff<0?"diff-low":"diff-high")+'">'+prettyNumber(diff,2)+'</b></span>'
    +'</div>';
}

function renderMapTable(){
  if(!marketMapData)return;
  const rows=(marketMapData.rows||[]).slice().sort(function(a,b){
    return Number(a.diff_candidate)-Number(b.diff_candidate);
  });
  $("#mapTableBody").innerHTML=rows.map(function(r){
    const diff=Number(r.diff_candidate);
    return '<tr data-bond="'+esc(r.bond_code)+'">'
      +'<td>'+esc(r.bond_code)+'</td>'
      +'<td>'+esc(r.bond_name)+'</td>'
      +'<td>'+prettyNumber(r.P,2)+'</td>'
      +'<td>'+prettyNumber(r.source_CV,2)+'</td>'
      +'<td>'+prettyNumber(r.remaining_months,1)+'</td>'
      +'<td>'+prettyNumber(r.remaining_size,2)+'</td>'
      +'<td>'+prettyNumber(r.base_anchor,2)+'</td>'
      +'<td>'+prettyNumber(r.duration_adjustment,2)+'</td>'
      +'<td>'+prettyNumber(r.scale_neutral,2)+'</td>'
      +'<td>'+prettyNumber(r.discovery_reference_candidate,2)+'</td>'
      +'<td class="'+(diff<0?"diff-low":"diff-high")+'">'+prettyNumber(diff,2)+'</td>'
      +'</tr>';
  }).join("");

  $("#mapTableBody").querySelectorAll("tr[data-bond]").forEach(function(tr){
    tr.addEventListener("click",function(){
      const code=tr.getAttribute("data-bond");
      const row=(marketMapData.rows||[]).find(function(x){return x.bond_code===code;});
      marketMapSelectedCode=code;
      renderMapSelected(row);
      renderMarketMap();
    });
  });
}

function renderMarketMap(){
  if(!marketMapData)return;
  updateMapControlState();
  const mode=mapModeTitle();
  $("#mapTitle").textContent=mode.title;
  $("#mapSubtitle").textContent=mode.subtitle;

  const zone=(marketMapData.zones||{}).support||[50,130];
  const xMin=Number(zone[0]||50), xMax=Number(zone[1]||130);
  const visible=(marketMapData.rows||[]).filter(function(r){
    const x=Number(r.source_CV);
    return Number.isFinite(x) && x>=xMin && x<=xMax && Number.isFinite(mapY(r));
  });

  if(!visible.length){
    $("#mapChart").innerHTML='<div class="chart-empty">当前范围没有可绘制样本。</div>';
    return;
  }

  const ys=visible.map(mapY);
  let yMin=qtile(ys,0.02), yMax=qtile(ys,0.98);
  const spread=Math.max(10,yMax-yMin);
  yMin-=spread*0.10;
  yMax+=spread*0.10;

  const W=1000,H=560;
  const m={l:72,r:28,t:26,b:58};
  const pw=W-m.l-m.r, ph=H-m.t-m.b;
  const sx=function(x){return m.l+(x-xMin)/(xMax-xMin)*pw;};
  const sy=function(y){return m.t+(yMax-y)/(yMax-yMin)*ph;};

  const xTicks=[];
  for(let x=Math.ceil(xMin/10)*10;x<=xMax;x+=10)xTicks.push(x);
  const stepY=Math.max(5,Math.ceil((yMax-yMin)/7/5)*5);
  const yStart=Math.ceil(yMin/stepY)*stepY;
  const yTicks=[];
  for(let y=yStart;y<=yMax;y+=stepY)yTicks.push(y);

  let svg='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="可转债藏宝图">';
  svg+='<rect x="'+m.l+'" y="'+m.t+'" width="'+pw+'" height="'+ph+'" class="plot-bg"/>';

  xTicks.forEach(function(x){
    const px=sx(x);
    svg+='<line x1="'+px+'" y1="'+m.t+'" x2="'+px+'" y2="'+(H-m.b)+'" class="grid-line"/>';
    svg+='<text x="'+px+'" y="'+(H-m.b+24)+'" class="axis-text" text-anchor="middle">'+x+'</text>';
  });
  yTicks.forEach(function(y){
    const py=sy(y);
    svg+='<line x1="'+m.l+'" y1="'+py+'" x2="'+(W-m.r)+'" y2="'+py+'" class="grid-line"/>';
    svg+='<text x="'+(m.l-12)+'" y="'+(py+4)+'" class="axis-text" text-anchor="end">'+y+'</text>';
  });

  svg+='<line x1="'+m.l+'" y1="'+(H-m.b)+'" x2="'+(W-m.r)+'" y2="'+(H-m.b)+'" class="axis-line"/>';
  svg+='<line x1="'+m.l+'" y1="'+m.t+'" x2="'+m.l+'" y2="'+(H-m.b)+'" class="axis-line"/>';
  svg+='<text x="'+(m.l+pw/2)+'" y="'+(H-12)+'" class="axis-label" text-anchor="middle">转股价值 CV</text>';
  svg+='<text x="18" y="'+(m.t+ph/2)+'" class="axis-label" text-anchor="middle" transform="rotate(-90 18 '+(m.t+ph/2)+')">'
    +(marketMapMode==="raw"?"实际转债价格":"标准化价格")+'</text>';

  const linePts=[];
  for(let i=0;i<=100;i++){
    const x=xMin+(xMax-xMin)*i/100;
    const y=baseAnchorAt(x);
    if(y>=yMin && y<=yMax)linePts.push(sx(x).toFixed(1)+','+sy(y).toFixed(1));
  }
  if(linePts.length>1)svg+='<polyline points="'+linePts.join(" ")+'" class="base-line"/>';

  const showLabels=$("#mapLabels").checked;
  visible.forEach(function(r){
    const x=Number(r.source_CV), y=mapY(r);
    if(y<yMin || y>yMax)return;
    const selected=r.bond_code===marketMapSelectedCode;
    const title=esc(r.bond_name+'｜价格 '+prettyNumber(r.P,2)+'｜CV '+prettyNumber(r.source_CV,2)+'｜实际-参考 '+prettyNumber(r.diff_candidate,2));
    svg+='<g class="bond-point" data-code="'+esc(r.bond_code)+'">';
    svg+='<circle cx="'+sx(x).toFixed(1)+'" cy="'+sy(y).toFixed(1)+'" r="'+(selected?6:4)+'" class="'+(selected?"point selected":"point")+'"><title>'+title+'</title></circle>';
    if(showLabels){
      svg+='<text x="'+(sx(x)+6).toFixed(1)+'" y="'+(sy(y)-6).toFixed(1)+'" class="point-label">'+esc(r.bond_name)+'</text>';
    }
    svg+='</g>';
  });

  const clipped=visible.filter(function(r){
    const y=mapY(r); return y<yMin || y>yMax;
  }).length;
  svg+='</svg>';
  if(clipped){
    svg+='<div class="chart-note">为保持主体可读，纵轴自动聚焦 2%–98% 分位；'+clipped+' 个极端点未画出，但仍保留在下方明细表。</div>';
  }
  $("#mapChart").innerHTML=svg;

  $("#mapChart").querySelectorAll(".bond-point").forEach(function(g){
    const code=g.getAttribute("data-code");
    const row=(marketMapData.rows||[]).find(function(x){return x.bond_code===code;});
    g.addEventListener("mouseenter",function(){renderMapSelected(row);});
    g.addEventListener("mouseleave",function(){
      const fixed=(marketMapData.rows||[]).find(function(x){return x.bond_code===marketMapSelectedCode;});
      renderMapSelected(fixed||null);
    });
    g.addEventListener("click",function(){
      marketMapSelectedCode=code;
      renderMapSelected(row);
      renderMarketMap();
    });
  });
}

async function loadMarketMap(){
  $("#mapOverall").textContent="加载中";
  $("#mapOverall").className="overall running";
  try{
    const r=await fetch("api/market-map/view");
    if(!r.ok)throw new Error(await r.text());
    marketMapData=await r.json();
    $("#mapOverall").textContent="已加载";
    $("#mapOverall").className="overall pass";
    renderMapMeta();
    renderMapSummary();
    renderMapTable();
    renderMarketMap();
  }catch(err){
    $("#mapOverall").textContent="加载失败";
    $("#mapOverall").className="overall fail";
    $("#mapMeta").textContent="藏宝图加载失败："+err.message;
  }
}

$("#mapRawBtn").addEventListener("click",function(){
  marketMapMode="raw";
  renderMarketMap();
});
$("#mapStdBtn").addEventListener("click",function(){
  marketMapMode="standard";
  renderMarketMap();
});
$("#mapDuration").addEventListener("change",renderMarketMap);
$("#mapScale").addEventListener("change",renderMarketMap);
$("#mapLabels").addEventListener("change",renderMarketMap);
$("#mapReloadBtn").addEventListener("click",loadMarketMap);

loadMarketMap();
