/* A deterministic film, not an execution mode of the trading engine. */
(function(root,factory){const api=factory();if(typeof module==="object"&&module.exports)module.exports=api;else root.Showcase=api;})(globalThis,function(){
 "use strict";
 const DURATION=88;
 const PLAYBACK_RATE=0.5,PLAYBACK_DURATION=DURATION/PLAYBACK_RATE;
 const toStoryTime=seconds=>Math.max(0,Math.min(PLAYBACK_DURATION,Number.isFinite(seconds)?seconds:0))*PLAYBACK_RATE;
 const toPlaybackTime=seconds=>Math.max(0,Math.min(DURATION,Number.isFinite(seconds)?seconds:0))/PLAYBACK_RATE;
 const quotes=[[0,100],[40,100],[42,99.4],[44,98.8],[46,98],[48,97.9],[50,98.7],[53,99.5],[55,100],[57,100.1],[60,99.3],[62,98.4],[64,98],[66,97.8],[68,98.5],[70,99.3],[72,100],[74,100.2],[76,101],[78,100.4],[80,100]];
 const orders=[
  {id:"DEMO-B01",side:"buy",price:98,quantity:10,submitted:46,filled:48},
  {id:"DEMO-S01",side:"sell",price:100,quantity:10,submitted:55,filled:57},
  {id:"DEMO-B02",side:"buy",price:98,quantity:10,submitted:64,filled:66},
  {id:"DEMO-S02",side:"sell",price:100,quantity:10,submitted:72,filled:74}
 ];
 const cues=[
  [0,"login","01　登入展示帳戶","在原本的登入視窗依序填入假帳號、假密碼並勾選確認，再按登入。"],
  [7,"login","連線與選帳，分開確認","登入後再選取展示帳戶 DEMO-001。整段動畫不會接觸券商或你的帳號。"],
  [10,"setup","02　建立一組實單網格","演示正式交易的設定畫面：商品 AAPL，最後成交價以假資料 100 美元呈現。"],
  [16,"setup","先決定價格範圍","將下界設為 94、上界設為 106 美元，價格區間落在示範成交價周邊。"],
  [22,"setup","每格 2 美元，每次 10 股","基本參數保持簡單。圖中可預覽買賣點，額外的股數或金額限制另放在進階選用。"],
  [26,"setup","原精靈預覽參數與網格","在原有預覽頁核對價格範圍、格距與股數，再按儲存；新增的假策略保持暫停。"],
  [28,"monitor","03　演示實單確認與庫存查核","先核對帳戶與實單設定，再查核委託及庫存。動畫中的確認與查核全部使用假資料。"],
  [37,"monitor","開啟監控，等待價格觸發","假策略開始監控：基準 100、下一買點 98。空手時不會先賣出股票。"],
  [40,"market","04　行情開始跳動","示範價格從 100 美元往下走。留意價格、下一買點，以及部位股數如何連動。"],
  [46,"market","價格到 98，出現買入委託","先顯示一筆假買單，等待成交回報。送出委託與已成交是不同的狀態。"],
  [48,"market","買入成交，持股變成 10 股","以 98 美元買入 10 股，示範成本為 980 美元。全部成交後，基準改為 98，下一賣點為 100。"],
  [55,"market","價格回到 100，出現賣出委託","已有 10 股部位，價格到下一賣點後，顯示一筆等待成交的假賣單。"],
  [57,"market","賣出成交，完成第一次循環","10 股在 100 美元賣出，持股回到 0。這次展示價差為 20 美元，示範費用設為 0。"],
  [60,"market","價格再次回落，等待下一格","第一輪完成後回到基準 100。圖上保留買入與賣出標記，方便對照進出場。"],
  [64,"market","第二次觸及買點","再次出現 98 美元、10 股的假買單。尚未成交前，部位仍維持 0 股。"],
  [66,"market","第二次買入，重新持有 10 股","假成交回報更新部位與成本；網格基準也跟著這次全部成交推進。"],
  [72,"market","價格反彈，第二次賣出","示範行情回到 100 美元，播放第二筆賣出委託，接著等待假成交回報。"],
  [74,"market","第二次賣出完成，部位歸零","兩次循環、四筆假成交完成，展示累計價差 40 美元。這是預先編排的數字，不是實際績效。"],
  [84,"complete","05　完整流程，半速回顧","已演示登入、建立實單網格、開啟監控，以及價格驅動的兩次進出場。可重播，或返回原本工作台；沒有建立或操作任何真實策略。"]
 ];
 function snapshot(seconds){
  const elapsed=Number.isFinite(seconds)?Math.max(0,Math.min(DURATION,seconds)):0;
  const cue=cues.filter(item=>item[0]<=elapsed).at(-1);
  const visibleQuotes=quotes.filter(item=>item[0]<=elapsed),visibleOrders=orders.filter(order=>order.submitted<=elapsed).map(order=>({...order,status:elapsed>=order.filled?"filled":"working"}));
  let quantity=0,cost=0,realized=0,anchor=100,cycles=0;
  for(const order of visibleOrders.filter(order=>order.status==="filled")){
   const value=Math.round(order.price*100)*order.quantity;
   if(order.side==="buy"){quantity+=order.quantity;cost+=value;}
   else{const removed=cost*order.quantity/quantity;quantity-=order.quantity;cost-=removed;realized+=value-removed;cycles++;}
   anchor=order.price;
  }
  return {elapsed,finished:elapsed>=DURATION,phase:cue[1],cue,price:visibleQuotes.at(-1)[1],quotes:visibleQuotes,orders:visibleOrders,quantity,cost:cost/100,realized:realized/100,anchor,cycles,nextBuy:anchor-2,nextSell:quantity?anchor+2:null,connected:elapsed>=8,created:elapsed>=26,armed:elapsed>=31,checked:elapsed>=34,monitoring:elapsed>=37};
 }
 return {DURATION,PLAYBACK_RATE,PLAYBACK_DURATION,toStoryTime,toPlaybackTime,quotes,orders,cues,snapshot};
});

