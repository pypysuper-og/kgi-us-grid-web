"use strict";
// One form and one backend preview for both editing paths; no duplicate strategy math.
const editorSteps=[
 ["商品與模式",["name","symbol","mode","direction"]],
 ["價格與格線",["lower_price","upper_price","start_price","gap","gap_unit","pause_buys_below_lower"]],
 ["部位與限制",["quantity","min_inventory","max_inventory","initial_inventory","initial_cost","opening_confirmed","max_order_value","daily_buy_limit","end_date","currency","quote_max_age"]],
 ["預覽與確認",[]]
];
let wizard=false,wizardStep=0,editorEpoch=0,lookupRevision=0,lookupTimer=null,editorConsumer=null,verifiedSymbol=null,layoutResult=null,editorBusy=false;
let layoutBasis="count",layoutTimer=null,layoutRevision=0;
const fieldInput=name=>$("strategy-form").elements.namedItem(name);
const marketStates={subscribing:"訂閱中",waiting:"等待本次訂閱首筆成交",receiving:"已接收參考資料・等待新成交",live:"有效成交",stale:"已過期・最後資料僅供參考",time_unverified:"來源時區未知",disconnected:"已斷線・等待重連",error:"行情查核／訂閱異常",unavailable:"訂閱不可用"};
function marketText(entry,symbol){
 if(!state?.owner)return "未登入選帳・無正式行情";
 if(!entry)return `${symbol} 等待商品查核／訂閱`;
 let status=entry.status;
 const q=entry.quote;
 if(q&&status==="live"){
  const ages=state.strategies.filter(s=>s.mode!=="demo"&&s.params.symbol===symbol).map(s=>s.params.quote_max_age),limit=ages.length?Math.min(...ages):15;
  if([q.source_time,q.received_time].some(t=>!t||Date.now()-Date.parse(t)>limit*1000))status="stale";
 }
 if($("connection").textContent==="後端失聯")status="disconnected";
 const label=q?.kind==="trade"?"最後成交":q?.kind==="snapshot"?"快照參考價":"參考價（非新成交）";
 return `${q?`${label} ${number(q.price)} USD · `:""}${marketStates[status]||status}${q?` · ${q.source} · ${q.source_time?timeLabel(q.source_time)+"（本機時間）":q.raw_time+"（時區未知）"}`:""}`;
}
function marketCell(s){const n=el("div",undefined,"market-cell");n.append(el("strong",s.params.symbol));n.append(el("small",s.mode==="demo"?(state.quotes?.[s.id]?`${number(state.quotes[s.id].price)} USD（離線）`:"離線行情"):marketText(state.market?.[s.params.symbol],s.params.symbol)));return n;}

