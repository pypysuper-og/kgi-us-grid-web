"use strict";
let showcaseStorySeconds=0,showcaseAvoid=[],showcaseAvoidKey="",showcaseCaptionKey="",showcasePlacementFrame=null,showcaseFrameSize="";
const showcaseClock=seconds=>`${String(Math.floor(seconds/60)).padStart(2,"0")}:${String(Math.floor(seconds%60)).padStart(2,"0")}`;
const showcaseDialog=el("dialog");showcaseDialog.id="showcase-dialog";showcaseDialog.setAttribute("aria-labelledby","showcase-title");
const showcaseHeader=el("div",undefined,"showcase-host-header"),showcaseTitle=el("h2","原介面操作放映 · 使用模擬資料"),showcaseBack=button("返回工作台",closeShowcase,"primary");showcaseTitle.id="showcase-title";showcaseBack.id="showcase-return";
showcaseHeader.append(showcaseTitle,el("span","原工作台監控狀態保持原樣"),showcaseBack);
const showcaseNarration=el("section",undefined,"showcase-narration"),showcaseCaptionTitle=el("strong"),showcaseCaption=el("p");
showcaseNarration.id="showcase-narration";
showcaseNarration.setAttribute("aria-label","展示步驟旁白");
showcaseCaptionTitle.id="showcase-caption-title";
showcaseCaption.id="showcase-caption";
showcaseCaption.setAttribute("aria-live","polite");
showcaseNarration.append(el("span","步驟旁白","showcase-narration-label"),showcaseCaptionTitle,showcaseCaption);

