"use strict";
let draftSession=null,draftTimer=null;
const libraryDialog=el("dialog");libraryDialog.id="library-dialog";libraryDialog.setAttribute("aria-labelledby","library-title");
const libraryHead=el("div",undefined,"dialog-head"),libraryTitle=el("h2","草稿與參數模板");libraryTitle.id="library-title";
libraryHead.append(libraryTitle,button("×",()=>libraryDialog.close(),"close"));
const libraryNote=el("p","草稿保留未完成輸入；模板與複製只帶參數，期初持股、成本及最少保留股數歸零，不帶委託或實單授權。","help");
const libraryActions=el("div",undefined,"editor-toolbar"),libraryBody=el("div"),libraryError=el("p",undefined,"error");libraryBody.id="library-body";libraryError.id="library-error";libraryError.setAttribute("role","alert");
const templateLabel=el("label","模板名稱"),templateName=el("input");templateName.id="template-name";templateName.maxLength=60;templateLabel.append(templateName);
const templateSave=button("儲存目前參數為模板",()=>safe(saveParameterTemplate,"library-error"));templateSave.id="template-save";
libraryActions.append(button("儲存目前草稿",()=>safe(async()=>{await flushEditorDraft(true);await renderLibrary();},"library-error")),button("重新整理",()=>safe(renderLibrary,"library-error")));
libraryDialog.append(libraryHead,libraryNote,libraryActions,templateLabel,templateSave,libraryBody,libraryError);document.body.append(libraryDialog);
const draftStatus=el("span","輸入後自動保存草稿","help");draftStatus.id="draft-status";draftStatus.setAttribute("role","status");
const editorLibraryButton=button("草稿／模板",()=>safe(openEditorLibrary,"strategy-error"));editorLibraryButton.id="editor-library";
$("strategy-form").querySelector(".editor-toolbar").append(editorLibraryButton,draftStatus);
const libraryOpen=button("草稿／模板",()=>safe(openEditorLibrary));libraryOpen.id="library-open";$("new-strategy").before(libraryOpen);