HELP.parameters=["總體參數設定與 CLOV 案例",[
 "模式：離線展示使用本機行情；行情模擬使用正式行情、本機撮合；正式交易須另外登入選帳、啟用實單、對帳及啟動。清單的模式選單只篩選顯示。儲存與預覽不下單。",
 "策略名稱用於辨識；商品代號先用券商 SubOrder 合約清單查核。存在於清單不保證當下可成交，行情權限、交易時段與券商最終受理仍須符合。離線演練可保留未驗證代號。",
 "方向：雙邊買賣／單邊買／單邊賣。只賣已有持股、不放空。每筆股數是每次委託股數；期初股數是分配給策略的既有庫存，不會啟動即買入。",
 "CLOV 案例：起始 4.50、下界 4.00、上界 5.00、固定間距 0.02。整個範圍有 50 個價格間隔、51 個價位；起始價是初始基準，不是立即成交價。",
 "基準 4.50、零持股：跌到 4.48 觸發買 1 股；該單全部成交才將基準移至委託格價 4.48。之後跌到 4.46 可再買，或漲到 4.50 可賣。若零持股先漲至 4.52，不會賣空。",
 "最少保留 0、最多持股 20、期初 0、每筆 1 股：持續下跌且逐筆全部成交時，第 20 筆在 4.10 即碰到持股上限，不會繼續買到 4.00。反彈賣出後才釋放額度。預覽只列部位允許的委託，不是一次掛滿格子。",
 "固定金額以 USD 計；百分比以起始價換算一次固定價差，不隨成交複利重算。自動劃格：間距＝（上界－下界）÷ 格子數。預設起始價為中點，可改；中點或自訂價未落在上下界格線時，實際格線以起始價平移並明示。",
 "連動：改格數會依上下界重算間距；改間距則固定上下界反算格數。改上下界沿用最近選定的計算方式。直接改起始價會停止跟隨中點；可勾選恢復。5.50～7.00 分20格得到0.075，未符合美分精度；可套用15格／0.10或25格／0.06。不會暗中四捨五入。",
 "買價下界／賣價上界預設限制委託限價；例如下界5.50、買單限價5.53，市場5.30仍可能成交在5.30。可勾選『行情低於下界時不新增買單』，回到範圍自動繼續；不會撤銷已送單，也不保證成交價位於界線內。不是停損或自動平倉。",
 "編輯預設保留目前網格基準及部位。例：SOUN起始6.00、間距0.03，買在5.97後只提高每日預算，下一買點仍是5.94，不能重買5.97。只有明確勾選『重設基準為本次起始價』才重設；改間距沿用目前基準。預覽使用本次選定基準及目前持股，儲存不自動啟動。",
 "期初總成本是既有部位合計美元成本，不是每股成本；未知留空，不當成 0。勾選來源確認不是實單開關。",
 "單筆最大金額限制價格×股數，買賣皆適用；每日買入上限按美東成交日累計，不因賣出扣回、不含後補費用。正式交易另受本次會話的帳戶限制。",
 "停止日期以台灣時間含當日；到期只停新單，委託與持股不會自動消失。結算選 TWD 仍以 USD 計算股價、成本、格距與金額限制。行情有效秒數是資料時效門檻，不是更新頻率。",
 "4.48 買 1 股、4.50 賣 1 股僅是 USD 0.02 毛價差，未扣交易費用。任何預覽都不是獲利保證。"
]];
HELP.market=["商品查核與動態行情",[
 "輸入商品後自動查核目前選定帳戶的美股合約清單，再取得快照及訂閱 USQuote Tick。未登入時先完成帳戶與連線；不會替你登入。離線展示可使用未驗證代號。",
 "合約清單確認的是券商列示的美股商品，不是當下保證受理或成交。休市、權限或暫停交易仍可能影響下單。沒有行情不能拿網格價或成本冒充。",
 "畫面區分來源快照、非新成交參考價、有效成交、過期與斷線。Tick 成交量為 0 不算新成交。Unix timestamp 或帶時區時間會自動辨識；來源缺時區時保留原時間與價格、標示時區未知，不要求手選。",
 "建立的非離線策略即使暫停仍持續顯示行情；相同商品共用訂閱，封存且無其他消費者才解除。關閉設定窗釋放編輯需求；登出／結束釋放行情。同帳戶重連自動查核後恢復原監控與剩餘實單額度，等待新有效行情；手動停止、登出及程式重啟不自動啟動。",
 "USQuote 訂閱目前用於展示；交易引擎仍使用 USData 快照及既有新鮮度與正常盤門檻。畫面顯示不是個人交易成交回報，不會改變部位。"
]];

