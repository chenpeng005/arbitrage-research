const reviewState={
  pool:null,
  detailCache:new Map(),
  filters:{search:"",path:"",state:""}
};

const PATH_LABELS={
  MATURITY_CASH:"到期现金",
  PUT:"回售",
  DOWNWARD_REVISION:"下修"
};

const STATE_LABELS={
  COMPLETED:"已完成深研",
  HOLD_WAITING_EVIDENCE:"等待证据",
  NOT_TRIGGERED:"尚未触发深研",
  PENDING:"等待研究",
  IN_PROGRESS:"研究中"
};

function $(s){return document.querySelector(s);}

function esc(x){
  return String(x==null?"":x).replace(/[&<>"']/g,function(m){
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m];
  });
}

function textValue(value){
  if(value===null || value===undefined || value==="")return "—";
  if(typeof value==="boolean")return value?"是":"否";
  if(typeof value==="number"){
    if(Number.isInteger(value))return String(value);
    return String(Number(value.toFixed(6)));
  }
  if(typeof value==="string")return value;
  return JSON.stringify(value,null,2);
}

function pathLabel(pathId){
  return PATH_LABELS[pathId]||pathId||"未知 Path";
}

function stateLabel(state){
  return STATE_LABELS[state]||state||"未知";
}

function stateClass(state){
  return String(state||"").toLowerCase();
}

function displayBondName(name){
  return String(name==null?"":name).replace(/转债$/,"");
}

function poolMetaText(pool){
  return [
    ["市场截面",pool.market_cutoff],
    ["Market Snapshot",pool.market_snapshot_id],
    ["Candidate Pool",pool.run_id],
    ["Economic Registry",pool.economic_registry_run_id]
  ].map(function(x){
    return '<span><b>'+esc(x[0])+'</b> '+esc(x[1]||"—")+'</span>';
  }).join("");
}

function countByState(pool,state){
  return Number((pool.research_state_summary||{})[state]||0);
}

function renderSummary(){
  const pool=reviewState.pool;
  const items=[
    ["候选债券",pool.bond_count],
    ["KEEP Path",pool.keep_path_count],
    ["已完成深研",countByState(pool,"COMPLETED")],
    ["等待证据",countByState(pool,"HOLD_WAITING_EVIDENCE")],
    ["尚未触发",countByState(pool,"NOT_TRIGGERED")]
  ];
  $("#reviewSummary").innerHTML=items.map(function(x){
    return '<article><span>'+esc(x[0])+'</span><strong>'+esc(x[1])+'</strong></article>';
  }).join("");
}

function bondMatches(bond){
  const search=reviewState.filters.search.trim().toLowerCase();
  const path=reviewState.filters.path;
  const state=reviewState.filters.state;

  if(search){
    const hay=(String(bond.bond_code||"")+" "+String(bond.bond_name||"")).toLowerCase();
    if(!hay.includes(search))return false;
  }
  if(path && !(bond.paths||[]).some(function(p){return p.path_id===path;}))return false;
  if(state && !(bond.paths||[]).some(function(p){return p.research_state===state;}))return false;
  return true;
}

function renderPathChips(paths){
  return (paths||[]).map(function(p){
    return '<span class="path-chip '+esc(stateClass(p.research_state))+'">'
      +esc(pathLabel(p.path_id))+' · '+esc(stateLabel(p.research_state))
      +'</span>';
  }).join("");
}

function renderBondCard(bond){
  return '<details class="card review-bond" data-code="'+esc(bond.bond_code)+'">'
    +'<summary>'
      +'<div class="bond-main"><div>'
        +'<div><span class="bond-code">'+esc(bond.bond_code)+'</span> '
        +'<span class="bond-name">'+esc(displayBondName(bond.bond_name))+'</span></div>'
        +'<div class="bond-sub">'+esc(bond.keep_path_count)+' 条 Economic KEEP · '+esc(bond.market_cutoff||"—")+'</div>'
      +'</div></div>'
      +'<div class="path-chips">'+renderPathChips(bond.paths)+'</div>'
    +'</summary>'
    +'<div class="review-detail"><div class="loading-box">展开后读取完整 Opportunity Record。</div></div>'
    +'</details>';
}

