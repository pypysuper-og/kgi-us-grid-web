/* Installed before the real workbench. No request is delegated to a real transport. */
"use strict";
(function(){
 const copy=value=>JSON.parse(JSON.stringify(value)),account={broker_id:"DEMO",account:"DEMO-001",account_flag:"展示假帳戶"};
 const calls=[],results=new Map();let owner=null,selectedAccount=null,logged=false,armed=false,created=null,active=false,reconciled=false,seconds=0,revision=0,draftRevision=0;
 const now=()=>new Date().toISOString();
 const row=()=>created?{id:"showcase-strategy",owner:"live:showcase-owner",mode:"live",status:active?"active":"paused",revision:1,reconciled:reconciled?1:0,anchor:String(Showcase.snapshot(seconds).anchor),params:copy(created),reason:active?"":"等待查核與確認"}:null;
 function position(){const m=Showcase.snapshot(active?seconds:0);return {strategy_id:"showcase-strategy",owner:"live:showcase-owner",mode:"live",name:created?.name||"",symbol:"AAPL",currency:"USD",quantity:m.quantity,cost:String(m.cost),average_cost:m.quantity?String(m.cost/m.quantity):null,realized:String(m.realized),buy_value:String(m.orders.filter(o=>o.side==="buy"&&o.status==="filled").reduce((v,o)=>v+o.price*o.quantity,0)),sell_value:String(m.orders.filter(o=>o.side==="sell"&&o.status==="filled").reduce((v,o)=>v+o.price*o.quantity,0)),limit_buy_value:"0",fees_complete:true,time_complete:true,cost_provisional:false,current_settlement_currency:"MUT",historical_settlement_currencies:[],handoffs:[],opening_replaced:false};}
 function orders(){return !active?[]:Showcase.snapshot(seconds).orders.map((o,i)=>({id:o.id,strategy_id:"showcase-strategy",owner:"live:showcase-owner",symbol:"AAPL",side:o.side,price:String(o.price),quantity:o.quantity,status:o.status,filled:o.status==="filled"?o.quantity:0,filled_value:o.status==="filled"?String(o.price*o.quantity):"0",unknown:false,broker_org:o.id,broker_id:o.id,settlement_currency:"MUT",created_at:now(),updated_at:now(),id_number:i+1}));}
 function fills(){return orders().filter(o=>o.status==="filled").map((o,i)=>({id:i+1,order_id:o.id,strategy_id:o.strategy_id,side:o.side,quantity:o.quantity,value:o.filled_value,time:now(),received_at:now(),time_quality:"source",settlement_currency:"MUT"}));}
 function market(){const time=now(),q={symbol:"AAPL",price:String(Showcase.snapshot(seconds).price),source_time:time,received_time:time,generation:1,source:"展示假行情",kind:"trade",source_timezone:"America/New_York"};return {AAPL:{symbol:"AAPL",name:"Apple（展示假資料）",status:"live",quote:q,generation:1}};}
 function snapshot(){const s=row();return {audit_seq:1,audit_error:null,ui_preferences:{left:50,upper:46,font_size:14},presentation_mode:"browser",live_preferences:{},updated_at:now(),stage:"原介面展示・全部假資料",generation:1,broker_connected:!!owner,market_open:true,owner,accounts:logged?[account]:[],selected:selectedAccount,live_available:armed,live_limits:{daily_buy_limit:null,orders_used:orders().length,orders_limit:null},live_reason:armed?"展示假資料：實單確認已演示，沒有真實授權":"展示假資料：不連接券商、不寫入真實帳本",strategies:s?[s]:[],orders:orders(),fills:fills(),history_version:revision+Math.floor(seconds),drafts:[],events:[],report:{rows:s?[position()]:[],version:revision+Math.floor(seconds),summary_version:revision+Math.floor(seconds)},quotes:active?{"showcase-strategy":market().AAPL.quote}:{},quote_warnings:{},market:owner?market():{},reconciliation:{},fault:null,closing:false};}
 function diagnostics(){return {seq:1,error:null,actor_alive:true,lines:[{time:now(),message:logged?"展示登入完成；使用固定假帳戶，未連接券商":"展示介面已就緒，等待自動演示登入"}],connection:{phase:selectedAccount?"connected":logged?"select_account":"logged_out",selected:selectedAccount,message:"全部為展示假資料"}};}
 function layout(p){const low=Number(p.lower_price),high=Number(p.upper_price),n=p.intervals||Math.round((high-low)/Number(p.gap)),gap=(high-low)/n;return {valid:Number.isFinite(gap)&&gap>=.01,error:"",suggestions:[],gap:gap.toFixed(2),start_price:p.start_price||((low+high)/2).toFixed(2),intervals:n,levels:n+1,anchor_aligned:true,prices:Array.from({length:Math.min(1001,n+1)},(_,i)=>(low+i*gap).toFixed(2))};}
 function preview(p){const params=p.params,anchor=Number(params.start_price),gap=Number(params.gap),qty=Number(params.quantity),rows=[];for(let price=anchor-gap;price>=Number(params.lower_price);price-=gap)rows.push({side:"buy",price:String(price),quantity:qty,inventory:rows.length*qty+qty});return {anchor:String(anchor),inventory:0,price_gap:String(gap),rows,totals:{buy_value:String(rows.reduce((v,r)=>v+Number(r.price)*qty,0)),additional_shares:rows.length*qty,price_gap:String(gap),buy_levels:rows.length,sell_levels:0,truncated:false}};}
 function command(action,p){
  calls.push({action,time:seconds,payload:copy(p)});
  if(!["history","preview","grid_layout","lookup","market_watch","report"].includes(action))revision++;
  switch(action){
   case "login": logged=true;return {accounts:[account]};
   case "select_account":owner="showcase-owner";selectedAccount=account;return {selected:account};
   case "lookup":return {generation:1,market:market().AAPL};
   case "market_watch":return {released:!!p.release};
   case "grid_layout":return layout(p);
   case "preview":return preview(p);
   case "create":created=copy(p.params);return {id:"showcase-strategy"};
   case "live_control":armed=!!p.enable;return {enabled:armed};
   case "reconcile":if(p.confirm)reconciled=true;return {matched:true,unknown_orders:0,explanation:"展示查核：假帳本與假庫存一致",position:position(),broker:{holdings:0,orders:[]},version:"showcase-reconcile"};
   case "recovery_preview":return {version:"showcase-recovery",strategies:[{id:"showcase-strategy",name:created.name,mode:"live",status:"paused",local_quantity:0,broker_quantity:0,orders:[]}]};
   case "recovery_confirm":if(!armed||!p.confirm)throw Error("展示尚未完成實單與庫存確認");reconciled=true;active=!!p.start;return {results:[{id:"showcase-strategy",name:created.name,status:active?"active":"reconciled",message:"展示假庫存核對完成，已開始假行情監控"}]};
   case "start":if(!armed||!reconciled)throw Error("展示尚未完成確認流程");active=true;return {started:true};
   case "library":return p.operation==="list"?{entries:[]}:{id:"showcase-draft",revision:++draftRevision,entry:{id:"showcase-draft",revision:draftRevision},deleted:true};
   case "history":return {rows:p.kind==="orders"?orders().reverse():p.kind==="fills"?fills().reverse():[],next:null};
   case "report":return snapshot().report;
   default:throw Error("展示未支援此操作，不會傳送至真實服務："+action);
  }
 }
 async function fakeFetch(url,options={}){
  const path=new URL(String(url),location.href).pathname;
  try{
   let data;
   if(path==="/api/session")data={token:"showcase-only",instance:"showcase",version:"0.1.13"};
   else if(path==="/api/state")data=snapshot();
   else if(path==="/api/diagnostics")data=diagnostics();
   else if(path.startsWith("/api/commands/")&&options.method==="POST"){
    const body=JSON.parse(options.body),action=path.split("/").at(-1);let result;
    if(typeof body.request_id!=="string"||!body.payload||typeof body.payload!=="object")throw Error("展示操作格式不符");
    try{result={status:"done",result:command(action,body.payload||{})};}catch(error){result={status:"error",message:error.message};}
    results.set(body.request_id,result);data={command_id:body.request_id,status:"queued"};
   }else if(path.startsWith("/api/commands/")&&results.has(path.split("/").at(-1)))data=results.get(path.split("/").at(-1));
   else throw Error("展示阻擋未支援的資料請求");
   return new Response(JSON.stringify(data),{status:200,headers:{"Content-Type":"application/json"}});
  }catch(error){return new Response(JSON.stringify({detail:error.message}),{status:403,headers:{"Content-Type":"application/json"}});}
 }
 Object.defineProperty(window,"fetch",{value:fakeFetch,writable:false,configurable:false});
 window.ShowcaseBackend={snapshot,diagnostics,calls,setTime:value=>{seconds=value;},get active(){return active;}};
 // Only the scripted DOM events are accepted. Playback controls live outside this frame.
 for(const type of ["click","pointerdown","keydown","submit","beforeinput","input","change"]){document.addEventListener(type,event=>{
  if(!event.isTrusted)return;
  event.preventDefault();event.stopImmediatePropagation();
  const target=event.target?.closest?.('button,a,input,select,textarea,summary,[role="button"],[role="tab"],[role="separator"],[tabindex]');
  const keyIntent=event.type==="keydown"&&(event.key==="Escape"||target&&event.key!=="Tab");
  if(event.type==="click"&&target||keyIntent)document.dispatchEvent(new Event("showcase-interaction"));
 },true);}
})();
