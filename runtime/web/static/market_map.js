let mapData=null;
let fixedCode=null;
const $=s=>document.querySelector(s);
function esc(x){return String(x??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]));}
function n(x,d=2){const v=Number(x);return Number.isFinite(v)?v.toFixed(d):"—";}
function nameOf(x){return String(x??"").replace(/转债$/,"");}
function val(sel){const x=$(sel).value.trim();if(x==="")return null;const v=Number(x);return Number.isFinite(v)?v:null;}
function rows(){
  if(!mapData)return [];
  const q=$("#mapSearch").value.trim().toLowerCase();
  const p0=val("#priceMin"),p1=val("#priceMax"),c0=val("#cvMin"),c1=val("#cvMax");
  return (mapData.rows||[]).filter(r=>{
    const p=Number(r.P),cv=Number(r.trusted_CV);
    if(q && !(String(r.bond_code)+" "+String(r.bond_name)).toLowerCase().includes(q))return false;
    if(p0!==null&&p<p0)return false;if(p1!==null&&p>p1)return false;
    if(c0!==null&&cv<c0)return false;if(c1!==null&&cv>c1)return false;
    return Number.isFinite(p)&&Number.isFinite(cv);
  });
}
function baseAnchor(cv){
  const p=((((mapData||{}).components||{}).base||{}).params)||{};
  const z=(cv-90)/20;
  return Number(p.beta0)+Number(p.beta1)*z+Number(p.beta2)*z*z;
}
function selected(r){
  const el=$("#mapSelected");
  if(!r){el.textContent="鼠标移到散点查看单券；点击可固定。";return;}
  const diff=Number(r.diff_to_reference);
  el.innerHTML='<div class="selected-line"><b>'+esc(nameOf(r.bond_name))+' '+esc(r.bond_code)+'</b>'
    +'<span>实际价 <b>'+n(r.P)+'</b></span>'
    +'<span>转股价值 <b>'+n(r.trusted_CV)+'</b></span>'
    +'<span>参考 <b>'+n(r.discovery_reference)+'</b></span>'
    +'<span>实际－参考 <b class="'+(diff<0?"diff-low":"diff-high")+'">'+n(diff)+'</b></span>'
    +'<span>剩余期限 '+n(r.remaining_months,1)+' 月</span>'
    +'</div>';
}
function draw(){
  if(!mapData)return;
  const rs=rows();
  $("#mapCount").textContent="当前显示 "+rs.length+" 只";
  if(!rs.length){$("#mapChart").innerHTML='<div class="map-empty">当前筛选条件下没有样本。</div>';$("#mapTableBody").innerHTML='<tr><td colspan="7">当前筛选条件下没有样本。</td></tr>';return;}
  const xs=rs.map(r=>Number(r.trusted_CV)),ys=rs.map(r=>Number(r.P));
  const xmin=Math.floor((Math.min(...xs)-5)/10)*10,xmax=Math.ceil((Math.max(...xs)+5)/10)*10;
  const ymin=Math.floor((Math.min(...ys)-5)/5)*5,ymax=Math.ceil((Math.max(...ys)+5)/5)*5;
  const W=1040,H=540,m={l:64,r:24,t:22,b:50},pw=W-m.l-m.r,ph=H-m.t-m.b;
  const sx=x=>m.l+(x-xmin)/(xmax-xmin||1)*pw, sy=y=>m.t+(ymax-y)/(ymax-ymin||1)*ph;
  let svg='<svg viewBox="0 0 '+W+' '+H+'"><rect x="'+m.l+'" y="'+m.t+'" width="'+pw+'" height="'+ph+'" class="plot-bg"/>';
  for(let x=Math.ceil(xmin/10)*10;x<=xmax;x+=10){const px=sx(x);svg+='<line x1="'+px+'" y1="'+m.t+'" x2="'+px+'" y2="'+(H-m.b)+'" class="grid-line"/><text x="'+px+'" y="'+(H-m.b+22)+'" class="axis-text" text-anchor="middle">'+x+'</text>';}
  const ystep=Math.max(5,Math.ceil((ymax-ymin)/7/5)*5);
  for(let y=Math.ceil(ymin/ystep)*ystep;y<=ymax;y+=ystep){const py=sy(y);svg+='<line x1="'+m.l+'" y1="'+py+'" x2="'+(W-m.r)+'" y2="'+py+'" class="grid-line"/><text x="'+(m.l-10)+'" y="'+(py+4)+'" class="axis-text" text-anchor="end">'+y+'</text>';}
  svg+='<line x1="'+m.l+'" y1="'+(H-m.b)+'" x2="'+(W-m.r)+'" y2="'+(H-m.b)+'" class="axis-line"/><line x1="'+m.l+'" y1="'+m.t+'" x2="'+m.l+'" y2="'+(H-m.b)+'" class="axis-line"/>';
  svg+='<text x="'+(m.l+pw/2)+'" y="'+(H-10)+'" class="axis-label" text-anchor="middle">转股价值</text><text x="18" y="'+(m.t+ph/2)+'" class="axis-label" text-anchor="middle" transform="rotate(-90 18 '+(m.t+ph/2)+')">实际转债价格</text>';
  const zone=((mapData.zones||{}).support)||[50,130],start=Math.max(xmin,Number(zone[0])),end=Math.min(xmax,Number(zone[1]));
  const pts=[]; if(end>start){for(let i=0;i<=120;i++){const x=start+(end-start)*i/120,y=baseAnchor(x);if(Number.isFinite(y))pts.push(sx(x).toFixed(1)+','+sy(y).toFixed(1));}}
  if(pts.length>1)svg+='<polyline points="'+pts.join(" ")+'" class="base-line"/>';
  rs.forEach(r=>{const x=Number(r.trusted_CV),y=Number(r.P),sel=String(r.bond_code)===String(fixedCode);svg+='<g class="bond-point" data-code="'+esc(r.bond_code)+'"><circle cx="'+sx(x).toFixed(1)+'" cy="'+sy(y).toFixed(1)+'" r="'+(sel?6:4)+'" class="'+(sel?"point selected":"point")+'"><title>'+esc(nameOf(r.bond_name))+'｜'+n(r.P)+'｜参考 '+n(r.discovery_reference)+'</title></circle><text x="'+(sx(x)+6).toFixed(1)+'" y="'+(sy(y)-5).toFixed(1)+'" class="point-label">'+esc(nameOf(r.bond_name))+'</text></g>';});
  svg+='</svg>';$("#mapChart").innerHTML=svg;
  $("#mapChart").querySelectorAll(".bond-point").forEach(g=>{const code=g.dataset.code,r=(mapData.rows||[]).find(x=>String(x.bond_code)===String(code));g.onmouseenter=()=>selected(r);g.onmouseleave=()=>selected((mapData.rows||[]).find(x=>String(x.bond_code)===String(fixedCode))||null);g.onclick=()=>{fixedCode=code;selected(r);draw();};});
  const sorted=rs.slice().sort((a,b)=>Number(a.diff_to_reference)-Number(b.diff_to_reference));
  $("#mapTableBody").innerHTML=sorted.map((r,i)=>{const diff=Number(r.diff_to_reference);return '<tr data-code="'+esc(r.bond_code)+'"><td>'+(i+1)+'</td><td>'+esc(r.bond_code)+'</td><td><b>'+esc(nameOf(r.bond_name))+'</b></td><td>'+n(r.P)+'</td><td>'+n(r.trusted_CV)+'</td><td>'+n(r.discovery_reference)+'</td><td class="'+(diff<0?"diff-low":"diff-high")+'">'+n(diff)+'</td></tr>';}).join("");
  $("#mapTableBody").querySelectorAll("tr[data-code]").forEach(tr=>{tr.onclick=()=>{const r=(mapData.rows||[]).find(x=>String(x.bond_code)===String(tr.dataset.code));fixedCode=tr.dataset.code;selected(r);draw();};});
}
async function load(){
  try{
    const r=await fetch("/api/market-map/view"); if(!r.ok)throw new Error(await r.text());
    mapData=await r.json();
    $("#mapMeta").innerHTML='<b>市场截面 '+esc(mapData.market_cutoff||"—")+'</b>　正式藏宝图';
    draw();
  }catch(e){$("#mapMeta").textContent="藏宝图读取失败："+e.message;$("#mapChart").innerHTML='<div class="map-empty">暂时无法读取藏宝图。</div>';}
}
["#mapSearch","#priceMin","#priceMax","#cvMin","#cvMax"].forEach(s=>$(s).addEventListener("input",draw));
$("#mapReset").onclick=()=>{$("#mapSearch").value="";$("#priceMin").value="80";$("#priceMax").value="160";$("#cvMin").value="20";$("#cvMax").value="200";fixedCode=null;selected(null);draw();};
load();
