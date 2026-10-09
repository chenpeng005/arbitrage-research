(function(){
  "use strict";

  function byId(id){return document.getElementById(id);}
  function text(x){return String(x==null?"":x);}
  function htmlEscape(x){
    return text(x).replace(/[&<>"']/g,function(m){
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m];
    });
  }

  function addStyles(){
    if(byId("notificationHistoryStyles"))return;
    const style=document.createElement("style");
    style.id="notificationHistoryStyles";
    style.textContent=`
      .notification-history-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 12px;padding:10px 12px;background:#fff;border:1px solid var(--line);border-radius:12px}
      .notification-history-bar button,.notification-history-bar select{border:1px solid var(--line);border-radius:9px;background:#fff;color:var(--text);padding:8px 10px;font:inherit}
      .notification-history-bar button{cursor:pointer;font-weight:700}
      .notification-history-bar button.active{background:#eef3ff;border-color:#aebde3;color:#294a95}
      .notification-history-bar select{min-width:230px}
      .notification-history-meta{font-size:12px;color:var(--muted);margin-left:auto}
      @media(max-width:720px){.notification-history-meta{width:100%;margin-left:0}.notification-history-bar select{min-width:0;flex:1}}
    `;
    document.head.appendChild(style);
  }

  function setCurrentCopy(){
    const title=document.querySelector("#notifications .attention-page-head h2");
    const note=document.querySelector("#notifications .attention-page-head .section-note");
    const otherNote=document.querySelector("#notifications .compact-increment-block .attention-block-head p");
    if(title)title.textContent="今天哪些变化值得看";
    if(note)note.textContent="重点变化稍微展开；其他增量只做概略提示。感兴趣时再进入具体转债查看完整研究。";
    if(otherNote)otherNote.textContent="最近48小时内值得知道、但暂时不需要展开阅读的变化。";
  }

  function renderSnapshot(snapshot){
    const feed=snapshot.feed||{};
    const items=feed.items||[];
    const otherItems=feed.other_items||[];
    const total=items.length+otherItems.length;
    const date=snapshot.snapshot_date||"—";
    const badge=byId("notificationBadge");
    const box=byId("notificationFeed");
    const other=byId("otherIncrementFeed");
    const title=document.querySelector("#notifications .attention-page-head h2");
    const note=document.querySelector("#notifications .attention-page-head .section-note");
    const otherNote=document.querySelector("#notifications .compact-increment-block .attention-block-head p");

    if(title)title.textContent=date+" 的提醒";
    if(note)note.textContent="这是当日正式更新结束后冻结的提醒快照；历史内容不会按今天的新规则重新计算。";
    if(otherNote)otherNote.textContent="当日快照中值得知道、但不需要展开阅读的其他增量。";
    if(badge){
      badge.textContent="历史 "+date+" · "+total+" 条";
      badge.className="badge "+(total?"idle":"pass");
    }
    if(byId("focusCount"))byId("focusCount").textContent=items.length;
    if(byId("otherCount"))byId("otherCount").textContent=otherItems.length;
    if(byId("attentionSummary")){
      byId("attentionSummary").innerHTML=
        '<b>'+htmlEscape(date)+'：重点 '+htmlEscape(items.length)+' 条</b>'
        +'　·　其他增量 '+htmlEscape(otherItems.length)+' 条'
        +(snapshot.market_cutoff?'　·　市场截止 '+htmlEscape(snapshot.market_cutoff):'')
        +(snapshot.update_status&&snapshot.update_status!=="PASS"?'　·　'+htmlEscape(snapshot.update_status):'');
    }
    if(box){
      box.innerHTML=items.length
        ?items.map(function(x){return window.renderReminderItem(x);}).join("")
        :'<div class="muted attention-empty">当日没有需要展开阅读的重点变化。</div>';
    }
    if(other){
      other.innerHTML=otherItems.length
        ?otherItems.map(function(x){return window.renderOtherIncrementItem(x);}).join("")
        :'<div class="muted attention-empty">当日没有其他增量。</div>';
    }
  }

  async function loadSnapshot(date){
    const meta=byId("notificationHistoryMeta");
    if(meta)meta.textContent="正在读取 "+date+"…";
    try{
      const r=await fetch("/static/notification-history/"+encodeURIComponent(date)+".json",{cache:"no-store"});
      if(!r.ok)throw new Error("HTTP "+r.status);
      const snapshot=await r.json();
      renderSnapshot(snapshot);
      byId("currentNotificationView")?.classList.remove("active");
      if(meta)meta.textContent="已冻结于 "+text(snapshot.captured_at||"").replace("T"," ").slice(0,19);
    }catch(e){
      if(meta)meta.textContent="历史提醒读取失败："+e.message;
    }
  }

  async function loadIndex(){
    const select=byId("notificationHistoryDate");
    const meta=byId("notificationHistoryMeta");
    if(!select)return;
    try{
      const r=await fetch("/static/notification-history/index.json",{cache:"no-store"});
      if(!r.ok){
        if(r.status===404){if(meta)meta.textContent="暂无历史快照；下一次正式更新后开始记录。";return;}
        throw new Error("HTTP "+r.status);
      }
      const index=await r.json();
      const items=index.items||[];
      items.forEach(function(item){
        const opt=document.createElement("option");
        opt.value=item.snapshot_date;
        opt.textContent=item.snapshot_date+"　重点 "+item.focus_count+" · 其他 "+item.other_count;
        select.appendChild(opt);
      });
      if(meta)meta.textContent=items.length?"已保存 "+items.length+" 个正式更新日":"暂无历史快照；下一次正式更新后开始记录。";
    }catch(e){
      if(meta)meta.textContent="历史索引读取失败";
    }
  }

  function setup(){
    if(window.location.pathname.replace(/\/+$/,"")!=="/notifications")return;
    const summary=byId("attentionSummary");
    if(!summary||byId("notificationHistoryBar"))return;
    addStyles();
    const bar=document.createElement("div");
    bar.id="notificationHistoryBar";
    bar.className="notification-history-bar";
    bar.innerHTML=
      '<button id="currentNotificationView" class="active" type="button">当前提醒</button>'
      +'<select id="notificationHistoryDate"><option value="">历史提醒：选择日期</option></select>'
      +'<span id="notificationHistoryMeta" class="notification-history-meta">正在读取历史索引。</span>';
    summary.parentNode.insertBefore(bar,summary);

    byId("currentNotificationView").addEventListener("click",async function(){
      byId("notificationHistoryDate").value="";
      this.classList.add("active");
      setCurrentCopy();
      if(typeof window.loadNotificationFeed==="function")await window.loadNotificationFeed();
      const indexMeta=byId("notificationHistoryMeta");
      if(indexMeta)indexMeta.textContent="当前提醒为实时注意力页；历史按正式更新日冻结。";
    });
    byId("notificationHistoryDate").addEventListener("change",function(){
      if(this.value)loadSnapshot(this.value);
    });
    loadIndex();
  }

  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",setup);
  else setup();
})();