const marketBox=el("section",undefined,"editor-market");marketBox.id="editor-market";
const marketTitle=el("div",undefined,"editor-toolbar");marketTitle.append(el("strong","商品查核與最新行情"),helpButton("market"),button("重新查核",()=>safe(lookupEditor,"symbol-status")),button("帳戶與連線",()=>{$("login-dialog").showModal();renderConnection();}));
const symbolStatus=el("p","請輸入商品代號。","help");symbolStatus.id="symbol-status";symbolStatus.setAttribute("role","status");
const symbolQuote=el("p",undefined,"help");symbolQuote.id="symbol-quote";
marketBox.append(marketTitle,symbolStatus,symbolQuote,el("p","行情時間自動辨識來源 timestamp／時區。缺少時區仍顯示原時間及價格，標示時區未知。","help"));
$("strategy-form").querySelector(".form-grid").append(marketBox);
const priceGrid=$("strategy-form").querySelector(".form-grid"),startLabel=fieldInput("start_price").closest("label");
const lowerGuard=el("label",undefined,"check"),lowerGuardInput=el("input");
lowerGuardInput.type="checkbox";lowerGuardInput.name="pause_buys_below_lower";
lowerGuard.append(lowerGuardInput,el("span","行情低於下界時不新增買單（回到範圍自動繼續，已送單不撤銷）"));
priceGrid.append(lowerGuard);
const anchorEdit=el("section",undefined,"auto-grid"),anchorChoice=el("label",undefined,"check"),resetAnchor=el("input");
anchorEdit.id="anchor-edit";resetAnchor.id="reset-anchor";resetAnchor.type="checkbox";
anchorChoice.append(resetAnchor,el("span","重設基準為本次起始價（未勾選則保留目前基準）"));
const anchorSummary=el("p",undefined,"help");anchorSummary.id="anchor-edit-summary";
anchorEdit.append(anchorChoice,anchorSummary);
priceGrid.insertBefore(anchorEdit,startLabel);
for(const name of ["lower_price","upper_price"])priceGrid.insertBefore(fieldInput(name).closest("label"),startLabel);
priceGrid.insertBefore($("auto-grid"),startLabel);
const layoutSuggestions=el("div",undefined,"editor-toolbar");layoutSuggestions.id="layout-suggestions";$("auto-options").append(layoutSuggestions);

function releaseEditorConsumer(){const old=editorConsumer;editorConsumer=null;if(old)safe(()=>command("market_watch",{consumer:old,release:true}));}
function invalidateSymbol(){lookupRevision++;verifiedSymbol=null;clearTimeout(lookupTimer);releaseEditorConsumer();$("symbol-status").textContent="商品已變更，等待查核。";$("symbol-quote").textContent="";}
async function lookupEditor(){
 const symbol=fieldInput("symbol").value.trim().toUpperCase(),revision=++lookupRevision,epoch=editorEpoch;
 releaseEditorConsumer();verifiedSymbol=null;
 if(!symbol){$("symbol-status").textContent="請輸入商品代號。";return false;}
 if(!state?.owner){$("symbol-status").textContent="未登入選帳，商品尚未經券商驗證。離線展示可繼續；其他模式請先登入。";return false;}
 const consumer=crypto.randomUUID();editorConsumer=consumer;$("symbol-status").textContent=`正在查核 ${symbol}…`;
 try{
  const result=await command("lookup",{symbol,consumer});
  if(revision!==lookupRevision||epoch!==editorEpoch||!$("strategy-dialog").open){await command("market_watch",{consumer,release:true});return false;}
  verifiedSymbol={symbol,generation:result.generation};
  $("symbol-status").textContent=`${symbol} · ${result.market?.name||symbol} · 券商美股合約已確認（實際受理仍依券商）`;
  await refresh();return true;
 }catch(e){if(revision===lookupRevision){$("symbol-status").textContent=e.message;releaseEditorConsumer();}return false;}
}
function renderEditorMarket(){
 if(!$("strategy-dialog").open)return;
 const symbol=fieldInput("symbol").value.trim().toUpperCase();
 if(verifiedSymbol&&(verifiedSymbol.generation!==state.generation||!state.owner)){verifiedSymbol=null;$("symbol-status").textContent="會話已變更，請重新查核商品。";}
 $("symbol-quote").textContent=marketText(state.market?.[symbol],symbol);
}
fieldInput("symbol").addEventListener("input",()=>{invalidateSymbol();lookupTimer=setTimeout(()=>safe(lookupEditor,"symbol-status"),650);});
fieldInput("mode").addEventListener("change",()=>{invalidateSymbol();safe(lookupEditor,"symbol-status");});
$("strategy-dialog").addEventListener("close",()=>{editorEpoch++;invalidateSymbol();});
setInterval(()=>{if(editorConsumer&&$("strategy-dialog").open&&!closed)safe(()=>command("market_watch",{consumer:editorConsumer}));},30000);

