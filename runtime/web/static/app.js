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
  ["C5","拟合剩余规模调整"],
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
  NEEDS_REVIEW:"需要人工处理",
  WAITING_FOR_CHAT:"等待 ChatGPT",
  CLAIMED_BY_CHAT:"ChatGPT 已领取"
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
let pipelineJob=null;
let pipelineTimer=null;
let currentChatTask=null;
let marketMapCalculationJobId=null;
let historicalSnapshots=[];

function localDateString(){
  const d=new Date();
  const local=new Date(d.getTime()-d.getTimezoneOffset()*60000);
  return local.toISOString().slice(0,10);
}

function chinaDateString(){
  try{
    return new Intl.DateTimeFormat("en-CA",{
      timeZone:"Asia/Shanghai",
      year:"numeric",
      month:"2-digit",
      day:"2-digit"
    }).format(new Date());
  }catch(_err){
    return localDateString();
  }
}

$("#cutoff").value=chinaDateString();
$("#calcCutoff").value=chinaDateString();
$("#pipelineCutoff").value=chinaDateString();

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

function displayBondName(name){
  return String(name==null?"":name).replace(/转债$/,"");
}

function numericFilterValue(selector){
  const raw=$(selector).value.trim();
  if(raw==="")return null;
  const n=Number(raw);
  return Number.isFinite(n)?n:null;
}

function filteredMarketRows(){
  const priceMin=numericFilterValue("#mapPriceMin");
  const priceMax=numericFilterValue("#mapPriceMax");
  const cvMin=numericFilterValue("#mapCvMin");
  const cvMax=numericFilterValue("#mapCvMax");
  const sizeMax=numericFilterValue("#mapSizeMax");
  const premiumMax=numericFilterValue("#mapPremiumMax");

  return (marketMapData&&marketMapData.rows?marketMapData.rows:[]).filter(function(r){
    const p=Number(r.P);
    const cv=Number(r.trusted_CV);
    const size=Number(r.remaining_size);
    const premium=(Number.isFinite(p)&&Number.isFinite(cv)&&cv!==0)
      ?(p/cv-1)*100
      :NaN;

    if(priceMin!==null && (!Number.isFinite(p)||p<priceMin))return false;
    if(priceMax!==null && (!Number.isFinite(p)||p>priceMax))return false;
    if(cvMin!==null && (!Number.isFinite(cv)||cv<cvMin))return false;
    if(cvMax!==null && (!Number.isFinite(cv)||cv>cvMax))return false;
    if(sizeMax!==null && (!Number.isFinite(size)||size>sizeMax))return false;
    if(premiumMax!==null && (!Number.isFinite(premium)||premium>premiumMax))return false;
    return true;
  });
}

