"use strict";
let orderQueryBusy=false;
async function openOrderDiagnostics(s){
 const dialog=$("order-diagnostics-dialog"),body=$("order-diagnostics-body");
 if(orderQueryBusy)return;
 orderQueryBusy=true;
 body.replaceChildren(el("p",`正在向券商查詢 ${s.params.symbol} 委託報表…`));
 $("order-diagnostics-error").textContent="";
 if(!dialog.open)dialog.showModal();
 try{
  const result=await command("order_diagnostics",{strategy_id:s.id});
  body.replaceChildren(el("p",`${result.symbol} · ${result.source} · ${timeLabel(result.queried_at)}`),el("p",result.note,"help"));
  if(!result.available)body.append(el("p",result.error,"error"));
  else if(!result.rows.length)body.append(el("p","本次報表未列出此商品；不代表未知委託未送出。","help"));
  if(!result.catalog.available)body.append(el("p",result.catalog.message,"help"));
  for(const r of result.rows){
   const card=el("section",undefined,"order-report-card");
   card.append(el("h3",`${r.symbol} · ${r.BuySell||"方向未提供"} · ${r.sales_status||r.sales_label}（${r.sales_status_code||"無狀態碼"}）`));
   card.append(table(["原始序號","委託序號","正式單號","與本地帳本關係"],[[r.orig_seqnum||"未提供",r.seqnum||"未提供",r.orderno||"未提供",{matched:"識別相符（查詢不改帳）",unmatched:"未配對／可能為外部委託",ambiguous:"多筆相符，需人工核對"}[r.match]]]));
   card.append(el("p",`委託 ${r.qty||"未知"} 股 × ${r.price||"未知"}；成交 ${r.exe_qty||"未知"} 股，均價 ${r.exe_avg_price||"未知"}；${r.exe_label}（${r.exe_status_code||"無狀態碼"}）`));
   card.append(el("p",`錯誤碼：${r.order_err_code||"未提供"}；券商原因：${r.order_err_msg||"未提供"}`,r.order_err_code&&r.order_err_code!=="0"?"error":"help"));
   card.append(el("p",`INI 說明：${r.ini_message||"沒有對照文字"}${r.ini_ambiguous?"（同碼有多筆定義，僅供輔助）":""}`,"help"));
   card.append(el("small",`來源日期／時間：${r.trade_date||"未知"} ${r.create_time||""}（保留來源值，未推定時區）`));
   body.append(card);
  }
  if(result.omitted)body.append(el("p",`另有 ${result.omitted} 筆未展示，請至券商核對完整報表。`,"error"));
  const detail=el("details"),summary=el("summary","本次會話收到的 callback 與使用說明");
  detail.append(summary,el("p","查詢成功不等於成交或撤單成功。未知單不重送；只有已確認拒單且零成交，完成對帳後才可建立新委託。斷線／重啟後 callback 清單會重建，歷史請看稽核。INI 是說明字典，不能代替券商原因。"));
  detail.append(table(["接收時間","事件","狀態","原始序號","訊息"],result.callbacks.map(e=>[timeLabel(e.received_at),e.task,e.status,e.org_seqnum||"未知",e.msg||"未提供"])));
  body.append(detail);
 }catch(e){body.replaceChildren();$("order-diagnostics-error").textContent=e.message;}
 finally{orderQueryBusy=false;}
}
function showShutdownOrders(orders){
 const host=$("shutdown-orders");host.replaceChildren();host.hidden=!orders.length;
 if(!orders.length)return;
 host.append(el("h3","以下委託尚未確認結束，請到券商 App 手動查核"));
 host.append(el("p","程式結束不代表撤單成功。請核對是否已成交、遭拒或仍有效，再決定是否手動撤單。"));
 host.append(table(["商品／方向","委託／已確認成交","限價","原始序號","需處理事項"],orders.map(o=>[
  `${o.symbol} ${o.side==="buy"?"買進":"賣出"}`,`${o.quantity} / ${o.filled}`,o.price,o.broker_org||"本地未取得",o.reason
 ])));
}
