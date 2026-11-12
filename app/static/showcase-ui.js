/* Operate the maintained workbench controls, never a second implementation. */
"use strict";
(function(){
 let running=false,base=0,startedAt=0,frame=null,lastPaint=-1,index=0,ready=false,failed=false,focusNode=null;
 const trace=[],cursor=document.createElement("div");cursor.id="showcase-cursor";cursor.textContent="↖";cursor.setAttribute("aria-hidden","true");document.body.append(cursor);
 const notice=document.createElement("div");notice.id="showcase-watermark";notice.textContent="原介面操作展示・使用模擬資料";document.body.append(notice);
 const find=selector=>typeof selector==="function"?selector():document.querySelector(selector);
 async function control(selector){for(let count=0;count<30;count++){const node=find(selector);if(node&&!node.disabled)return node;await new Promise(resolve=>setTimeout(resolve,50));}throw Error("原介面控制項尚未就緒："+String(selector));}
 function mark(node){
  focusNode=node;
  document.querySelectorAll(".showcase-focus").forEach(n=>n.classList.remove("showcase-focus"));node.classList.add("showcase-focus");node.scrollIntoView({block:"nearest",inline:"nearest"});
  const host=[...document.querySelectorAll("dialog[open]")].at(-1)||document.body;host.append(cursor);
  const box=node.getBoundingClientRect();cursor.style.left=Math.min(innerWidth-30,box.left+Math.min(box.width/2,90))+"px";cursor.style.top=Math.min(innerHeight-35,box.top+box.height/2)+"px";
 }
 async function click(selector){const node=await control(selector);mark(node);node.click();}
 async function fill(selector,value){const node=await control(selector);mark(node);node.focus({preventScroll:true});node.value=value;node.dispatchEvent(new Event(node.tagName==="SELECT"?"change":"input",{bubbles:true}));}
 async function check(selector){const node=await control(selector);mark(node);node.checked=true;node.dispatchEvent(new Event("change",{bubbles:true}));}
 async function submit(selector){const node=await control(selector);mark(node.querySelector("button[type=submit]"));node.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));}
 const input=name=>`#strategy-form [name="${name}"]`;
 const steps=[
  [0,"開啟原登入視窗",()=>click("#login-open")],
  [1,"輸入模擬帳號",()=>fill('#login-form [name="person_id"]',"DEMO")],
  [2,"完成模擬帳號",()=>fill('#login-form [name="person_id"]',"DEMO-USER")],
  [3,"填入模擬密碼",()=>fill('#login-form [name="password"]',"demo-password")],
  [4,"勾選登入確認",()=>check('#login-form [name="confirm_production"]')],
  [5,"按下登入",()=>submit("#login-form")],
  [7,"選擇展示帳戶",()=>click("#account-selector button")],
  [10,"新增原網格表單",()=>click("#new-strategy")],
  [11,"切換原逐步精靈",()=>click("#editor-mode")],
  [12,"輸入策略名稱",()=>fill(input("name"),"AAPL 展示網格")],
  [13,"輸入商品",()=>fill(input("symbol"),"AAPL")],
  [14,"選擇正式交易",()=>fill(input("mode"),"live")],
  [15,"商品確認下一步",()=>click("#wizard-next")],
  [17,"輸入價格範圍",async()=>{await fill(input("lower_price"),"94");await fill(input("upper_price"),"106");}],
  [19,"價格確認下一步",()=>click("#wizard-next")],
  [21,"輸入六個價格間隔",()=>fill("#grid-intervals","6")],
  [23,"計算格距後下一步",()=>click("#wizard-next")],
  [24,"設定每筆十股",()=>fill(input("quantity"),"10")],
  [25,"原介面預覽網格",()=>click("#wizard-next")],
  [28,"儲存原策略表單",()=>submit("#strategy-form")],
  [30,"按策略列啟動",()=>click('#strategies [data-strategy-id="showcase-strategy"] td:nth-child(2) button')],
  [32,"勾選原實單確認",()=>check('#live-form [name="confirm"]')],
  [33,"確認展示實單設定",()=>submit("#live-form")],
  [35,"勾選原庫存查核確認",()=>check("#recovery-ack")],
  [37,"確認配對庫存並啟動",()=>click("#recovery-confirm")],
  [40,"查看原圖像化網格",()=>click('[data-tab="plan"]')],
  [46,"切換委託頁看買單",()=>click('[data-tab="orders"]')],
  [48.4,"切換成交頁看買入",()=>click('[data-tab="fills"]')],
  [55,"切換委託頁看賣單",()=>click('[data-tab="orders"]')],
  [57.4,"切換成本頁看價差",()=>click('[data-tab="pnl"]')],
  [60,"回原網格圖等待下一格",()=>click('[data-tab="plan"]')],
  [64,"查看第二筆買單",()=>click('[data-tab="orders"]')],
  [66.4,"查看第二筆買入成交",()=>click('[data-tab="fills"]')],
  [72,"查看第二筆賣單",()=>click('[data-tab="orders"]')],
  [74.4,"查看兩次循環損益",()=>click('[data-tab="pnl"]')],
  [80,"回原網格看部位歸零",()=>click('[data-tab="plan"]')],
  [84,"原成本頁回顧",()=>click('[data-tab="pnl"]')]
 ];
 const elapsed=()=>Math.min(Showcase.DURATION,base+(running?(performance.now()-startedAt)/1000*Showcase.PLAYBACK_RATE:0));
 function avoidRects(){return [focusNode?.closest("label")||focusNode,...document.querySelectorAll("dialog[open] .dialog-head")].filter(node=>node?.isConnected).map(node=>node.getBoundingClientRect()).filter(rect=>rect.width&&rect.height).map(({x,y,width,height})=>({x,y,width,height}));}
 function publish(type="status",error=""){const storySeconds=elapsed(),s=Showcase.snapshot(storySeconds);parent.postMessage({kind:"us-grid-showcase",type,seconds:Showcase.toPlaybackTime(storySeconds),duration:Showcase.PLAYBACK_DURATION,storySeconds,running,finished:storySeconds>=Showcase.DURATION,phase:s.phase,title:s.cue[2],text:s.cue[3],step:trace.at(-1)?.label||"準備播放",ready,error,avoid:avoidRects()},"*");}
 async function paint(seconds){
  ShowcaseBackend.setTime(seconds);state=ShowcaseBackend.snapshot();render();
  while(running&&index<steps.length&&steps[index][0]<=seconds){const [time,label,fn]=steps[index];await fn();trace.push({time,label});index++;}
  state=ShowcaseBackend.snapshot();render();publish();
 }
 async function tick(now){
  if(!running||failed)return;
  if(now-lastPaint>=150){lastPaint=now;try{await paint(elapsed());}catch(error){failed=true;running=false;publish("error",error.message);}}
  if(elapsed()>=Showcase.DURATION){base=Showcase.DURATION;running=false;publish();return;}
  if(running)frame=requestAnimationFrame(tick);
 }
 window.addEventListener("message",event=>{
  if(event.source!==parent||event.data?.kind!=="us-grid-showcase-control"||!ready||failed)return;
  if(event.data.action==="play"&&!running){startedAt=performance.now();running=true;frame=requestAnimationFrame(tick);publish();}
  if(event.data.action==="pause"&&running){base=elapsed();running=false;cancelAnimationFrame(frame);publish();}
 });
 document.addEventListener("showcase-interaction",()=>{
  if(running)base=elapsed();running=false;cancelAnimationFrame(frame);publish("interaction");
 });
 window.addEventListener("resize",()=>{if(ready)publish();});
 window.ShowcaseDemo={trace,get ready(){return ready;},get failed(){return failed;},get seconds(){return elapsed();},get playbackSeconds(){return Showcase.toPlaybackTime(elapsed());},get running(){return running;}};
 window.addEventListener("DOMContentLoaded",async()=>{
  try{for(let count=0;count<50&&!state;count++)await new Promise(resolve=>setTimeout(resolve,50));if(!state)throw Error("原工作台尚未完成初始化");ready=true;publish("ready");}catch(error){failed=true;publish("error",error.message);}
 });
})();
