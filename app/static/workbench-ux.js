"use strict";
const historyViews=new Map();
const searchInput=el("input");searchInput.id="strategy-search";searchInput.type="search";searchInput.placeholder="搜尋名稱或商品";searchInput.setAttribute("aria-label","搜尋策略名稱或商品");
$("mode-filter").before(searchInput);searchInput.addEventListener("input",()=>{if(state)render();});
$("mode-filter").addEventListener("change",()=>{if(state)render();});
const statusFilter=el("select");statusFilter.id="status-filter";statusFilter.setAttribute("aria-label","篩選策略封存狀態");
for(const [value,label]of [["current","使用中策略"],["archived","已封存策略"],["all","全部（含封存）"]]){const option=el("option",label);option.value=value;statusFilter.append(option);}
$("mode-filter").after(statusFilter);statusFilter.addEventListener("change",()=>{if(state)render();});
function visibleStrategies(){const query=searchInput.value.trim().toLocaleLowerCase();return state.strategies.filter(s=>(statusFilter.value==="all"||(statusFilter.value==="archived"?s.status==="archived":s.status!=="archived"))&&($("mode-filter").value==="all"||s.mode===$("mode-filter").value)&&(!query||`${s.params.name} ${s.params.symbol}`.toLocaleLowerCase().includes(query)));}

const moreMenu=el("details",undefined,"more-menu");moreMenu.id="more-actions";
const moreToggle=el("summary"),moreIcon=el("span","⋯","more-menu-icon"),moreArrow=el("span","▾","more-menu-arrow");
moreIcon.setAttribute("aria-hidden","true");moreArrow.setAttribute("aria-hidden","true");moreToggle.append(moreIcon,el("span","更多功能"),moreArrow);moreToggle.setAttribute("aria-expanded","false");moreToggle.setAttribute("aria-controls","more-actions-panel");
moreMenu.append(moreToggle);const moreBody=el("div",undefined,"more-actions");moreBody.id="more-actions-panel";
for(const node of [$("quote-open"),$("backup"),$("import-open"),$("layout-open"),$("live-open")]){const help=node.nextElementSibling?.matches(".help-trigger")?node.nextElementSibling:null,row=el("div",undefined,"more-action-row");row.append(node);if(help)row.append(help);moreBody.append(row);}
moreMenu.append(moreBody);$("new-strategy").before(moreMenu);
function alignMoreMenu(){
 if(!moreMenu.open)return;moreBody.style.transform="";
 const bounds=moreBody.getBoundingClientRect(),shift=bounds.left<12?12-bounds.left:bounds.right>innerWidth-12?innerWidth-12-bounds.right:0;
 if(shift)moreBody.style.transform=`translateX(${shift}px)`;
}
moreMenu.addEventListener("toggle",()=>{moreToggle.setAttribute("aria-expanded",String(moreMenu.open));alignMoreMenu();});
window.addEventListener("resize",alignMoreMenu);
moreBody.addEventListener("click",e=>{if(e.target.closest("button"))moreMenu.open=false;});
document.addEventListener("click",e=>{if(!moreMenu.contains(e.target))moreMenu.open=false;});
moreMenu.addEventListener("keydown",e=>{if(e.key==="Escape"){moreMenu.open=false;moreMenu.querySelector("summary").focus();}});

const attentionDialog=el("dialog");attentionDialog.id="attention-dialog";attentionDialog.setAttribute("aria-labelledby","attention-title");
const attentionHead=el("div",undefined,"dialog-head"),attentionTitle=el("h2","需要您處理的項目"),attentionBody=el("div");attentionTitle.id="attention-title";attentionBody.id="attention-body";
attentionHead.append(attentionTitle,button("×",()=>attentionDialog.close(),"close"));attentionDialog.append(attentionHead,attentionBody);document.body.append(attentionDialog);
const attentionButton=button("查看",()=>{renderAttention();attentionDialog.showModal();},"ghost");attentionButton.id="attention-open";attentionButton.setAttribute("aria-label","查看需處理項目");$("metric-attention").closest("article").append(attentionButton);
let attentionFingerprint="";
function renderAttention(){
 const items=WorkspaceView.attentionItems(state);$("metric-attention").textContent=items.length;
 const fingerprint=JSON.stringify(items);if(fingerprint===attentionFingerprint)return;attentionFingerprint=fingerprint;attentionBody.replaceChildren();
 if(!items.length)attentionBody.append(el("p","目前沒有需要人工處理的項目。正常等待行情、休市及手動暫停會留在各策略狀態中。","help"));
 for(const item of items){const row=el("section",undefined,"attention-entry"),copy=el("div");copy.append(el("strong",item.title),el("p",item.reason));
  const label={connection:"帳戶與連線",live:"實單設定",edit:"核對策略設定",reconcile:"核對庫存與委託"}[item.action];
  row.append(copy,button(label,()=>safe(async()=>{
   attentionDialog.close();const s=state.strategies.find(s=>s.id===item.id);
   if(item.action==="connection"||item.action==="reconcile"&&s?.mode!=="demo"&&(!state.owner||s.owner!==`${s.mode}:${state.owner}`)){$("login-open").click();return;}
   if(item.action==="live"){$("live-open").click();return;}
   if(item.action==="edit"){await openStrategy(s);return;}
   if(s){selected=s.id;render();if(s.mode==="demo")await reconcile(s);else await openRecovery([s.id],false);}
  }),"primary"));attentionBody.append(row);
 }
}

