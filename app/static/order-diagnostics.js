"use strict";
let orderQueryBusy=false;
async function openOrderDiagnostics(s,refresh=true,snapshotId=null){
 const dialog=$("order-diagnostics-dialog"),body=$("order-diagnostics-body");
 if(orderQueryBusy)return;
 orderQueryBusy=true;
 $("order-diagnostics-title").textContent="券商委託診斷（唯讀）";
 body.replaceChildren(el("p",refresh?`正在向券商查詢 ${s.params.symbol} 委託報表…`:"正在讀取前次保存的查核…"));
 $("order-diagnostics-error").textContent="";
 if(!dialog.open)dialog.showModal();
 try{
  const result=await command("order_diagnostics",{strategy_id:s.id,refresh,snapshot_id:snapshotId});
  body.replaceChildren(el("p",`${result.symbol} · ${result.source} · ${timeLabel(result.queried_at)}${result.saved?" · 保存快照（非即時）":""}`),el("p",result.note,"help"),el("p",result.coverage_note,"help"));
  const counts=result.match_counts||{};
  if(counts.unmatched||counts.ambiguous||counts.conflict)body.append(el("p",`未配對 ${counts.unmatched||0}；多筆相符 ${counts.ambiguous||0}；識別／參數缺漏或衝突 ${counts.conflict||0}。已保存查核，請逐筆核對來源與時間；不會自動認領、重送或修改部位。`,"notice"));
  if(!result.available)body.append(el("p",result.error,"error"));
  else if(!result.rows.length)body.append(el("p","本次報表未列出此商品；不代表未知委託未送出。","help"));
  if(!result.catalog.available)body.append(el("p",result.catalog.message,"help"));
  if(result.history?.length){
   const label=el("label","保存查核（最近 20 次；非即時）"),history=el("select");history.setAttribute("aria-label","保存查核時間");
   for(const item of result.history){const option=el("option",`${timeLabel(item.queried_at)} · ${item.available?"取得報表":"查詢失敗"} · 未配對 ${item.match_counts.unmatched||0}`);option.value=item.id;history.append(option);}
   history.value=result.snapshot_id??result.history[0].id;
   history.addEventListener("change",()=>openOrderDiagnostics(s,false,Number(history.value)));label.append(history);body.append(label);
  }
  for(const r of result.rows){
   const card=el("section",undefined,"order-report-card");
   card.append(el("h3",`${r.symbol} · ${r.BuySell||"方向未提供"} · ${r.sales_status||r.sales_label}（${r.sales_status_code||"無狀態碼"}）`));
   card.append(table(["原始序號","委託序號","正式單號","與本地帳本關係"],[[r.orig_seqnum||"未提供",r.seqnum||"未提供",r.orderno||"未提供",{matched:"識別相符（查詢不改帳）",other_strategy:"已歸屬此帳戶的其他策略",unmatched:"未配對／可能為外部委託",ambiguous:"多筆相符，需人工核對",conflict:"識別相符但參數缺漏或不符，需核對"}[r.match]]]));
   if(r.pairing)card.append(el("p",`配對來源：${r.pairing.basis==="manual"?"人工確認；不能當成原始送單識別證明":r.pairing.basis==="identity"?"已核對識別":"舊紀錄未註明"}。本地建立 ${timeLabel(r.pairing.local_created_at)}；配對確認 ${timeLabel(r.pairing.confirmed_at)}。`,"notice"));
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
 finally{const controls=el("div",undefined,"editor-toolbar");controls.append(button("重新查詢券商",()=>openOrderDiagnostics(s,true)),button("查看前次保存查核",()=>openOrderDiagnostics(s,false)));body.append(controls);orderQueryBusy=false;}
}
async function openOrderEvidence(order){
 if(orderQueryBusy)return;
 orderQueryBusy=true;
 const dialog=$("order-diagnostics-dialog"),body=$("order-diagnostics-body");
 $("order-diagnostics-title").textContent="送單依據與配對來源（唯讀）";
 $("order-diagnostics-error").textContent="";body.replaceChildren(el("p","正在讀取本地送單證據…"));
 if(!dialog.open)dialog.showModal();
 try{
  const result=await command("order_evidence",{order_id:order.id}),d=result.decision;
  body.replaceChildren(el("h3",`${order.symbol} · ${order.side==="buy"?"買進":"賣出"} · ${order.broker_org||"未取得原始序號"}`),el("p",result.note,"help"));
  if(!d)body.append(el("p","舊委託未留存送單決策快照；行情、部位與預算不能事後補猜。","notice"));
  else{
   const checks=d.checks,p=d.strategy.params;
   body.append(table(["送單當時","紀錄"],[
    ["決策時間／策略版本",`${timeLabel(d.evaluated_at)} / ${d.strategy.revision}`],
    ["執行模式／方向／限價／股數",`${modeNames[d.order.mode]} / ${d.order.side==="buy"?"買進":"賣出"} / ${d.order.price} / ${d.order.quantity}`],
    ["基準／固定價差／週期",`${d.strategy.anchor} / ${d.strategy.price_gap} / ${d.strategy.cycle}`],
    ["觸發行情／來源",`${d.quote.price} USD / ${d.quote.source}`],
    ["行情接收／來源時間",`${timeLabel(d.quote.received_time)} / ${d.quote.source_time?timeLabel(d.quote.source_time):`${d.quote.raw_source_time||"未提供"}（時區未知）`}`],
    ["持股／最少／最多",`${d.position.quantity} / ${p.min_inventory} / ${p.max_inventory}`],
    ["價格界線／低於下界政策",`${p.lower_price}–${p.upper_price} / ${p.pause_buys_below_lower?"不新增買單":"只限制委託限價"}`],
    ["委託金額／單筆上限 USD",`${d.order.value} / ${p.max_order_value}`],
    ["每日買入已用／策略上限 USD",`${checks.strategy_buy_spent} / ${checks.strategy_daily_limit}`],
    ["帳戶已用／保留量／上限 USD",d.order.mode==="live"?`${checks.account_buy_spent} / ${checks.reserved_buys} / ${checks.account_daily_limit}`:"不適用"],
    ["本次送單前已用／會話上限",d.order.mode==="live"?`${checks.session_orders_used} / ${checks.session_orders_limit}`:"不適用"],
    ["預算日期／時區",`${checks.budget_day} / ${checks.budget_day_timezone}`],
    ["送單結算方式／檢查結果",`${settlementLabel(d.order.currency)} / 通過送單前檢查（不代表券商受理或成交）`]
   ]));
  }
  if(result.pairing){const p=result.pairing;body.append(el("p",`配對來源：${p.basis==="manual"?"人工確認；不是原始送單識別證明":p.basis==="identity"?"已核對識別":"舊紀錄未註明"}。本地建立 ${timeLabel(p.local_created_at)}；券商來源 ${p.broker_trade_date||""} ${p.broker_create_time||"未留存（不推定時區）"}；確認 ${timeLabel(p.confirmed_at)}。`,"notice"));}
  body.append(el("p","成交日期／時區與費用請至成交、成本損益頁核對；本快照不補齊歷史缺漏，不修改帳本、不觸發委託。","help"));
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
