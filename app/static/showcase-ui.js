/* Operate the maintained workbench controls, never a second implementation. */
"use strict";
(function(){
 let ready=false,running=false,startedAt=0,base=0,rate=Showcase.PLAYBACK_RATE,timer=null,painting=false,index=0,failed=false,focusNode=null;
 const trace=[],cursor=document.createElement("div");
 cursor.id="showcase-cursor";cursor.textContent="↖";cursor.hidden=true;cursor.setAttribute("aria-hidden","true");document.body.append(cursor);
 let cursorPoint=null,cursorMotion=null;
 const reducedMotion=matchMedia("(prefers-reduced-motion: reduce)");
 const notice=document.createElement("div");notice.id="showcase-watermark";notice.textContent="原介面操作展示・使用模擬資料";document.body.append(notice);
 const find=selector=>document.querySelector(selector);
 const available=node=>node&&!node.disabled&&node.getClientRects().length>0;
 async function control(selector){
  for(let count=0;count<30&&running;count++){
   const node=find(selector);
   if(available(node))return node;
   await new Promise(resolve=>setTimeout(resolve,50));
  }
  if(!running)return null;
  throw Error("原介面控制項尚未就緒："+String(selector));
 }
 function highlight(node){
  focusNode?.classList.remove("showcase-focus");
  focusNode=node;
  node.classList.add("showcase-focus");
 }
 const cursorHost=node=>node.closest("dialog[open]")||document.body;
 const pin=(value,limit)=>Math.max(0,Math.min(limit,value));
 function targetPoint(node){
  const box=node.getBoundingClientRect();
  return {x:pin(box.left+Math.min(box.width/2,90),innerWidth-30),y:pin(box.top+box.height/2,innerHeight-35)};
 }
 const translate=point=>`translate3d(${point.x}px,${point.y}px,0)`;
 async function pointAt(node){
  highlight(node);
  node.scrollIntoView({block:"nearest",inline:"nearest"});
  const host=cursorHost(node);
  if(cursor.parentElement!==host)host.append(cursor);
  const target=targetPoint(node);
  const from=cursorPoint||{x:pin(target.x-42,innerWidth-30),y:pin(target.y+32,innerHeight-35)};
  const destination=translate(target);
  cursorPoint=target;
  cursor.style.transform=destination;
  cursor.hidden=false;
  if(reducedMotion.matches)return;
  // Glide during the first 240 ms, then hold the target on screen for 80 ms.
  const motion=cursor.animate([
   {transform:translate(from),easing:"ease-out"},
   {transform:destination,offset:.75},
   {transform:destination}
  ],{duration:320});
  cursorMotion=motion;
  try{await motion.finished;}
  finally{cursorMotion=null;}
 }
 async function operate(selector,action){
  for(let attempt=0;attempt<3&&running;attempt++){
   const node=await control(selector);
   if(!running||!node)return false;
   await pointAt(node);
   if(!running)return false;
   // A workbench refresh can replace a button while the cursor is moving.
   const current=find(selector);
   if(!available(current)||cursorHost(current)!==cursor.parentElement)continue;
   const point=targetPoint(current);
   if(Math.hypot(point.x-cursorPoint.x,point.y-cursorPoint.y)>2)continue;
   if(current!==focusNode)highlight(current);
   action(current);
   return true;
  }
  if(!running)return false;
  throw Error("展示控制項的位置持續變動："+selector);
 }
 const click=selector=>operate(selector,node=>node.click());
 const fill=(selector,value)=>operate(selector,node=>{
  node.focus({preventScroll:true});
  node.value=value;
  node.dispatchEvent(new Event(node.tagName==="SELECT"?"change":"input",{bubbles:true}));
 });
 const check=selector=>operate(selector,node=>{
  node.checked=true;
  node.dispatchEvent(new Event("change",{bubbles:true}));
 });
 const submit=selector=>operate(selector+" button[type=submit]",node=>{
  node.form.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));
 });
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
  [17,"輸入價格範圍",async()=>await fill(input("lower_price"),"94")&&fill(input("upper_price"),"106")],
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
 const marketChanges=[
  ...Showcase.quotes.map(([time])=>time),
  ...Showcase.orders.flatMap(order=>[order.submitted,order.filled])
 ];
 let previousStoryTime=0;
 const elapsed=(now=performance.now())=>Math.min(Showcase.DURATION,base+(running?(now-startedAt)/1000*rate:0));
 const finished=()=>base===Showcase.DURATION&&index===steps.length;
 function avoidRects(){return [focusNode?.closest("label")||focusNode,...document.querySelectorAll("dialog[open] .dialog-head")].filter(node=>node?.isConnected).map(node=>node.getBoundingClientRect()).filter(rect=>rect.width&&rect.height).map(({x,y,width,height})=>({x,y,width,height}));}
 function publish(type="status",error=""){
  const storySeconds=elapsed(),s=Showcase.snapshot(storySeconds);
  parent.postMessage({kind:"us-grid-showcase",type,seconds:Showcase.toPlaybackTime(storySeconds,rate),duration:Showcase.toPlaybackTime(Showcase.DURATION,rate),storySeconds,running,finished:finished(),phase:s.phase,title:s.cue[2],text:s.cue[3],step:trace.at(-1)?.label||"準備播放",ready,error,avoid:avoidRects()},"*");
 }
 async function paint(seconds){
  ShowcaseBackend.setTime(seconds);
  // Scripted controls already update the workbench through their own handlers.
  if(marketChanges.some(time=>time>previousStoryTime&&time<=seconds)){
   state=ShowcaseBackend.snapshot();
   render();
  }
  previousStoryTime=seconds;
  while(running&&index<steps.length&&steps[index][0]<=seconds){
   const [time,label,operateStep]=steps[index];
   if(!await operateStep())break;
   trace.push({time,label});
   index++;
  }
 }
 function schedule(delay=125){
  if(running&&timer===null)timer=setTimeout(tick,delay);
 }
 function pause(){
  if(running)base=elapsed();
  running=false;
  clearTimeout(timer);
  timer=null;
  if(cursorMotion?.playState==="running")cursorMotion.pause();
 }
 async function paintAt(seconds){
  painting=true;
  try{
   await paint(seconds);
   if(running&&seconds===Showcase.DURATION&&index===steps.length){
    pause();
    publish();
   }
  }catch(error){
   pause();
   failed=true;
   publish("error",error.message);
  }finally{
   painting=false;
  }
 }
 function tick(){
  timer=null;
  if(!running||failed)return;
  if(!painting)paintAt(elapsed());
  publish();
  schedule();
 }
 window.addEventListener("message",event=>{
  const data=event.data;
  if(event.source!==parent||data?.kind!=="us-grid-showcase-control"||!ready||failed)return;
  if(data.action==="play"&&!running&&!finished()){
   startedAt=performance.now();
   running=true;
   if(cursorMotion?.playState==="paused")cursorMotion.play();
   schedule(0);
   publish();
  }
  if(data.action==="pause"&&running){pause();publish();}
  if(data.action==="set-rate"&&Showcase.PLAYBACK_RATES.includes(data.rate)&&data.rate!==rate){
   const now=performance.now();
   base=elapsed(now);
   startedAt=now;
   rate=data.rate;
   publish();
  }
 });
 document.addEventListener("showcase-interaction",()=>{
  pause();
  publish("interaction");
 });
 window.addEventListener("resize",()=>{if(ready)publish();});
 window.ShowcaseDemo={trace,get ready(){return ready;},get failed(){return failed;},get seconds(){return elapsed();},get rate(){return rate;},get playbackSeconds(){return Showcase.toPlaybackTime(elapsed(),rate);},get running(){return running;}};
 window.addEventListener("DOMContentLoaded",async()=>{
  try{for(let count=0;count<50&&!state;count++)await new Promise(resolve=>setTimeout(resolve,50));if(!state)throw Error("原工作台尚未完成初始化");ready=true;publish("ready");}catch(error){failed=true;publish("error",error.message);}
 });
})();