const draftBanner=el("section",undefined,"draft-resume");draftBanner.id="draft-resume";draftBanner.hidden=true;document.querySelector(".metrics").before(draftBanner);
let draftFingerprint="";
function renderWorkbenchUX(){
 if(typeof renderInventoryHandoffState==="function")renderInventoryHandoffState();if(typeof renderTransferState==="function")renderTransferState();
 renderAttention();const drafts=state.drafts||[],fingerprint=JSON.stringify(drafts);
 if(draftFingerprint===fingerprint)return;draftFingerprint=fingerprint;draftBanner.hidden=!drafts.length;draftBanner.replaceChildren();if(!drafts.length)return;
 const latest=drafts[0],copy=el("div");copy.append(el("strong",`有 ${drafts.length} 份未完成草稿`),el("span",`最近：${latest.title} · ${timeLabel(latest.updated_at)}`));
 const resume=button("繼續上次草稿",()=>safe(async()=>{resume.disabled=true;try{await loadLibraryEntry(latest);}finally{resume.disabled=false;}}),"primary");resume.id="draft-resume-latest";
 draftBanner.append(copy,resume,button("查看全部草稿",()=>safe(openEditorLibrary)));
}

function buildHistory(body,s,kind){
 const key=`${s.id}:${kind}`;let view=historyViews.get(key);
 if(!view){view={before:null,trail:[],rows:null,nextCursor:null,version:null,busy:false};historyViews.set(key,view);}
 const host=el("section",undefined,"history-view"),controls=el("div",undefined,"history-controls"),status=el("p",undefined,"help"),content=el("div");status.setAttribute("role","status");
 const previous=button("上一頁",()=>{view.before=view.trail.pop();view.rows=null;refreshHistory(s,kind);});
 const next=button("下一頁",()=>{view.trail.push(view.before);view.before=view.nextCursor;view.rows=null;refreshHistory(s,kind);});
 const latest=button("最新紀錄",()=>{view.before=null;view.trail=[];view.rows=null;refreshHistory(s,kind);});
 const reload=button("重新讀取",()=>{view.version=null;refreshHistory(s,kind);});
 controls.append(previous,next,latest,reload);host.append(controls,status,content);body.append(host);Object.assign(view,{host,previous,nextButton:next,latest,reload,status,content});
 paintHistory(s,kind,view);refreshHistory(s,kind);
}
function paintHistory(s,kind,view){
 if(!view.host?.isConnected)return;
 for(const b of [view.previous,view.nextButton,view.latest,view.reload])b.disabled=view.busy;
 view.previous.disabled||=!view.trail.length;view.nextButton.disabled||=view.nextCursor===null||!view.rows;
 // Cursor and button have separate names to keep page navigation independent of renders.
 view.host.setAttribute("aria-busy",String(view.busy));
 view.status.textContent=view.error||`${view.busy?"正在讀取… · ":""}第 ${view.trail.length+1} 頁 · 每頁最多 50 筆`;
 if(view.busy){const spinner=el("span",undefined,"activity-spinner");spinner.setAttribute("aria-hidden","true");view.status.prepend(spinner);}
 const fingerprint=JSON.stringify([view.rows,s.revision]);
 if(view.rows&&view.content._rows!==fingerprint){view.content.replaceChildren(historyTable(s,kind,view.rows));view.content._rows=fingerprint;}
}
async function refreshHistory(s,kind){
 const view=historyViews.get(`${s.id}:${kind}`);if(!view?.host?.isConnected)return;
 if(view.busy){paintHistory(s,kind,view);return;}
 if(view.rows&&view.version===state.history_version){paintHistory(s,kind,view);return;}
 const version=state.history_version,before=view.before;view.busy=true;view.error="";paintHistory(s,kind,view);
 try{const result=await command("history",{strategy_id:s.id,kind,before});view.rows=result.rows;view.nextCursor=result.next;view.version=version;}
 catch(e){view.error=e.message;view.version=version;view.rows??=[];}
 finally{view.busy=false;paintHistory(s,kind,view);}
}

let stateTimer,diagnosticsTimer,pollEpoch=0,nativeHidden=false;
function pollingSchedule(){return WorkspaceView.pollingDelays(document.hidden||nativeHidden,loginPending||["logging_in","selecting_account","reconnecting","logging_out"].includes(diagnostics?.connection?.phase),$("login-dialog").open);}
function startWorkbenchPolling(immediate=false){
 const epoch=++pollEpoch;clearTimeout(stateTimer);clearTimeout(diagnosticsTimer);
 const schedule=(kind,fn)=>{const timer=setTimeout(async()=>{await fn();if(epoch===pollEpoch&&!closed)schedule(kind,fn);},pollingSchedule()[kind]);if(kind==="state")stateTimer=timer;else diagnosticsTimer=timer;};
 if(immediate&&!document.hidden&&!nativeHidden){refresh();pollDiagnostics();}
 if(!closed){schedule("state",refresh);schedule("diagnostics",pollDiagnostics);}
}
window.setWorkbenchHidden=value=>{nativeHidden=!!value;startWorkbenchPolling(!nativeHidden);};
document.addEventListener("visibilitychange",()=>startWorkbenchPolling(true));
$("login-open").addEventListener("click",()=>startWorkbenchPolling(true));
$("login-form").addEventListener("submit",()=>startWorkbenchPolling());
$("login-dialog").addEventListener("close",()=>startWorkbenchPolling());
