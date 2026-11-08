/* Product manual: update topics alongside user-visible workflow changes. */
(function(root,factory){const api=factory();if(typeof module==="object"&&module.exports)module.exports=api;else root.ManualContent=api;})(globalThis,function(){
 "use strict";
 const definitions=[
  {id:"overview",title:"從這裡開始",group:"入門",keywords:"工作台 新手 快速開始 功能 介紹",help:["overview"],summary:"建立、預覽、核對，再決定何時啟動。基本設定保持簡單。",sections:[
   ["日常操作路徑",["先選擇執行模式，再新增網格。可用逐步精靈設定商品、價格範圍、格距與每格股數；預覽不會送單。","儲存後保持暫停。核對部位、成本與行情來源，再啟動需要監控的策略；上方摘要可查看監控、在途單與需處理項目。"]],
   ["常用入口",["「功能說明」是這個滿版手冊；各區的「？」提供當下的小提示，也可連到相關完整主題。","備份、離線行情、匯入／匯出、版面與實單設定放在「更多功能」。草稿／模板與新增網格保留在主操作列。封存策略可從清單右上方篩選。"]]],related:["modes","parameters","monitoring"]},
  {id:"modes",title:"離線、行情模擬與正式交易",group:"入門",keywords:"demo paper live 模式 登入 模擬單 實單",help:["modes"],summary:"三種模式各有自己的部位與委託；不能跨模式交接。",sections:[["未登入也可以規劃",["離線展示不要求商品行情或券商登入，可使用離線行情練習。其他模式也可先設定參數；需要帳戶時，畫面會再提示。","行情模擬只在本機撮合。正式交易需要登入、選帳、明確啟用實單及查核策略，這些動作彼此分開。"]]],related:["quotes","simulation","recovery"]},
  {id:"parameters",title:"精靈、基本參數與網格預覽",group:"策略設定",keywords:"上下界 最後成交 起始 格距 參數 精靈 網格 圖像",help:["parameters","settings","gap"],summary:"基本設定只保留規劃網格所需的項目，額外限制集中到進階選用。",sections:[
   ["參考最新價格",["已登入並有商品行情時，精靈可在最後成交價附近建議價格範圍；上方保留商品與來源時間供對照。沒有行情時直接手動設定即可。","上界低於最後成交價、或下界高於最後成交價時會提示，仍可依自己的規劃設定。參考價格不是保證成交價。"]],
   ["如何看圖",["圖像預覽標示上下界、目前基準與下一買賣點。規劃只是依參數推算，尚未代表實際委託；真正的委託與成交要到相應分頁核對。"]]],related:["limits","inventory","library"]},
  {id:"limits",title:"進階選用風控",group:"策略設定",keywords:"風控 上限 股數 margin 預算 金額 筆數",help:["limits","orderLimit"],summary:"最少保留、最多股數與金額限制都是額外選用，不放在基本設定中。",sections:[["選用與取消",["未設持股上限時，畫面顯示不另設上限；金額與送單筆數可留空。若舊策略已有設定，仍會繼續生效，請到進階選用檢查。","選用上限可能阻止新單或部位交接；遇到提示時先核對設定。啟用額外限制不會替你啟動策略。"]]],related:["parameters","monitoring","recovery"]},
  {id:"quotes",title:"行情、來源時間與等待畫面",group:"日常使用",keywords:"行情 API 等待 動畫 最後成交 更新 過期 價格",help:["market","quote"],summary:"參考數字要搭配來源與時間看；API 進行中會顯示處理狀態。",sections:[["資料取得時",["行情查詢、庫存核對或委託配對需要等待。請看正在處理的文字與動態提示；完成前不要連續點同一個確認。","時間不明或資料過期時，畫面會標示；有參考價格不等於策略當下可交易。離線行情僅用於離線展示，不會改動正式行情。"]]],related:["parameters","monitoring","troubleshooting"]},
  {id:"monitoring",title:"啟動、停止與狀態摘要",group:"日常使用",keywords:"監控 等待 全部 啟動 停止 狀態 摘要",help:["strategies","details"],summary:"儲存與啟動是分開的；停止監控會保留已成交部位。",sections:[["先處理哪一項",["每列摘要會說明正在監控、等待行情、手動暫停或需要查核。上方「需處理」集中列出有下一步可做的問題。","批次查核後會另開結果畫面，逐策略說明已恢復或被阻擋的原因；可只重新查核待處理項目。成功的策略不需重做。"]]],related:["recovery","inventory","shutdown"]},
  {id:"recovery",title:"對帳、配對與恢復監控",group:"日常使用",keywords:"核對 查核 庫存 blocker 恢復 配對 部分成功 未知委託",help:["reconcile"],summary:"核對本機與來源帳本；被阻擋的策略保持暫停並提供下一步。",sections:[
   ["看到需處理時",["先看結果卡片原因，例如停止日期已過、在途委託尚未配對、總分配超過持股。調整後重新查核，再明確確認啟動。","有舊策略佔用同一批部位時，使用「交接部位」，不要在新策略再重複登記期初股數；已重複登記時需明確勾選更正並核對成本。"]],
   ["結果未明",["API 回應中斷不代表操作失敗。交接與結束模擬提供「查詢本次處理結果」；先讀回紀錄再決定下一步，不會自動重送。未知正式委託須以券商查核為準。"]]],related:["inventory","simulation","troubleshooting"]},
  {id:"inventory",title:"期初成本與部位交接",group:"部位與歷史",keywords:"交接 部位 庫存 AUR AUR2 期初 成本 重複 過期 轉移",help:["inventory"],summary:"將剩餘部位與原成本交給新策略，舊策略的成交與已實現損益歷史保留。",sections:[
   ["交接給新策略",["停止來源、接管及同帳戶同商品的監控，先處理在途或未知委託。在來源列按「交接部位」，選擇相同模式、帳戶與商品的接管策略。","接管策略可為空部位，或尚未交易且登記相同股數的重複期初部位。若是同一批股票，明確勾選更正重複登記，重新查核交接前後的股數與成本。","確認後來源剩餘股數為0，接管策略取得部位與成本；兩邊保持暫停。核對後可封存舊策略，並另外查核及啟動新策略。來源到期不阻止交接。"]],
   ["模式與成本邊界",["正式策略交接會查核券商同商品庫存與委託；行情模擬及離線展示只查本機模擬帳本，不需為交接而登入。不同模式或帳戶不能互轉。","成本未知仍保留未知。後續補正來源期初成本時，承接成本會依帳本重算；已更正的接管重複期初不可再獨立覆蓋。交接本身不產生成交或損益。"]]],related:["simulation","archive","pnl"]},
  {id:"simulation",title:"結束模擬並保留歷史",group:"部位與歷史",keywords:"刪除 模擬單 行情模擬 paper demo SOUN 結束 清除 持股",help:[],summary:"不再使用的模擬部位可直接結束並封存，不必先建立另一個策略。",sections:[
   ["不再需要這組模擬",["停止同模式、帳戶與商品的監控，並處理在途委託。在仍有持股的模擬策略按「封存」，會開啟結束模擬預覽。","核對策略股數、剩餘成本與同商品模擬帳本的前後差異，勾選同意並確認。股數歸零且策略封存，不製造賣出成交、現金流或已實現損益，原始紀錄仍保留。","若還要由新策略繼續管理，改用「交接部位」。正式持股不提供結束模擬功能。"]]],related:["inventory","archive","modes"]},
  {id:"archive",title:"尋找與還原封存策略",group:"部位與歷史",keywords:"封存 哪裡 找回 歷史 還原 恢復 刪除",help:[],summary:"清單右上方的狀態篩選可切換使用中、已封存或全部策略。",sections:[
   ["查看原始紀錄",["將「使用中策略」切成「已封存策略」，可再依模式或名稱搜尋。選取策略後仍可閱讀設定、成交、成本與事件；「查看歷史」直接開事件分頁。","封存不會刪除策略或帳本。正式策略需先無部位、無在途委託並停止；持股可先交接。模擬持股則可在確認後結束模擬並封存。"]],
   ["回到工作台",["按「還原為暫停」並確認，再切回「使用中策略」。策略不會自動啟動，不會恢復實單授權，也不會延長停止日期。工作台最多15組未封存策略。","結束過的模擬還原時持股仍是0；原歷史可供查閱，不會重建已結束的部位。"]]],related:["simulation","inventory","library"]},
  {id:"library",title:"草稿、模板與複製策略",group:"策略設定",keywords:"保存 草稿 模板 複製 副本 重用 參數",help:[],summary:"重用設定，不複製部位、委託或實單授權。",sections:[
   ["接續未完成設定",["新增或編輯時可保存草稿；上方未完成草稿提示可接續上次輸入。「草稿／模板」可瀏覽與管理已存項目。草稿不是已啟動的策略。"]],
   ["重用常用參數",["將常用設定存為模板，或在策略列按「複製」。複製只帶參數，期初部位不沿用；需自己核對商品、模式、停止日期及進階選用設定。","若要把現有持股交給複製出的新策略，先儲存，再另用「交接部位」。讀取模板或複製都不會啟用實單。"]]],related:["parameters","transfer","inventory"]},
  {id:"transfer",title:"匯入／匯出策略與備份帳本",group:"資料管理",keywords:"匯入 匯出 JSON 舊策略 備份 還原 搬移",help:["import","backup"],summary:"策略匯出分享參數；帳本備份保留完整歷史。兩者用途不同。",sections:[["匯出哪些內容",["在「更多功能 → 匯入／匯出策略」選擇策略並匯出 JSON。僅包含可重用的參數，不帶庫存、委託、帳戶憑證或實單授權。","匯入先預覽並選擇項目，新增的策略保持暫停。舊格式仍會辨識；要延續原有真實部位，請核對後交接，不能靠匯入重建庫存。"]]],related:["library","archive","pnl"]},
  {id:"pnl",title:"成本、損益與歷史來源",group:"部位與歷史",keywords:"損益 成本 費用 美東 日期 盈虧 幣別",help:["pnl"],summary:"目前部位與成本、指定日期的成交及已實現損益分別顯示。",sections:[["交接與結束模擬後",["交接後的成本頁會列承接來源；已實現損益留在原策略。結束模擬只清除剩餘模擬部位，不把成本當作損失，也不改寫既有成交。","已封存策略仍可閱讀歷史。請依來源時間、費用及成本的完整程度解讀數值。"]]],related:["inventory","simulation","transfer"]},
  {id:"layout",title:"版面、搜尋與局部更新",group:"日常使用",keywords:"UI 字體 縮放 版面 搜尋 效能 局部 更新 固定",help:[],summary:"工作區保留捲動與分頁狀態，長紀錄分頁讀取。",sections:[["調整閱讀方式",["「更多功能 → 版面／字體」可調整字體；分隔線可拖曳或用方向鍵改變工作區比例。窄畫面改用工作區切換。","清單搜尋可搭配模式及封存篩選。資料定時更新時只更新相關內容，避免重建輸入與整個工作區。","說明頁使用獨立閱讀介面。按「返回工作台」或關閉後，回到原選取策略、分頁及未完成輸入；閱讀不會停止背景監控。"]]],related:["overview","monitoring","troubleshooting"]},
  {id:"shutdown",title:"桌面視窗與結束程式",group:"資料管理",keywords:"PyWebView 關閉 視窗 結束 登出 瀏覽器",help:[],summary:"關閉桌面視窗時，會先詢問繼續使用或結束程式。",sections:[["離開前的選擇",["「繼續使用」會返回工作台；結束程式可選保留委託，或嘗試撤單後結束。程式會停止監控、保存帳本並登出；持股不會平倉。","撤單或登出未確認時會顯示處理結果。未成交委託可能仍存在，請到券商核對。桌面關閉確認也可從閱讀說明時使用。","桌面入口為預設；可在介面設定切換下次使用瀏覽器。關閉瀏覽器分頁不會自動停止後端，請使用「結束程式」。"]]],related:["monitoring","transfer","recovery"]},
  {id:"troubleshooting",title:"常見問題與下一步",group:"支援",keywords:"錯誤 問題 卡住 不能 啟動 API 沒反應 不成功",help:[],summary:"從畫面顯示的原因處理，保留已成功的結果。",sections:[
   ["分配後持股超過來源庫存",["檢查是否將同一批持股重複登記到兩組策略。先停止同商品策略，再從舊策略交接，核對是否需要更正接管策略的重複期初登記。"]],
   ["不能封存或交接",["檢查是否仍監控中、有在途或未知委託，或接管策略不同模式／帳戶／商品。模擬部位不再使用時可確認結束並封存；正式持股需交接或實際處理。"]],
   ["等待很久或連線中斷",["保留錯誤與稽核紀錄。交接或結束模擬若結果未明，先按查詢本次結果；委託則先查單與對帳，避免把未知結果當成失敗而重送。"]]],related:["recovery","inventory","simulation"]},
  {id:"showcase",title:"慢速原介面展示放映（全部為假資料）",group:"入門",keywords:"展示 動畫 放映 176秒 半速 0.5倍速 浮動旁白 假資料 播放 重播 暫停 返回",help:[],summary:"共用原工作台介面，觀看動畫游標操作登入、策略精靈、實單確認、對帳與兩次進出場。",sections:[["開始與重播",["按頂端「展示放映」，再按「開始慢速展示」。畫面會自動點擊原有登入視窗、填寫假帳號密碼、操作策略精靈、儲存網格，再依序完成原實單確認、對帳與啟動；價格跳動後切換原委託、成交與成本頁。","畫面內會浮出大字旁白卡，盡量避開當前操作欄位。以0.5倍速播放，約2分56秒，暫停時間不計；可暫停、繼續或重新播放。展示費用固定為0，價差數字是預先編排，並非實際績效。"]],["與工作台的關係",["展示使用原來的表單與按鈕，但使用者不能操作frame內的功能，只能控制外層播放；內部由動畫游標演示。嘗試點擊原介面按鈕或輸入時會先暫停，詢問繼續播放或停止展示並返回工作台；該次操作不會執行。假帳號、行情、庫存與成交不會寫入帳本，也不需要券商登入。","按「返回工作台」會結束放映並回到原選取與分頁。原工作台若已有監控，仍依原狀態運行；切換展示不會啟動、停止或變更原策略。展示放映與可手動操作的「離線展示」策略是不同功能。"]]],related:["overview","modes","parameters"]},
  {id:"changes",title:"本版更新與使用邊界",group:"支援",keywords:"版本 更新 0.1.13 說明 支援",help:[],summary:"0.1.13：半速原介面放映、畫面浮動旁白與操作時的暫停／返回提示。",sections:[["這次新增",["頂端「展示放映」可觀看登入、建立實單網格、開啟監控、價格跳動與兩次進出場，附文字旁白與播放控制。所有資料固定為虛構，不操作真實功能。","展示是獨立唯讀播放器，返回後保留原工作台與監控狀態。既有離線策略演練、模擬部位交接、封存及完整說明可繼續使用。"]],["資料相容性",["帳本格式維持第5版，本次不變更帳本資料或交易引擎。請備份後升級；第5版帳本不可交給更舊的程式開啟。"]]],related:["showcase","overview","simulation"]}
 ];
 const normalize=value=>String(value||"").normalize("NFKC").toLocaleLowerCase();
 function topics(help={}){return definitions.map(topic=>({...topic,sections:[...topic.sections,...topic.help.filter(key=>help[key]).map(key=>[help[key][0],help[key][1]])]}));}
 function resolve(key){return definitions.find(topic=>topic.id===key||topic.help.includes(key))?.id||"overview";}
 function search(all,query){const tokens=normalize(query).trim().split(/\s+/).filter(Boolean);return all.filter(topic=>tokens.every(token=>normalize([topic.title,topic.summary,topic.keywords,...topic.sections.flat(2)].join(" ")).includes(token))).sort((a,b)=>{const rank=t=>tokens.filter(token=>normalize(t.title+" "+t.keywords).includes(token)).length;return rank(b)-rank(a);});}
 return {topics,resolve,search};
});
