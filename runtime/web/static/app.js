const stepsDef=[
  ["S0","冻结本次运行"],
  ["S1","获取主行情"],
  ["S2","可转债范围筛选"],
  ["S3","补充到期日"],
  ["S4","补充剩余规模"],
  ["S5","多来源交叉审计"],
  ["S6","数据准备完成"]
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
  core_zone:"核心区间样本"
};

const summaryLabels={
  source_candidate:"主源候选",
  base_sample:"基础样本",
  duration_sample:"期限样本",
  scale_sample:"规模样本",
  support_zone:"支持区间",
  core_zone:"核心区间"
};

const $=s=>document.querySelector(s);
let currentJob=null;
let timer=null;
$("#cutoff").value=new Date().toISOString().slice(0,10);

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

function metricText(key,value){
  if(["cv_diff_ratio_median","cv_diff_ratio_max"].includes(key)){
    return (Number(value)*100).toFixed(4)+"%";
  }
  if(key==="snapshot_mode"){
    return value==="LIVE_TEST"?"盘中测试":"正式收盘截面";
  }
  return value;
}

function renderSkeleton(){
  $("#summary").classList.add("hidden");
  $("#summaryMetrics").innerHTML="";
  $("#steps").innerHTML=stepsDef.map(function(item){
    const id=item[0], name=item[1];
    return '<article class="step pending" id="step-'+id+'">'
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
  if(["calc_CV","source_CV","P","S","K","K_aux","K_aux_diff","remaining_size","issue_size"].includes(key)){
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

function renderStep(event){
  if(!event)return;
  const el=$("#step-"+event.id);
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

function setOverall(status){
  const el=$("#overall");
  el.textContent=statusText(status);
  el.className="overall "+statusClass(status);
}

function updateProgress(data){
  const done=Object.values(data.steps||{}).filter(function(x){
    return ["PASS","WARNING","FAIL"].includes(x.status);
  }).length;
  $("#progressText").textContent=done+" / 7";
  $("#bar").style.width=Math.min(done/7*100,100)+"%";
}

function renderSummary(data){
  const s6=(data.steps||{}).S6;
  if(!s6 || !s6.metrics)return;
  $("#summary").classList.remove("hidden");
  $("#summaryMetrics").innerHTML=Object.entries(summaryLabels).map(function(entry){
    const key=entry[0], label=entry[1], value=s6.metrics[key];
    return '<article><span>'+esc(label)+'</span><strong>'+esc(value==null?"—":value)+'</strong></article>';
  }).join("");
}

async function poll(){
  if(!currentJob)return;
  const r=await fetch("api/runs/"+currentJob);
  const data=await r.json();
  setOverall(data.status);
  const mode=data.snapshot_mode==="LIVE_TEST"?"盘中测试":"正式收盘截面";
  $("#runMeta").innerHTML='<div class="meta-grid">'
    +'<span><b>运行任务</b> '+esc(data.job_id)+'</span>'
    +'<span><b>模式</b> '+esc(mode)+'</span>'
    +'<span><b>市场截面</b> '+esc(data.market_cutoff)+'</span>'
    +'</div>';
  Object.values(data.steps||{}).forEach(renderStep);
  updateProgress(data);
  renderSummary(data);
  if(["PASS","WARNING","FAIL"].includes(data.status)){
    clearInterval(timer);
    timer=null;
    $("#runBtn").disabled=false;
  }
}

$("#runBtn").addEventListener("click",async function(){
  renderSkeleton();
  setOverall("PENDING");
  $("#runBtn").disabled=true;
  const payload={snapshot_mode:$("#mode").value,market_cutoff:$("#cutoff").value};
  try{
    const r=await fetch("api/runs",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(payload)
    });
    if(!r.ok)throw new Error("启动失败");
    const data=await r.json();
    currentJob=data.job_id;
    await poll();
    timer=setInterval(poll,1000);
  }catch(err){
    setOverall("FAIL");
    $("#runMeta").textContent="无法启动本次运行："+err.message;
    $("#runBtn").disabled=false;
  }
});

renderSkeleton();
