"use strict";
const manualPage=el("dialog");manualPage.id="manual-page";manualPage.setAttribute("aria-labelledby","manual-title");
const manualHeader=el("header",undefined,"manual-header"),manualBrand=el("div"),manualTitle=el("h1","功能說明"),manualSearch=el("input"),manualReturn=button("返回工作台",()=>manualPage.close(),"primary"),manualClose=button("×",()=>manualPage.close(),"close");manualTitle.id="manual-title";manualBrand.append(manualTitle,el("small","美股網格工作台 · 操作手冊"));manualSearch.id="manual-search";manualSearch.type="search";manualSearch.placeholder="搜尋主題、功能或問題";manualSearch.setAttribute("aria-label","搜尋功能說明");manualReturn.id="manual-return";manualClose.setAttribute("aria-label","關閉功能說明並返回工作台");manualHeader.append(manualBrand,manualSearch,manualReturn,manualClose);
const manualLayout=el("div",undefined,"manual-layout"),manualSidebar=el("nav",undefined,"manual-sidebar"),manualCount=el("p",undefined,"help"),manualTopics=el("div"),manualArticle=el("article",undefined,"manual-article");manualSidebar.setAttribute("aria-label","說明主題");manualCount.id="manual-results";manualCount.setAttribute("role","status");manualTopics.id="manual-topics";manualSidebar.append(manualCount,manualTopics);manualArticle.id="manual-article";manualArticle.tabIndex=-1;manualLayout.append(manualSidebar,manualArticle);manualPage.append(manualHeader,manualLayout);document.body.append(manualPage);
let manualCurrent="overview",manualCatalog=[],manualFocus=null,manualPreviousTitle="";
function manualLink(topic){const link=button(topic.title,()=>selectManualTopic(topic.id));link.dataset.topic=topic.id;if(topic.id===manualCurrent)link.setAttribute("aria-current","page");return link;}
function renderManualTopics(){
 const found=ManualContent.search(manualCatalog,manualSearch.value);manualCount.textContent=manualSearch.value.trim()?`找到 ${found.length} 個主題`:`共 ${found.length} 個主題`;
 manualTopics.replaceChildren();const groups=new Map();
 for(const topic of found){if(!groups.has(topic.group))groups.set(topic.group,[]);groups.get(topic.group).push(topic);}
 for(const [group,topics]of groups){manualTopics.append(el("h2",group));for(const topic of topics)manualTopics.append(manualLink(topic));}
 if(!found.length)manualTopics.append(el("p","找不到相符主題，試試「交接」、「封存」或「草稿」。","help"));
}
function selectManualTopic(key,focus=true){
 manualCurrent=ManualContent.resolve(key);const topic=manualCatalog.find(t=>t.id===manualCurrent);manualArticle.replaceChildren();
 manualArticle.append(el("p",topic.group,"manual-eyebrow"));const heading=el("h2",topic.title);heading.tabIndex=-1;manualArticle.append(heading,el("p",topic.summary,"manual-intro"));
 const toc=el("nav",undefined,"manual-toc");toc.setAttribute("aria-label","本頁章節");toc.append(el("strong","本頁內容"));
 topic.sections.forEach(([title,paragraphs],index)=>{const section=el("section"),h=el("h3",title);h.id=`manual-section-${index}`;section.append(h);for(const paragraph of paragraphs)section.append(el("p",paragraph));manualArticle.append(section);toc.append(button(title,()=>h.scrollIntoView({block:"start"})));});
 manualArticle.insertBefore(toc,manualArticle.children[3]||null);
 const related=el("aside",undefined,"manual-related");related.append(el("h3","相關主題"));for(const id of topic.related){const other=manualCatalog.find(t=>t.id===id);if(other)related.append(manualLink(other));}manualArticle.append(related);
 manualArticle.scrollTop=0;renderManualTopics();if(focus)heading.focus({preventScroll:true});
}
function openManual(key="overview"){
 if(!manualPage.open){manualFocus=document.activeElement;manualPreviousTitle=document.title;manualSearch.value="";manualCatalog=ManualContent.topics(HELP);manualPage.showModal();}
 selectManualTopic(key);document.title="功能說明 · 美股網格工作台";
}
manualSearch.addEventListener("input",renderManualTopics);
manualSearch.addEventListener("keydown",event=>{if(event.key==="Enter"){const first=ManualContent.search(manualCatalog,manualSearch.value)[0];if(first)selectManualTopic(first.id);}});
manualPage.addEventListener("close",()=>{document.title=manualPreviousTitle;if(manualFocus?.isConnected)manualFocus.focus({preventScroll:true});});
$("manual-open").addEventListener("click",()=>openManual());
