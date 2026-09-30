"use strict";
let recoveryIntent=null,recoveryPlan=null,recoveryBusy=false,recoveryContext=null;
function eligibleStrategies(){return state.strategies.filter(s=>s.status!=="archived"&&(s.mode==="demo"||s.owner===`${s.mode}:${state.owner}`));}
async function openRecovery(ids=null,start=false,after=null){
 if(recoveryBusy)return;
 if(ids===null&&!state.owner&&state.strategies.some(s=>s.mode!=="demo"&&s.status!=="archived")){
  recoveryIntent={ids:null,start,after};renderConnection();$("login-error").textContent="請先登入選帳，再返回全部恢復監控。";
  if(!$("login-dialog").open)$("login-dialog").showModal();return;
 }
 const targets=ids===null?eligibleStrategies():state.strategies.filter(s=>ids.includes(s.id));
 if(!targets.length){toast("目前帳戶沒有可恢復的策略");return;}
 if(start&&targets.some(s=>s.mode==="live"&&s.status!=="active")&&!state.live_available){
  recoveryIntent={ids:targets.map(s=>s.id),start,after};openLiveSettings(targets.find(s=>s.mode==="live"));return;
 }
 recoveryContext={ids:targets.map(s=>s.id),start,after};
 $("recovery-title").textContent=start?"查核、配對並恢復監控":"委託配對與對帳";
 if(!$("recovery-dialog").open)$("recovery-dialog").showModal();
 await loadRecovery();
}
async function loadRecovery(){
 if(recoveryBusy||!recoveryContext)return;
 recoveryBusy=true;recoveryPlan=null;$("recovery-confirm").disabled=true;
 $("recovery-error").textContent="";$("recovery-results").replaceChildren();
 $("recovery-body").replaceChildren(el("p","正在讀取券商委託、成交與庫存，整理可配對項目…"));
 $("recovery-ack").checked=false;
 try{
  recoveryPlan=await command("recovery_preview",{strategy_ids:recoveryContext.ids,report_timezone:$("recovery-timezone").value||null});
  const body=$("recovery-body");body.replaceChildren();
  for(const s of recoveryPlan.strategies){
   const card=el("section",undefined,"recovery-card");
   card.append(el("h3",`${s.name} · ${modeNames[s.mode]}`),el("p",`本地持股 ${s.local_quantity}；券商帳戶持股 ${s.broker_quantity??"不適用"}。${s.status==="active"?"已在監控中，保持原狀。":""}`));
   if(!s.orders.length)card.append(el("p","沒有待配對委託；確認時會核對帳本並恢復可啟動策略。","help"));
   for(const o of s.orders){
    const group=el("div",undefined,"recovery-order"),label=el("label",`${o.side==="buy"?"買進":"賣出"} ${o.quantity} 股 × ${o.price}；本地建立 ${timeLabel(o.created_at)}`);
    const select=el("select");select.dataset.recoveryOrder=o.id;select.setAttribute("aria-label",`配對 ${s.name} 委託`);
    const none=el("option","保留待處理（尚未找到對應券商單）");none.value="";select.append(none);
    for(const candidate of o.options){const opt=el("option",`${candidate.org} · ${candidate.status} · 委託 ${candidate.quantity} / 成交 ${candidate.filled||0} · ${candidate.time||"來源時間未提供"}`);opt.value=candidate.key;select.append(opt);}
    select.value=o.choice;
    const info=el("p",undefined,"help");
    const describe=()=>{const candidate=o.options.find(x=>x.key===select.value);info.textContent=candidate?`${o.basis==="identity"&&select.value===o.choice?"已由確定識別自動帶入":"建議／手動配對：請核對券商單號與時間"}。券商原因：${candidate.message||"未提供"}。${candidate.error||candidate.warning||"可於最後確認後套用"}`:"未找到可確定的委託，保持未知；可查券商 App 或重新查核。";info.className=candidate?.error?"error":"help";};
    select.addEventListener("change",describe);describe();label.append(select);group.append(label,info);card.append(group);
   }
   body.append(card);
  }
  $("recovery-confirm").textContent=recoveryContext.start?"確認配對與庫存，恢復可啟動策略":"確認配對與庫存";
 }catch(e){$("recovery-body").replaceChildren();$("recovery-error").textContent=e.message+"；未更改配對或帳本，可稍後重新查核。";}
 finally{recoveryBusy=false;$("recovery-confirm").disabled=!recoveryPlan||!$("recovery-ack").checked;}
}
$("recovery-refresh").addEventListener("click",()=>safe(loadRecovery,"recovery-error"));
$("recovery-timezone").addEventListener("change",()=>safe(loadRecovery,"recovery-error"));
$("recovery-ack").addEventListener("change",()=>{$("recovery-confirm").disabled=recoveryBusy||!recoveryPlan||!$("recovery-ack").checked;});
$("recovery-confirm").addEventListener("click",async()=>{
 if(recoveryBusy||!recoveryPlan||!$("recovery-ack").checked)return;
 recoveryBusy=true;$("recovery-confirm").disabled=true;$("recovery-error").textContent="";
 const selections={};document.querySelectorAll("[data-recovery-order]").forEach(s=>selections[s.dataset.recoveryOrder]=s.value);
 try{
  const result=await command("recovery_confirm",{version:recoveryPlan.version,selections,confirm:true,start:recoveryContext.start});
  recoveryPlan=null;await refresh();
  $("recovery-results").replaceChildren(table(["策略","結果"],result.results.map(r=>[r.name,r.message])));
  if(result.results.length&&result.results.every(r=>r.status!=="blocked")&&(recoveryContext.after||recoveryContext.start&&result.results.every(r=>r.status==="active"))){
   const {after:next,start}=recoveryContext;$("recovery-dialog").close();recoveryContext=null;
   toast(`${start?"已恢復監控":"已完成對帳"} ${result.results.length} 組；詳情可查看策略事件與成交紀錄`);if(next)await next();
  }
  else toast(`完成 ${result.results.filter(r=>r.status!=="blocked").length} 組；待處理 ${result.results.filter(r=>r.status==="blocked").length} 組`);
 }catch(e){recoveryPlan=null;$("recovery-error").textContent=e.message+"；請按重新查核。";}
 finally{recoveryBusy=false;}
});
const oldReconcile=reconcile;
reconcile=async function(s,resume=false){
 if(s.mode!=="live")return oldReconcile(s,resume);
 const retry=resume&&startIntent?.retry_order_id;
 await openRecovery([s.id],resume&&!retry,retry?continueStartFlow:null);
 if(resume&&!retry)cancelStartFlow();
};
async function offerResumeAfterLogin(){
 if(startIntent||recoveryIntent||$("recovery-dialog").open)return;
 const paused=eligibleStrategies().filter(s=>s.mode!=="demo"&&s.status==="paused");
 if(!paused.length)return;
 let resume=false;
 actionDialog("登入完成：是否恢復全部監控？",`目前帳戶有 ${paused.length} 組暫停策略。先查核委託、成交與庫存，最後由你確認；不會因登入就直接下單。`,[
  {text:"查核並恢復全部",cls:"primary",fn:async()=>{resume=true;}},
  {text:"暫不恢復",fn:async()=>{}},
 ],async()=>{if(resume)await openRecovery(paused.map(s=>s.id),true);});
}
$("monitor-all-start").addEventListener("click",()=>safe(()=>openRecovery(null,true)));
$("monitor-all-stop").addEventListener("click",()=>{
 let result;
 actionDialog("全部停止監控","停止所有未封存策略的新單。請選擇保留在途委託，或嘗試撤銷目前帳戶的本機委託；未確認的撤單仍需查核。",[
  {text:"全部停止，保留委託",fn:async()=>{result=await command("bulk_stop",{cancel:false});}},
  {text:"全部停止，嘗試撤單",cls:"danger",fn:async()=>{result=await command("bulk_stop",{cancel:true});}},
 ],async()=>{const body=el("div");body.append(table(["策略","結果"],result.results.map(r=>[r.name,r.message+(r.orders?.length?"；"+r.orders.map(o=>o.result).join("；"):"")])));actionDialog("全部停止結果",body,[{text:"關閉",fn:async()=>{}}]);});
});
