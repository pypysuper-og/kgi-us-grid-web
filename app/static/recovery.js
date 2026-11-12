"use strict";
let recoveryIntent=null,recoveryPlan=null,recoveryBusy=false,recoveryContext=null,recoveryApplying=false,recoveryResultBusy=false,recoveryOutcome=null;
function eligibleStrategies(){return state.strategies.filter(s=>s.status!=="archived"&&(s.mode==="demo"||s.owner===`${s.mode}:${state.owner}`));}
async function openRecovery(ids=null,start=false,after=null){
 if(recoveryBusy)return;
 if(ids===null&&!state.owner&&state.strategies.some(s=>s.mode!=="demo"&&s.status!=="archived")){
  recoveryIntent={ids:null,start,after};renderConnection();$("login-error").textContent="請先登入選帳，再返回全部恢復監控。";
  if(!$("login-dialog").open)$("login-dialog").showModal();return;
 }
 const targets=ids===null?eligibleStrategies():state.strategies.filter(s=>ids.includes(s.id));
 if(!targets.length){toast("目前帳戶沒有可恢復的策略");return;}
 const context={ids:targets.map(s=>s.id),start,after,prepared:false};
 recoveryContext=context;recoveryPlan=null;recoveryBusy=true;
 $("recovery-title").textContent=start?"查核、配對並恢復監控":"委託配對與對帳";
 $("recovery-error").textContent="";$("recovery-ack").checked=false;$("recovery-confirm").disabled=true;
 $("recovery-body").setAttribute("aria-busy","true");
 $("recovery-body").replaceChildren(el("p",`正在準備 ${targets.length} 組策略的查核；完成後接續實單確認或委託與部位核對，無須重複啟動。`));
 if(!$("recovery-dialog").open)$("recovery-dialog").showModal();
 try{
  if(state.recovery_status!==undefined)await command("recovery_takeover",{strategy_ids:context.ids});
  if(recoveryContext!==context||!$("recovery-dialog").open)return;
  context.prepared=true;
  if(start&&targets.some(s=>s.mode==="live"&&s.status!=="active")&&!state.live_available){
   recoveryIntent={ids:context.ids,start,after};recoveryContext=null;$("recovery-dialog").close();
   openLiveSettings(null,targets);return;
  }
 }catch(e){
  if(recoveryContext===context&&$("recovery-dialog").open){
   $("recovery-body").replaceChildren();
   $("recovery-error").textContent=e.message+"；尚未套用配對或啟動新策略，可按重新查核。";
  }
  return;
 }finally{recoveryBusy=false;$("recovery-body").setAttribute("aria-busy","false");}
 if(recoveryContext!==context||!$("recovery-dialog").open)return;
 await loadRecovery();
}
async function loadRecovery(){
 if(recoveryBusy||!recoveryContext)return;
 const context=recoveryContext;
 if(!context.prepared)return openRecovery(context.ids,context.start,context.after);
 recoveryBusy=true;recoveryPlan=null;$("recovery-confirm").disabled=true;
 $("recovery-body").setAttribute("aria-busy","true");
 $("recovery-error").textContent="";
 $("recovery-body").replaceChildren(el("p","正在讀取券商委託、成交與庫存，整理可配對項目…"));
 $("recovery-ack").checked=false;
 try{
  const plan=await command("recovery_preview",{strategy_ids:context.ids,report_timezone:$("recovery-timezone").value||null});
  if(recoveryContext!==context||!$("recovery-dialog").open)return;
  recoveryPlan=plan;
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
 }catch(e){if(recoveryContext===context&&$("recovery-dialog").open){$("recovery-body").replaceChildren();$("recovery-error").textContent=e.message+"；未更改配對或帳本，可稍後重新查核。";}}
 finally{recoveryBusy=false;$("recovery-body").setAttribute("aria-busy","false");$("recovery-confirm").disabled=!recoveryPlan||!$("recovery-ack").checked;}
}
$("recovery-dialog").addEventListener("close",()=>{
 if($("recovery-dialog").open)return;
 recoveryContext=null;recoveryPlan=null;$("recovery-ack").checked=false;$("recovery-confirm").disabled=true;
});
$("recovery-refresh").addEventListener("click",()=>safe(loadRecovery,"recovery-error"));
$("recovery-timezone").addEventListener("change",()=>safe(loadRecovery,"recovery-error"));
$("recovery-ack").addEventListener("change",()=>{$("recovery-confirm").disabled=recoveryBusy||!recoveryPlan||!$("recovery-ack").checked;});
async function recoveryResultAction(fn){
 if(recoveryResultBusy)return;recoveryResultBusy=true;$("recovery-result-error").textContent="";
 const controls=[...$("recovery-result-dialog").querySelectorAll("button")],disabled=controls.map(b=>b.disabled);
 controls.forEach(b=>b.disabled=true);
 try{await fn();}catch(e){$("recovery-result-error").textContent=e.message;}
 finally{recoveryResultBusy=false;controls.forEach((b,i)=>b.disabled=disabled[i]);}
}
async function recoveryResultTargets(ids,context){
 await refresh();
 if(state.owner!==context.owner||state.generation!==context.generation)throw Error("帳戶會話已變更，請返回工作台重新查核。");
 const targets=ids.map(id=>state.strategies.find(s=>s.id===id));
 if(targets.some(s=>!s||s.status==="archived"||s.mode!=="demo"&&s.owner!==`${s.mode}:${state.owner}`))throw Error("策略或所屬帳戶已變更，請返回工作台重新查核。");
 if(targets.some(s=>s.status==="active"))throw Error("其中策略目前已在監控，請返回工作台查看最新狀態。");
 return targets;
}
function showRecoveryResult(result,context,updateError=""){
 const rows=result.results,blocked=rows.filter(r=>r.status==="blocked"),active=rows.filter(r=>r.status==="active").length,reconciled=rows.filter(r=>r.status==="reconciled").length;
 recoveryOutcome={ids:blocked.map(r=>r.id),context};
 $("recovery-result-title").textContent=`${context.start?"恢復監控結果":"查核結果"}：${blocked.length?(active||reconciled?"部分完成":context.start?"尚未啟動":"仍需處理"):"已完成"}`;
 $("recovery-result-summary").textContent=`本次結果 · 監控中 ${active} 組 · 已對帳 ${reconciled} 組 · 需處理 ${blocked.length} 組`;
 $("recovery-result-error").textContent=updateError;
 const body=$("recovery-result-body");body.replaceChildren();
 for(const row of rows){
  const needsAction=row.status==="blocked",card=el("section",undefined,`recovery-result-card${needsAction?" needs-action":""}`),head=el("div",undefined,"recovery-result-head");card.dataset.strategyId=row.id;
  head.append(el("h3",row.name),el("span",needsAction?"需處理":row.status==="active"?"監控中":"已對帳",`pill${needsAction?" warn":""}`));
  card.append(head,el("p",row.message));
  if(needsAction){
   const settings=/停止日期/.test(row.message),actions=el("div",undefined,"recovery-result-actions");
   card.append(el("p",settings?"請核對並調整停止日期；儲存後重新查核與確認啟動。":"請核對阻擋原因，重新查核委託、成交與庫存後再確認。","help"));
   actions.append(button(settings?"調整策略設定":"重新查核此策略",()=>recoveryResultAction(async()=>{
    const [s]=await recoveryResultTargets([row.id],context);
    if(settings){selected=s.id;render();await openStrategy(s);if($("strategy-dialog").open){const field=fieldInput("end_date");field.scrollIntoView({block:"center"});field.focus({preventScroll:true});}}
    else{$("recovery-result-dialog").close();await openRecovery([s.id],context.start);}
   })));
   if(/分配後持股超過券商持股/.test(row.message))actions.append(button("交接既有部位",()=>recoveryResultAction(async()=>{const [s]=await recoveryResultTargets([row.id],context);openInventoryHandoff(null,s);})));card.append(actions);
  }
  body.append(card);
 }
 $("recovery-result-recheck").disabled=!blocked.length;
 $("recovery-result-recheck").textContent=`重新查核待處理${blocked.length?`（${blocked.length} 組）`:"策略"}`;
 if(!$("recovery-result-dialog").open)$("recovery-result-dialog").showModal();
 $("recovery-result-title").focus({preventScroll:true});
}
$("recovery-result-recheck").addEventListener("click",()=>recoveryResultAction(async()=>{
 const outcome=recoveryOutcome;if(!outcome?.ids.length)return;
 const targets=await recoveryResultTargets(outcome.ids,outcome.context);
 $("recovery-result-dialog").close();await openRecovery(targets.map(s=>s.id),outcome.context.start);
}));
$("recovery-result-dialog").addEventListener("close",()=>{recoveryOutcome=null;});
$("recovery-result-dialog").addEventListener("cancel",e=>{if(recoveryResultBusy)e.preventDefault();});
$("recovery-dialog").addEventListener("cancel",e=>{if(recoveryApplying)e.preventDefault();});
$("recovery-confirm").addEventListener("click",async()=>{
 if(recoveryBusy||!recoveryPlan||!$("recovery-ack").checked)return;
 recoveryBusy=true;recoveryApplying=true;$("recovery-confirm").disabled=true;$("recovery-error").textContent="";
 const context={...recoveryContext,owner:state.owner,generation:state.generation},close=document.querySelector('#recovery-dialog [data-close="recovery-dialog"]');close.disabled=true;
 const selections={};document.querySelectorAll("[data-recovery-order]").forEach(s=>selections[s.dataset.recoveryOrder]=s.value);
 try{
  const result=await command("recovery_confirm",{version:recoveryPlan.version,selections,confirm:true,start:context.start});
  recoveryPlan=null;$("recovery-ack").checked=false;
  let updateError="";try{await refresh();}catch(e){updateError=`已收到本次查核結果；畫面更新失敗：${e.message}。請稍後返回工作台確認。`;}
  $("recovery-dialog").close();recoveryContext=null;
  const complete=result.results.length&&result.results.every(r=>r.status!=="blocked")&&(context.after||context.start&&result.results.every(r=>r.status==="active"));
  if(!complete||updateError){
   cancelStartFlow();recoveryIntent=null;showRecoveryResult(result,context,updateError);
  }
  else{toast(`${context.start?"已恢復監控":"已完成對帳"} ${result.results.length} 組；詳情可查看策略事件與成交紀錄`);if(context.after)await safe(context.after);}
 }catch(e){recoveryPlan=null;$("recovery-error").textContent=e.message+"；請按重新查核。";}
 finally{recoveryBusy=false;recoveryApplying=false;close.disabled=false;}
});
const oldReconcile=reconcile;
reconcile=async function(s,resume=false){
 if(s.mode!=="live")return oldReconcile(s,resume);
 const retry=resume&&startIntent?.retry_order_id;
 await openRecovery([s.id],resume&&!retry,retry?continueStartFlow:null);
 if(resume&&!retry)cancelStartFlow();
};
async function offerResumeAfterLogin(){
 if(startIntent||recoveryIntent||$("recovery-dialog").open||$("recovery-result-dialog").open)return;
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
