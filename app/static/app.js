"use strict";
const $ = id => document.getElementById(id);
const modeNames={demo:"離線展示",paper:"行情模擬",live:"正式交易"};
const stateNames={active:"監控中",paused:"已暫停",archived:"已封存",prepared:"準備送單",awaiting_report:"等待回報",working:"委託中",partially_filled:"部分成交",filled:"全部成交",canceled:"已撤銷",rejected:"被拒",expired:"已失效",unknown:"結果未明"};
let diagnostics=null,diagnosticBusy=false,loginPending=false,selectionPending=false,shutdownPending=false;
let token="",state=null,selected=null,tab="plan",editing=null,closed=false,pollBusy=false;
const reportSelection=new Map();
const terminal=new Set(["filled","canceled","rejected","expired"]);
function el(tag,text,className){const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);if(className)n.className=className;return n;}
function button(text,fn,cls="secondary"){const n=el("button",text,cls);n.type="button";n.addEventListener("click",fn);return n;}
function number(value,digits=2){return value===null||value===undefined?"未知":Number(value).toLocaleString("zh-TW",{maximumFractionDigits:digits});}
function timeLabel(value){if(!value)return "未知";return new Date(value).toLocaleString("zh-TW",{hour12:false});}
function toast(text){$("toast").textContent=text;$("toast").hidden=false;setTimeout(()=>$("toast").hidden=true,4500);}
async function request(url,options={}){let response;try{response=await fetch(url,{...options,headers:{"Content-Type":"application/json","X-Grid-Token":token,...options.headers}});}catch{throw Error("後端連線中斷；請查核狀態，不要重送交易");}if(!response.ok){const data=await response.json().catch(()=>({}));throw Error(typeof data.detail==="string"?data.detail:"輸入或操作不符合條件");}return response.json();}
async function command(action,payload={}){
 const id=crypto.randomUUID();await request(`/api/commands/${action}`,{method:"POST",body:JSON.stringify({request_id:id,payload})});
 const deadline=Date.now()+90000;
 while(Date.now()<deadline){const result=await request(`/api/commands/${id}`);if(result.status==="error")throw Error(result.message);if(result.status==="done")return result.result;await new Promise(r=>setTimeout(r,350));}
 throw Error("操作仍在處理；請查看狀態。畫面逾時不代表交易失敗，也不會自動重送。");
}
async function safe(fn,target){try{return await fn();}catch(e){if(target)$(target).textContent=e.message;else toast(e.message);}}
function table(headers,rows){const wrap=el("div",undefined,"table-wrap"),t=el("table"),head=el("thead"),tr=el("tr");headers.forEach(h=>tr.append(el("th",h)));head.append(tr);t.append(head);const body=el("tbody");rows.forEach(row=>{const r=el("tr");row.forEach(value=>{const cell=el("td");if(value instanceof Node)cell.append(value);else cell.textContent=value??"未知";r.append(cell);});body.append(r);});t.append(body);wrap.append(t);return wrap;}
function badge(text,cls="neutral"){return el("span",text,"pill "+cls);}
function strategy(){return state?.strategies.find(s=>s.id===selected);}
function position(sid){return state?.report.rows.find(p=>p.strategy_id===sid);}
function settlementLabel(value){return value==="TWD"?"TWD 台幣":value==="MUT"?"MUT 外幣":"未知";}
function canRetry(s,o){const orders=state.orders.filter(x=>x.strategy_id===s.id);return s.mode==="live"&&s.status==="paused"&&o?.id===orders.at(-1)?.id&&o?.status==="rejected"&&!o.unknown&&!o.filled&&!orders.some(x=>!terminal.has(x.status)||x.unknown);}
function defaultParams(){const d=new Date();d.setDate(d.getDate()+30);return {name:"AAPL 雙邊網格",symbol:"AAPL",mode:"demo",direction:"both",start_price:"100",gap:"2",gap_unit:"amount",quantity:10,min_inventory:0,initial_inventory:0,max_inventory:100,initial_cost:null,opening_confirmed:false,lower_price:"80",upper_price:"120",max_order_value:"2000",daily_buy_limit:"10000",currency:"MUT",end_date:d.toISOString().slice(0,10),quote_max_age:15};}
function openStrategy(s=null){openGridEditor(s);}

function readParams(){const p={};for(const input of $("strategy-form").elements){if(!input.name)continue;p[input.name]=input.type==="checkbox"?input.checked:input.value;}for(const key of ["quantity","min_inventory","initial_inventory","max_inventory","quote_max_age"])p[key]=Number(p[key]);p.initial_cost=p.initial_cost===""?null:p.initial_cost;return p;}
function actionDialog(title,content,actions,afterSuccess=null){$("action-title").textContent=title;$("action-body").replaceChildren();if(content instanceof Node)$("action-body").append(content);else $("action-body").append(el("p",content));$("action-error").textContent="";$("action-buttons").replaceChildren();for(const action of actions){const b=button(action.text,async()=>{b.disabled=true;try{await action.fn();$("action-dialog").close();await refresh();if(afterSuccess)await afterSuccess();}catch(e){$("action-error").textContent=e.message;}finally{b.disabled=false;}},action.cls||"secondary");$("action-buttons").append(b);}if(!$("action-dialog").open)$("action-dialog").showModal();}
function startStrategy(s){beginStartFlow(s);}

