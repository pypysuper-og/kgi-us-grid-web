"use strict";
let transferDocument=null,transferPreview=null,transferBusy=false,transferApplying=false,transferObjectUrl=null;
const transferDialog=el("dialog");transferDialog.id="strategy-transfer-dialog";transferDialog.setAttribute("aria-labelledby","strategy-transfer-title");
const transferHead=el("div",undefined,"dialog-head"),transferTitle=el("h2","匯入／匯出策略"),transferClose=button("×",()=>transferDialog.close(),"close");transferTitle.id="strategy-transfer-title";transferClose.setAttribute("aria-label","關閉策略匯入與匯出");transferHead.append(transferTitle,transferClose);
const transferTabs=el("nav",undefined,"tabs transfer-tabs"),transferExportTab=button("匯出參數",()=>setTransferTab("export")),transferImportTab=button("匯入檔案",()=>setTransferTab("import"));transferExportTab.id="transfer-tab-export";transferImportTab.id="transfer-tab-import";transferTabs.append(transferExportTab,transferImportTab);
const transferNote=el("p","只攜帶策略參數；不帶庫存、成本、委託、成交、帳戶或實單授權。匯入不會覆寫既有策略，新策略保持暫停。","help");
const transferExportPane=el("section"),transferImportPane=el("section"),transferExportList=el("div",undefined,"transfer-scroll"),transferImportList=el("div",undefined,"transfer-scroll");transferExportList.id="transfer-export-list";transferImportList.id="transfer-import-list";
const transferExportStatus=el("p",undefined,"help"),transferImportStatus=el("p",undefined,"help"),transferFileName=el("p","支援本程式匯出的JSON，以及舊版策略陣列。","help"),transferError=el("p",undefined,"error");transferError.id="transfer-error";transferError.setAttribute("role","alert");
const transferExportButton=button("匯出 JSON",exportTransfer,"primary");transferExportButton.id="transfer-export";
const transferAll=button("全選",()=>{transferExportList.querySelectorAll("input").forEach(n=>n.checked=true);renderTransferState();}),transferClear=button("清除選擇",()=>{transferExportList.querySelectorAll("input").forEach(n=>n.checked=false);renderTransferState();}),transferRefresh=button("重新整理",()=>{if(state)renderTransferExports();});
const transferExportActions=el("div",undefined,"transfer-actions");transferExportActions.append(transferAll,transferClear,transferRefresh,transferExportButton);transferExportPane.append(el("p","勾選要匯出的策略，可選單筆或多筆。","help"),transferExportActions,transferExportStatus,transferExportList);
const transferRead=button("選擇 JSON 檔",()=>$("legacy-file").click()),transferRecheck=button("重新預覽",()=>runTransfer(previewTransfer)),transferLogin=button("帳戶與連線",()=>$("login-open").click());transferRead.id="transfer-read";transferRecheck.id="transfer-recheck";
const transferConfirm=button("確認匯入",()=>runTransfer(applyTransfer,true),"primary");transferConfirm.id="transfer-confirm";
const transferImportActions=el("div",undefined,"transfer-actions");transferImportActions.append(transferRead,transferRecheck,transferLogin,transferConfirm);transferImportPane.append(transferFileName,transferImportActions,transferImportStatus,transferImportList,$("legacy-file"));
transferDialog.append(transferHead,transferTabs,transferNote,transferExportPane,transferImportPane,transferError);document.body.append(transferDialog);
transferExportList.addEventListener("change",renderTransferState);transferImportList.addEventListener("change",renderTransferState);
transferDialog.addEventListener("cancel",e=>{if(transferApplying)e.preventDefault();});

