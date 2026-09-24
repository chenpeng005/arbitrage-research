const stepsDef=[
  ["S0","冻结本次运行"],
  ["S1","获取主行情"],
  ["S2","Universe 分类"],
  ["S3","补充到期日"],
  ["S4","补充剩余规模"],
  ["S5","跨源审计"],
  ["S6","Acquisition Ready"]
];
const $=s=>document.querySelector(s);
let currentJob=null, timer=null;
$("#cutoff").value=new Date().toISOString().slice(0,10);

function esc(x){return String(x??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]))}
function renderSkeleton(){
  $("#steps").innerHTML=stepsDef.map(([id,name])=>`<article class="step pending" id="step-${id}">
    <div class="step-head"><div class="step-title"><span class="sid">${id}</span><strong>${name}</strong></div><span class="badge">PENDING</span></div>
    <p class="conclusion">等待程序进入本步骤。</p><div class="metrics"></div>
  </article>`).join("");
}
function statusClass(x){return (x||"idle").toLowerCase()}
function renderStep(event){
  if(!event)return;
  const el=$("#step-"+event.id); if(!el)return;
  el.classList.remove("pending");
  const badge=el.querySelector(".badge");
  badge.textContent=event.status||"RUNNING"; badge.className="badge "+statusClass(event.status);
  el.querySelector(".conclusion").textContent=event.conclusion||"正在执行…";
  const metrics=event.metrics||{};
  el.querySelector(".metrics").innerHTML=Object.entries(metrics).map(([k,v])=>`<span class="metric"><b>${esc(k)}</b> · ${esc(v)}</span>`).join("");
  el.querySelectorAll(".warning-box").forEach(x=>x.remove());
  (event.warnings||[]).forEach(w=>el.insertAdjacentHTML("beforeend",`<div class="warning-box">${esc(w)}</div>`));
}
function setOverall(status){
  const el=$("#overall"); el.textContent=status||"IDLE"; el.className="overall "+statusClass(status);
}
function updateProgress(data){
  const done=Object.values(data.steps||{}).filter(x=>["PASS","WARNING","FAIL"].includes(x.status)).length;
  $("#progressText").textContent=`${done} / 7`;
  $("#bar").style.width=`${Math.min(done/7*100,100)}%`;
}
async function poll(){
  if(!currentJob)return;
  const r=await fetch("/api/runs/"+currentJob);
  const data=await r.json();
  setOverall(data.status);
  $("#runMeta").innerHTML=`<div class="meta-grid"><span><b>Job</b> ${esc(data.job_id)}</span><span><b>模式</b> ${esc(data.snapshot_mode)}</span><span><b>Cutoff</b> ${esc(data.market_cutoff)}</span></div>`;
  Object.values(data.steps||{}).forEach(renderStep);
  updateProgress(data);
  if(["PASS","WARNING","FAIL"].includes(data.status)){
    clearInterval(timer); timer=null; $("#runBtn").disabled=false;
  }
}
$("#runBtn").addEventListener("click",async()=>{
  renderSkeleton(); setOverall("PENDING"); $("#runBtn").disabled=true;
  const payload={snapshot_mode:$("#mode").value,market_cutoff:$("#cutoff").value};
  const r=await fetch("/api/runs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const data=await r.json(); currentJob=data.job_id;
  await poll(); timer=setInterval(poll,1000);
});
renderSkeleton();
