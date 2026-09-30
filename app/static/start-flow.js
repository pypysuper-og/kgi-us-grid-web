"use strict";
// Navigation retains the selected strategy, never an implicit order/start permission.
let startIntent=null;
function cancelStartFlow(){startIntent=null;}
function beginStartFlow(s){startIntent={id:s.id,revision:s.revision};safe(continueStartFlow);}
function beginRetryFlow(s,o){startIntent={id:s.id,revision:s.revision,retry_order_id:o.id};safe(continueStartFlow);}
function openLiveSettings(s=null){
 $("live-summary").textContent=(s?`啟動「${s.params.name}」：完成此步後返回對帳／啟動確認。 `:"")+(state?.selected?`${state.selected.broker_id} · ${state.selected.account}`:"尚未選帳")+" · "+(state?.live_reason||"");
 const f=$("live-form");f.elements.confirm.checked=false;
 const limits=state?.live_limits,used=limits?.orders_used??0,remaining=Math.max(0,(limits?.orders_limit??0)-used);
 $("live-order-budget").textContent=`本次程式執行已用 ${used} 筆；${state?.live_available?`目前剩餘 ${remaining} 筆`:"目前實單未啟用"}。重新啟用時，剩餘額度會設為下方填寫的筆數。`;
 if(s&&!f.elements.daily_buy_limit.value)f.elements.daily_buy_limit.value=state.live_limits?.daily_buy_limit||s.params.daily_buy_limit;
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
 actionDialog(intent.retry_order_id?"確認拒單後重新送單":"啟動策略",`「${s.params.name}」 · ${modeNames[s.mode]} · ${s.params.symbol}\n後續新單結算：${settlementLabel(s.params.currency)}。${intent.retry_order_id?`原拒單結算：${settlementLabel(state.orders.find(o=>o.id===intent.retry_order_id)?.settlement_currency)}。`:""}\n${intent.retry_order_id?"原單必須已確認拒單且零成交；這會建立新單，並重新檢查行情、價格與額度。":"設定與對帳已就緒。"}每筆 ${s.params.quantity} 股，最多 ${s.params.max_inventory} 股，每日買入上限 USD ${number(s.params.daily_buy_limit)}。\n行情過期時警示並等待新行情，不需重新啟動。`,[
  {text:intent.retry_order_id?"確認建立新委託":"確認啟動",cls:"primary",fn:async()=>{if(intent.retry_order_id){await command("retry_order",{order_id:intent.retry_order_id,revision:s.revision,confirm:true});}else{await command("start",{strategy_id:s.id,revision:s.revision,confirm_live:s.mode==="live"});}cancelStartFlow();}},
  {text:"暫不啟動",fn:async()=>cancelStartFlow()},
 ]);
}
for(const id of ["login-dialog","live-dialog","action-dialog"]){
 $(id).addEventListener("cancel",()=>{cancelStartFlow();recoveryIntent=null;});
 document.querySelectorAll(`[data-close="${id}"]`).forEach(b=>b.addEventListener("click",()=>{cancelStartFlow();recoveryIntent=null;}));
}
