"use strict";
(()=>{
let layoutPrefs={left:50,upper:46,font_size:14},layoutLoaded=false,layoutPending=false,layoutSaving=false,layoutTimer=null;
function applyLayout(){
 document.documentElement.style.setProperty("--left-pane",layoutPrefs.left+"%");
 document.documentElement.style.setProperty("--upper-pane",layoutPrefs.upper+"%");
 document.documentElement.style.setProperty("--ui-scale",String(layoutPrefs.font_size/14));
 $("layout-font").value=layoutPrefs.font_size;$("layout-font-value").textContent=layoutPrefs.font_size+" px";
 $("split-horizontal").setAttribute("aria-valuenow",String(Math.round(layoutPrefs.left)));
 $("split-vertical").setAttribute("aria-valuenow",String(Math.round(layoutPrefs.upper)));
}
window.loadLayout=function(values){if(layoutLoaded||!values)return;layoutPrefs={...values};layoutLoaded=true;applyLayout();};
function scheduleLayoutSave(){layoutPending=true;$("layout-status").textContent="尚未儲存…";clearTimeout(layoutTimer);layoutTimer=setTimeout(saveLayout,300);}
async function saveLayout(){
 if(layoutSaving)return;layoutSaving=true;
 try{while(layoutPending){layoutPending=false;const values={...layoutPrefs};await command("ui_preferences",{values});}$("layout-status").textContent="已保存，下次啟動自動套用";}
 catch(e){$("layout-status").textContent="未保存："+e.message;toast("版面設定未保存，請再調整或按儲存重試");}
 finally{layoutSaving=false;}
}
function bindSplitter(id,key,parent,axis,min,max){
 const handle=$(id);let dragging=false;
 const update=e=>{const rect=document.querySelector(parent).getBoundingClientRect(),size=axis==="x"?rect.width:rect.height;if(!size)return;const delta=axis==="x"?e.clientX-rect.left:e.clientY-rect.top;layoutPrefs[key]=Math.max(min,Math.min(max,Math.round(delta/size*1000)/10));applyLayout();};
 handle.addEventListener("pointerdown",e=>{if(e.button!==0)return;dragging=true;handle.setPointerCapture(e.pointerId);document.body.classList.add("resizing");update(e);});
 handle.addEventListener("pointermove",e=>{if(dragging)update(e);});
 const end=()=>{if(!dragging)return;dragging=false;document.body.classList.remove("resizing");scheduleLayoutSave();};
 handle.addEventListener("pointerup",end);handle.addEventListener("pointercancel",end);handle.addEventListener("lostpointercapture",end);
 handle.addEventListener("keydown",e=>{const delta=["ArrowLeft","ArrowUp"].includes(e.key)?-2:["ArrowRight","ArrowDown"].includes(e.key)?2:0;if(!delta)return;e.preventDefault();layoutPrefs[key]=Math.max(min,Math.min(max,layoutPrefs[key]+delta));applyLayout();scheduleLayoutSave();});
}
bindSplitter("split-horizontal","left",".workspace","x",25,75);
bindSplitter("split-vertical","upper",".strategy-stack","y",20,80);
$("layout-open").addEventListener("click",()=>$("layout-dialog").showModal());
$("layout-font").addEventListener("input",e=>{layoutPrefs.font_size=Number(e.target.value);applyLayout();scheduleLayoutSave();});
$("layout-reset").addEventListener("click",()=>{layoutPrefs={left:50,upper:46,font_size:14};applyLayout();scheduleLayoutSave();});
$("layout-save").addEventListener("click",scheduleLayoutSave);
})();
