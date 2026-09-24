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
    return ["PASS","WARNING","FAIL"].includes(x.status);
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

  if(["PASS","WARNING","FAIL"].includes(data.status)){
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

  if(["PASS","WARNING","FAIL"].includes(data.status)){
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