function captureDraft(){
 const params={};for(const input of $("strategy-form").elements)if(input.name)params[input.name]=input.type==="checkbox"?input.checked:input.value;
 return {params,layout:{wizard,step:wizardStep,auto:$("auto-enabled").checked,intervals:$("grid-intervals").value,midpoint:$("midpoint-start").checked,basis:layoutBasis,reset_anchor:resetAnchor.checked,advanced:$("advanced-settings").open,opening:$("opening-settings").open},base:editing?{id:editing.id,revision:editing.revision}:null};
}
function editorLibraryOpened(seed){
 clearTimeout(draftTimer);
 draftSession={id:seed?.entry?.kind==="draft"?seed.entry.id:null,revision:seed?.entry?.kind==="draft"?seed.entry.revision:null,tail:Promise.resolve(),dirty:false,committed:false,last:JSON.stringify(captureDraft())};
 draftStatus.textContent=seed?.entry?.kind==="draft"?"草稿已載入，修改後自動保存":"輸入後自動保存草稿";
}
function markEditorDraftDirty(){
 if(!draftSession||draftSession.committed||!$("strategy-dialog").open)return;
 draftSession.dirty=true;draftStatus.textContent="草稿有變更，等待保存…";clearTimeout(draftTimer);
 draftTimer=setTimeout(()=>flushEditorDraft().catch(()=>{}),800);
}
async function flushEditorDraft(force=false){
 clearTimeout(draftTimer);const session=draftSession;if(!session||session.committed||!force&&!session.dirty)return;
 const data=captureDraft(),fingerprint=JSON.stringify(data);
 session.tail=session.tail.catch(()=>{}).then(async()=>{
  if(session.committed||!force&&session.last===fingerprint)return;
  if(draftSession===session)draftStatus.textContent="正在保存草稿…";
  try{
   const result=await command("library",{operation:"save",kind:"draft",data,entry_id:session.id,revision:session.revision});
   session.id=result.id;session.revision=result.revision;session.last=fingerprint;
   if(draftSession===session){session.dirty=JSON.stringify(captureDraft())!==fingerprint;draftStatus.textContent=session.dirty?"仍有變更，稍後繼續保存":"草稿已保存至本機";if(session.dirty&&$("strategy-dialog").open)draftTimer=setTimeout(()=>flushEditorDraft().catch(()=>{}),800);}
  }catch(e){if(draftSession===session)draftStatus.textContent=`草稿未保存：${e.message}`;throw e;}
 });
 return session.tail;
}
async function finishEditorDraft(){
 const session=draftSession;if(!session)return;session.committed=true;clearTimeout(draftTimer);
 try{await session.tail;if(session.id)await command("library",{operation:"delete",entry_id:session.id,revision:session.revision});}
 catch{toast("策略已儲存；舊草稿未能移除，可稍後在草稿清單整理。");}
}
function seedLayout(params){
 const gap=Number(params.gap)*(params.gap_unit==="percent"?Number(params.start_price)/100:1),count=(Number(params.upper_price)-Number(params.lower_price))/gap,rounded=Math.round(count);
 const auto=Number.isFinite(count)&&rounded>=1&&rounded<=1000&&Math.abs(count-rounded)<1e-7&&params.gap_unit!=="percent";
 return {auto,intervals:String(auto?rounded:20),midpoint:false,basis:"count",advanced:!auto};
}
async function copyStrategy(s){
 const result=await command("copy_params",{strategy_id:s.id,revision:s.revision});
 if($("strategy-dialog").open)await flushEditorDraft();
 openGridEditor(null,{params:result.params,layout:seedLayout(result.params)});
 toast("已複製參數；期初持股與成本已清空，儲存後保持暫停。");
}
async function openEditorLibrary(){
 libraryError.textContent="";const available=$("strategy-dialog").open;
 libraryActions.firstElementChild.disabled=!available;templateName.disabled=!available;templateSave.disabled=!available;
 templateName.value=available?fieldInput("name").value:"";
 if(!libraryDialog.open)libraryDialog.showModal();await renderLibrary();
}
async function renderLibrary(){
 const result=await command("library",{operation:"list"});libraryBody.replaceChildren();
 for(const kind of ["draft","template"]){
  const group=el("section",undefined,"library-group");group.append(el("h3",kind==="draft"?"未完成草稿":"參數模板"));
  const entries=result.entries.filter(e=>e.kind===kind);
  if(!entries.length)group.append(el("p",kind==="draft"?"尚無草稿；設定時會自動保存。":"尚無模板；可在設定視窗保存常用參數。","help"));
  for(const entry of entries){const row=el("div",undefined,"library-entry"),info=el("div");info.append(el("strong",entry.title),el("small",timeLabel(entry.updated_at)));row.dataset.entryId=entry.id;row.append(info,button(kind==="draft"?"繼續編輯":"套用參數",()=>safe(()=>loadLibraryEntry(entry),"library-error")),button("刪除",()=>{actionDialog("刪除草稿／模板",`刪除「${entry.title}」？不會更動任何策略、持股或委託。`,[{text:"確認刪除",fn:()=>deleteLibraryEntry(entry)}]);}));group.append(row);}
  libraryBody.append(group);
 }
}
async function deleteLibraryEntry(entry){
 const session=draftSession?.id===entry.id?draftSession:null;
 const remove=async()=>{
  await command("library",{operation:"delete",entry_id:entry.id,revision:entry.revision});
  if(session){session.id=null;session.revision=null;session.last="";session.dirty=false;if(draftSession===session)draftStatus.textContent="草稿已刪除；繼續輸入後另存新草稿";}
 };
 if(session){clearTimeout(draftTimer);session.tail=session.tail.catch(()=>{}).then(remove);await session.tail;}else await remove();
 await renderLibrary();
}
async function loadLibraryEntry(entry){
 const result=await command("library",{operation:"get",entry_id:entry.id});
 if($("strategy-dialog").open)await flushEditorDraft();
 let s=null,params=result.data.params,layout=result.data.layout||seedLayout(params);
 if(result.kind==="draft"&&result.data.base){
  await refresh();s=state.strategies.find(s=>s.id===result.data.base.id);
  if(!s||s.revision!==result.data.base.revision||s.status!=="paused")throw Error("原策略已變更或不再暫停；草稿仍保留，請先回到原策略核對。");
  await takeOverRecovery(s);
 }
 libraryDialog.close();openGridEditor(s,{params,layout,entry:result});toast(result.kind==="draft"?"已恢復草稿，尚未儲存為策略":"已套用模板；期初持股為零，尚未啟動。");
}
async function saveParameterTemplate(){
 libraryError.textContent="";await applyLayout();validateFields();
 await command("library",{operation:"save",kind:"template",title:templateName.value,data:{params:readParams()}});await renderLibrary();toast("模板已保存，未建立或啟動策略。");
}
$("strategy-form").addEventListener("input",markEditorDraftDirty);
$("strategy-form").addEventListener("change",markEditorDraftDirty);
for(const id of ["editor-mode","wizard-back","wizard-next"])$(id).addEventListener("click",()=>setTimeout(markEditorDraftDirty,0));
$("strategy-dialog").addEventListener("close",()=>{clearTimeout(draftTimer);if(draftSession?.dirty&&!draftSession.committed)flushEditorDraft().catch(e=>toast(`草稿未保存：${e.message}`));});