function stopStrategy(s){const pending=state.orders.filter(o=>o.strategy_id===s.id&&(!terminal.has(o.status)||o.unknown));actionDialog("停止策略：選擇未成交單處理",`${s.params.name} 有 ${pending.length} 筆在途／未知委託。兩種選項都會停止新單、保留已成交部位，不會平倉。`,[{text:"停止並保留未成交單",fn:()=>command("stop",{strategy_id:s.id,cancel:false})},{text:"停止並撤銷本策略委託",cls:"danger",fn:()=>command("stop",{strategy_id:s.id,cancel:true})}]);}
async function reconcile(s,resume=false){const result=await command("reconcile",{strategy_id:s.id});const body=el("div");body.append(el("p",result.explanation),el("p",`策略持股 ${result.position.quantity} 股；未知委託 ${result.unknown_orders} 筆。${result.matched?"目前帳本一致，可確認後重新啟動。":"存在差異或契約尚未驗證，暫不開放自動交易。"}`));body.append(el("pre",JSON.stringify(result.broker,null,2)));const actions=result.matched&&!result.unknown_orders?[{text:"確認對帳",cls:"primary",fn:()=>command("reconcile",{strategy_id:s.id,confirm:true,version:result.version})}]:[{text:"保留暫停",fn:async()=>{cancelStartFlow();}}];actionDialog("對帳與恢復",body,actions,resume?continueStartFlow:null);}
function renderStrategies(){
 const list=$("strategies"),top=list.scrollTop,left=list.querySelector(".table-wrap")?.scrollLeft||0;list.replaceChildren();
 const visible=state.strategies.filter(s=>s.status!=="archived"&&($("mode-filter").value==="all"||s.mode===$("mode-filter").value));
 $("empty").hidden=visible.length>0;
 const existing=state.strategies.some(s=>s.status!=="archived");
 $("empty-title").textContent=existing?"目前篩選沒有符合的網格":"建立你的第一組網格";
 $("empty-message").textContent=existing?"可切換「所有模式」查看其他策略，或自行新增網格。":"自行選擇商品、執行模式與網格參數。新增後保持暫停。";
 if(visible.length){
  const wrap=table(["監控狀態","執行","策略名稱／模式","商品／最新行情","起始價位","持股","停止日期（台灣）","管理"],[]);
  const tbody=wrap.querySelector("tbody");wrap.querySelector("table").className="strategy-table";
  for(const s of visible){
   const p=position(s.id),row=el("tr",undefined,"strategy-card"+(s.id===selected?" active-selection":""));
   row.tabIndex=0;row.setAttribute("aria-label",`策略 ${s.params.name}`);
   row.setAttribute("aria-selected",String(s.id===selected));
   const pick=()=>{selected=s.id;renderStrategies();renderDetail();};
   row.addEventListener("click",e=>{if(!e.target.closest("button"))pick();});
   row.addEventListener("keydown",e=>{if((e.key==="Enter"||e.key===" ")&&e.target===row){e.preventDefault();pick();}});
   const name=el("div",undefined,"strategy-name");name.append(el("strong",s.params.name),el("small",modeNames[s.mode]));
   if(s.reason)name.append(el("small",s.reason,"reason"));if(state.quote_warnings?.[s.id])name.append(el("small",state.quote_warnings[s.id],"reason"));
   const actions=el("div",undefined,"row-actions");
   actions.append(button("對帳",()=>safe(()=>reconcile(s))),button("編輯",()=>openStrategy(s)),button("封存",()=>actionDialog("封存策略","僅無部位、無在途委託且已停止的策略可封存。歷史仍保留。",[{text:"確認封存",fn:()=>command("archive",{strategy_id:s.id})}])));
   if(s.mode==="live"){
    actions.append(button("查單",()=>openOrderDiagnostics(s)));
    const latest=state.orders.filter(o=>o.strategy_id===s.id).at(-1);
    if(canRetry(s,latest))actions.append(button("重新送單",()=>beginRetryFlow(s,latest),"danger"));
   }
   const cells=[badge(stateNames[s.status],s.status==="active"?"":"neutral"),button(s.status==="active"?"停止":"啟動",()=>s.status==="active"?stopStrategy(s):startStrategy(s),s.status==="active"?"secondary":"primary"),name,marketCell(s),number(s.params.start_price),`${p?.quantity??0} 股`,s.params.end_date,actions];
   for(const value of cells){const cell=el("td");if(value instanceof Node)cell.append(value);else cell.textContent=value;row.append(cell);}
   tbody.append(row);
  }
  list.append(wrap);wrap.scrollLeft=left;list.scrollTop=top;
 }
 renderSettings();
}
function renderSettings(){
 const host=$("strategy-settings"),s=strategy(),top=host.scrollTop;host.replaceChildren(el("h3","策略設定內容"));host.querySelector("h3").append(helpButton("settings"));
 if(!s){host.append(el("p","選取上方策略查看設定。","help"));return;}
 const p=s.params,pos=position(s.id),q=state.quotes[s.id],grid=el("dl",undefined,"settings-grid");
 const fields=[["結算方式（後續新單）",settlementLabel(p.currency)],["策略名稱",p.name],["監控商品",p.symbol],["策略方向",p.direction==="both"?"雙邊買賣":p.direction==="buy"?"單邊買進":"單邊賣出"],["執行模式",modeNames[s.mode]],["起始價位",number(p.start_price)],["網格基準",number(s.anchor)],["網格間距",`${number(p.gap)} ${p.gap_unit==="percent"?"%（起始價）":"USD"}`],["每筆股數",`${p.quantity} 股`],["庫存下限",`${p.min_inventory} 股`],["庫存上限",`${p.max_inventory} 股`],["目前部位",`${pos?.quantity??0} 股`],["最新行情",s.mode==="demo"?(q?`${number(q.price)} USD（離線）`:"尚未取得"):marketText(state.market?.[p.symbol],p.symbol)],["買價下界",number(p.lower_price)],["賣價上界",number(p.upper_price)],["單筆金額上限",`${number(p.max_order_value)} USD`],["每日買入上限",`${number(p.daily_buy_limit)} USD`]];
 for(const [label,value]of fields)grid.append(el("dt",label),el("dd",value));
 host.append(grid);
 const note=el("p",s.reason||"設定僅供查閱；修改請按策略列的「編輯」。","settings-note");
 if(q&&s.mode==="demo")note.append(el("span",`行情來源：${q.source} · ${timeLabel(q.source_time)}`));
 host.append(note);host.scrollTop=top;
}
function renderDetail(){const s=strategy(),body=$("detail");body.replaceChildren();$("detail-title").textContent=s?s.params.name:"策略與帳本";$("detail-mode").textContent=s?modeNames[s.mode]:"尚未選取";if(!s){body.append(el("p","選擇左側策略開始查看","empty-small"));return;}const params=s.params,p=position(s.id),orders=state.orders.filter(o=>o.strategy_id===s.id);const tips=el("div",undefined,"detail-tips");tips.append(button("？ "+(tab==="pnl"?"成本與日期說明":"本頁使用說明"),()=>showHelp(tab==="pnl"?"pnl":tab==="events"?"reconcile":"details"),"ghost"));body.append(tips);if(tab==="plan"){const gap=Number(params.gap)*(params.gap_unit==="percent"?Number(params.start_price)/100:1);const summary=el("div",undefined,"summary-line");summary.append(el("span",`下一買點 ${number(Number(s.anchor)-gap)}`),el("span",`下一賣點 ${number(Number(s.anchor)+gap)}`),el("span",`範圍 ${number(params.lower_price)}–${number(params.upper_price)}`));body.append(summary);const rows=[];for(let sign of [-1,1]){if(params.direction!="both"&&params.direction!=(sign<0?"buy":"sell"))continue;const count=sign<0?Math.floor((params.max_inventory-(p?.quantity||0))/params.quantity):Math.floor(((p?.quantity||0)-params.min_inventory)/params.quantity);for(let i=1;i<=Math.min(count,100);i++){const price=Number(s.anchor)+sign*gap*i;if(price<Number(params.lower_price)||price>Number(params.upper_price))break;rows.push([sign<0?"買進":"賣出",number(price),params.quantity,`${(p?.quantity||0)-sign*params.quantity*i} 股`]);}}body.append(table(["方向","觸發／限價","每筆股數","規劃後庫存"],rows));body.append(el("p","規劃為預估；每策略一次只持有一筆未結束網格委託，實際成交才更新庫存。","help"));}
else if(tab==="orders"){
 if(s.mode==="live")body.append(button("查詢券商委託",()=>openOrderDiagnostics(s)));
 body.append(table(["方向","限價","成交／委託","狀態與原因","原單號","送單結算方式","操作"],orders.slice().reverse().map(o=>{
  const actions=el("div",undefined,"row-actions");
  if(canRetry(s,o))actions.append(button("重新送單",()=>beginRetryFlow(s,o),"danger"));
  if(o.unknown&&s.mode==="live")actions.append(button("查核配對",()=>safe(()=>openRecovery([s.id],false))));
  if(o.filled&&terminal.has(o.status))actions.append(button("核對費用",()=>feeDialog(o)));
  return [o.side==="buy"?"買進":"賣出",number(o.price),`${o.filled} / ${o.quantity}`,`${stateNames[o.status]||o.status}${o.unknown?" · 待核對":""}${o.raw_status?" · "+o.raw_status:""}`,o.broker_org||"等待回報",settlementLabel(o.settlement_currency),actions];
 })));
}
else if(tab==="fills"){body.append(table(["時間與來源","方向","股數","成交金額 USD","送單結算方式"],state.fills.filter(f=>f.strategy_id===s.id).map(f=>[f.time_quality==="received"?`來源時間 ${f.source_time||"未提供"}（時區／日期未確認）；接收 ${timeLabel(f.received_at)}`:timeLabel(f.time),f.side==="buy"?"買進":"賣出",f.quantity,number(f.value),settlementLabel(f.settlement_currency)])));body.append(el("p","只呈現已確認成交。結算方式保留送單當時設定；金額仍為美元，未推估換匯或券商實際扣款。","help"));}
else if(tab==="pnl"){renderPnl(body,s,p);}

else{for(const e of state.events.filter(e=>!e.strategy_id||e.strategy_id===s.id)){const node=el("div",undefined,"event");node.append(el("time",timeLabel(e.time)),el("p",e.message));body.append(node);}}}
function renderPnl(body,s,p){
 const saved=reportSelection.get(s.id)||{day:"",row:null,version:null};
 const controls=el("div",undefined,"summary-line"),input=el("input"),content=el("div");
 input.type="date";input.value=saved.day;input.setAttribute("aria-label","損益日期");
 input.addEventListener("change",()=>{saved.day=input.value;saved.row=null;reportSelection.set(s.id,saved);});
 function show(row){content.replaceChildren(table(["項目","數值"],[["目前設定結算方式",settlementLabel(row.current_settlement_currency)],["歷史成交結算方式",(row.historical_settlement_currencies||[]).map(settlementLabel).join("、")||"尚無成交"],["目前持股",`${row.quantity} 股`],["目前總成本 USD",number(row.cost)],["移動平均成本 USD",number(row.average_cost)],[saved.day?`${saved.day} 已實現 USD`:"累計已實現 USD",number(row.realized)],["成交時間狀態",row.time_complete===false?"日期歸屬待核對；成本依接收順序暫估，單日損益未知":"來源時間可確認"],["費用狀態",row.fees_complete?"已登錄（離線預設0，可核對）":"尚未完整，損益暫估"]]));}
 controls.append(input,button("查詢",()=>safe(async()=>{const report=await command("report",{day:input.value||null});Object.assign(saved,{day:input.value,row:report.rows.find(r=>r.strategy_id===s.id),version:report.version});reportSelection.set(s.id,saved);show(saved.row);})),button("匯出 Excel",()=>{window.location.href="/api/export.xlsx"+(input.value?"?day="+encodeURIComponent(input.value):"");}));
 if(s.params.initial_inventory>0)controls.append(button("核對期初成本",()=>openingDialog(s)));
 body.append(controls,content);
 if(saved.day&&(!saved.row||saved.version!==state.report.version))content.append(el("p","日期已選取或帳本已更新，請按查詢取得該日報表。"));else show(saved.row||p);
 body.append(el("p","依美東成交日篩選已實現損益；持倉與成本為目前值。美元記帳，不含未實現損益與台幣換匯損益。","help"));
}
function openingDialog(s){const body=el("div"),amount=el("input"),reason=el("input");body.append(el("p",`請核對期初 ${s.params.initial_inventory} 股的總成本（USD），不是目前剩餘持股成本。更正以追加記錄保存並重算歷史。`));amount.type="number";amount.min="0";amount.step=".01";amount.setAttribute("aria-label","期初總成本 USD");reason.placeholder="成本來源／更正原因";reason.setAttribute("aria-label","成本來源");body.append(amount,reason);actionDialog("核對期初成本",body,[{text:"保存成本",cls:"primary",fn:()=>command("opening_cost",{strategy_id:s.id,amount:amount.value,reason:reason.value})}]);}
function feeDialog(order){const body=el("div");body.append(el("p","輸入此筆委託的累計實際美元費用；更正會追加記錄並重算，不會修改成交股數。"));const amount=el("input");amount.type="number";amount.min="0";amount.step=".01";amount.value="0";amount.setAttribute("aria-label","累計費用 USD");const reason=el("input");reason.placeholder="費用來源／更正原因";reason.setAttribute("aria-label","費用來源");body.append(amount,reason);actionDialog("核對費用",body,[{text:"保存費用",cls:"primary",fn:()=>command("fee",{order_id:order.id,amount:amount.value,reason:reason.value})}]);}
function render(){if(typeof loadLayout==="function")loadLayout(state.ui_preferences);const live=state.strategies.filter(s=>s.status!=="archived");$("metric-active").textContent=live.filter(s=>s.status==="active").length;$("metric-orders").textContent=state.orders.filter(o=>!terminal.has(o.status)||o.unknown).length;$("metric-holdings").textContent=state.report.rows.reduce((v,r)=>v+r.quantity,0);$("metric-attention").textContent=live.filter(s=>!s.reconciled||s.reason).length;$("notice").textContent=state.fault||[state.live_reason,state.stage?.includes("重連")?state.stage:""].filter(Boolean).join(" · ");$("connection").textContent=state.fault?"需檢查":"本機已連線";$("connection").className="pill"+(state.fault?" bad":"");$("freshness").textContent="最近更新 "+timeLabel(state.updated_at);if(!selected&&live.length)selected=live[0].id;renderStrategies();if(!$("detail").contains(document.activeElement)){const top=$("detail").scrollTop;renderDetail();$("detail").scrollTop=top;}renderAccounts();renderEditorMarket();}
async function refresh(){if(pollBusy||closed)return;pollBusy=true;try{state=await request("/api/state");render();}catch(e){$("connection").textContent="後端失聯";$("connection").className="pill bad";$("notice").textContent=e.message;if(state){renderStrategies();renderEditorMarket();}}finally{pollBusy=false;}}
const HELP={
 orderLimit:["送單筆數限制",["這是本程式的實單操作額度，不是券商規則、股數、格數或成交次數。所有正式策略共用；買單與賣單每次進入送單流程各計1筆。查詢、撤單、離線與行情模擬不計。","已嘗試送單即佔用額度；被拒、結果未明或之後撤銷也不退回。重新送出新委託另計1筆。買1筆再賣1筆，共2筆。","填20並啟用，表示從現在起最多再嘗試20筆；重新設定會將剩餘額度改為20，不是加在原本剩餘額度上。這不是每日自動重置的限制。","用完後，下一次觸發新單的策略會暫停，已送出的委託仍會追蹤，不會自動撤單或平倉。需重新設定額度並啟動暫停策略；本次設定不會自動啟動策略。","每日買入金額、持股與單筆金額上限仍分別生效。程式重啟後不沿用實單授權，需重新確認。"]],
 overview:["工作台怎麼用",["按「新增網格」自行選擇模式，可用逐步精靈完成參數與預覽，再另行啟動。所有策略都需要另按啟動，儲存不會執行交易。","桌面左側是策略與設定，右側用分頁查看規劃、委託、成交、成本與事件。窄畫面用「策略與設定／規劃與紀錄」切換。主畫面固定，長資料在區塊內捲動。","上方「離線行情」開啟測試小視窗；各區旁的「？」可隨時查看操作說明。關閉說明只關閉視窗。","關閉瀏覽器不會停止後端。離開前請停止策略，或用「結束程式」選擇未成交單的處理方式。"]],
 strategies:["策略監控與停止",["點選策略列可查看該策略的設定與紀錄；切換分頁或查看說明不會啟停策略。","啟動後等待新行情。停止只禁止新單；每次停止時可選保留委託或撤銷本策略的未成交單，不會賣掉已成交持股。","部分成交後撤單會保留實際部位並暫停。先核對剩餘持股，再重新啟動；不自動補足原委託。","封存只適用於已停止、沒有部位且沒有在途單的策略；歷史資料仍保留。"]],
 settings:["策略設定內容",["這裡是所選策略的唯讀摘要。修改請按策略列的「編輯」；需先停止策略並處理在途委託。","網格基準在委託全部成交後推進；起始價是建立策略的參數，兩者可能不同。","最新行情是上次取得的資料，來源時間列在下方；顯示數字不代表資料仍即時有效。"]],
 details:["規劃、委託與成交",["規劃：顯示從目前基準推算的價格與股數，不代表已送出這些委託。每策略同時最多一筆未結束網格單。","委託：查看原單號、狀態及累計成交。方法返回或券商受理不等於成交，結果未明時請對帳，不要重送。","成交：只列已確認成交。成本損益與事件各有自己的分頁；切換分頁不影響監控。"]],
 modes:["三種模式的差別",["離線展示：完全本機，使用「離線行情」演練，可在任何時間測試。","行情模擬：需要正式唯讀登入取得行情，但委託在本機撮合；只在正常盤執行。這不是券商提供的美股模擬環境。","正式交易：登入選帳後，在「實單設定」填入預算與筆數，明確啟用；再對帳並啟動正式策略。斷線暫停新單，同帳戶重連查核一致後自動恢復原監控與剩餘額度；登出或程式重啟仍須重新啟用。"]],
 gap:["網格間距與觸發",["固定金額：例如起始100、間距2，下一買點98、下一賣點102。全部成交後才推進基準。","百分比：以起始價換算一次固定價差，不是每次複利重算。例如起始200、間距1%即固定2美元。","只有真實成交數量會改變本地持股。規劃表只是預估，部分成交時仍先追蹤同一筆委託。"]],
 inventory:["期初部位與庫存界線",["最少保留股數 ≤ 期初股數 ≤ 最多持股數；只支援現股多頭與整數股。","期初股數需自行核對來源，不能把舊設定或別的策略持股直接當作本策略庫存。","成本請填這批期初股票的美元總成本，不是每股均價。未知留空，系統不當成0；之後可在成本頁追加核對。"]],
 limits:["金額上限與停止日期",["單筆金額上限與每日買入上限以USD計算，買入金額不含後續補登的費用；股數另受庫存上限約束。","每日買入按美東成交日計算。停止日期沿用台灣時間，含當日；到期只停新單，未成交委託仍可能存在。","帳戶預算只涵蓋本程式管理的委託，不能當成券商帳戶可用資金；其他平台交易請另行核對。"]],
 pnl:["成本與損益怎麼看",["移動加權平均：買入成交及買入費用加入成本，賣出依當時均價扣除成本；賣出費用扣減已實現損益。","選日期只篩選該美東日的已實現損益與買賣金額；持股、總成本及均價始終是目前值。選好日期後按「查詢」。","費用可在委託頁追加核對；離線／行情模擬預設0。期初成本未知或費用不完整時，不要把暫估值當成最終淨損益。","目前以USD記帳，不含未實現損益、TWD換匯損益、股利或拆股。"]],
 reconcile:["對帳與重新啟動",["重啟後策略保持暫停。按「對帳」核對本地成交與來源帳本，確認一致後再另按「啟動」。","對帳有版本檢查：若持股或委託改變，舊的確認不能直接套用。","有未知委託或來源資料不足時保留暫停；不要以重新送單試探結果。模擬帳本一致不等於正式券商對帳通過。"]],
 backup:["備份與還原",["備份帳本使用SQLite一致性備份，完成完整性檢查後保存在runtime的backups子目錄；目前全部保留，不自動輪替。","還原使用專案restore.py，只允許尚不存在的新目錄，避免覆蓋正在使用的帳本。詳細命令見README。","還原後仍須對帳；備份不能消除備份時間以後發生的交易。運作中不要自行刪除-wal／-shm檔案。"]],
 import:["匯入舊策略",["選取舊版策略JSON後，先預覽每列結果，再確認匯入。相同來源重跑不會重複建立。","只匯入參數，模式為離線展示且保持暫停；不匯入舊持股、帳密或成本。價格界線與金額限制須再核對。","無效列會略過；確認匯入後可在策略清單檢查已建立的內容。"]],
 quote:["離線行情演練",["僅影響離線展示策略，不登入或送出券商委託。商品需與策略一致，並先啟動策略。","單次最多成交股數留空代表全部；0只掛單；3可觀察部分成交。上限分別套用每筆匹配的委託，不是整個帳戶的成交量。","例如預設AAPL：送入98模擬買入，再送入100模擬賣出。關閉行情視窗不會停止策略。"]]
};
function showHelp(key){
 const [title,paragraphs]=HELP[key]||HELP.overview;
 $("help-title").textContent=title;$("help-body").replaceChildren();
 const list=el("ol");for(const text of paragraphs)list.append(el("li",text));$("help-body").append(list);
 if(!$("help-dialog").open)$("help-dialog").showModal();
}
function helpButton(key){const b=button("?",()=>showHelp(key),"help-trigger");b.setAttribute("aria-label",HELP[key][0]+"說明");b.title=HELP[key][0];return b;}
function setupHelp(){
 document.querySelectorAll("[data-help]").forEach(b=>b.addEventListener("click",()=>showHelp(b.dataset.help)));
 for(const [selector,key]of [[".strategies-panel h2","strategies"],[".detail-panel h2","details"],["#quote-title","quote"],["#strategy-form-title","settings"]])document.querySelector(selector).after(helpButton(key));
 for(const [id,key]of [["backup","backup"],["import-open","import"]])$(id).after(helpButton(key));
 for(const [name,key]of [["mode","modes"],["gap","gap"],["initial_inventory","inventory"],["initial_cost","inventory"],["max_order_value","limits"],["end_date","limits"]]){
  const input=$("strategy-form").elements.namedItem(name),label=input.closest("label"),title=el("span",undefined,"label-title");
  title.append(label.firstChild,helpButton(key));label.prepend(title);
 }
}
$("quote-open").addEventListener("click",()=>$("quote-dialog").showModal());
document.querySelectorAll("[data-workspace]").forEach(b=>b.addEventListener("click",()=>{
 document.querySelector(".workspace").dataset.view=b.dataset.workspace;
 document.querySelectorAll("[data-workspace]").forEach(t=>t.classList.toggle("selected",t===b));
}));
setupHelp();
document.querySelectorAll("[data-close]").forEach(b=>b.addEventListener("click",()=>$(b.dataset.close).close()));
document.querySelectorAll("[data-tab]").forEach(b=>b.addEventListener("click",()=>{tab=b.dataset.tab;document.querySelectorAll("[data-tab]").forEach(t=>t.classList.toggle("selected",t===b));renderDetail();}));
$("mode-filter").addEventListener("change",renderStrategies);$("new-strategy").addEventListener("click",()=>openStrategy());
$("strategy-form").addEventListener("submit",e=>{e.preventDefault();saveEditor();});
$("preview").addEventListener("click",()=>safe(previewEditor,"strategy-error"));
$("empty-create").addEventListener("click",()=>openStrategy());
$("quote-form").addEventListener("submit",e=>{e.preventDefault();const f=e.currentTarget;safe(async()=>{await command("quote",{symbol:f.elements.symbol.value,price:f.elements.price.value,fill_limit:f.elements.fill_limit.value===""?null:Number(f.elements.fill_limit.value)});await refresh();toast("離線行情已送入");});});
$("backup").addEventListener("click",()=>safe(async()=>{const r=await command("backup");toast("已建立並驗證備份："+r.file);}));
$("login-open").addEventListener("click",()=>{renderConnection();$("login-dialog").showModal();});
$("login-form").addEventListener("submit",e=>{e.preventDefault();if(loginPending)return;const f=e.currentTarget,payload={person_id:f.elements.person_id.value,password:f.elements.password.value,confirm_production:f.elements.confirm_production.checked};f.elements.password.value="";f.elements.person_id.value="";loginPending=true;$("login-error").textContent="";renderConnection();safe(async()=>{try{await command("login",payload);await refresh();await pollDiagnostics();}finally{payload.password="";payload.person_id="";loginPending=false;renderConnection();}},"login-error");});
$("reconnect").addEventListener("click",()=>safe(async()=>{await command("reconnect");await pollDiagnostics();},"login-error"));
$("live-open").addEventListener("click",()=>{cancelStartFlow();openLiveSettings();});
$("live-form").addEventListener("submit",e=>{e.preventDefault();const f=e.currentTarget,b=f.querySelector("button[type=submit]");b.disabled=true;safe(async()=>{try{await command("live_control",{enable:true,confirm:f.elements.confirm.checked,daily_buy_limit:f.elements.daily_buy_limit.value,max_orders:Number(f.elements.max_orders.value),report_timezone:f.elements.report_timezone.value||null});$("live-dialog").close();await refresh();toast("實單設定已完成");await continueStartFlow();}finally{b.disabled=false;}},"live-error");});
$("live-disable").addEventListener("click",()=>safe(async()=>{await command("live_control",{enable:false});$("live-dialog").close();await refresh();},"live-error"));
$("logout").addEventListener("click",()=>{$("login-dialog").close();actionDialog("登出帳戶","會先停止相關策略的新單。保留未成交單時，登出後券商仍可能成交；選撤單則須確認結果後才能登出。",[{text:"保留委託並登出",fn:()=>command("logout",{cancel:false})},{text:"撤銷本機委託後登出",cls:"danger",fn:()=>command("logout",{cancel:true})}]);});
$("shutdown-open").addEventListener("click",()=>actionDialog("結束工作台","停止監控並先登出券商，再結束後端。已成交持股不會平倉。選擇嘗試撤單時，未取得确认的委託仍會列在結束畫面，請另至券商手動查核。",[{text:"保留委託並結束",fn:()=>shutdown(false)},{text:"嘗試撤單後結束",cls:"danger",fn:()=>shutdown(true)}]));
$("shutdown-back").addEventListener("click",()=>{$("shutdown-screen").hidden=true;closed=false;refresh();});
$("import-open").addEventListener("click",()=>$("legacy-file").click());
$("legacy-file").addEventListener("change",e=>safe(async()=>{const file=e.target.files[0];if(!file)return;if(file.size>500000)throw Error("檔案過大");const records=JSON.parse(await file.text());const result=await command("import_legacy",{records});const body=el("div");body.append(el("p","只匯入離線參數；不認領舊庫存與成本。請核對價格與金額限制後才啟動。"),table(["策略","檢查"],result.rows.map(r=>[r.name,r.error||(r.existing?"已匯入，略過":"可匯入")])));actionDialog("匯入預覽",body,[{text:"確認匯入",cls:"primary",fn:()=>command("import_legacy",{records,apply:true})}]);e.target.value="";}));
safe(async()=>{const session=await request("/api/session");token=session.token;await refresh();await pollDiagnostics();setInterval(refresh,1200);setInterval(pollDiagnostics,500);});

