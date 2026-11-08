"use strict";
const presentationLabel=el("label","啟動介面（下次啟動生效）"),presentationSelect=el("select");presentationSelect.id="presentation-mode";
for(const [value,text]of [["desktop","桌面視窗 · PyWebView（預設）"],["browser","瀏覽器 · Web UI"]]){const option=el("option",text);option.value=value;presentationSelect.append(option);}
presentationLabel.append(presentationSelect);$("layout-dialog").querySelector(".dialog-actions").before(presentationLabel,el("p","桌面視窗的關閉按鈕會先確認是否結束程式。瀏覽器關閉頁面不會停止後端，仍可使用「結束程式」。","help"));
$("layout-open").addEventListener("click",()=>{presentationSelect.value=state?.presentation_mode||"desktop";});
presentationSelect.addEventListener("change",()=>safe(async()=>{presentationSelect.disabled=true;try{await command("presentation_mode",{mode:presentationSelect.value});await refresh();toast("啟動介面已保存，下次啟動生效。");}finally{presentationSelect.disabled=false;}},"layout-status"));
