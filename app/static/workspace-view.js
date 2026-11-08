"use strict";
// Read-only presentation. These models never submit orders or change strategy parameters.
const WorkspaceView = (() => {
 const terminal = new Set(["filled", "canceled", "rejected", "expired"]);
 const cents = value => Math.round(Number(value) * 100);
 const money = value => Number(value).toLocaleString("zh-TW", {minimumFractionDigits:2, maximumFractionDigits:2});
 function gridModel(params, anchor, inventory, priceGap=null) {
  const low=cents(params.lower_price),high=cents(params.upper_price),base=cents(anchor);
  const gap=cents(priceGap??(params.gap_unit==="percent"?Number(params.start_price)*Number(params.gap)/100:params.gap));
  if(![low,high,base,gap].every(Number.isSafeInteger)||gap<=0||high<=low)return null;
  const quantity=Number(params.quantity),held=Number(inventory),minimum=Number(params.min_inventory),maximum=params.max_inventory==null?Infinity:Number(params.max_inventory);
  const next={};
  for(const [side,sign] of [["buy",-1],["sell",1]]) {
   const price=base+sign*gap;
   let reason="";
   if(params.direction!=="both"&&params.direction!==side)reason=side==="buy"?"未啟用買進方向":"未啟用賣出方向";
   else if(price<low||price>high)reason="下一格超出價格範圍";
   else if(side==="buy"&&held+quantity>maximum)reason="剩餘持股額度不足每筆股數";
   else if(side==="sell"&&held-quantity<minimum)reason=held<quantity?"目前沒有足夠可賣股數":"已達最少保留股數";
   next[side]={price:price/100,allowed:!reason,reason};
  }
  const first=Math.ceil((low-base)/gap),last=Math.floor((high-base)/gap),count=Math.max(0,last-first+1);
  const indices=new Set();
  for(let i=0;i<Math.min(count,64);i++)indices.add(first+(count<=64?i:Math.round(i*(count-1)/63)));
  const levels=[...indices].map(i=>(base+i*gap)/100);
  const rows=[];
  for(const [side,sign,capacity] of [["buy",-1,maximum-held],["sell",1,held-minimum]]) {
   if(params.direction!=="both"&&params.direction!==side)continue;
   for(let i=1;i<=Math.min(100,Math.floor(capacity/quantity));i++) {
    const price=base+sign*gap*i;if(price<low||price>high)break;
    rows.push({side,price:price/100,quantity,inventory:held-sign*quantity*i});
   }
  }
  return {low:low/100,high:high/100,anchor:base/100,gap:gap/100,inventory:held,quantity,next,levels,levelCount:count,sampled:count>64,boundaryAligned:(base-low)%gap===0&&(high-base)%gap===0,rows};
 }
 function pendingOrders(s,state) {return (state.orders||[]).filter(o=>o.strategy_id===s.id&&(!terminal.has(o.status)||o.unknown));}
 function displayQuote(s,state) {
  if(s.mode==="demo") {const q=state.quotes?.[s.id];return q?{...q,kind:"demo",label:"離線行情"}:null;}
  if(!state.owner||s.owner!==`${s.mode}:${state.owner}`)return null;
  const entry=state.market?.[s.params.symbol],q=entry?.quote;
  return q?{...q,status:entry.status,label:q.kind==="trade"?"最後成交價":q.kind==="snapshot"?"快照參考價":"參考價（非新成交）"}:null;
 }
 function describeStrategy(s,state,now=Date.now(),backendLost=false) {
  const p=s.params,position=(state.report?.rows||[]).find(r=>r.strategy_id===s.id),grid=gridModel(p,s.anchor,position?.quantity??p.initial_inventory);
  const pending=pendingOrders(s,state),engine=state.quotes?.[s.id];
  const result=(code,title,detail="",tone="neutral")=>({code,title,detail,tone,grid});
  if(backendLost)return result("backend_lost","後端失聯，策略狀態待確認","最後顯示的狀態僅供參考，請確認工作台連線。","warning");
  if(s.status==="archived")return result("archived","策略已封存");
  if(state.fault||state.audit_error)return result("fault","交易服務需處理",state.fault||state.audit_error,"warning");
  if(pending.some(o=>o.unknown))return result("unknown_order","委託結果待查核","保留原單；確認券商結果前不建立新網格單。","warning");
  if(pending.length) {
   const o=pending[0],remaining=Math.max(0,o.quantity-o.filled);
   return result("pending_order",s.status==="active"?"委託尚未結束，等待回報":"已停止新單，仍有在途委託",`${o.side==="buy"?"買進":"賣出"} ${o.quantity} 股，已成交 ${o.filled} 股，剩餘 ${remaining} 股；限價 USD ${money(o.price)}。`,"waiting");
  }
  if(s.status!=="active") {
   const recovery=state.recovery_status?.[s.id];
   if(recovery?.state==="checking")return result("recovering","監控已暫停，正在背景查核",`查核完成前尚未恢復監控。${s.reason?`上次查核：${s.reason}`:""}`,"waiting");
   if(recovery?.state==="retry_wait")return result("recovery_wait","等待下一次自動查核",`${s.reason||recovery.message||""} 第 ${recovery.attempt} 次查核未通過；${recovery.retry_at?`下次 ${new Date(recovery.retry_at).toLocaleString("zh-TW",{hour12:false})} 自動查核；`:""}差異排除後自動恢復。`,"waiting");
   if(recovery?.state==="connection_wait")return result("recovery_connection","等待券商重連後查核",s.reason||"","waiting");
   if(recovery?.state==="manual")return result("manual","已交由您處理，背景自動恢復已停止",s.reason||"請核對委託及庫存。","warning");
   return result(s.reconciled?"paused":"reconcile",s.reconciled?"等待手動啟動":"策略暫停，等待對帳與確認",s.reason||"儲存參數不會自動啟動策略。");
  }
  const today=new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Taipei",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date(now));
  if(p.end_date<today)return result("expired","已達停止日期，等待停止新單","既有委託及持股仍需追蹤。","warning");
  if(s.mode!=="demo") {
   if(!state.owner||s.owner!==`${s.mode}:${state.owner}`)return result("account","請登入此策略所屬帳戶","目前會話不具備此策略的交易條件。","warning");
   if(state.broker_connected===false)return result("disconnected","券商已斷線，等待恢復","重新連線與查核完成後才能恢復新單。","waiting");
   if(s.mode==="live"&&!state.live_available)return result("live_disabled","本次實單尚未啟用","監控狀態不代表已取得實單權限。","warning");
   if(state.market_open==null)return result("session_unknown","等待確認交易時段","請確認後端已更新並重新啟動工作台。","waiting");
   if(state.market_open===false)return result("market_closed","等待美股正常交易時段","非正常盤期間不觸發新單；離線展示不受此限制。","waiting");
  }
  const age=p.quote_max_age*1000,fresh=t=>{const delta=now-Date.parse(t);return Number.isFinite(delta)&&delta>=-2000&&delta<=age;};
  if(!engine||engine.symbol!==p.symbol||engine.generation!==state.generation)return result("quote_wait",s.mode==="demo"?"等待離線行情":"等待策略行情",s.mode==="demo"?`可用「離線行情」輸入 ${p.symbol} 價格，開始演練。`:"等待可用的策略行情；畫面參考價不代表已具備送單條件。","waiting");
  if(!fresh(engine.received_time)||!(engine.source_time?fresh(engine.source_time):engine.source_updated))return result("quote_stale",s.mode==="demo"?"等待下一筆離線行情":"行情過期，等待有效更新",s.mode==="demo"?"離線演練收到新價格時才會評估；監控持續中。":"保留監控，收到有效行情後繼續評估。","waiting");
  if(!s.reconciled)return result("reconcile","等待完成對帳","核對帳本後才能評估新單。","warning");
  if(state.quote_warnings?.[s.id])return result("quote_warning",state.quote_warnings[s.id],"監控持續中。","waiting");
  if(!grid)return result("parameters","網格參數需核對","目前無法建立價格圖。","warning");
  if(p.pause_buys_below_lower&&Number(engine.price)<grid.low)return result("below_lower","行情低於下界，等待回到範圍","已選用暫停新增買單；既有委託不會自動撤銷。","waiting");
  const allowed=Object.entries(grid.next).filter(([,v])=>v.allowed);
  if(!allowed.length)return result("position_limit","目前部位或界線沒有可新增的網格單",`買進：${grid.next.buy.reason}；賣出：${grid.next.sell.reason}。`,"waiting");
  const crossed=allowed.find(([side,v])=>side==="buy"?Number(engine.price)<=v.price:Number(engine.price)>=v.price);
  if(crossed)return result("triggered","已觸及網格價，等待引擎查核結果","尚不代表已送單或成交，仍需確認交易條件與券商結果。","waiting");
  const wording=allowed.map(([side,v])=>`${side==="buy"?"跌至":"漲至"} USD ${money(v.price)} ${side==="buy"?"買進":"賣出"} ${p.quantity} 股`).join("；");
  const blocked=Object.entries(grid.next).filter(([,v])=>!v.allowed).map(([side,v])=>`${side==="buy"?"買進":"賣出"}：${v.reason}`).join("；");
  return result("price_wait",`等待價格：${wording}`,blocked||"每策略一次只建立一筆未結束網格單，全部成交後才移動基準。","ready");
 }
 function setText(node,value) {const text=String(value??"");if(node.textContent!==text)node.textContent=text;}
 function summaryNode(compact=false) {
  const host=document.createElement("section");host.className=compact?"strategy-summary compact":"strategy-summary";
  const title=document.createElement("strong"),detail=document.createElement("p");title.className="strategy-summary-title";detail.className="strategy-summary-detail";host.append(title,detail);return host;
 }
 function updateSummary(host,description) {if(host.dataset.state!==description.code)host.dataset.state=description.code;if(host.dataset.tone!==description.tone)host.dataset.tone=description.tone;setText(host.querySelector(".strategy-summary-title"),description.title);setText(host.querySelector(".strategy-summary-detail"),description.detail);}
 function drawGrid(host,model,quote=null,orders=[]) {
  const fingerprint=JSON.stringify([model,quote?[quote.price,quote.label,quote.status]:null,orders.map(o=>[o.id,o.side,o.price,o.quantity,o.filled,o.status,o.unknown])]);
  if(host._gridFingerprint===fingerprint)return;host._gridFingerprint=fingerprint;
  host.replaceChildren();host.classList.add("grid-visual");
  if(!model){const note=document.createElement("p");note.textContent="完成有效的價格與格線設定後顯示網格圖。";host.append(note);return;}
  const caption=document.createElement("figcaption");caption.textContent=`網格價格圖 · ${model.levelCount} 個參考價位${!model.boundaryAligned?"（依目前基準平移）":""}${model.sampled?"（圖中抽樣顯示格線，參數不變）":""}`;host.append(caption);
  const namespace="http://www.w3.org/2000/svg",svg=document.createElementNS(namespace,"svg");svg.setAttribute("viewBox","0 0 800 230");svg.setAttribute("role","img");svg.setAttribute("aria-label",`價格區間 USD ${money(model.low)} 至 ${money(model.high)}，基準 ${money(model.anchor)}。${quote?`${quote.label} ${money(quote.price)}`:"尚未取得行情"}。格線為規劃參考。`);
  function node(tag,attributes,text){const n=document.createElementNS(namespace,tag);for(const [key,value]of Object.entries(attributes))n.setAttribute(key,String(value));if(text!==undefined)n.textContent=text;svg.append(n);return n;}
  const quoteValue=quote&&Number(quote.price)>0?Number(quote.price):null;
  const values=[model.low,model.high,model.anchor,...orders.map(o=>Number(o.price)),...(quoteValue===null?[]:[quoteValue])],minimum=Math.min(...values),maximum=Math.max(...values),span=Math.max(.01,maximum-minimum),x=value=>48+(value-minimum)/span*704;
  node("rect",{x:x(model.low),y:58,width:Math.max(1,x(model.high)-x(model.low)),height:80,class:"grid-band"});
  for(const price of model.levels)node("line",{x1:x(price),x2:x(price),y1:59,y2:138,class:"grid-level"});
  for(const [price,label,align]of [[model.low,"下界","start"],[model.high,"上界","end"]])node("text",{x:x(price),y:158,"text-anchor":align,class:"grid-bound"},`${label} ${money(price)}`);
  function marker(price,label,y,kind,shape="circle") {
   const at=x(price),anchor=at<145?"start":at>655?"end":"middle";
   node("line",{x1:at,x2:at,y1:y+5,y2:130,class:`grid-marker-line ${kind}`});
   if(shape==="rect")node("rect",{x:at-5,y:y-5,width:10,height:10,class:`grid-marker ${kind}`});
   else node("circle",{cx:at,cy:y,r:5,class:`grid-marker ${kind}`});
   node("text",{x:at,y:y-12,"text-anchor":anchor,class:`grid-label ${kind}`},`${label} ${money(price)}`);
  }
  marker(model.anchor,"基準",91,"anchor");
  if(quoteValue!==null)marker(quoteValue,quote.label,35,"quote");
  for(const [side,point]of Object.entries(model.next))if(point.allowed)marker(point.price,side==="buy"?"下一買點":"下一賣點",side==="buy"?193:220,side);
  for(const order of orders)marker(Number(order.price),order.unknown?"待查委託":`${order.side==="buy"?"買單":"賣單"}餘${Math.max(0,order.quantity-order.filled)}股`,120,"order","rect");
  host.append(svg);
  const legend=document.createElement("p");legend.className="grid-legend";legend.textContent="細線：參考格線 · 圓點：行情／基準／下一買賣點 · 方塊：實際在途或待查委託。規劃不代表已送單。";host.append(legend);
  if(!quote){const note=document.createElement("p");note.className="help";note.textContent="尚無行情，仍可查看與設定網格；不以起始價代替成交價。";host.append(note);}
  if(quote&&["stale","disconnected","error","time_unverified"].includes(quote.status)){const note=document.createElement("p");note.className="help";note.textContent="圖上行情為最後資料，來源狀態或時間需確認，僅供參考。";host.append(note);}
 }
 function attentionItems(state){
  const items=[];
  if(state.fault||state.audit_error)items.push({id:"service",title:"交易服務需查核",reason:state.fault||state.audit_error,action:"connection"});
  for(const s of state.strategies||[]){
   if(s.status==="archived")continue;
   const pending=pendingOrders(s,state),recovery=state.recovery_status?.[s.id];
   let action="",reason="",title=s.params.name;
   if(pending.some(o=>o.unknown)){action="reconcile";reason="委託結果未明，請查核原單與庫存";}
   else if(["checking","retry_wait","connection_wait"].includes(recovery?.state))continue;
   else if(!s.reconciled){action="reconcile";reason=s.reason||"尚未完成庫存與委託核對";}
   else if(s.status==="active"){
    const d=describeStrategy(s,state);
    if(d.code==="account"){action="connection";reason=d.title;}
    if(d.code==="live_disabled"){action="live";reason=d.title;}
    if(["parameters","expired"].includes(d.code)){action="edit";reason=d.title;}
   }
   if(action)items.push({id:s.id,title,reason,action});
  }
  return items;
 }
 function pollingDelays(hidden,loginBusy,diagnosticOpen){return hidden?{state:5000,diagnostics:5000}:{state:1200,diagnostics:loginBusy||diagnosticOpen?500:3000};}
 return {gridModel,pendingOrders,displayQuote,describeStrategy,setText,summaryNode,updateSummary,drawGrid,attentionItems,pollingDelays};
})();
if(typeof module!=="undefined")module.exports=WorkspaceView;