function setTransferTab(tab){
 if(transferBusy)return;transferExportPane.hidden=tab!=="export";transferImportPane.hidden=tab!=="import";
 transferExportTab.classList.toggle("selected",tab==="export");transferImportTab.classList.toggle("selected",tab==="import");transferError.textContent="";renderTransferState();
}
function openStrategyTransfer(tab="export"){
 if(transferBusy)return;renderTransferExports();setTransferTab(tab);if(!transferDialog.open)transferDialog.showModal();
}
function renderTransferExports(){
 const strategies=state?.strategies.filter(s=>s.status!=="archived")||[];
 transferExportList.replaceChildren(table(["匯出","策略","商品","模式"],strategies.map(s=>{
  const input=el("input");input.type="checkbox";input.checked=s.id===selected;input.dataset.strategyId=s.id;input.dataset.revision=s.revision;input.setAttribute("aria-label",`匯出 ${s.params.name}`);
  return [input,s.params.name,s.params.symbol,modeNames[s.mode]];
 })));
 renderTransferState();
}
function renderTransferState(){
 if(!transferDialog.open&&!transferExportList.children.length)return;
 const exports=[...transferExportList.querySelectorAll("input:checked")],imports=[...transferImportList.querySelectorAll("input:checked")];
 const available=15-(state?.strategies.filter(s=>s.status!=="archived").length||0),context=transferPreview?.context;
 const stale=context&&(context.owner!==state?.owner||context.generation!==state?.generation);
 transferExportStatus.textContent=`已選 ${exports.length} 組；一次最多15組。`;
 transferExportButton.textContent=`匯出 JSON${exports.length?`（${exports.length} 組）`:""}`;
 transferExportButton.disabled=transferBusy||!exports.length||exports.length>15;
 transferConfirm.textContent=imports.length?`確認匯入 ${imports.length} 組（保持暫停）`:"確認匯入";
 transferConfirm.disabled=transferBusy||!transferPreview||!imports.length||imports.length>available||!!stale;
 transferRecheck.disabled=transferBusy||!transferDocument;
 for(const control of [transferAll,transferClear,transferRefresh,transferRead,transferLogin,transferExportTab,transferImportTab])control.disabled=transferBusy;
 transferClose.disabled=transferApplying;$("legacy-file").disabled=transferBusy;
 transferDialog.querySelectorAll('input[type="checkbox"]').forEach(n=>n.disabled=transferBusy||n.dataset.blocked==="true");
 if(transferPreview){const rows=transferPreview.rows;transferImportStatus.textContent=stale?"帳戶會話已變更，請重新預覽。":`${transferPreview.format==="legacy"?"舊格式 → 離線展示":`策略檔案 ${transferPreview.format}`} · 已選 ${imports.length} 組；目前最多可新增 ${Math.max(0,available)} 組。${rows.some(r=>r.error)?"有未通過檢查的項目，請核對下方原因。":""}`;}
}
async function runTransfer(fn,applying=false){
 if(transferBusy)return;transferBusy=true;transferApplying=applying;transferError.textContent="";renderTransferState();
 try{await fn();}catch(e){transferError.textContent=e.message;}finally{transferBusy=false;transferApplying=false;renderTransferState();}
}
function exportTransfer(){return runTransfer(async()=>{
 const selections=[...transferExportList.querySelectorAll("input:checked")].map(n=>({id:n.dataset.strategyId,revision:Number(n.dataset.revision)}));
 const bundle=await command("strategy_export",{selections});
 if(transferObjectUrl)URL.revokeObjectURL(transferObjectUrl);
 transferObjectUrl=URL.createObjectURL(new Blob([JSON.stringify(bundle,null,2)],{type:"application/json;charset=utf-8"}));
 const link=el("a"),stamp=new Intl.DateTimeFormat("sv-SE",{timeZone:"Asia/Taipei",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"}).format(new Date()).replace(/\D/g,"");
 link.href=transferObjectUrl;link.download=`grid-strategies-${stamp}.json`;document.body.append(link);link.click();link.remove();
 toast(`已產生 ${bundle.strategies.length} 組策略的JSON，請依下載／儲存視窗完成保存。`);
});}
window.addEventListener("pagehide",()=>{if(transferObjectUrl)URL.revokeObjectURL(transferObjectUrl);});

function transferParamsNode(p){
 if(!p)return "—";const box=el("div"),details=el("details"),summary=el("summary","完整參數");
 box.append(el("span",`USD ${p.lower_price}–${p.upper_price} · 每筆 ${p.quantity} 股 · ${inventoryLimitLabel(p.max_inventory)}`));
 details.append(summary,table(["項目","設定"],[["起始價／格距",`${p.start_price} / ${p.gap}${p.gap_unit==="percent"?"%（起始價）":" USD"}`],["方向",{both:"雙邊買賣",buy:"單邊買",sell:"單邊賣"}[p.direction]],["結算／停止日期",`${settlementLabel(p.currency)} / ${p.end_date}`],["單筆／每日買入限制",`${limitLabel(p.max_order_value)} / ${limitLabel(p.daily_buy_limit)}`],["行情有效期",`${p.quote_max_age} 秒`],["低於下界",p.pause_buys_below_lower?"不新增買單":"依委託限價界線"]]));box.append(details);return box;
}
function renderTransferImports(preselect){
 transferImportList.replaceChildren(table(["匯入","策略／商品","模式","參數預覽","檢查結果"],transferPreview.rows.map(r=>{
  const input=el("input");input.type="checkbox";input.dataset.index=r.index;input.dataset.blocked=String(!!r.error||!!r.existing||!!r.duplicate);input.checked=preselect&&!r.error&&!r.existing&&!r.duplicate;input.setAttribute("aria-label",`匯入 ${r.name}`);
  return [input,`${r.name}${r.params?` · ${r.params.symbol}`:""}`,r.params?modeNames[r.params.mode]:"—",transferParamsNode(r.params),r.created?"已建立（暫停）":r.error||(r.existing?"此檔已匯入，略過":r.duplicate?"檔內重複，略過":r.note)];
 })));
 renderTransferState();
}
async function previewTransfer(){
 if(!transferDocument)throw Error("請先選擇策略JSON檔");
 transferPreview=await command("strategy_import",{document:transferDocument});renderTransferImports(true);
}
async function applyTransfer(){
 const selected=[...transferImportList.querySelectorAll("input:checked")].map(n=>Number(n.dataset.index));
 transferPreview=await command("strategy_import",{document:transferDocument,apply:true,selected,preview_token:transferPreview.preview_token});
 renderTransferImports(false);await refresh();toast(`已新增 ${transferPreview.created} 組策略，均保持暫停。`);
}
$("import-open").addEventListener("click",()=>openStrategyTransfer());
$("legacy-file").addEventListener("change",e=>{
 const file=e.target.files[0];if(!file||transferBusy)return;openStrategyTransfer("import");
 runTransfer(async()=>{
  transferDocument=null;transferPreview=null;transferImportList.replaceChildren();transferImportStatus.textContent="正在讀取檔案…";transferFileName.textContent=file.name;
  if(file.size>500000)throw Error("策略檔案最多500 KB");
  try{transferDocument=JSON.parse(await file.text());}catch{throw Error("無法讀取JSON格式，請重新選擇策略檔案");}
  await previewTransfer();
 }).finally(()=>{e.target.value="";if(!transferPreview)transferImportStatus.textContent="請選擇有效的策略JSON檔，再預覽確認。";});
});
