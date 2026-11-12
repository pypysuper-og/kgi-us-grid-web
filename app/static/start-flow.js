"use strict";
// Navigation retains the selected strategy, never an implicit order/start permission.
let startIntent=null;
function cancelStartFlow(){startIntent=null;}
function beginStartFlow(s){startIntent={id:s.id,revision:s.revision};safe(async()=>{await takeOverRecovery(s);await continueStartFlow();});}
function beginRetryFlow(s,o){startIntent={id:s.id,revision:s.revision,retry_order_id:o.id};safe(async()=>{await takeOverRecovery(s);await continueStartFlow();});}
function openLiveSettings(s=null,targets=null){
 const liveTargets=targets?.filter(row=>row.mode==="live")||[];
 const context=targets?`本次查核 ${targets.length} 組策略：${targets.map(row=>row.params.name).join("、")}。其中 ${liveTargets.length} 組正式策略共用本次實單設定；完成後返回整批對帳／啟動確認。 `:s?`啟動「${s.params.name}」：完成實單確認後返回對帳／啟動流程。 `:"此帳戶的正式策略共用本次實單設定。 ";
 const status=state?.live_available?"實單已啟用":state?.selected?"實單尚未啟用，請核對帳戶並確認":"請先登入並選帳";
 $("live-summary").textContent=context+(state?.selected?`${state.selected.broker_id} · ${state.selected.account}`:"尚未選帳")+" · "+status;
 const f=$("live-form");f.elements.confirm.checked=false;
 const limits=state?.live_limits,used=limits?.orders_used??0,remaining=limits?.orders_limit==null?null:Math.max(0,limits.orders_limit-used);
 $("live-order-budget").textContent=`本次程式執行已用 ${used} 筆；${state?.live_available?(remaining===null?"未另設送單筆數限制":`目前剩餘 ${remaining} 筆`):"目前實單未啟用"}。填入筆數會設定接下來的共用額度，留空則不另設限制。`;
 if(state?.live_preferences!==undefined){
  const saved=state.live_preferences;
  f.elements.daily_buy_limit.value=saved.daily_buy_limit??"";
  f.elements.max_orders.value=saved.max_orders??"";
  f.elements.report_timezone.value=saved.report_timezone??"";
 }else{f.elements.daily_buy_limit.value=limits?.daily_buy_limit??"";f.elements.max_orders.value="";f.elements.report_timezone.value="";}
 $("live-advanced").open=!!(f.elements.daily_buy_limit.value||f.elements.max_orders.value);
 $("live-report-options").open=!!f.elements.report_timezone.value;
 $("live-preference-note").textContent="進階選項會按帳戶記住；每次登入後仍需重新確認實單。已保存的限制會展開顯示，可清空停用。";
 $("live-error").textContent="";if(!$("live-dialog").open)$("live-dialog").showModal();
}
async function continueStartFlow(){
 if(recoveryIntent){const intent=recoveryIntent;recoveryIntent=null;await openRecovery(intent.ids,intent.start,intent.after);return;}
 const intent=startIntent;if(!intent)return;
 state=await request("/api/state");if(startIntent!==intent)return;
 const s=state.strategies.find(row=>row.id===intent.id);
 if(!s||s.revision!==intent.revision||s.status==="archived"){cancelStartFlow();throw Error("策略已變更，請重新選擇啟動。");}
 selected=s.id;render();
 if(s.status==="active"){cancelStartFlow();return;}
 if(s.mode!=="demo"&&(!state.owner||s.owner!==`${s.mode}:${state.owner}`)){
  $("login-error").textContent="請登入並選擇此策略所屬帳戶；完成後返回原策略啟動流程。";
  renderConnection();if(!$("login-dialog").open)$("login-dialog").showModal();return;
 }
 if(s.mode==="live"&&!state.live_available){openLiveSettings(s);return;}
 const pending=state.orders.filter(o=>o.strategy_id===s.id&&(!terminal.has(o.status)||o.unknown));
 if(pending.some(o=>o.unknown)){await reconcile(s,true);return;}
 if(!s.reconciled){await reconcile(s,true);return;}
 actionDialog(intent.retry_order_id?"確認拒單後重新送單":"啟動策略",`「${s.params.name}」 · ${modeNames[s.mode]} · ${s.params.symbol}\n後續新單結算：${settlementLabel(s.params.currency)}。${intent.retry_order_id?`原拒單結算：${settlementLabel(state.orders.find(o=>o.id===intent.retry_order_id)?.settlement_currency)}。`:""}\n${intent.retry_order_id?"原單必須已確認拒單且零成交；這會建立新單，並重新檢查行情、價格與額度。":"設定與對帳已就緒。"}每筆 ${s.params.quantity} 股，持股上限：${inventoryLimitLabel(s.params.max_inventory)}；策略每日買入：${limitLabel(s.params.daily_buy_limit)}。\n暫無新行情時會保持監控，收到有效新行情後再判斷，不需重新啟動。`,[
  {text:intent.retry_order_id?"確認建立新委託":"確認啟動",cls:"primary",fn:async()=>{if(intent.retry_order_id){await command("retry_order",{order_id:intent.retry_order_id,revision:s.revision,confirm:true});}else{await command("start",{strategy_id:s.id,revision:s.revision,confirm_live:s.mode==="live"});}cancelStartFlow();}},
  {text:"暫不啟動",fn:async()=>cancelStartFlow()},
 ]);
}
for(const id of ["login-dialog","live-dialog","action-dialog"]){
 $(id).addEventListener("cancel",()=>{cancelStartFlow();recoveryIntent=null;});
 document.querySelectorAll(`[data-close="${id}"]`).forEach(b=>b.addEventListener("click",()=>{cancelStartFlow();recoveryIntent=null;}));
}