function renderList(){
  const pool=reviewState.pool;
  const rows=(pool.bonds||[])
    .filter(bondMatches)
    .slice()
    .sort(function(a,b){return String(a.bond_code).localeCompare(String(b.bond_code));});

  $("#visibleCount").textContent="当前视图 "+rows.length+" / "+pool.bond_count+" 只";
  $("#reviewList").innerHTML=rows.length
    ?rows.map(renderBondCard).join("")
    :'<article class="card muted">当前筛选条件下没有债券。重置筛选可恢复全量 Candidate Pool。</article>';

  document.querySelectorAll(".review-bond").forEach(function(el){
    el.addEventListener("toggle",function(){
      if(el.open)loadBondDetail(el.dataset.code,el);
    });
  });
}

function renderScalarSection(title,obj){
  if(!obj || typeof obj!=="object" || Array.isArray(obj))return "";
  const entries=Object.entries(obj);
  if(!entries.length)return "";
  return '<section class="review-section"><h4>'+esc(title)+'</h4><div class="kv-grid">'
    +entries.map(function(entry){
      return '<div class="kv-item"><div class="kv-key">'+esc(entry[0])+'</div>'
        +'<div class="kv-value">'+esc(textValue(entry[1]))+'</div></div>';
    }).join("")
    +'</div></section>';
}

function renderListSection(title,items){
  if(!Array.isArray(items) || !items.length)return "";
  return '<section class="review-section"><h4>'+esc(title)+'</h4><ul>'
    +items.map(function(x){return '<li>'+esc(textValue(x))+'</li>';}).join("")
    +'</ul></section>';
}

function renderEvidence(items){
  if(!Array.isArray(items) || !items.length)return "";
  return '<section class="review-section"><h4>关键证据</h4><div class="evidence-list">'
    +items.map(function(x){
      const supports=Array.isArray(x.supports)?x.supports.join("、"):textValue(x.supports);
      return '<div class="evidence-item">'
        +'<div class="evidence-title">'+esc(x.title||x.claim||x.evidence_id||"证据")+'</div>'
        +'<div class="evidence-meta">'
          +esc(x.source_type||"")+(x.source_date?" · "+esc(x.source_date):"")
          +(x.confidence?" · confidence="+esc(x.confidence):"")
          +(supports&&supports!=="—"?" · supports="+esc(supports):"")
        +'</div>'
        +(x.locator?'<div class="evidence-meta">'+esc(x.locator)+'</div>':"")
      +'</div>';
    }).join("")
    +'</div></section>';
}

function renderFactSpine(value){
  if(!value || typeof value!=="object")return "";
  return '<section class="review-section"><h4>Fact Spine</h4>'
    +'<details class="json-detail"><summary>展开完整事实脊柱</summary><pre>'
    +esc(JSON.stringify(value,null,2))+'</pre></details></section>';
}

function renderEconomicJudgment(value){
  if(!value)return "";
  return '<details class="json-detail"><summary>展开 Economic Judgment 原文</summary><pre>'
    +esc(JSON.stringify(value,null,2))+'</pre></details>';
}

function renderPathResult(result,state){
  if(!result){
    if(state==="NOT_TRIGGERED"){
      return '<div class="empty-result">Economic KEEP 已成立；当前尚未到 Engineering Trigger 节点，因此没有 Path Result。</div>';
    }
    return '<div class="empty-result">当前尚无可展示的 Path Result。</div>';
  }

  const hold=state==="HOLD_WAITING_EVIDENCE"
    ?'<div class="hold-box"><b>当前研究未闭合。</b> Economic KEEP 保留；以下为当前完整 Path Result 与尚待闭合的 UNKNOWN-B。</div>'
    :"";

  return hold
    +renderScalarSection("Judgments",result.judgments)
    +renderFactSpine(result.fact_spine)
    +renderListSection("UNKNOWN-A",result.unknown_a)
    +renderListSection("UNKNOWN-B",result.unknown_b)
    +renderListSection("关键风险",result.key_risks)
    +renderListSection("失效条件",result.failure_conditions)
    +renderListSection("下一更新节点",result.next_update_nodes)
    +renderEvidence(result.key_evidence)
    +'<details class="json-detail"><summary>完整 Path Result JSON</summary><pre>'
      +esc(JSON.stringify(result,null,2))+'</pre></details>';
}

