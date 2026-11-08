"use strict";
let simulationExitStrategy=null,simulationExitPreview=null,simulationExitOperation=null,simulationExitBusy=false,simulationExitApplied=false;
const simulationExitDialog=el("dialog");simulationExitDialog.id="simulation-exit-dialog";simulationExitDialog.setAttribute("aria-labelledby","simulation-exit-title");
const simulationExitHead=el("div",undefined,"dialog-head"),simulationExitTitle=el("h2","結束模擬並封存"),simulationExitClose=button("×",()=>simulationExitDialog.close(),"close");simulationExitTitle.id="simulation-exit-title";simulationExitClose.setAttribute("aria-label","關閉模擬結束預覽");simulationExitHead.append(simulationExitTitle,simulationExitClose);
const simulationExitBody=el("div"),simulationExitError=el("p",undefined,"error"),simulationExitAckLabel=el("label",undefined,"check"),simulationExitAck=el("input");simulationExitBody.id="simulation-exit-preview";simulationExitError.id="simulation-exit-error";simulationExitError.setAttribute("role","alert");simulationExitAck.type="checkbox";simulationExitAck.id="simulation-exit-ack";
simulationExitAckLabel.append(simulationExitAck,el("span","我確認結束這組模擬部位並封存；原始紀錄保留，不產生賣出成交或損益。"));
const simulationExitRead=button("重新預覽",()=>runSimulationExit(false)),simulationExitConfirm=button("確認結束模擬並封存",()=>runSimulationExit(true),"danger"),simulationExitStatus=button("查詢本次處理結果",()=>runSimulationExit("status")),simulationExitBack=button("返回工作台",()=>simulationExitDialog.close());simulationExitRead.id="simulation-exit-read";simulationExitConfirm.id="simulation-exit-confirm";simulationExitStatus.id="simulation-exit-status";simulationExitStatus.hidden=true;
const simulationExitActions=el("div",undefined,"dialog-actions");simulationExitActions.append(simulationExitBack,simulationExitRead,simulationExitStatus,simulationExitConfirm);
simulationExitDialog.append(simulationExitHead,el("p","這是本機模擬的退場操作。若要保留部位讓新策略管理，請返回清單使用「交接部位」。結束後可在「已封存策略」查看歷史。","help"),simulationExitBody,simulationExitError,simulationExitAckLabel,simulationExitActions);document.body.append(simulationExitDialog);
function renderSimulationExit(){
 const pending=!!simulationExitOperation&&!simulationExitApplied;
 simulationExitRead.disabled=simulationExitBusy||pending;simulationExitConfirm.disabled=simulationExitBusy||pending||!simulationExitPreview||!simulationExitAck.checked;
 simulationExitAck.disabled=simulationExitBusy||pending;simulationExitStatus.disabled=simulationExitBusy;simulationExitStatus.hidden=!pending;
 simulationExitClose.disabled=simulationExitBack.disabled=simulationExitBusy;simulationExitBody.setAttribute("aria-busy",String(simulationExitBusy));
 simulationExitRead.textContent=simulationExitBusy?"正在處理…":"重新預覽";simulationExitRead.classList.toggle("busy-action",simulationExitBusy);
}
function showSimulationExit(receipt){
 const cost=receipt.cost===null?"未知（不當作0）":`USD ${number(receipt.cost)}`;
 simulationExitBody.replaceChildren(el("h3",`${receipt.name} · ${modeNames[receipt.mode]} · ${receipt.symbol}`),table(["項目","處理前","處理後"],[["策略持股",`${receipt.quantity} 股`,"0 股"],["策略剩餘成本",cost,"USD 0"],["同商品模擬帳本",`${receipt.book_before} 股`,`${receipt.book_after} 股`],["策略狀態","已暫停","已封存"]]),el("p",receipt.applied?"已結束模擬並封存。歷史仍保留，可從清單的「已封存策略」查閱。":"請核對預覽，勾選同意後才會寫入。","help"));
}
async function openSimulationExit(strategy){
 if(simulationExitBusy)return;
 if(simulationExitOperation&&!simulationExitApplied){if(!simulationExitDialog.open)simulationExitDialog.showModal();simulationExitError.textContent="請先查詢上一筆處理結果，再開始新的操作。";return;}
 simulationExitStrategy=strategy;simulationExitPreview=null;simulationExitOperation=null;simulationExitApplied=false;simulationExitAck.checked=false;simulationExitAckLabel.hidden=simulationExitConfirm.hidden=simulationExitRead.hidden=false;simulationExitBody.replaceChildren();
 if(!simulationExitDialog.open)simulationExitDialog.showModal();await runSimulationExit(false);
}
async function runSimulationExit(confirm){
 if(simulationExitBusy)return;simulationExitBusy=true;simulationExitError.textContent="";renderSimulationExit();
 try{
  let receipt;
  if(confirm==="status"){
   const result=await command("simulation_exit_status",{operation_id:simulationExitOperation});
   if(!result.found){simulationExitOperation=null;simulationExitError.textContent="尚無完成紀錄。請先核對工作台，再重新預覽；不會自動重送。";return;}
   receipt=result.receipt;
  }else if(confirm){
   const p=simulationExitPreview;simulationExitOperation=p.operation_id;
   receipt=await command("simulation_exit",{strategy_id:p.strategy_id,confirm:true,version:p.version,operation_id:p.operation_id});
  }else{
   simulationExitPreview=null;simulationExitAck.checked=false;
   receipt=await command("simulation_exit",{strategy_id:simulationExitStrategy.id});simulationExitPreview=receipt;
  }
  showSimulationExit(receipt);
  if(receipt.applied){simulationExitApplied=true;simulationExitPreview=null;simulationExitAckLabel.hidden=simulationExitConfirm.hidden=simulationExitRead.hidden=true;await refresh();}
 }catch(e){simulationExitError.textContent=e.message;simulationExitPreview=null;simulationExitAck.checked=false;}
 finally{simulationExitBusy=false;renderSimulationExit();}
}
simulationExitAck.addEventListener("change",renderSimulationExit);
simulationExitDialog.addEventListener("cancel",event=>{if(simulationExitBusy)event.preventDefault();});