function renderAccounts(){
 const selector=$("account-selector"),accounts=state?.accounts||[],key=JSON.stringify(accounts);
 if(selector.dataset.accounts===key)return;selector.dataset.accounts=key;selector.replaceChildren();
 for(const a of accounts)selector.append(button(`${a.broker_id} · ${a.account} · ${a.account_flag}`,async()=>{if(selectionPending)return;selectionPending=true;$("login-error").textContent="";renderConnection();try{await command("select_account",{account:a.account,broker_id:a.broker_id});await refresh();await pollDiagnostics();$("login-dialog").close();toast("正式帳戶已選定；實單需另行啟用");await continueStartFlow();await offerResumeAfterLogin();}catch(e){$("login-error").textContent=e.message;}finally{selectionPending=false;renderConnection();}},"primary"));
}
function renderConnection(){
 const c=diagnostics?.connection||{},phase=c.phase||"logged_out",connected=phase==="connected"&&diagnostics?.actor_alive;
 const titles={logged_out:"券商未登入",logging_in:"正式登入中",login_failed:"登入失敗",select_account:"登入成功・待選帳",selecting_account:"帳戶初始化中",connected:"正式帳戶已連線",disconnected:"券商已斷線",reconnect_wait:"斷線・等待重連",reconnecting:"正在重連",reconnect_exhausted:"重連待處理",logging_out:"正在登出",logout_failed:"登出未完成"};
 $("broker-connection").textContent=diagnostics&&!diagnostics.actor_alive?"交易服務已停止":titles[phase]||phase;
 $("broker-connection").className="pill "+(connected?"":phase==="logged_out"?"neutral":"bad");
 $("broker-connection").title=c.message||titles[phase]||phase;
 $("account").textContent=c.selected?`${c.selected.broker_id} · ${c.selected.account}`:"未選帳戶";
 $("live-open").textContent=state?.live_available&&connected?"實單已啟用":"實單設定";
 $("live-open").classList.toggle("armed",!!state?.live_available&&connected);
 const wait=c.retry_at?` · 第 ${c.attempt+1} 次重連於 ${Math.max(0,Math.ceil(c.retry_at-Date.now()/1000))} 秒後`:"";
 $("login-stage").textContent=(c.message||titles[phase]||phase)+wait;
 const accountPhase=["select_account","selecting_account"].includes(phase),sessionPhase=!accountPhase&&!["logged_out","login_failed","logging_in"].includes(phase);
 $("login-form").hidden=accountPhase||sessionPhase;$("account-step").hidden=!accountPhase;$("connected-step").hidden=!sessionPhase;
 const loginBusy=loginPending||phase==="logging_in",loginForm=$("login-form");
 loginForm.querySelectorAll("input,button[type=submit]").forEach(control=>{control.disabled=loginBusy;});
 loginForm.setAttribute("aria-busy",String(loginBusy));
 loginForm.querySelector("button").textContent=loginBusy?"登入中…":"登入凱基";
 $("connected-account").textContent=c.selected?`${c.selected.broker_id} · ${c.selected.account}`:"會話尚未選帳，請查看連線狀態";
 $("login-phase-title").textContent=accountPhase?"登入成功 → 選擇帳戶":sessionPhase?"連線狀態與 SDK 輸出":"登入即時輸出";
 for(const [id,active]of [["step-login",!accountPhase&&!sessionPhase],["step-account",accountPhase],["step-ready",sessionPhase]])$(id).classList.toggle("current",active);
 $("account-selector").querySelectorAll("button").forEach(b=>b.disabled=selectionPending||phase==="selecting_account");
 $("reconnect").disabled=["logged_out","login_failed","logging_in","logging_out","logout_failed","selecting_account","reconnecting"].includes(phase);
 $("logout").disabled=["logged_out","logging_in","logging_out"].includes(phase);
 if(diagnostics?.error)$("notice").textContent=diagnostics.error;
}
async function pollDiagnostics(){
 if(diagnosticBusy||closed||shutdownPending)return;diagnosticBusy=true;
 try{diagnostics=await request("/api/diagnostics");const output=$("sdk-output"),content=diagnostics.lines.map(l=>`${new Date(l.time).toLocaleTimeString("zh-TW",{hour12:false})}  ${l.message}`).join("\n")||"等待登入輸出…";if(output.textContent!==content){const bottom=output.scrollTop+output.clientHeight>=output.scrollHeight-30;output.textContent=content;if(bottom)output.scrollTop=output.scrollHeight;}renderConnection();}catch{ $("broker-connection").textContent="券商狀態未知（後端失聯）";$("broker-connection").className="pill bad"; }finally{diagnosticBusy=false;}
}
async function shutdown(cancel){
 if(shutdownPending)return;shutdownPending=true;$("action-dialog").close();$("shutdown-screen").hidden=false;$("shutdown-back").hidden=true;document.querySelector(".shutdown-symbol").textContent="…";$("shutdown-title").textContent="正在結束程式";$("shutdown-message").textContent="停止策略並確認券商登出中…";
 showShutdownOrders([]);
 try{const result=await command("shutdown",{cancel});showShutdownOrders(result.unresolved_orders||[]);if(!result.shutdown||!result.logout_confirmed)throw Error("尚未取得登出與結束確認");closed=true;$("shutdown-message").textContent="已確認登出，等待後端結束…";
  for(let i=0;i<20;i++){await new Promise(r=>setTimeout(r,500));try{await fetch("/api/session",{signal:AbortSignal.timeout(1500)});}catch(e){if(e.name==="TimeoutError")continue;document.querySelector(".shutdown-symbol").textContent="✓";$("shutdown-title").textContent="程式已結束";$("shutdown-message").textContent="可以關閉視窗";$("shutdown-detail").textContent="券商已登出，帳本與稽核紀錄已保存。下方未確認委託請另至券商查核；仍有效的委託可能繼續成交。";return;}}
  throw Error("已確認登出，但尚未確認後端結束，請查看執行視窗。");
 }catch(e){document.querySelector(".shutdown-symbol").textContent="!";$("shutdown-title").textContent="尚未確認程式結束";$("shutdown-message").textContent=e.message;$("shutdown-detail").textContent="請返回工作台核對狀態，勿直接假定已登出或重送交易。";$("shutdown-back").hidden=false;closed=false;}finally{shutdownPending=false;}
}