function renderPathPanel(path){
  const state=path.research_state;
  const meta=[
    "Economic KEEP",
    stateLabel(state),
    path.current_event_state||"无事件状态"
  ].join(" · ");

  const info=[
    ["Research State",stateLabel(state)],
    ["Research Status",path.research_status||"—"],
    ["Review Ready",path.review_ready===null||path.review_ready===undefined?"—":(path.review_ready?"是":"否")]
  ];

  return '<article class="path-panel">'
    +'<div class="path-head"><div><h3>'+esc(pathLabel(path.path_id))+'</h3>'
      +'<div class="path-meta">'+esc(meta)+'</div></div>'
      +'<span class="path-chip '+esc(stateClass(state))+'">'+esc(stateLabel(state))+'</span></div>'
    +'<div class="path-body">'
      +'<div class="info-grid">'+info.map(function(x){
        return '<div class="info-box"><span>'+esc(x[0])+'</span><strong>'+esc(x[1])+'</strong></div>';
      }).join("")+'</div>'
      +renderEconomicJudgment(path.economic_judgment)
      +renderPathResult(path.path_result,state)
    +'</div></article>';
}

function renderBondDetail(record){
  const stateLabelMap={
    HAS_HOLD:"含等待证据 Path",
    HAS_COMPLETED_RESEARCH:"已有完成深研",
    ECONOMIC_KEEP_WAITING_TRIGGER:"等待 Trigger",
    RESEARCH_IN_PROGRESS:"研究处理中"
  };
  return '<div class="meta-grid" style="margin-bottom:12px">'
    +'<span><b>记录状态</b> '+esc(stateLabelMap[record.record_state]||record.record_state)+'</span>'
    +'<span><b>KEEP Path</b> '+esc(record.keep_path_count)+'</span>'
    +'<span><b>市场截面</b> '+esc(record.market_cutoff||"—")+'</span>'
    +'</div>'
    +(record.paths||[]).map(renderPathPanel).join("");
}

async function loadBondDetail(code,detailsEl){
  const zone=detailsEl.querySelector(".review-detail");
  if(reviewState.detailCache.has(code)){
    zone.innerHTML=renderBondDetail(reviewState.detailCache.get(code));
    return;
  }
  zone.innerHTML='<div class="loading-box">正在读取 '+esc(code)+' 的完整 Opportunity Record……</div>';
  try{
    const r=await fetch("/api/opportunity/records/"+encodeURIComponent(code));
    if(!r.ok)throw new Error(await r.text());
    const record=await r.json();
    reviewState.detailCache.set(code,record);
    zone.innerHTML=renderBondDetail(record);
  }catch(err){
    zone.innerHTML='<div class="hold-box"><b>详情读取失败：</b>'+esc(err.message)+'</div>';
  }
}

function bindFilters(){
  $("#reviewSearch").addEventListener("input",function(e){
    reviewState.filters.search=e.target.value||"";
    renderList();
  });
  $("#reviewPath").addEventListener("change",function(e){
    reviewState.filters.path=e.target.value||"";
    renderList();
  });
  $("#reviewState").addEventListener("change",function(e){
    reviewState.filters.state=e.target.value||"";
    renderList();
  });
  $("#reviewReset").addEventListener("click",function(){
    $("#reviewSearch").value="";
    $("#reviewPath").value="";
    $("#reviewState").value="";
    reviewState.filters={search:"",path:"",state:""};
    renderList();
  });
}

async function loadReview(){
  try{
    const r=await fetch("/api/opportunity/candidate-pool/latest");
    if(!r.ok)throw new Error(await r.text());
    const pool=await r.json();
    reviewState.pool=pool;
    $("#reviewOverall").textContent="已加载";
    $("#reviewOverall").className="overall pass";
    $("#reviewMeta").innerHTML='<div class="meta-grid">'+poolMetaText(pool)+'</div>';
    renderSummary();
    renderList();
  }catch(err){
    $("#reviewOverall").textContent="加载失败";
    $("#reviewOverall").className="overall fail";
    $("#reviewMeta").textContent="Candidate Pool 读取失败："+err.message;
    $("#reviewList").innerHTML='<article class="card hold-box">无法加载 Review 数据。</article>';
  }
}

bindFilters();
loadReview();
