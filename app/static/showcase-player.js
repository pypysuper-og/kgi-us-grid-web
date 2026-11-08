"use strict";
let showcaseDuration=176,showcaseAvoid=[],showcaseCaptionKey="";
const showcaseClock=seconds=>`${String(Math.floor(seconds/60)).padStart(2,"0")}:${String(Math.floor(seconds%60)).padStart(2,"0")}`;
const showcaseDialog=el("dialog");showcaseDialog.id="showcase-dialog";showcaseDialog.setAttribute("aria-labelledby","showcase-title");
const showcaseHeader=el("div",undefined,"showcase-host-header"),showcaseTitle=el("h2","原介面操作放映 · 全部使用假資料"),showcaseBack=button("返回工作台",closeShowcase,"primary");showcaseTitle.id="showcase-title";showcaseBack.id="showcase-return";
showcaseHeader.append(showcaseTitle,el("span","原工作台監控狀態保持原樣"),showcaseBack);
const showcaseNarration=el("section",undefined,"showcase-narration"),showcaseCaptionTitle=el("strong","按下開始，觀看原介面操作"),showcaseCaption=el("p","自動點擊原登入視窗、逐步精靈、實單確認與成交分頁；所有帳號、行情與委託都是假資料。");showcaseNarration.id="showcase-narration";showcaseNarration.setAttribute("aria-label","展示步驟旁白");showcaseCaptionTitle.id="showcase-caption-title";showcaseCaption.id="showcase-caption";showcaseCaption.setAttribute("aria-live","polite");showcaseNarration.append(el("span","步驟旁白 · 0.5× 慢速","showcase-narration-label"),showcaseCaptionTitle,showcaseCaption);
const showcaseFooter=el("div",undefined,"showcase-controls"),showcasePlay=button("▶ 開始慢速展示",toggleShowcase,"primary"),showcaseRestart=button("重新播放",()=>resetShowcase(true)),showcaseProgress=el("progress"),showcaseTime=el("span","00:00 / 02:56"),showcaseState=el("span","載入原介面中…");showcasePlay.id="showcase-play";showcaseRestart.id="showcase-restart";showcaseProgress.id="showcase-progress";showcaseProgress.max=showcaseDuration;showcaseProgress.value=0;showcaseProgress.setAttribute("aria-label","展示播放進度");showcaseTime.id="showcase-time";showcaseState.id="showcase-play-state";showcaseState.setAttribute("role","status");showcaseFooter.append(showcasePlay,showcaseRestart,showcaseProgress,showcaseTime,showcaseState);
showcaseDialog.append(showcaseHeader,showcaseNarration,showcaseFooter);document.body.append(showcaseDialog);
let showcaseFocus=null,showcasePreviousTitle="",showcaseRunning=false,showcaseReady=false,showcaseFinished=false,showcaseAutoPlay=false;
const showcaseExitDialog=el("dialog"),showcaseExitTitle=el("h2","要離開展示，回到工作台嗎？"),showcaseExitContinue=button("繼續播放",continueShowcase,"primary"),showcaseExitStop=button("停止展示並返回工作台",closeShowcase,"secondary"),showcaseExitActions=el("div",undefined,"dialog-actions");showcaseExitDialog.id="showcase-exit-dialog";showcaseExitTitle.id="showcase-exit-title";showcaseExitDialog.setAttribute("aria-labelledby","showcase-exit-title");showcaseExitContinue.id="showcase-exit-continue";showcaseExitStop.id="showcase-exit-stop";showcaseExitContinue.autofocus=true;showcaseExitActions.append(showcaseExitContinue,showcaseExitStop);showcaseExitDialog.append(showcaseExitTitle,el("p","展示已暫停，剛才的操作沒有執行。可以繼續觀看，或停止展示回到原本工作台。"),showcaseExitActions);document.body.append(showcaseExitDialog);
function placeShowcaseNarration(){
 const frame=showcaseDialog.querySelector("iframe");if(!showcaseDialog.open||!frame)return;
 const bounds=frame.getBoundingClientRect(),host=showcaseDialog.getBoundingClientRect(),pad=16;
 showcaseNarration.style.width=Math.max(0,Math.min(540,bounds.width-pad*2))+"px";showcaseNarration.style.maxHeight=Math.max(0,bounds.height-pad*2)+"px";
 const width=showcaseNarration.offsetWidth,height=showcaseNarration.offsetHeight,left=bounds.left-host.left+pad,top=bounds.top-host.top+pad,right=Math.max(left,bounds.right-host.left-width-pad),bottom=Math.max(top,bounds.bottom-host.top-height-pad);
 const positions=[{x:right,y:top,name:"top-right"},{x:left,y:bottom,name:"bottom-left"},{x:right,y:bottom,name:"bottom-right"},{x:left,y:top,name:"top-left"}];
 const overlap=(a,b)=>Math.max(0,Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x))*Math.max(0,Math.min(a.y+a.height,b.y+b.height)-Math.max(a.y,b.y));
 for(const position of positions)position.score=showcaseAvoid.reduce((sum,rect,index)=>sum+overlap({...position,width,height},{x:bounds.left-host.left+rect.x-12,y:bounds.top-host.top+rect.y-12,width:rect.width+24,height:rect.height+24})*(index===0?8:1),0);
 positions.sort((a,b)=>a.score-b.score);const chosen=positions[0];showcaseNarration.style.left=chosen.x+"px";showcaseNarration.style.top=chosen.y+"px";showcaseNarration.dataset.placement=chosen.name;
}
function setShowcaseCaption(title,text){
 const key=title+"|"+text;if(showcaseCaptionKey!==key){showcaseCaptionKey=key;showcaseCaptionTitle.textContent=title;showcaseCaption.textContent=text;showcaseNarration.classList.remove("showcase-caption-pop");void showcaseNarration.offsetWidth;showcaseNarration.classList.add("showcase-caption-pop");}placeShowcaseNarration();
}
function resetShowcase(autoplay=false){
 if(showcaseExitDialog.open)showcaseExitDialog.close();showcaseDialog.querySelector("iframe")?.remove();showcaseReady=false;showcaseRunning=false;showcaseFinished=false;showcaseAutoPlay=autoplay;showcaseAvoid=[];showcasePlay.disabled=true;showcaseRestart.disabled=true;showcaseState.textContent="載入原介面中…";showcaseProgress.max=showcaseDuration;showcaseProgress.value=0;showcaseTime.textContent="00:00 / "+showcaseClock(showcaseDuration);showcasePlay.textContent="▶ 開始慢速展示";setShowcaseCaption("按下開始，觀看原介面操作","0.5倍速放映，約2分56秒。嘗試操作展示裡的按鈕時，會先暫停並詢問是否回到工作台。");
 const frame=el("iframe");frame.id="showcase-frame";frame.title="原工作台假資料操作放映";frame.setAttribute("sandbox","allow-scripts");frame.referrerPolicy="no-referrer";frame.src="/static/showcase.html";showcaseNarration.before(frame);
}
function openShowcase(){if(showcaseDialog.open)return;showcaseFocus=document.activeElement;showcasePreviousTitle=document.title;resetShowcase();showcaseDialog.showModal();document.title="展示放映 · 美股網格工作台";}
function closeShowcase(){if(showcaseExitDialog.open)showcaseExitDialog.close();if(showcaseDialog.open)showcaseDialog.close();}
function requestShowcaseExit(){
 if(!showcaseDialog.open||showcaseExitDialog.open)return;
 showcaseDialog.querySelector("iframe")?.contentWindow.postMessage({kind:"us-grid-showcase-control",action:"pause"},"*");showcaseExitContinue.textContent=showcaseFinished?"重新播放":"繼續播放";showcaseExitDialog.showModal();
}
function continueShowcase(){
 showcaseExitDialog.close();if(showcaseFinished){resetShowcase(true);return;}if(!showcaseReady){showcaseAutoPlay=true;return;}
 showcaseDialog.querySelector("iframe")?.contentWindow.postMessage({kind:"us-grid-showcase-control",action:"play"},"*");
}
function toggleShowcase(){if(!showcaseReady)return;if(showcaseFinished){resetShowcase(true);return;}showcaseDialog.querySelector("iframe").contentWindow.postMessage({kind:"us-grid-showcase-control",action:showcaseRunning?"pause":"play"},"*");}
window.addEventListener("message",event=>{
 const frame=showcaseDialog.querySelector("iframe"),data=event.data;
 if(!frame||event.source!==frame.contentWindow||event.origin!=="null"||data?.kind!=="us-grid-showcase")return;
 showcaseAvoid=Array.isArray(data.avoid)?data.avoid:[];
 if(Number.isFinite(data.duration)&&data.duration>0){showcaseDuration=data.duration;showcaseProgress.max=showcaseDuration;}
 if(data.type==="error"){showcaseState.textContent="展示步驟未完成";setShowcaseCaption("展示步驟未完成",data.error);showcasePlay.disabled=true;showcaseRestart.disabled=false;return;}
 if(data.type==="ready"){showcaseReady=true;showcasePlay.disabled=false;showcaseState.textContent="準備播放";placeShowcaseNarration();if(showcaseAutoPlay){showcaseAutoPlay=false;toggleShowcase();}return;}
 showcaseRunning=!!data.running;showcaseFinished=!!data.finished;showcaseRestart.disabled=false;
 showcasePlay.textContent=showcaseRunning?"Ⅱ 暫停放映":showcaseFinished?"↻ 再播放一次":"▶ 繼續放映";
 showcaseState.textContent=showcaseFinished?"放映完成":showcaseRunning?"正在放映":"已暫停";
 const seconds=Math.max(0,Math.min(showcaseDuration,Number(data.seconds)||0));showcaseProgress.value=seconds;showcaseTime.textContent=showcaseClock(seconds)+" / "+showcaseClock(showcaseDuration);
 setShowcaseCaption(data.title,data.text);
 if(data.type==="interaction")requestShowcaseExit();
});
showcaseDialog.addEventListener("close",()=>{if(showcaseExitDialog.open)showcaseExitDialog.close();showcaseDialog.querySelector("iframe")?.remove();document.title=showcasePreviousTitle;if(showcaseFocus?.isConnected)showcaseFocus.focus({preventScroll:true});});
showcaseDialog.addEventListener("cancel",event=>{event.preventDefault();requestShowcaseExit();});
showcaseExitDialog.addEventListener("cancel",event=>{event.preventDefault();continueShowcase();});
window.addEventListener("resize",placeShowcaseNarration);
$("showcase-open").addEventListener("click",openShowcase);