function metricText(key,value){
  if(["cv_diff_ratio_median","cv_diff_ratio_max"].includes(key)){
    return (Number(value)*100).toFixed(4)+"%";
  }
  if(key==="snapshot_mode"){
    if(value==="LIVE_TEST")return "盘中测试";
    if(value==="REPLAY_TEST")return "历史回放测试";
    if(value==="HISTORICAL_REPLAY")return "历史正式截面回放";
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
    "calc_CV","trusted_CV","source_CV","P","S","K","K_aux","K_aux_diff",
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
  if(data.snapshot_mode==="HISTORICAL_REPLAY")mode="历史正式截面回放";

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

const pipelinePhaseLabels={
  PENDING:"等待启动",
  ACQUISITION:"数据获取与确定性审计",
  AI_SEMANTIC_AUDIT:"AI 语义审计",
  WAITING_FOR_CHAT:"等待 ChatGPT 交互处理",
  RESUMING_AFTER_CHAT:"ChatGPT 已通过，恢复 Runtime",
  CALCULATION:"市场价值映射计算",
  COMPLETE:"完成",
  STOPPED_AT_AI:"停止在 AI 审计",
  STOPPED_AFTER_AI_VALIDATION:"AI 结果未形成可信输入",
  STOPPED_AT_ACQUISITION:"停止在数据获取",
  STOPPED_AT_CALCULATION:"停止在计算",
  CONTROLLER_ERROR:"Controller 错误"
};

async function copyText(text){
  if(navigator.clipboard && navigator.clipboard.writeText){
    await navigator.clipboard.writeText(text);
    return;
  }
  const ta=document.createElement("textarea");
  ta.value=text;
  document.body.appendChild(ta);
  ta.select();
  document.execCommand("copy");
  ta.remove();
}

async function loadChatTask(taskId){
  if(!taskId)return null;
  const r=await fetch("api/chat-tasks/"+encodeURIComponent(taskId));
  if(!r.ok)throw new Error(await r.text());
  return await r.json();
}

async function renderChatHandoff(data){
  const box=$("#chatHandoff");
  if(data.status!=="WAITING_FOR_CHAT" || !data.chat_task_id){
    if(data.status!=="RUNNING")box.classList.add("hidden");
    return;
  }

  box.classList.remove("hidden");
  currentChatTask=await loadChatTask(data.chat_task_id);
  const chatState=(currentChatTask||{}).status||"WAITING_FOR_CHAT";
  $("#chatTaskBadge").textContent=statusText(chatState);
  $("#chatTaskBadge").className="overall "+statusClass(chatState);

  const reqs=((currentChatTask||{}).semantic_review_request||{}).requests||[];
  const names=reqs.map(function(x){
    return (x.bond_name||x.bond_code||"")+" · "+(x.field||"");
  });

  $("#chatTaskSummary").innerHTML=
    '<b>Task ID：</b>'+esc(currentChatTask.task_id)
    +'　<b>冲突：</b>'+esc(names.join("；")||"-")
    +(chatState==="CLAIMED_BY_CHAT"
      ?'<br><span class="muted">该任务已被一个 Chat 领取；其他 Chat 不会再领取同一任务。领取超时后会自动释放。</span>'
      :'<br><span class="muted">推荐：点击“复制一句话给 ChatGPT”，回到任意一个可访问本项目服务器的 Chat 直接粘贴。第一个 Chat 会先领取任务。</span>');

  $("#chatTaskPreview").textContent=JSON.stringify(currentChatTask,null,2);
}

async function initializeMarketStatus(){
  try{
    const r=await fetch("api/market-status");
    if(!r.ok)throw new Error(await r.text());
    const status=await r.json();

    if(status.china_date){
      $("#pipelineCutoff").value=status.china_date;
      $("#cutoff").value=status.china_date;
      $("#calcCutoff").value=status.china_date;
    }

    if(!status.after_close_gate){
      if(status.latest_replay){
        $("#pipelineMode").value="HISTORICAL_REPLAY";
        $("#pipelineMeta").innerHTML=
          '<b>当前中国市场时间：</b>'+esc(status.china_time)
          +'。今日正式收盘尚未冻结，已默认切换到最近正式历史截面：'
          +esc(status.latest_replay.market_cutoff)
          +'。你也可以改选盘中测试。';
      }else{
        $("#pipelineMode").value="LIVE_TEST";
        $("#pipelineMeta").innerHTML=
          '<b>当前中国市场时间：</b>'+esc(status.china_time)
          +'。今日正式收盘尚未冻结，且暂无可回放正式历史截面，已默认切换到盘中测试。';
      }
    }else{
      $("#pipelineMode").value="CLOSE";
      $("#pipelineMeta").innerHTML=
        '<b>当前中国市场时间：</b>'+esc(status.china_time)
        +'。今日正式收盘模式已可用。';
    }

    updatePipelineModeUi();
  }catch(err){
    $("#pipelineMeta").textContent="市场时间状态读取失败："+err.message;
  }
}

async function loadHistoricalSnapshots(){
  const r=await fetch("api/market-map/history");
  if(!r.ok)throw new Error(await r.text());
  const data=await r.json();
  historicalSnapshots=(data.items||[]).filter(function(x){
    return x.replay_ready;
  });

  const select=$("#pipelineHistorySnapshot");
  if(!historicalSnapshots.length){
    select.innerHTML='<option value="">暂无可回放的正式历史截面</option>';
    return;
  }

  select.innerHTML=historicalSnapshots.map(function(x){
    const label=(x.market_cutoff||"-")
      +" · "+(x.model_version||"-")
      +" · "+String(x.snapshot_id||"").slice(-15);
    return '<option value="'+esc(x.snapshot_id)+'">'+esc(label)+'</option>';
  }).join("");

  syncHistoricalSelection();
}

function syncHistoricalSelection(){
  if($("#pipelineMode").value!=="HISTORICAL_REPLAY")return;
  const id=$("#pipelineHistorySnapshot").value;
  const item=historicalSnapshots.find(function(x){return x.snapshot_id===id;});
  if(item){
    $("#pipelineCutoff").value=item.market_cutoff||"";
  }
}

function updatePipelineModeUi(){
  const historical=$("#pipelineMode").value==="HISTORICAL_REPLAY";
  $("#pipelineHistoryWrap").classList.toggle("hidden",!historical);
  $("#pipelineCutoffWrap").classList.toggle("hidden",historical);
  if(historical){
    loadHistoricalSnapshots().catch(function(err){
      $("#pipelineMeta").textContent="历史快照列表读取失败："+err.message;
    });
  }
}

async function pollPipeline(){
  if(!pipelineJob)return;
  const r=await fetch("api/runs/"+pipelineJob);
  const data=await r.json();
  setOverall("#pipelineOverall",data.status);

  const phase=pipelinePhaseLabels[data.phase]||data.phase||"等待";
  $("#pipelineMeta").innerHTML='<div class="meta-grid">'
    +'<span><b>Pipeline</b> '+esc(data.job_id)+'</span>'
    +'<span><b>阶段</b> '+esc(phase)+'</span>'
    +'<span><b>模式</b> '+esc(
      data.snapshot_mode==="HISTORICAL_REPLAY"?"历史回放":
      (data.snapshot_mode==="LIVE_TEST"?"盘中测试":"今日正式运行")
    )+'</span>'
    +'<span><b>市场截面</b> '+esc(data.market_cutoff||"-")+'</span>'
    +(data.historical_snapshot_id?'<span><b>历史来源</b> '+esc(data.historical_snapshot_id)+'</span>':"")
    +'<span><b>AI方式</b> '+esc(data.ai_execution_mode==="INTERACTIVE_CHAT"?"ChatGPT交互":"自动API（DeepSeek）")+'</span>'
    +(data.acquisition_job_id?'<span><b>Acquisition</b> '+esc(data.acquisition_job_id)+'</span>':"")
    +(data.ai_job_id?'<span><b>AI Job</b> '+esc(data.ai_job_id)+'</span>':"")
    +(data.ai_status?'<span><b>AI 状态</b> '+esc(statusText(data.ai_status))+'</span>':"")
    +(data.chat_task_id?'<span><b>Chat Task</b> '+esc(data.chat_task_id)+'</span>':"")
    +(data.calculation_job_id?'<span><b>Calculation</b> '+esc(data.calculation_job_id)+'</span>':"")
    +(data.error?'<span><b>错误</b> '+esc(data.error)+'</span>':"")
    +'</div>';

  try{
    await renderChatHandoff(data);
  }catch(err){
    $("#chatTaskSummary").textContent="Chat Task 读取失败："+err.message;
  }

  if(["PASS","WARNING","FAIL","NEEDS_REVIEW"].includes(data.status)){
    clearInterval(pipelineTimer);
    pipelineTimer=null;
    $("#pipelineRunBtn").disabled=false;
    if(["PASS","WARNING"].includes(data.status)){
      if(data.snapshot_mode==="CLOSE"){
        try{await loadMarketMap();}catch(_err){}
      }else if(data.snapshot_mode==="HISTORICAL_REPLAY" && data.calculation_job_id){
        try{await loadMarketMap(data.calculation_job_id);}catch(_err){}
      }
    }
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

$("#pipelineRunBtn").addEventListener("click",async function(){
  setOverall("#pipelineOverall","PENDING");
  $("#pipelineRunBtn").disabled=true;

  const mode=$("#pipelineMode").value;
  const payload={
    snapshot_mode:mode,
    market_cutoff:$("#pipelineCutoff").value,
    ai_execution_mode:$("#pipelineAiMode").value
  };
  if(mode==="HISTORICAL_REPLAY"){
    payload.historical_snapshot_id=$("#pipelineHistorySnapshot").value;
    if(!payload.historical_snapshot_id){
      setOverall("#pipelineOverall","FAIL");
      $("#pipelineMeta").textContent="请先选择一个可回放的历史正式截面。";
      $("#pipelineRunBtn").disabled=false;
      return;
    }
  }

  currentChatTask=null;
  $("#chatHandoff").classList.add("hidden");
  $("#pipelineMeta").textContent="Controller 正在启动完整藏宝图 Runtime。";

  try{
    const r=await fetch("api/market-map-runs",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(payload)
    });
    if(!r.ok)throw new Error(await r.text());
    const data=await r.json();
    pipelineJob=data.job_id;
    await pollPipeline();
    pipelineTimer=setInterval(pollPipeline,1000);
  }catch(err){
    setOverall("#pipelineOverall","FAIL");
    $("#pipelineMeta").textContent="无法启动完整 Runtime："+err.message;
    $("#pipelineRunBtn").disabled=false;
  }
});

$("#chatCopyBtn").addEventListener("click",async function(){
  if(!currentChatTask)return;
  try{
    await copyText(currentChatTask.chat_instruction);
    $("#chatTaskSummary").innerHTML+='<br><b>已复制。</b> 回到本项目 ChatGPT 对话直接粘贴即可。';
  }catch(err){
    $("#chatTaskSummary").innerHTML+='<br><b>复制失败：</b>'+esc(err.message);
  }
});

$("#chatCopyFullBtn").addEventListener("click",async function(){
  if(!currentChatTask)return;
  try{
    await copyText(JSON.stringify(currentChatTask,null,2));
    $("#chatTaskSummary").innerHTML+='<br><b>完整任务 JSON 已复制。</b>';
  }catch(err){
    $("#chatTaskSummary").innerHTML+='<br><b>复制失败：</b>'+esc(err.message);
  }
});

$("#chatResumeBtn").addEventListener("click",async function(){
  if(!pipelineJob)return;
  $("#chatResumeBtn").disabled=true;
  try{
    const r=await fetch(
      "api/market-map-runs/"+encodeURIComponent(pipelineJob)+"/resume-after-chat",
      {method:"POST"}
    );
    if(!r.ok)throw new Error(await r.text());
    $("#chatTaskSummary").innerHTML+='<br><b>Program Validator 已通过，Runtime 正在恢复。</b>';
    await pollPipeline();
  }catch(err){
    $("#chatTaskSummary").innerHTML+='<br><b>尚不能继续：</b>'+esc(err.message);
  }finally{
    $("#chatResumeBtn").disabled=false;
  }
});

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
  const source=marketMapData.source_type==="FORMAL_REGISTRY"
    ?"正式收盘 Registry"
    :(marketMapData.source_type==="HISTORICAL_REPLAY"?"历史回放结果":"未知来源");
  const scaleStatus=((((marketMapData||{}).components||{}).scale_neutral||{}).status||"") === "REQUIRED" ? "正式" : "非正式";
  const warningCount=(marketMapData.acquisition_warnings||[]).length;
  $("#mapMeta").innerHTML='<div class="meta-grid">'
    +'<span><b>来源</b> '+esc(source)+'</span>'
    +'<span><b>市场截面</b> '+esc(marketMapData.market_cutoff)+'</span>'
    +'<span><b>Snapshot</b> '+esc(marketMapData.snapshot_id||"-")+'</span>'
    +'<span><b>模型版本</b> '+esc(marketMapData.model_version||"-")+'</span>'
    +'<span><b>模型 Support</b> '+esc((z.support||[]).join("–"))+'</span>'
    +'<span><b>Core</b> '+esc((z.core||[]).join("–"))+'</span>'
    +'<span><b>规模调整</b> '+esc(scaleStatus)+'</span>'
    +'<span><b>上游警告</b> '+esc(warningCount)+'</span>'
    +'</div>';
}

function renderMapSelected(row){
  const el=$("#mapSelected");
  if(!row){
    el.className="map-selected muted";
    el.textContent="鼠标移到散点上查看单券；点击散点可以固定查看。";
    return;
  }
  const diff=Number(row.diff_to_reference);
  el.className="map-selected";
  el.innerHTML='<div class="selected-title"><strong>'+esc(displayBondName(row.bond_name))+'</strong><span>'+esc(row.bond_code)+'</span></div>'
    +'<div class="selected-grid">'
    +'<span>实际价格 <b>'+prettyNumber(row.P,2)+'</b></span>'
    +'<span>转股价值 <b>'+prettyNumber(row.trusted_CV,2)+'</b></span>'
    +'<span>剩余期限 <b>'+prettyNumber(row.remaining_months,1)+' 月</b></span>'
    +'<span>剩余规模 <b>'+prettyNumber(row.remaining_size,2)+' 亿</b></span>'
    +'<span>Base <b>'+prettyNumber(row.base_anchor,2)+'</b></span>'
    +'<span>期限调整 <b>'+prettyNumber(row.duration_adjustment,2)+'</b></span>'
    +'<span>规模调整 <b>'+prettyNumber(row.scale_neutral,2)+'</b></span>'
    +'<span>藏宝图参考 <b>'+prettyNumber(row.discovery_reference,2)+'</b></span>'
    +'<span>实际 - 参考 <b class="'+(diff<0?"diff-low":"diff-high")+'">'+prettyNumber(diff,2)+'</b></span>'
    +'</div>';
}

function renderMapTable(){
  if(!marketMapData)return;
  const rows=filteredMarketRows().slice().sort(function(a,b){
    return Number(a.diff_to_reference)-Number(b.diff_to_reference);
  });
  $("#mapTableBody").innerHTML=rows.map(function(r){
    const diff=Number(r.diff_to_reference);
    return '<tr data-bond="'+esc(r.bond_code)+'">'
      +'<td>'+esc(r.bond_code)+'</td>'
      +'<td>'+esc(displayBondName(r.bond_name))+'</td>'
      +'<td>'+prettyNumber(r.P,2)+'</td>'
      +'<td>'+prettyNumber(r.trusted_CV,2)+'</td>'
      +'<td>'+prettyNumber(r.remaining_months,1)+'</td>'
      +'<td>'+prettyNumber(r.remaining_size,2)+'</td>'
      +'<td>'+prettyNumber(r.base_anchor,2)+'</td>'
      +'<td>'+prettyNumber(r.duration_adjustment,2)+'</td>'
      +'<td>'+prettyNumber(r.scale_neutral,2)+'</td>'
      +'<td>'+prettyNumber(r.discovery_reference,2)+'</td>'
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
  const visible=filteredMarketRows().filter(function(r){
    return Number.isFinite(Number(r.trusted_CV)) && Number.isFinite(mapY(r));
  });

  if(!visible.length){
    $("#mapChart").innerHTML='<div class="chart-empty">当前筛选条件下没有可绘制样本。</div>';
    return;
  }

  const xs=visible.map(function(r){return Number(r.trusted_CV);});
  const ys=visible.map(mapY);

  const rawXMin=Math.min.apply(null,xs);
  const rawXMax=Math.max.apply(null,xs);
  const xSpread=Math.max(10,rawXMax-rawXMin);
  let xMin=Math.floor((rawXMin-xSpread*0.04)/10)*10;
  let xMax=Math.ceil((rawXMax+xSpread*0.04)/10)*10;
  if(xMax<=xMin)xMax=xMin+10;

  const rawYMin=Math.min.apply(null,ys);
  const rawYMax=Math.max.apply(null,ys);
  const ySpread=Math.max(10,rawYMax-rawYMin);
  let yMin=Math.floor((rawYMin-ySpread*0.06)/5)*5;
  let yMax=Math.ceil((rawYMax+ySpread*0.06)/5)*5;
  if(yMax<=yMin)yMax=yMin+10;

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

  function baseLinePoints(startX,endX){
    const pts=[];
    if(!(endX>startX))return pts;
    for(let i=0;i<=100;i++){
      const x=startX+(endX-startX)*i/100;
      const y=baseAnchorAt(x);
      if(y>=yMin && y<=yMax){
        pts.push(sx(x).toFixed(1)+','+sy(y).toFixed(1));
      }
    }
    return pts;
  }

  const supportMin=Number(zone[0]||50);
  const supportMax=Number(zone[1]||130);

  const leftPts=baseLinePoints(xMin,Math.min(xMax,supportMin));
  if(leftPts.length>1){
    svg+='<polyline points="'+leftPts.join(" ")+'" class="base-line extrapolated"/>';
  }

  const modelXMin=Math.max(xMin,supportMin);
  const modelXMax=Math.min(xMax,supportMax);
  const linePts=baseLinePoints(modelXMin,modelXMax);
  if(linePts.length>1){
    svg+='<polyline points="'+linePts.join(" ")+'" class="base-line"/>';
  }

  const rightPts=baseLinePoints(Math.max(xMin,supportMax),xMax);
  if(rightPts.length>1){
    svg+='<polyline points="'+rightPts.join(" ")+'" class="base-line extrapolated"/>';
  }

  visible.forEach(function(r){
    const x=Number(r.trusted_CV), y=mapY(r);
    if(y<yMin || y>yMax)return;
    const selected=r.bond_code===marketMapSelectedCode;
    const shortName=displayBondName(r.bond_name);
    const title=esc(shortName+'｜价格 '+prettyNumber(r.P,2)+'｜CV '+prettyNumber(r.trusted_CV,2)+'｜实际-参考 '+prettyNumber(r.diff_to_reference,2));
    svg+='<g class="bond-point" data-code="'+esc(r.bond_code)+'">';
    svg+='<circle cx="'+sx(x).toFixed(1)+'" cy="'+sy(y).toFixed(1)+'" r="'+(selected?6:4)+'" class="'+(selected?"point selected":"point")+'"><title>'+title+'</title></circle>';
    svg+='<text x="'+(sx(x)+6).toFixed(1)+'" y="'+(sy(y)-6).toFixed(1)+'" class="point-label">'+esc(shortName)+'</text>';
    svg+='</g>';
  });

  svg+='</svg>';
  svg+='<div class="chart-note">当前显示 '+visible.length+' 只；坐标轴按筛选结果自动缩放。红色实线 = 模型 Support '
    +esc((zone||[]).join("–"))
    +'；红色虚线 = 同一 Base 公式的数学外推，仅供观察，不属于正式有效区间。</div>';
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
