"use strict";
let handoffPreview=null,handoffBusy=false,handoffApplying=false,handoffApplied=false,handoffOperation=null;
const handoffDialog=el("dialog");handoffDialog.id="inventory-handoff-dialog";handoffDialog.setAttribute("aria-labelledby","handoff-title");
const handoffHead=el("div",undefined,"dialog-head"),handoffTitle=el("h2","交接既有部位"),handoffClose=button("×",()=>handoffDialog.close(),"close");handoffTitle.id="handoff-title";handoffClose.setAttribute("aria-label","關閉部位交接");handoffHead.append(handoffTitle,handoffClose);
const handoffNote=el("p","僅調整本機策略的部位歸屬，不會買賣或移動券商資產。舊策略的成交與已實現損益歷史保留，新策略承接剩餘部位與原成本；完成後兩邊保持暫停。","help");
const handoffForm=el("div",undefined,"form-grid"),handoffSource=el("select"),handoffTarget=el("select");handoffSource.id="handoff-source";handoffTarget.id="handoff-target";
for(const [text,control]of [["來源策略（交接全部剩餘部位）",handoffSource],["接管策略（同帳戶、同商品）",handoffTarget]]){const label=el("label",text);label.append(control);handoffForm.append(label);}
const handoffDuplicateLabel=el("label",undefined,"check handoff-duplicate"),handoffDuplicate=el("input");handoffDuplicate.type="checkbox";handoffDuplicate.id="handoff-duplicate";handoffDuplicateLabel.append(handoffDuplicate,el("span","接管策略登記的是同一批持股；確認交接時更正這筆重複期初登記，並承接來源成本。"));
const handoffRead=button("查核交接預覽",()=>runHandoff(previewHandoff)),handoffLogin=button("帳戶與連線",()=>$("login-open").click()),handoffStatus=button("查詢本次交接結果",()=>runHandoff(checkHandoffStatus));handoffRead.id="handoff-read";handoffStatus.id="handoff-status";handoffStatus.hidden=true;
const handoffActions=el("div",undefined,"handoff-actions");handoffActions.append(handoffRead,handoffLogin,handoffStatus);
const handoffBody=el("div"),handoffError=el("p",undefined,"error"),handoffAckLabel=el("label",undefined,"check"),handoffAck=el("input");handoffBody.id="handoff-preview";handoffError.id="handoff-error";handoffError.setAttribute("role","alert");handoffAck.type="checkbox";handoffAck.id="handoff-ack";handoffAckLabel.append(handoffAck,el("span","我已核對來源、接管策略、股數與成本；同意依本次預覽交接，完成後另行查核與啟動。"));
const handoffConfirm=button("確認交接，保持暫停",()=>runHandoff(applyHandoff,true),"primary"),handoffBack=button("回工作台",()=>handoffDialog.close());handoffConfirm.id="handoff-confirm";const handoffFooter=el("div",undefined,"dialog-actions");handoffFooter.append(handoffBack,handoffConfirm);
handoffDialog.append(handoffHead,handoffNote,handoffForm,handoffDuplicateLabel,handoffActions,handoffBody,handoffError,handoffAckLabel,handoffFooter);document.body.append(handoffDialog);