function openGridEditor(s=null){
 editing=s;editorEpoch++;invalidateSymbol();wizard=false;wizardStep=0;layoutResult=null;
 $("strategy-form-title").textContent=s?"編輯網格":"新增網格";
 const values=s?{...defaultParams(),...s.params}:{...defaultParams(),name:"",symbol:"",mode:$("mode-filter").value==="all"?"":$("mode-filter").value};
 resetAnchor.checked=false;
 for(const [key,value]of Object.entries(values)){const input=fieldInput(key);if(!input)continue;if(input.type==="checkbox")input.checked=!!value;else input.value=value??"";input.disabled=!!s&&["mode","symbol","initial_inventory","initial_cost"].includes(key);}
 clearTimeout(layoutTimer);layoutRevision++;layoutBasis="count";
 $("auto-enabled").checked=!s;$("grid-intervals").value="20";$("midpoint-start").checked=true;$("auto-result").textContent="";layoutSuggestions.replaceChildren();
 $("form-preview").replaceChildren();$("strategy-error").textContent="";renderEditorStep();$("strategy-dialog").showModal();
 if(values.symbol)safe(lookupEditor,"symbol-status");else $("symbol-status").textContent="請輸入商品代號，登入選帳後將自動查核。";
 if(!s)scheduleLayout();
}
function renderEditorStep(){
 $("editor-mode").textContent=wizard?"切換完整表單":"切換逐步精靈";
 $("wizard-steps").hidden=!wizard;$("wizard-tip").hidden=!wizard;
 $("wizard-steps").replaceChildren(...editorSteps.map(([title],i)=>el("span",`${i+1} ${title}`,i===wizardStep?"current":"")));
 $("wizard-tip").textContent=["先選擇商品及執行模式。登入只用於查核與行情，不會自動下單。","先填上下界與格數，間距及中點自動計算；也可改間距反算格數、改起始價。格子數是價格間隔數。","庫存限制會縮減可買／可賣格子。期初股數是已有部位，不會自動買入。","核對參數與可能委託後儲存；策略保持暫停。上一步可返回修改。"][wizardStep];
 editorSteps.forEach(([,names],i)=>names.forEach(name=>{fieldInput(name).closest("label").hidden=wizard&&i!==wizardStep;}));
 marketBox.hidden=wizard&&wizardStep!==0;$("auto-grid").hidden=wizard&&wizardStep!==1;
 anchorEdit.hidden=!editing||(wizard&&wizardStep!==1);
 if(editing)anchorSummary.textContent=`目前基準 ${number(editing.anchor)}；目前持股 ${position(editing.id)?.quantity??0} 股。${resetAnchor.checked?"儲存時明確重設為起始價；成交與成本仍保留。":"本次編輯保留基準；改預算、結算或間距不會偷偷回到起始價。"}`;
 $("auto-options").hidden=!$("auto-enabled").checked;
 fieldInput("gap").readOnly=false;fieldInput("gap_unit").disabled=$("auto-enabled").checked;
 $("preview-section").hidden=wizard&&wizardStep!==3;
 $("wizard-back").hidden=!wizard;$("wizard-back").disabled=wizardStep===0||editorBusy;
 $("wizard-next").hidden=!wizard||wizardStep===3;
 $("preview").hidden=wizard&&wizardStep!==3;
 document.querySelector("#strategy-form button[type=submit]").hidden=wizard&&wizardStep!==3;
}
function validateFields(step=null){
 for(let i=0;i<editorSteps.length;i++)for(const name of editorSteps[i][1]){
  if(step!==null&&i!==step)continue;
  const input=fieldInput(name);if(!input.checkValidity()){
   if(wizard){wizardStep=i;renderEditorStep();}input.reportValidity();throw Error("請先完成標示的欄位。");
  }
 }
}
function layoutArgs(){return {lower_price:fieldInput("lower_price").value,upper_price:fieldInput("upper_price").value,basis:layoutBasis,...(layoutBasis==="count"?{intervals:Number($("grid-intervals").value)}:{gap:fieldInput("gap").value}),start_price:$("midpoint-start").checked?null:fieldInput("start_price").value};}
function scheduleLayout(){
 clearTimeout(layoutTimer);layoutRevision++;layoutResult=null;layoutSuggestions.replaceChildren();
 $("strategy-error").textContent="";$("form-preview").replaceChildren(el("p","參數已變更，請重新預覽。","help"));
 if(!$("auto-enabled").checked){$("auto-result").textContent="";return;}
 fieldInput("gap_unit").value="amount";$("auto-result").textContent="參數已變更，正在重新計算…";
 layoutTimer=setTimeout(()=>applyLayout(true).catch(()=>{}),250);
}
async function applyLayout(draft=false){
 clearTimeout(layoutTimer);
 if(!$("auto-enabled").checked)return;
 const args=layoutArgs(),epoch=editorEpoch,revision=++layoutRevision,fingerprint=JSON.stringify(args);
 layoutResult=null;layoutSuggestions.replaceChildren();$("auto-result").textContent="正在重新計算…";
 try{
  const result=await command("grid_layout",{...args,draft:true});
  if(epoch!==editorEpoch||revision!==layoutRevision||fingerprint!==JSON.stringify(layoutArgs())||!$("auto-enabled").checked){if(draft)return;throw Error("參數已變更，請再次繼續／預覽。");}
  fieldInput("gap").value=result.gap;fieldInput("gap_unit").value="amount";fieldInput("start_price").value=result.start_price;
  $("grid-intervals").value=result.intervals;
  $("auto-result").textContent=`${layoutBasis==="count"?"依格數算間距":"依間距反算格數"}：${result.intervals} 個間隔${result.levels?`／${result.levels} 個價位`:""}；間距 USD ${result.gap}；起始 ${result.start_price}。${result.valid?(result.anchor_aligned?"":"起始價未落在上下界格線，實際委託格線以起始價平移，可能不抵達界線。"):`目前組合不可儲存：${result.error}。`}`;
  for(const suggestion of result.suggestions)layoutSuggestions.append(button(`改用 ${suggestion.intervals} 格（間距 ${suggestion.gap}）`,()=>{layoutBasis="count";$("grid-intervals").value=suggestion.intervals;scheduleLayout();}));
  if(!draft||!wizard||wizardStep===1)$("strategy-error").textContent=result.error;
  if(!result.valid){if(!draft)throw Error(result.error);return;}
  layoutResult=result;
 }catch(e){
  if(epoch===editorEpoch&&revision===layoutRevision){if(!$("auto-result").textContent.includes("目前組合不可儲存"))$("auto-result").textContent="目前輸入尚無法計算；請核對上下界、格數與間距。";if(!draft||!wizard||wizardStep===1)$("strategy-error").textContent=e.message;}
  if(!draft)throw e;
 }
}
async function requireProduct(){if(fieldInput("mode").value==="demo")return;if(!verifiedSymbol||verifiedSymbol.symbol!==fieldInput("symbol").value.trim().toUpperCase()||verifiedSymbol.generation!==state.generation){if(!await lookupEditor())throw Error("請先登入選帳，完成商品查核後繼續。");}}
async function previewEditor(){
 await applyLayout();validateFields();await requireProduct();
 const epoch=editorEpoch,params=readParams(),anchorReset=resetAnchor.checked,fingerprint=JSON.stringify([params,anchorReset]);
 const result=await command("preview",{params,...(editing?{strategy_id:editing.id,revision:editing.revision,reset_anchor:anchorReset}:{})});
 if(epoch!==editorEpoch||fingerprint!==JSON.stringify([readParams(),resetAnchor.checked]))return;
 const host=$("form-preview");host.replaceChildren(el("p",`${params.symbol} · ${modeNames[params.mode]} · 基準 ${number(result.anchor)} · 目前持股 ${result.inventory} · 固定價差 USD ${number(result.price_gap)} · 買 ${result.rows.filter(r=>r.side==="buy").length} 格／賣 ${result.rows.filter(r=>r.side==="sell").length} 格`,"summary-line"));
 host.append(table(["方向","限價 USD","股數","規劃後庫存"],result.rows.map(r=>[r.side==="buy"?"買進":"賣出",number(r.price),r.quantity,r.inventory])));
 host.prepend(table(["確認項目","設定"],[
  ["策略／方向",`${params.name} · ${params.direction==="both"?"雙邊買賣":params.direction==="buy"?"單邊買":"單邊賣"}`],
  ["價格範圍 USD",`${params.lower_price}–${params.upper_price}`],["每筆／最少／期初／最多股數",`${params.quantity} / ${params.min_inventory} / ${params.initial_inventory} / ${params.max_inventory}`],
  ["期初總成本 USD",params.initial_cost??"未知"],["單筆／每日買入上限 USD",`${params.max_order_value} / ${params.daily_buy_limit}`],
  ["停止日期（台灣）／結算",`${params.end_date} / ${params.currency}`],["行情有效秒數",params.quote_max_age],
  ["行情低於下界",params.pause_buys_below_lower?"不新增買單，持續監控；已送單不撤銷":"只限制委託限價，可接受較低成交價"],
  ["編輯基準政策",editing?(resetAnchor.checked?"明確重設為起始價":"保留目前基準"):"以起始價建立"]
 ]));
 if($("auto-enabled").checked&&layoutResult&&!layoutResult.anchor_aligned)host.append(el("p","起始價未落在上下界格線，實際委託格線以起始價平移，可能不抵達上下界。","notice"));
 if(!result.rows.length)host.append(el("p","目前方向、部位與界線沒有可規劃的委託；請回上一步檢查。","notice"));
 host.append(el("p","每側最多顯示 500 筆規劃。單筆／每日金額與交易條件另在執行時檢查。","help"));
 if($("auto-enabled").checked&&layoutResult){const d=el("details"),summary=el("summary",`上下界完整參考格線（${layoutResult.levels} 個價位，不代表委託）`);d.append(summary,el("p",layoutResult.prices.map(p=>number(p)).join(" · "),"grid-prices"));host.append(d);}
 $("strategy-error").textContent="";
}
async function saveEditor(){
 if(editorBusy)return;
 if(wizard&&wizardStep<3){$("wizard-next").click();return;}
 editorBusy=true;const b=document.querySelector("#strategy-form button[type=submit]");b.disabled=true;
 await safe(async()=>{await applyLayout();validateFields();await requireProduct();const payload={params:readParams()};if(editing)Object.assign(payload,{strategy_id:editing.id,revision:editing.revision,reset_anchor:resetAnchor.checked});const result=await command(editing?"edit":"create",payload);selected=result.id;$("mode-filter").value=payload.params.mode;$("strategy-dialog").close();await refresh();toast("策略已儲存，尚未啟動");},"strategy-error");
 b.disabled=false;editorBusy=false;
}
$("editor-mode").addEventListener("click",()=>{wizard=!wizard;wizardStep=0;renderEditorStep();});
$("wizard-back").addEventListener("click",()=>{if(wizardStep>0)wizardStep--;$("strategy-error").textContent="";renderEditorStep();});
$("wizard-next").addEventListener("click",()=>safe(async()=>{
 if(editorBusy)return;editorBusy=true;$("wizard-next").disabled=true;
 try{if(wizardStep===1)await applyLayout();validateFields(wizardStep);if(wizardStep===0)await requireProduct();if(wizardStep===2)await previewEditor();wizardStep=Math.min(3,wizardStep+1);$("strategy-error").textContent="";renderEditorStep();}finally{editorBusy=false;$("wizard-next").disabled=false;renderEditorStep();}
},"strategy-error"));
for(const id of ["auto-enabled","midpoint-start"])$(id).addEventListener("change",()=>{if(id==="auto-enabled")layoutBasis="count";renderEditorStep();scheduleLayout();});
$("grid-intervals").addEventListener("input",()=>{layoutBasis="count";scheduleLayout();});
for(const name of ["lower_price","upper_price","start_price","gap"])fieldInput(name).addEventListener("input",()=>{if(name==="start_price")$("midpoint-start").checked=false;if(name==="gap")layoutBasis="gap";scheduleLayout();});
$("strategy-form").addEventListener("input",()=>{$("form-preview").replaceChildren(el("p","參數已變更，請重新預覽。","help"));});
resetAnchor.addEventListener("change",renderEditorStep);