const showcaseFooter=el("div",undefined,"showcase-controls");
const showcasePlay=button("▶ 開始放映",toggleShowcase,"primary");
const showcaseRestart=button("重新播放",()=>resetShowcase(true));
const showcaseSpeed=el("select");
const showcaseSpeedLabel=el("label","播放速度","showcase-speed");
showcaseSpeed.id="showcase-speed";
showcaseSpeedLabel.htmlFor=showcaseSpeed.id;
for(const rate of Showcase.PLAYBACK_RATES){
 const option=el("option",rate.toFixed(1)+"×");
 option.value=String(rate);
 showcaseSpeed.append(option);
}
showcaseSpeedLabel.append(showcaseSpeed);
const showcaseProgress=el("progress"),showcaseTime=el("span"),showcaseState=el("span","載入原介面中…");
showcasePlay.id="showcase-play";
showcaseRestart.id="showcase-restart";
showcaseProgress.id="showcase-progress";
showcaseProgress.max=Showcase.DURATION;
showcaseProgress.value=0;
showcaseProgress.setAttribute("aria-label","展示播放進度");
showcaseTime.id="showcase-time";
showcaseState.id="showcase-play-state";
showcaseState.setAttribute("role","status");
showcaseFooter.append(showcasePlay,showcaseRestart,showcaseSpeedLabel,showcaseProgress,showcaseTime,showcaseState);
showcaseDialog.append(showcaseHeader,showcaseNarration,showcaseFooter);document.body.append(showcaseDialog);
let showcaseFocus=null,showcasePreviousTitle="",showcaseRunning=false,showcaseReady=false,showcaseFinished=false,showcaseAutoPlay=false;
const showcaseExitDialog=el("dialog"),showcaseExitTitle=el("h2","要離開展示，回到工作台嗎？"),showcaseExitContinue=button("繼續播放",continueShowcase,"primary"),showcaseExitStop=button("停止展示並返回工作台",closeShowcase,"secondary"),showcaseExitActions=el("div",undefined,"dialog-actions");showcaseExitDialog.id="showcase-exit-dialog";showcaseExitTitle.id="showcase-exit-title";showcaseExitDialog.setAttribute("aria-labelledby","showcase-exit-title");showcaseExitContinue.id="showcase-exit-continue";showcaseExitStop.id="showcase-exit-stop";showcaseExitContinue.autofocus=true;showcaseExitActions.append(showcaseExitContinue,showcaseExitStop);showcaseExitDialog.append(showcaseExitTitle,el("p","展示已暫停，剛才的操作沒有執行。可以繼續觀看，或停止展示回到原本工作台。"),showcaseExitActions);document.body.append(showcaseExitDialog);
function placeShowcaseNarration(){
 const frame=showcaseDialog.querySelector("iframe");if(!showcaseDialog.open||!frame)return;
 const bounds=frame.getBoundingClientRect(),host=showcaseDialog.getBoundingClientRect(),pad=16;
 const size=bounds.width+"|"+bounds.height;
 // A previous corner can be outside the resized frame. Fit immediately on resize.
 showcaseNarration.style.transitionDuration=size===showcaseFrameSize?"":"0s";
 showcaseFrameSize=size;
 showcaseNarration.style.width=Math.max(0,Math.min(540,bounds.width-pad*2))+"px";showcaseNarration.style.maxHeight=Math.max(0,bounds.height-pad*2)+"px";
 const width=showcaseNarration.offsetWidth,height=showcaseNarration.offsetHeight,left=bounds.left-host.left+pad,top=bounds.top-host.top+pad,right=Math.max(left,bounds.right-host.left-width-pad),bottom=Math.max(top,bounds.bottom-host.top-height-pad);
 const positions=[{x:right,y:top,name:"top-right"},{x:left,y:bottom,name:"bottom-left"},{x:right,y:bottom,name:"bottom-right"},{x:left,y:top,name:"top-left"}];
 const overlap=(a,b)=>Math.max(0,Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x))*Math.max(0,Math.min(a.y+a.height,b.y+b.height)-Math.max(a.y,b.y));
 for(const position of positions)position.score=showcaseAvoid.reduce((sum,rect,index)=>sum+overlap({...position,width,height},{x:bounds.left-host.left+rect.x-12,y:bounds.top-host.top+rect.y-12,width:rect.width+24,height:rect.height+24})*(index===0?8:1),0);
 positions.sort((a,b)=>a.score-b.score);
 const best=positions[0];
 const chosen=positions.find(p=>p.name===showcaseNarration.dataset.placement&&p.score===best.score)||best;
 showcaseNarration.style.transform=`translate3d(${chosen.x}px,${chosen.y}px,0)`;
 showcaseNarration.dataset.placement=chosen.name;
}
function scheduleShowcaseNarration(){
 if(!showcaseDialog.open||showcasePlacementFrame!==null)return;
 showcasePlacementFrame=requestAnimationFrame(()=>{
  showcasePlacementFrame=null;
  placeShowcaseNarration();
 });
}
function setShowcaseCaption(title,text){
 const key=title+"|"+text;
 if(showcaseCaptionKey===key)return;
 showcaseCaptionKey=key;
 showcaseCaptionTitle.textContent=title;
 showcaseCaption.textContent=text;
 if(showcaseDialog.open&&!matchMedia("(prefers-reduced-motion: reduce)").matches){
  showcaseNarration.animate({opacity:[.7,1]},{duration:180,easing:"ease-out"});
 }
 scheduleShowcaseNarration();
}
function updateShowcaseProgress(seconds){
 showcaseStorySeconds=Math.max(0,Math.min(Showcase.DURATION,Number.isFinite(seconds)?seconds:0));
 const rate=Number(showcaseSpeed.value);
 const current=showcaseClock(Showcase.toPlaybackTime(showcaseStorySeconds,rate));
 const total=showcaseClock(Showcase.toPlaybackTime(Showcase.DURATION,rate));
 if(showcaseProgress.value!==showcaseStorySeconds)showcaseProgress.value=showcaseStorySeconds;
 const timer=current+" / "+total;
 if(showcaseTime.textContent!==timer)showcaseTime.textContent=timer;
}
function sendShowcaseControl(action,details={}){
 showcaseDialog.querySelector("iframe")?.contentWindow.postMessage({kind:"us-grid-showcase-control",action,...details},"*");
}
function resetShowcase(autoplay=false){
 if(showcaseExitDialog.open)showcaseExitDialog.close();
 showcaseDialog.querySelector("iframe")?.remove();
 showcaseReady=false;
 showcaseRunning=false;
 showcaseFinished=false;
 showcaseAutoPlay=autoplay;
 showcaseAvoid=[];
 showcaseAvoidKey="";
 showcaseFrameSize="";
 delete showcaseNarration.dataset.placement;
 showcasePlay.disabled=true;
 showcaseRestart.disabled=true;
 showcaseState.textContent="載入原介面中…";
 updateShowcaseProgress(0);
 showcasePlay.textContent="▶ 開始放映";
 setShowcaseCaption("按下開始，觀看原介面操作","下方可選播放速度。嘗試操作展示裡的按鈕時，會先暫停並詢問是否回到工作台。");
 const frame=el("iframe");frame.id="showcase-frame";frame.title="原工作台模擬資料操作放映";frame.setAttribute("sandbox","allow-scripts");frame.referrerPolicy="no-referrer";frame.src="/static/showcase.html";showcaseNarration.before(frame);
}
function openShowcase(){
 if(showcaseDialog.open)return;
 showcaseFocus=document.activeElement;
 showcasePreviousTitle=document.title;
 showcaseSpeed.value=String(Showcase.PLAYBACK_RATE);
 resetShowcase();
 showcaseDialog.showModal();
 document.title="展示放映 · 美股網格工作台";
 scheduleShowcaseNarration();
}
function closeShowcase(){if(showcaseExitDialog.open)showcaseExitDialog.close();if(showcaseDialog.open)showcaseDialog.close();}
function requestShowcaseExit(){
 if(!showcaseDialog.open||showcaseExitDialog.open)return;
 sendShowcaseControl("pause");showcaseExitContinue.textContent=showcaseFinished?"重新播放":"繼續播放";showcaseExitDialog.showModal();
}
function continueShowcase(){
 showcaseExitDialog.close();if(showcaseFinished){resetShowcase(true);return;}if(!showcaseReady){showcaseAutoPlay=true;return;}
 sendShowcaseControl("play");
}
function toggleShowcase(){
 if(!showcaseReady)return;
 if(showcaseFinished){resetShowcase(true);return;}
 sendShowcaseControl(showcaseRunning?"pause":"play");
}
showcaseSpeed.addEventListener("change",()=>{
 updateShowcaseProgress(showcaseStorySeconds);
 if(showcaseReady)sendShowcaseControl("set-rate",{rate:Number(showcaseSpeed.value)});
});
window.addEventListener("message",event=>{
 const frame=showcaseDialog.querySelector("iframe"),data=event.data;
 if(!showcaseDialog.open||!frame||event.source!==frame.contentWindow||event.origin!=="null"||data?.kind!=="us-grid-showcase")return;
 const avoid=Array.isArray(data.avoid)?data.avoid:[];
 const avoidKey=JSON.stringify(avoid);
 if(avoidKey!==showcaseAvoidKey){
  showcaseAvoidKey=avoidKey;
  showcaseAvoid=avoid;
  scheduleShowcaseNarration();
 }
 if(data.type==="error"){
  showcaseReady=false;
  showcaseState.textContent="展示步驟未完成";
  setShowcaseCaption("展示步驟未完成",data.error);
  showcasePlay.disabled=true;
  showcaseRestart.disabled=false;
  return;
 }
 if(data.type==="ready"){
  showcaseReady=true;
  showcasePlay.disabled=false;
  showcaseState.textContent="準備播放";
  sendShowcaseControl("set-rate",{rate:Number(showcaseSpeed.value)});
  if(showcaseAutoPlay){showcaseAutoPlay=false;toggleShowcase();}
  return;
 }
 showcaseRunning=!!data.running;showcaseFinished=!!data.finished;showcaseRestart.disabled=false;
 updateShowcaseProgress(data.storySeconds);
 const started=showcaseRunning||showcaseStorySeconds>0||showcaseFinished;
 const playLabel=showcaseRunning?"Ⅱ 暫停放映":showcaseFinished?"↻ 再播放一次":started?"▶ 繼續放映":"▶ 開始放映";
 const status=showcaseFinished?"放映完成":showcaseRunning?"正在放映":started?"已暫停":"準備播放";
 if(showcasePlay.textContent!==playLabel)showcasePlay.textContent=playLabel;
 if(showcaseState.textContent!==status)showcaseState.textContent=status;
 if(started)setShowcaseCaption(data.title,data.text);
 if(data.type==="interaction")requestShowcaseExit();
});
showcaseDialog.addEventListener("close",()=>{
 if(showcaseExitDialog.open)showcaseExitDialog.close();
 if(showcasePlacementFrame!==null)cancelAnimationFrame(showcasePlacementFrame);
 showcasePlacementFrame=null;
 showcaseDialog.querySelector("iframe")?.remove();
 document.title=showcasePreviousTitle;
 if(showcaseFocus?.isConnected)showcaseFocus.focus({preventScroll:true});
});
showcaseDialog.addEventListener("cancel",event=>{event.preventDefault();requestShowcaseExit();});
showcaseExitDialog.addEventListener("cancel",event=>{event.preventDefault();continueShowcase();});
window.addEventListener("resize",placeShowcaseNarration);
$("showcase-open").addEventListener("click",openShowcase);