function handoffStrategies(){return(state?.strategies||[]).filter(s=>s.status!=="archived"&&(s.mode!=="live"||s.owner===`live:${state?.owner}`));}
function handoffOption(select,text,value){const option=el("option",text);option.value=value;select.append(option);}
function fillHandoffTargets(preferred=null){
 const source=state?.strategies.find(s=>s.id===handoffSource.value),previous=preferred||handoffTarget.value;handoffTarget.replaceChildren();handoffOption(handoffTarget,"請選擇接管策略","");
 const targets=handoffStrategies().filter(s=>s.id!==source?.id&&s.mode===source?.mode&&s.owner===source?.owner&&s.params.symbol===source?.params.symbol);
 for(const s of targets)handoffOption(handoffTarget,`${s.params.name} · ${s.params.symbol} · 目前 ${position(s.id)?.quantity??0} 股`,s.id);
 handoffTarget.value=targets.some(s=>s.id===previous)?previous:targets.length===1?targets[0].id:"";
}
function invalidateHandoff(){
 handoffPreview=null;handoffAck.checked=false;handoffStatus.hidden=!handoffOperation;
 const live=state?.strategies.find(s=>s.id===handoffSource.value)?.mode==="live";handoffLogin.hidden=!live;
 handoffBody.replaceChildren(el("p",live?"先查核券商庫存與委託，核對交接前後的股數及成本後再確認。":"查核本機模擬庫存與委託；僅能交給相同模式、帳戶與商品的策略，不需登入或查詢券商。","help"));
 handoffDuplicateLabel.hidden=!(position(handoffTarget.value)?.quantity>0);renderInventoryHandoffState();
}
function openInventoryHandoff(source=null,target=null){
 if(handoffBusy)return;
 if(handoffOperation&&!handoffApplied){if(!handoffDialog.open)handoffDialog.showModal();handoffError.textContent="請先查詢上一筆交接結果，再進行新的交接。";return;}
 handoffOperation=null;handoffApplied=false;handoffError.textContent="";handoffForm.hidden=false;handoffActions.hidden=false;handoffAckLabel.hidden=false;handoffConfirm.hidden=false;handoffDuplicate.checked=false;
 const sources=handoffStrategies().filter(s=>(position(s.id)?.quantity??0)>0&&s.id!==target?.id);
 handoffSource.replaceChildren();handoffOption(handoffSource,"請選擇來源策略","");
 for(const s of sources)handoffOption(handoffSource,`${s.params.name} · ${modeNames[s.mode]} · ${s.params.symbol} · ${position(s.id).quantity} 股`,s.id);
 const candidates=target?sources.filter(s=>s.mode===target.mode&&s.owner===target.owner&&s.params.symbol===target.params.symbol):sources;
 handoffSource.value=sources.some(s=>s.id===source?.id)?source.id:candidates.length===1?candidates[0].id:"";
 fillHandoffTargets(target?.id);invalidateHandoff();
 if(source?.mode==="live"&&!state?.owner)handoffError.textContent="請先登入並選擇策略所屬帳戶，再重新開啟部位交接。";
 if(!handoffDialog.open)handoffDialog.showModal();
}
function renderInventoryHandoffState(){
 if(!handoffDialog.open&&!handoffForm.children.length)return;
 const context=handoffPreview?.context,stale=context&&(context.owner!==state?.owner||context.generation!==state?.generation);
 const unresolved=!!handoffOperation&&!handoffApplied;
 handoffSource.disabled=handoffTarget.disabled=handoffDuplicate.disabled=handoffBusy||handoffApplied||unresolved;
 handoffRead.disabled=handoffBusy||unresolved||!handoffSource.value||!handoffTarget.value;
 handoffLogin.disabled=handoffStatus.disabled=handoffBusy;
 handoffClose.disabled=handoffBack.disabled=handoffApplying;
 handoffAck.disabled=handoffBusy||handoffApplied;
 handoffConfirm.disabled=handoffBusy||handoffApplied||!handoffPreview?.can_apply||!handoffAck.checked||!!stale;
 if(stale)handoffError.textContent="帳戶會話已變更，請重新查核交接。";
}
async function runHandoff(fn,applying=false){
 if(handoffBusy)return;handoffBusy=true;handoffApplying=applying;handoffError.textContent="";renderInventoryHandoffState();
 handoffBody.setAttribute("aria-busy","true");handoffRead.textContent=applying?"正在確認交接…":"正在查核…";handoffRead.classList.add("busy-action");
 try{await fn();}catch(e){handoffError.textContent=e.message;if(applying){handoffPreview=null;handoffAck.checked=false;handoffStatus.hidden=!handoffOperation;}}
 finally{handoffBusy=false;handoffApplying=false;handoffBody.setAttribute("aria-busy","false");handoffRead.textContent="查核交接預覽";handoffRead.classList.remove("busy-action");renderInventoryHandoffState();}
}
function renderHandoffPreview(receipt){
 const cost=value=>value===null?"未知（不當作0）":`USD ${number(value)}`;
 handoffBody.replaceChildren(table(["策略","交接前股數","交接後股數","交接前成本","交接後成本"],[
  [receipt.source.name,receipt.source.quantity,receipt.source_after,cost(receipt.source.cost),"USD 0"],
  [receipt.target.name,receipt.target.quantity,receipt.target_after,cost(receipt.target.cost),cost(receipt.target_cost_after)]
 ]));
 handoffBody.prepend(el("p",`${receipt.applied?"本次交接紀錄":"交接預覽"} · ${timeLabel(receipt.completed_at||receipt.checked_at)}`,"help"));
 handoffBody.append(el("p",`${receipt.reference_label||"券商持股"} ${receipt.broker_quantity}${receipt.mode&&receipt.mode!=="live"?` → ${receipt.reference_after}`:""} 股；本地總分配 ${receipt.allocated_before} → ${receipt.allocated_after} 股。${receipt.replace_duplicate?"接管策略原期初登記將追加更正，股數不重複累加；原紀錄仍保留。":""}`,"help"));
 handoffBody.append(el("p",receipt.applied?`本次交接完成時，舊 ${receipt.source.name} 為0股，可返回工作台核對後封存。兩邊交接時保持暫停，${receipt.target.name} 請重新查核後啟動；後續狀態以工作台為準。`:receipt.blocker||"請核對上述交接結果，勾選確認後才會寫入。",receipt.applied?"handoff-success":receipt.can_apply?"help":"error"));
}
async function previewHandoff(){
 handoffPreview=null;handoffAck.checked=false;handoffOperation=null;handoffStatus.hidden=true;
 handoffPreview=await command("inventory_handoff",{source_id:handoffSource.value,target_id:handoffTarget.value,replace_duplicate:handoffDuplicate.checked});
 renderHandoffPreview(handoffPreview);
}
async function showHandoffCompleted(receipt){
 handoffApplied=true;handoffPreview=null;handoffAck.checked=false;renderHandoffPreview(receipt);
 handoffForm.hidden=handoffActions.hidden=handoffDuplicateLabel.hidden=handoffAckLabel.hidden=handoffConfirm.hidden=true;
 try{await refresh();}catch(e){handoffError.textContent=`交接已完成；畫面更新失敗：${e.message}。請返回工作台重新整理。`;}
}
async function applyHandoff(){
 const preview=handoffPreview;handoffOperation=preview.operation_id;
 const receipt=await command("inventory_handoff",{source_id:preview.source.id,target_id:preview.target.id,replace_duplicate:preview.replace_duplicate,confirm:true,version:preview.version,operation_id:preview.operation_id});
 await showHandoffCompleted(receipt);
}
async function checkHandoffStatus(){
 const result=await command("inventory_handoff_status",{operation_id:handoffOperation});
 if(result.found)await showHandoffCompleted(result.receipt);
 else{handoffOperation=null;handoffStatus.hidden=true;handoffError.textContent="尚無已完成的交接紀錄；請返回工作台核對部位，再重新預覽。不會自動重送交接。";}
}
handoffSource.addEventListener("change",()=>{handoffDuplicate.checked=false;fillHandoffTargets();invalidateHandoff();});
handoffTarget.addEventListener("change",()=>{handoffDuplicate.checked=false;invalidateHandoff();});
handoffDuplicate.addEventListener("change",invalidateHandoff);handoffAck.addEventListener("change",renderInventoryHandoffState);
handoffDialog.addEventListener("cancel",e=>{if(handoffApplying)e.preventDefault();});
