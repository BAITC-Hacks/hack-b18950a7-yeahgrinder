import { dataProvider, demo, suppliers, urgency } from './mockDashboard.js';
import { buildWorkbook } from './export.js';

const $ = selector => document.querySelector(selector);
const fmt = n => new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(n);
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths = {
  arrow:'M7 17 17 7M7 7h10v10', chevron:'m9 5 7 7-7 7', close:'m6 6 12 12M18 6 6 18',
  box:'m12 3 9 5-9 5-9-5 9-5ZM3 8v9l9 5 9-5V8M12 13v9',
  alert:'m12 3 10 18H2L12 3ZM12 9v5M12 17h.01',
  bag:'M4 7h16l-1 14H5L4 7ZM8 7V6a4 4 0 0 1 8 0v1',
  truck:'M2 6h12v12H2V6ZM14 10h4l4 4v4h-8M7 18h.01M18 18h.01',
  search:'M16 16l5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  filter:'M4 6h16M7 12h10M10 18h4', download:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
  check:'m5 12 4 4L19 6', plus:'M12 5v14M5 12h14', trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7',
  info:'M12 11v6M12 7h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  file:'M14 2H5v20h14V7l-5-5ZM14 2v6h5M8 12h8M8 16h6',
  settings:'M4 7h16M4 17h16M8 4v6M16 14v6', reset:'M3 10a9 9 0 1 1 1 8M3 4v6h6',
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name]||paths.box}"/></svg>`;
const badge = p => `<span class="risk-badge ${urgency[p.urgency].className}">${urgency[p.urgency].label}</span>`;
const unit = (n,p) => `${fmt(n)} ${esc(p.unit)}`;
const positions = n => `${fmt(n)} ${n%10===1&&n%100!==11?'позиция':n%10>=2&&n%10<=4&&(n%100<12||n%100>14)?'позиции':'позиций'}`;
const days = n => n == null ? 'Нет данных' : `${fmt(n)} ${n%10===1&&n%100!==11?'день':n%10>=2&&n%10<=4&&(n%100<12||n%100>14)?'дня':'дней'}`;
const storageKey = 'supplyai-demo-v2';
let storageFailed=false;
function readSaved() {
  try { const raw=JSON.parse(localStorage.getItem(storageKey)||'null');
    if(raw?.version===demo.version && raw.edits && Array.isArray(raw.draft)) return raw;
  } catch { storageFailed=true; }
  return {version:demo.version,edits:{},draft:[],rejected:[],approved:null,scenario:'base'};
}
const saved=readSaved();
const state={page:'Сегодня',review:false,restored:false,tab:'recommendations',query:'',supplier:'Все',category:'Все',risk:'Все',transit:'Все',oneoff:'Все',abc:'Все',selected:new Set(),productId:null,detailTab:'recommendation',chartId:'iek-1',showSpike:false};
let allProducts=[], sources=[];
function persist(){try{localStorage.setItem(storageKey,JSON.stringify(saved));}catch{storageFailed=true; toast('Сохранение недоступно. Правки останутся только до закрытия страницы.');}}
function invalidate(){saved.approved=null;}
function products(){return allProducts.map(p=>saved.scenario==='growth'?{...p,...p.growthScenario,need:p.growthScenario.forecast_horizon+p.safety_stock-p.stock_current-p.in_transit_in_horizon}:p);}
function find(id){return products().find(p=>p.id===id);}
function quantity(p){return saved.edits[p.id]?.qty ?? p.recommended_qty;}
function unitCost(p){return p.unit_cost ?? (p.order_value&&p.recommended_qty?p.order_value/p.recommended_qty:null);}
function rationale(p){const base=p.reason_text||'';return changed(p)?`${base} Менеджер изменил количество: ${fmt(p.recommended_qty)} → ${fmt(quantity(p))} ${p.unit}${comment(p)?` (${comment(p)})`:''}.`.trim():base;}
function changed(p){return quantity(p)!==p.recommended_qty;}
function comment(p){return saved.edits[p.id]?.comment||'';}
function filtered(){const q=state.query.trim().toLocaleLowerCase('ru');return products().filter(p=>(state.supplier==='Все'||p.supplier===state.supplier)&&(state.category==='Все'||p.category===state.category)&&(state.transit==='Все'||(state.transit==='Есть'?p.in_transit_in_horizon>0:!p.in_transit_in_horizon))&&(state.oneoff==='Все'||(p.excluded_events?.length>0))&&(state.abc==='Все'||p.abc===state.abc)&&(!state.review||needsReview(p))&&(!state.restored||p.lost_qty>0)&&(!q||`${p.name} ${p.article} ${p.sku} ${p.supplier}`.toLocaleLowerCase('ru').includes(q)));}
function toast(message){$('#toast').textContent=message;$('#toast').classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').classList.remove('show'),3500);}
function navigate(page,tab='recommendations'){state.page=page;state.tab=tab;state.query='';state.supplier='Все';state.category='Все';state.risk='Все';state.transit='Все';state.oneoff='Все';state.abc='Все';state.review=false;state.restored=false;state.selected.clear();render();window.scrollTo({top:0,behavior:'instant'});}
function header(title,description,actions=''){return `<header class="header"><div><div class="eyebrow">ПЛАНИРОВАНИЕ ЗАПАСОВ / ${saved.scenario==='growth'?'СЦЕНАРИЙ +20%':'22 СЕНТЯБРЯ 2026'}</div><h1>${title}</h1><p>${description}</p></div><div class="header-actions">${actions}</div></header>`;}
const btn = (text,action,kind='',extra='')=>`<button class="btn ${kind}" data-action="${action}" ${extra}>${text}</button>`;
function empty(title,description,action=''){return `<div class="empty"><div class="empty-icon">${icon('box')}</div><h2>${title}</h2><p>${description}</p>${action}</div>`;}
function filterBar(category=true){return `<div class="filters"><label class="filter-search">${icon('search')}<input id="list-search" aria-label="Поиск по товару, коду или артикулу" placeholder="Товар, код 1С или артикул" value="${esc(state.query)}"></label><select id="supplier-filter" aria-label="Поставщик"><option value="Все">Все поставщики</option>${suppliers.map(s=>`<option ${state.supplier===s?'selected':''}>${s}</option>`).join('')}</select>${category?`<select id="category-filter" aria-label="Категория"><option value="Все">Все категории</option>${[...new Set(allProducts.map(p=>p.category))].map(c=>`<option ${state.category===c?'selected':''}>${c}</option>`).join('')}</select>`:''}${extraFilters()}${state.review?btn('На проверку ✕','clear-review','chip active'):''}${state.restored?btn('Досчитан спрос ✕','clear-restored','chip active'):''}${state.query||state.supplier!=='Все'||state.category!=='Все'||state.transit!=='Все'||state.oneoff!=='Все'||state.abc!=='Все'||state.review||state.restored?btn('Сбросить','clear-filters','text'):''}</div>`;}

function extraFilters(){
 const abcs=[...new Set(allProducts.map(p=>p.abc).filter(Boolean))].sort();
 const sel=(id,label,value,opts)=>`<select id="${id}" aria-label="${label}">${opts.map(([v,t])=>`<option value="${v}" ${value===v?'selected':''}>${t}</option>`).join('')}</select>`;
 return sel('transit-filter','Товар в пути',state.transit,[['Все','В пути: все'],['Есть','Есть в пути'],['Нет','Ничего нет в пути']])
  +(allProducts.some(p=>p.excluded_events?.length)?sel('oneoff-filter','Разовые сделки',state.oneoff,[['Все','Разовые сделки: все'],['Есть','Была разовая сделка']]):'')
  +(abcs.length?sel('abc-filter','Категория важности',state.abc,[['Все','Важность: все'],...abcs.map(a=>[a,`Категория ${a}`])]):'');
}
const TABLE_LIMIT=300;
function table(rows,{select=false,catalog=false}={}){
 if(!rows.length)return empty('Ничего не найдено','Попробуйте изменить запрос или сбросить фильтры.',btn('Сбросить фильтры','clear-filters'));
 const selectable=rows.filter(p=>!saved.rejected.includes(p.id));
 return `<div class="table-wrap"><table><thead><tr>${select?`<th class="check-cell"><input type="checkbox" id="select-all" aria-label="Выбрать все показанные рекомендации" ${selectable.length&&selectable.every(p=>state.selected.has(p.id))?'checked':''}></th>`:''}<th>Товар</th><th class="num">Остаток</th><th class="num">В пути</th><th class="num">Запаса хватит</th><th class="num">${catalog?'Рекомендация':'К заказу'}</th><th>Срочность</th><th></th></tr></thead><tbody>${rows.slice(0,TABLE_LIMIT).map(p=>`<tr data-row="${p.id}" class="${state.selected.has(p.id)?'selected':''}">${select?`<td class="check-cell"><input type="checkbox" data-select="${p.id}" aria-label="Выбрать ${esc(p.name)}" ${state.selected.has(p.id)?'checked':''} ${saved.rejected.includes(p.id)?'disabled':''}></td>`:''}<td class="product"><button class="product-open" data-action="product" data-id="${p.id}"><strong>${esc(p.name)}</strong><small>${esc(p.article)} · ${p.supplier}</small>${p.quality==='Нужна проверка'?'<span class="quality-dot">Нужна проверка данных</span>':''}</button></td><td class="num" data-label="Остаток">${unit(p.stock_current,p)}</td><td class="num" data-label="В пути">${p.in_transit_in_horizon?unit(p.in_transit_in_horizon,p):'—'}</td><td class="num" data-label="Хватит на">${coverCell(p)}</td><td class="num order" data-label="${catalog?'Рекомендация':'К заказу'}">${saved.rejected.includes(p.id)?'<span class="muted">Отклонено</span>':quantity(p)?unit(quantity(p),p):'—'}${changed(p)?'<small class="edited" style="display:block;margin-top:4px;font-size:12px">Изменено вами</small>':''}${saved.draft.includes(p.id)?'<small style="display:block;margin-top:4px;font-size:12px">В черновике</small>':''}</td><td class="urgency-cell">${badge(p)}</td><td class="arrow-cell"><button class="btn text small" data-action="product" data-id="${p.id}" aria-label="Открыть ${esc(p.name)}">${icon('chevron')}</button></td></tr>`).join('')}</tbody></table><div class="table-info">${positions(rows.length)}${rows.length>TABLE_LIMIT?` · показаны первые ${TABLE_LIMIT} — уточните поиск или фильтр`:''} · Количества указаны в единицах товара · Нажмите на строку для объяснения</div></div>`;
}
function chart(p,{detail=false,spike=false}={}){
 const series=p.history;
 const values=series.flatMap(x=>[x.clean,x.forecast,...(!p.excluded_events.length||spike?[x.raw]:[])]).filter(x=>x!==null);
 const max=Math.max(...values,1)*1.13, X=i=>42+i*75, Y=n=>155-n/max*130;
 const points=key=>series.map((s,i)=>s[key]===null?'':`${X(i)},${Y(s[key])}`).filter(Boolean).join(' ');
 const yLabel=v=>v>=1000?`${fmt(v/1000)}к`:fmt(Math.round(v));
 const rawVisible=!p.excluded_events.length||spike;
 return `<div class="plot"><svg viewBox="0 0 600 190" role="img" aria-label="${esc(p.name)}: продажи, скорректированный спрос и прогноз, ${esc(p.unit)}"><defs><linearGradient id="shade-${detail?'detail':'overview'}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#4285ee" stop-opacity=".06"/><stop offset="1" stop-color="#4285ee" stop-opacity=".06"/></linearGradient></defs>${[0,.5,1].map(f=>`<line x1="42" x2="580" y1="${Y(max*f)}" y2="${Y(max*f)}" stroke="#edf1f7"/><text class="plot-label" x="33" y="${Y(max*f)+4}" text-anchor="end">${yLabel(max*f)}</text>`).join('')}<rect x="417" y="12" width="165" height="144" fill="#f3f8ff" opacity=".6"/><line x1="417" x2="417" y1="12" y2="156" stroke="#cfddee" stroke-dasharray="3 4"/><text class="plot-label" x="448" y="23">Прогноз</text><polygon points="42,155 ${points('clean')} 417,155" fill="url(#shade-${detail?'detail':'overview'})"/>${rawVisible?`<polyline points="${points('raw')}" fill="none" stroke="#b6c1d1" stroke-width="2"/>`:''}<polyline points="${points('clean')}" fill="none" stroke="#4384ed" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/><polyline points="${points('forecast')}" fill="none" stroke="#58a687" stroke-width="2.5" stroke-dasharray="5 5" stroke-linecap="round"/>${series.map((s,i)=>`<text class="plot-label" text-anchor="middle" x="${X(i)}" y="179">${s.month}</text><circle tabindex="0" data-chart-point="${i}" data-product="${p.id}" cx="${X(i)}" cy="${Y(s.clean??s.forecast)}" r="5" fill="#fff" stroke="${i>5?'#58a687':'#4384ed'}" stroke-width="2"><title>${s.month}: ${s.raw!==null?`факт ${unit(s.raw,p)}, скорректировано ${unit(s.clean,p)}`:`прогноз ${unit(s.forecast,p)}`}</title></circle>`).join('')}</svg><div class="plot-tooltip" role="status"></div></div><div class="chart-legend">${rawVisible?'<span><i class="key raw"></i>Факт</span>':''}<span><i class="key"></i>Скорректированный спрос</span><span><i class="key forecast"></i>Прогноз</span></div>${p.excluded_events.length?`<div class="context-note">${icon('info')} ${spike?'Полная шкала: виден разовый всплеск.':'Масштаб регулярного спроса; разовая продажа скрыта.'} ${detail?btn(spike?'Регулярный спрос':'Показать всплеск','spike','text small'):''}</div>`:''}`;
}

const money = v => v>=1e6 ? `${fmt(Math.round(v/1e5)/10)} млн ₸` : `${fmt(Math.round(v))} ₸`;
const KPI_TIPS = {
  order:'Позиций, по которым расчёт рекомендует заказ',
  critical:'Запаса не хватит до прихода новой поставки',
  bare:'Критично и ничего нет в пути: без заказа позиция уйдёт в дефицит — здесь нужно вмешаться сейчас',
  oneoff:'У стольких товаров крупные разовые продажи исключены и не раздувают регулярный заказ',
  value:'Сумма рекомендованного заказа по себестоимости. У IEK цен в данных нет',
};
function supplierKpis(ps){
 const cards=suppliers.map((s,i)=>{const rows=ps.filter(p=>p.supplier===s);if(!rows.length)return '';
  const crit=rows.filter(p=>p.urgency==='CRITICAL'),bare=crit.filter(p=>!p.in_transit_in_horizon).length;
  const value=rows.reduce((a,p)=>a+(p.order_value||0),0);
  const estimated=rows.filter(p=>(p.warnings||[]).some(w=>w.startsWith('Остаток оценочный'))).length;
  const tiles=[
   ['К заказу',rows.filter(p=>p.recommended_qty>0).length,'позиций','order','recommendations',''],
   ['Критично',crit.length,'запас кончится','critical','urgent','warn'],
   ['Нет в пути',bare,'и ничего не едет','bare','urgent-bare',bare?'alert':''],
   ['Разовые сделки',rows.filter(p=>p.excluded_events?.length).length,'исключены','oneoff','oneoff','calm'],
  ];
  return `<article class="card supplier-kpi"><div class="supplier-kpi-head"><div class="supplier-logo ${i?'se':''}">${i?'SE':'IEK'}</div><div class="supplier-kpi-title"><h2>${s}</h2><div class="card-sub">${fmt(rows.length)} товаров · поставка ${esc(demo.leadText?.[s]||(i?'45 дней · допущение':'24–30 дней · демо-срок'))}</div></div>${value?`<div class="supplier-value" title="${esc(KPI_TIPS.value)}"><small>Сумма заказа</small><strong>${money(value)}</strong></div>`:''}</div><div class="kpi-grid">${tiles.map(([label,v,hint,tip,action,tone])=>`<button class="kpi ${tone}" data-action="${action}" data-supplier="${s}" title="${esc(KPI_TIPS[tip])}"><span>${label}</span><strong>${fmt(v)}</strong><small>${hint}</small></button>`).join('')}</div>${estimated?`<div class="kpi-note">${icon('info')}<span>${estimated===rows.length?`Остаток всех товаров ${i?'поставщика':'ИЭК'}`:`Остаток ${fmt(estimated)} ${estimated%10===1&&estimated%100!==11?'товара':'товаров'}`} — оценка: приходы за сентябрь в выгрузке не видны. Сверьте с 1С перед заказом.</span></div>`:''}</article>`;}).join('');
 return cards?`<section class="supplier-kpis" aria-label="Показатели по поставщикам">${cards}</section>`:'';
}

// --- «Что делать сегодня?»: стартовый экран — список дел вместо сухих цифр
const IMPORTANCE={'1':0,'A':0,'2':1,'B':1,'5':1,'3':2,'C':2,'7':3};
const rankOf=p=>IMPORTANCE[p.abc]??3;
const needsReview=p=>p.recommended_qty>0&&(p.warnings||[]).some(w=>w.startsWith('Округление')||w.startsWith('Заказ заметно'));
const supplierName=s=>s==='IEK'?'ИЭК':s;
function todayTasks(){
 const ps=products(), hasAbc=ps.some(p=>p.abc);
 const order=(a,b)=>rankOf(a)-rankOf(b)||(a.cover_days??0)-(b.cover_days??0)||(b.order_value||0)-(a.order_value||0)||quantity(b)-quantity(a);
 const orders=suppliers.map(s=>{
  const bare=ps.filter(p=>p.supplier===s&&p.urgency==='CRITICAL'&&!p.in_transit_in_horizon&&quantity(p)>0&&!saved.rejected.includes(p.id)).sort(order);
  const important=hasAbc?bare.filter(p=>rankOf(p)===0):bare;
  const items=important.length?important:bare;
  return {key:`order-${s}`,supplier:s,items,rest:bare.length-items.length,important:hasAbc&&important.length>0};
 }).filter(t=>t.items.length);
 return {orders,review:ps.filter(needsReview).sort(order),coming:ps.filter(p=>p.urgency==='CRITICAL'&&p.in_transit_in_horizon>0).sort(order),ps};
}
function todayItem(p){return `<button class="today-item" data-action="product" data-id="${p.id}"><span class="ti-name">${esc(p.name)}<small>${esc(p.article)}</small></span><span class="ti-stock">${coverCell(p)}</span><span class="ti-qty">${unit(quantity(p),p)}</span></button>`;}
function orderTask(t,n){
 const done=t.items.every(p=>saved.draft.includes(p.id)), value=t.items.reduce((a,p)=>a+(p.order_value||0),0);
 const imp=t.important?(t.supplier==='IEK'?' категории A':' категории 1'):'';
 return `<article class="today-card fire ${done?'done':''}"><div class="today-card-head"><span class="today-step">${done?icon('check'):n}</span><div><div class="today-tag">🔥 Сегодня</div><h2>Заказать у ${esc(supplierName(t.supplier))}</h2><p><strong>${positions(t.items.length)}${imp}</strong> закончатся раньше, чем придёт новая поставка, а по ним ничего не едет.${value?` Сумма ≈ <strong>${money(value)}</strong>.`:''}</p></div></div><div class="today-items">${t.items.slice(0,5).map(todayItem).join('')}${t.items.length>5?`<button class="today-more" data-action="today-open" data-supplier="${esc(t.supplier)}" data-important="${t.important?'1':''}">и ещё ${positions(t.items.length-5)} ${icon('arrow')}</button>`:''}</div><div class="today-actions">${done?`<span class="today-done">${icon('check')} Все в черновике</span>${btn('Открыть черновик','open-draft','small')}`:btn(icon('plus')+` Добавить ${fmt(t.items.length)} в черновик`,'today-add','primary',`data-task="${esc(t.key)}"`)+btn('Списком','today-open','small',`data-supplier="${esc(t.supplier)}" data-important="${t.important?'1':''}"`)}</div>${t.rest>0?`<div class="today-later">${icon('info')}<span>Ещё ${positions(t.rest)} менее важных тоже без поставки — можно заказать на этой неделе.</span><button class="link-btn" data-action="today-open" data-supplier="${esc(t.supplier)}">Показать</button></div>`:''}</article>`;
}
function smallTask(kind,tag,n,title,text,items,action,label){
 return `<article class="today-card ${kind}"><div class="today-card-head"><span class="today-step">${n}</span><div><div class="today-tag">${tag}</div><h2>${title}</h2><p>${text}</p></div></div>${items.length?`<div class="today-items">${items.slice(0,3).map(todayItem).join('')}</div>`:''}<div class="today-actions">${btn(label,action,kind==='warn'?'primary':'small')}</div></article>`;
}
function dateLabel(){const d=demo.asOf?new Date(demo.asOf+'T12:00:00'):new Date();return `${d.toLocaleDateString('ru-RU',{day:'numeric',month:'long'})} · ${demo.live?'выгрузка 1С':'демо-данные'}`;}
function today(){
 const {orders,review,coming,ps}=todayTasks();
 const draftRows=saved.draft.map(find).filter(p=>p&&quantity(p)>0);
 const h=new Date().getHours(), hello=h<12?'Доброе утро':h<18?'Добрый день':'Добрый вечер';
 const done=orders.filter(t=>t.items.every(p=>saved.draft.includes(p.id))).length;
 const oneoff=ps.filter(p=>p.excluded_events?.length).length, restored=ps.filter(p=>p.lost_qty>0).length;
 const loop=ps.find(p=>p.sku==='130200305_')||ps.find(p=>p.excluded_events?.length);
 let n=orders.length;
 const hero=`<section class="today-hero"><div><div class="eyebrow">${esc(dateLabel())}</div><h1>${hello}! Что делать сегодня 🔥</h1><p>Из ${fmt(ps.length)} товаров отобрали то, что требует решения сегодня. Начните сверху — остальное подождёт.</p></div>${orders.length?`<button class="today-progress" data-action="open-draft" title="Открыть черновик"><strong>${done} из ${orders.length}</strong><span>срочных заказов собрано</span><div class="bar"><i style="width:${done/orders.length*100}%"></i></div></button>`:''}</section>${aiBriefBlock()}`;
 const fire=orders.length?`<section class="today-fire">${orders.map((t,i)=>orderTask(t,i+1)).join('')}</section>`:`<div class="notice info"><div>${icon('check')}</div><div><strong>Срочных заказов нет</strong>По всем критичным позициям уже едет поставка.</div></div>`;
 const side=[
  review.length?smallTask('warn','⚠️ Проверить',++n,'Проверить перед заказом',`<strong>${positions(review.length)}</strong>: кратность сильно увеличила заказ или он заметно больше обычных продаж. Посмотрите и поправьте количество.`,review,'today-review','Разобрать список'):'',
  coming.length?smallTask('info','🚚 Проследить',++n,'Проследить за поставками',`<strong>${positions(coming.length)}</strong> в критичном запасе, но поставка уже едет. Проверьте, что приход не задержится.`,coming,'today-coming','Посмотреть'):'',
  `<article class="today-card ok"><div class="today-card-head"><span class="today-step">${icon('check')}</span><div><div class="today-tag">✅ Уже сделано за вас</div><h2>Расчёт готов</h2></div></div><ul class="today-checks"><li><button class="check-link" data-action="catalog">Пересчитали <strong>${fmt(ps.length)}</strong> товаров по данным на ${esc(dateLabel().split(' · ')[0])}</button></li>${oneoff?`<li><button class="check-link" data-action="oneoff">Исключили разовые сделки у <strong>${fmt(oneoff)}</strong> товаров — они не раздувают заказ</button></li>`:''}${restored?`<li><button class="check-link" data-action="today-restored">Досчитали спрос у <strong>${fmt(restored)}</strong> товаров за месяцы без остатка</button></li>`:''}</ul><div class="today-actions">${loop?btn('Пример: разовая сделка','today-example','small',`data-id="${esc(loop.id)}"`):''}${btn('Весь обзор','go-overview','small')}</div></article>`,
 ].join('');
 const bar=draftRows.length?`<div class="today-draftbar glass"><span>${icon('bag')} В черновике <strong>${positions(draftRows.length)}</strong></span>${btn('Перейти к утверждению '+icon('arrow'),'open-draft','primary')}</div>`:'';
 return hero+fire+`<section class="today-side">${side}</section>`+bar;
}
function categoryTrends(ps){
 // Demand index per category: each product is normalised to its own actual-month mean (units differ:
 // шт/м/упак), then averaged per month. 100 = the category's usual level; dashed = forecast.
 const win=p=>(p.history||[]).map(h=>h.month).join('|'),freq={};for(const p of ps)freq[win(p)]=(freq[win(p)]||0)+1;
 const ref=Object.entries(freq).sort((a,b)=>b[1]-a[1])[0]?.[0]||'',labels=ref?ref.split('|'):[];
 if(labels.length<5)return '';
 const byCat={};
 for(const p of ps){
  if(win(p)!==ref)continue;
  const act=(p.history||[]).filter(h=>h.raw!==null&&h.clean!==null);
  const mean=act.reduce((a,h)=>a+h.clean,0)/(act.length||1);
  if(act.filter(h=>h.clean>0).length<4||!(mean>0))continue;  // regular sellers only: a lone sale would dominate the index
  const row={};for(const h of p.history){const v=h.raw!==null?h.clean:h.forecast;if(v!==null)row[h.month]=v/mean*100;}
  (byCat[p.category]??=[]).push(row);
 }
 const isFc=Object.fromEntries((ps.find(p=>win(p)===ref)?.history||[]).map(h=>[h.month,h.raw===null]));
 const avg=a=>a.reduce((x,y)=>x+y,0)/a.length;
 const med=a=>{const b=[...a].sort((x,y)=>x-y),m=b.length>>1;return b.length%2?b[m]:(b[m-1]+b[m])/2;};  // robust to single outlier products
 const rows=Object.entries(byCat).filter(([,l])=>l.length>=10).map(([cat,l])=>{
  const idx=labels.map(m=>{const v=l.map(r=>r[m]).filter(v=>v!==undefined);return v.length?med(v):null;});
  const act=idx.filter((v,i)=>v!==null&&!isFc[labels[i]]),fc=idx.filter((v,i)=>v!==null&&isFc[labels[i]]);
  const recent=act.slice(-3),early=act.slice(0,3);
  return {cat,n:l.length,idx,change:Math.round((avg(recent)/avg(early)-1)*100),fc:fc.length?Math.round((avg(fc)/avg(recent)-1)*100):null};
 }).sort((a,b)=>b.n-a.n).slice(0,8);
 if(!rows.length)return '';
 const actLabels=labels.filter(m=>!isFc[m]);
 const spark=r=>{const vals=r.idx.filter(v=>v!==null),lo=Math.min(...vals),hi=Math.max(...vals),span=hi-lo||1;
  const X=i=>4+i*(152/(labels.length-1)),Y=v=>32-(v-lo)/span*28;
  const line=keep=>r.idx.map((v,i)=>v===null||!keep(i)?'':`${X(i).toFixed(1)},${Y(v).toFixed(1)}`).filter(Boolean).join(' ');
  const lastAct=labels.findLastIndex(m=>!isFc[m]);
  return `<svg class="trend-spark" viewBox="0 0 160 36" aria-hidden="true"><polyline points="${line(i=>i<=lastAct)}" fill="none" stroke="#475467" stroke-width="1.6"/><polyline points="${line(i=>i>=lastAct)}" fill="none" stroke="#98a2b3" stroke-width="1.6" stroke-dasharray="3 3"/></svg>`;};
 const pct=v=>v===null?'—':`${v>0?'+':v<0?'−':''}${Math.abs(v)}%`;
 return `<section class="card category-trends"><div class="card-heading"><div><h2>Тренд спроса по категориям</h2><div class="card-sub">Медианный индекс спроса без разовых сделок, 100 — обычный уровень товара · изменение: ${actLabels.slice(-3)[0]}–${actLabels.at(-1)} к ${actLabels[0]}–${actLabels[2]} · пунктир — прогноз</div></div></div><div class="trend-list">${rows.map(r=>`<button class="trend-row" data-action="category-trend" data-category="${esc(r.cat)}"><span class="trend-name">${esc(r.cat)}<small>${r.n} товаров</small></span>${spark(r)}<span class="trend-change ${r.change>=5?'up':r.change<=-5?'down':''}">${pct(r.change)}</span><span class="trend-fc">прогноз ${pct(r.fc)}</span></button>`).join('')}</div></section>`;
}
function overview(){
 const ps=products(), counts=Object.fromEntries(Object.keys(urgency).map(k=>[k,ps.filter(p=>p.urgency===k).length]));
 const needs=ps.filter(p=>p.recommended_qty>0), critical=counts.CRITICAL, transit=ps.filter(p=>p.in_transit_in_horizon>0).length;
 const cards=[['Товаров в анализе',ps.length,demo.live?`2 поставщика · выгрузка 1С на ${demo.asOf.split('-').reverse().join('.')}`:'2 поставщика · демо-набор','box','','catalog'],['Высокая срочность',critical+counts.HIGH,`Критичных: ${critical} · из них ничего нет в пути: ${ps.filter(p=>p.urgency==='CRITICAL'&&!p.in_transit_in_horizon).length}`,'alert','warn','high-priority'],['Рекомендовано к заказу',needs.length,`разовые сделки исключены у ${ps.filter(p=>p.excluded_events.length).length} товаров`,'bag','blue','recommendations'],['Товары в пути',transit,'позиций с поступлениями','truck','good','transit']];
 const colors={CRITICAL:'#de7770',HIGH:'#edb477',PLANNED:'#abc1e1',OK:'#7bb69b'};
 let start=0;const stops=Object.keys(counts).map(k=>{const a=start;start+=counts[k]/ps.length*100;return `${colors[k]} ${a}% ${start}%`;}).join(',');
 const attention=ps.filter(p=>p.urgency==='CRITICAL'||p.urgency==='HIGH').sort((a,b)=>urgency[a.urgency].rank-urgency[b.urgency].rank||a.cover_days-b.cover_days).slice(0,5);
 const cp=find(state.chartId)||attention[0]||ps[0];
 if(!cp)return header('Обзор закупок','Нет данных для показа');
 const chartList=[...new Set([cp,...attention,...ps.filter(p=>p.urgency==='CRITICAL').slice(0,60)])];
 return header('Обзор закупок','Состояние запасов и рекомендации на сегодня',btn('К рекомендациям '+icon('arrow'),'recommendations','primary'))+
 `<section class="metrics" aria-label="Ключевые показатели">${cards.map(([label,value,foot,ic,cl,action])=>`<button class="metric" data-action="${action}"><div class="metric-top">${label}<span class="metric-icon ${cl}">${icon(ic)}</span></div><div class="metric-value">${value}</div><div class="metric-foot">${foot}</div></button>`).join('')}</section>
 ${supplierKpis(ps)}
 <section class="analytics"><article class="card"><div class="card-heading"><div><h2>Продажи и прогноз</h2><div class="card-sub">${cp.history_label||(cp.id==='iek-loop'?'Архивный демо-кейс · 2025':'Апрель — ноябрь 2026')} · ${cp.unit}</div></div><select id="chart-product" class="mini-select" aria-label="Товар для графика">${chartList.map(p=>`<option value="${p.id}" ${p.id===cp.id?'selected':''}>${esc(p.name)}</option>`).join('')}</select></div>${chart(cp)}<div class="card-bottom"><span>График базового сценария</span><button class="btn text small" data-action="product" data-id="${cp.id}">Разобрать ${icon('arrow')}</button></div></article>
 <article class="card"><div class="card-heading"><div><h2>Срочность пополнения</h2><div class="card-sub">${demo.live?'Все товары выгрузки':'Все товары демо-набора'}</div></div>${icon('filter')}</div><div class="risk-body"><div class="donut" style="background:conic-gradient(${stops})"><div class="donut-center"><strong>${ps.length}</strong><span>позиций</span></div></div><div class="risk-legend">${Object.keys(counts).map(k=>`<button class="risk-item risk-filter" data-action="risk" data-risk="${k}"><span class="risk-label"><i class="risk-swatch" style="background:${colors[k]}"></i>${urgency[k].label}</span><strong>${counts[k]}</strong></button>`).join('')}</div></div><div class="card-bottom"><span>Критичных позиций: ${critical}</span><button class="btn text small" data-action="urgent">Посмотреть ${icon('arrow')}</button></div></article></section>
 ${categoryTrends(ps)}
 <section class="attention"><div class="attention-head"><div><h2>Требуют внимания</h2><p>Сначала критичные позиции и короткий запас</p></div>${btn('Все рекомендации '+icon('arrow'),'recommendations')}</div>${table(attention)}</section>
`;
}
function recommendations(){
 let rows=filtered().filter(p=>state.risk==='Все'||p.urgency===state.risk);
 if(state.risk==='Срочные')rows=filtered().filter(p=>['CRITICAL','HIGH'].includes(p.urgency));
 if(state.risk==='В пути')rows=filtered().filter(p=>p.in_transit_in_horizon>0);
 rows.sort((a,b)=>urgency[a.urgency].rank-urgency[b.urgency].rank||quantity(b)-quantity(a));
 return filterBar()+`<div class="chips">${[['Все','Все'],['Срочные','Срочные'],...Object.entries(urgency).map(([k,v])=>[k,v.label]),['В пути','В пути']].map(([k,v])=>`<button class="chip ${state.risk===k?'active':''}" data-action="filter-risk" data-risk="${k}">${v}<span class="count">${k==='Все'?filtered().length:filtered().filter(p=>k==='В пути'?p.in_transit_in_horizon>0:k==='Срочные'?['CRITICAL','HIGH'].includes(p.urgency):p.urgency===k).length}</span></button>`).join('')}</div>`+table(rows,{select:true})+ (state.selected.size?`<div class="floating-selection glass"><strong>Выбрано ${state.selected.size} поз.</strong>${btn('Снять','unselect','text small')}${btn(icon('plus')+' В черновик','add-selected','primary')}</div>`:'')+`<div class="context-note">${icon('info')} Рекомендации — отправная точка. Финальное количество выбираете вы.</div>`;
}
function draft(){
 const rows=saved.draft.map(find).filter(Boolean);
 if(!rows.length)return empty('Черновик пока пуст','Выберите товары в рекомендациях или добавьте их из карточки товара.',btn('Открыть рекомендации','recommendations','primary'));
 const approved=saved.approved;
 const edits=rows.filter(changed).length;
 return `${approved?`<div class="approved-banner"><strong>${icon('check')} Заказ утверждён локально</strong>${new Date(approved.at).toLocaleString('ru-RU')}${approved.by?` · утвердил ${esc(approved.by)}`:''} · ${positions(approved.rows.length)} · ${demo.live?'Без':'Демо, без'} отправки поставщикам</div>`:''}<div class="draft-layout"><div>${suppliers.map(s=>{const items=rows.filter(p=>p.supplier===s);return !items.length?'':`<section class="draft-group"><div class="draft-group-header"><div class="supplier-logo ${s==='IEK'?'':'se'}">${s==='IEK'?'IEK':'SE'}</div><h2>${s}</h2><span class="count">${items.length} поз.</span></div>${items.map(p=>`<article class="draft-line"><div class="line-details"><button class="product-open" data-action="product" data-id="${p.id}"><strong>${esc(p.name)}</strong></button><small>Рекомендовано ${unit(p.recommended_qty,p)} · кратность ${unit(p.moq,p)}</small>${changed(p)?'<small class="edited">Количество изменено вручную</small>':''}${quantity(p)%p.moq?'<small class="edited">Количество не кратно партии — проверьте перед подтверждением</small>':''}${comment(p)?`<small>Комментарий: ${esc(comment(p))}</small>`:''}</div><label class="qty"><input type="number" min="0" step="1" data-draft-qty="${p.id}" value="${quantity(p)}" aria-label="Количество ${esc(p.name)}">${p.unit}</label><button class="icon-btn" data-action="remove-draft" data-id="${p.id}" aria-label="Убрать ${esc(p.name)}">${icon('trash')}</button></article>`).join('')}</section>`;}).join('')}</div><aside class="card summary"><h2>Итог заказа</h2><div class="summary-row"><span>Поставщиков</span><strong>${new Set(rows.map(p=>p.supplier)).size}</strong></div><div class="summary-row"><span>Позиций</span><strong>${rows.length}</strong></div><div class="summary-row"><span>Изменено вручную</span><strong>${edits}</strong></div><div class="summary-row"><span>Нужна проверка</span><strong>${rows.filter(p=>p.quality==='Нужна проверка').length}</strong></div><hr style="border:0;border-top:1px solid #e7edf5"><p>${(()=>{const priced=rows.filter(p=>unitCost(p));const v=priced.reduce((a,p)=>a+unitCost(p)*quantity(p),0);const miss=rows.length-priced.length;if(!priced.length)return 'Себестоимости этих позиций в выгрузке нет — сумма не считается. Цены есть только у части товаров Systeme Electric.';return `Сумма по себестоимости: <strong>${fmt(Math.round(v))} ₸</strong>`+(miss?`<br>Без цены в выгрузке: ${miss} из ${rows.length} поз. — в сумму не вошли.`:'');})()}</p>${approved?btn(icon('download')+' Скачать XLSX','export','primary')+btn(icon('download')+' Скачать CSV','export-csv'):btn('Подтвердить заказ '+icon('check'),'confirm','primary')}<p>${approved?'Любая правка отменит подтверждение.':'Проверьте количества и оговорки перед утверждением.'} Поставщику ничего не отправляется.</p></aside></div>`;
}
function purchases(){return header('Закупки','От рекомендации — к вашему решению',btn(icon('settings')+' Параметры','settings'))+`<div class="tabs"><button class="tab ${state.tab==='recommendations'?'active':''}" data-action="purchase-tab" data-tab="recommendations">Рекомендации<span class="count">${products().filter(p=>p.recommended_qty>0).length}</span></button><button class="tab ${state.tab==='draft'?'active':''}" data-action="purchase-tab" data-tab="draft">Черновик<span class="count">${saved.draft.length}</span></button></div>${saved.scenario==='growth'?'<div class="notice info"><div>'+icon('info')+'</div><div>'+(demo.live?'<strong>Сценарий: прирост спроса +20%</strong>Количества пересчитаны движком. Срочность, покрытие и графики — базового сценария.':'<strong>Демонстрационный сценарий: прирост +20%</strong>Показаны заранее подготовленные количества. Срочность, покрытие и графики относятся к базовому сценарию.')+'</div></div>':''}`+(state.tab==='draft'?draft():recommendations());}
function catalog(){return header('Товары',demo.live?'Весь каталог, включая позиции с достаточным запасом':'Весь демо-каталог, включая позиции с достаточным запасом')+filterBar()+table(filtered(),{catalog:true});}
function dataPage(){return header('Данные и допущения','Прозрачность источников — основа доверия к рекомендации')+`<div class="notice"><div>${icon('alert')}</div><div>${demo.live?`<strong>Выгрузка 1С · ${fmt(allProducts.length)} товаров</strong>Расчёт выполнен по доступному срезу данных — остатки и приходы могут изменить рекомендацию. Остаток ИЭК — оценка: приходы за сентябрь в выгрузке не видны.`:`<strong>Демонстрационный набор · ${allProducts.length} товаров</strong>Файлы не загружались, engine не подключён. Ниже показан пример состояния источников и проверок.`}</div></div><div class="data-grid"><div>${sources.map((s,i)=>`<article class="source-card clickable" data-action="source" data-i="${i}" tabindex="0" role="button"><div class="source-icon">${icon('file')}</div><div><h2>${s.name}</h2><p>${s.type}</p><p>${s.description}</p></div><div class="source-meta"><span>${s.date}</span><span>${s.status}</span></div></article>`).join('')}</div><aside class="card"><h2>На что обратить внимание</h2><p class="reason">Остатки IEK оценочные. Срок поставки Systeme Electric принят как допущение. Эти оговорки видны в каждой карточке.</p>${products().filter(p=>p.quality==='Нужна проверка').map(p=>`<div class="review-product"><div><strong>${p.name}</strong><small>${p.excluded_events.length?'Разовая крупная продажа исключена':p.lost_qty?'Восстановлен упущенный спрос':'Проверить данные'}</small></div><button class="btn text small" data-action="product-data" data-id="${p.id}" aria-label="Проверить ${p.name}">${icon('arrow')}</button></div>`).join('')}<details class="disclosure"><summary>Как читать статусы качества</summary><p>«Данных достаточно» — обязательные источники доступны.<br>«Есть допущения» — часть параметров оценена.<br>«Нужна проверка» — обнаружена проблема, требующая решения менеджера.</p></details></aside></div>`;}
function render(){
 const focus=document.activeElement, selection=focus?.id==='list-search'?focus.selectionStart:null;
 $('#crumb').textContent=state.page;
 document.querySelectorAll('[data-nav]').forEach(b=>{b.classList.toggle('active',b.dataset.nav===state.page);b.setAttribute('aria-current',b.dataset.nav===state.page?'page':'false');b.title=b.dataset.nav;});
 $('#page').innerHTML=state.page==='Сегодня'?today():state.page==='Обзор'?overview():state.page==='Закупки'?purchases():state.page==='Товары'?catalog():dataPage();
 if(demo.live&&!render.labelled){render.labelled=true;const pill=document.querySelector('.mode-pill');if(pill)pill.lastChild.textContent='Данные 1С';const foot=document.querySelector('.footer-note');if(foot)foot.textContent=`QadamSupply · выгрузка 1С на ${demo.asOf.split('-').reverse().join('.')} · Изменения сохраняются только в этом браузере`;}
 document.body.classList.toggle('has-draftbar',!!document.querySelector('.today-draftbar'));
 if(!demo.live&&allProducts.length)$('#page').insertAdjacentHTML('afterbegin',`<div class="notice">Демо-данные: API расчёта недоступен (${esc(demo.liveError||'нет ответа')}). Запустите сервер: .venv/bin/uvicorn api:app</div>`);
 if(storageFailed)$('#page').insertAdjacentHTML('afterbegin','<div class="notice">Локальное сохранение недоступно. Правки сохраняются только в текущем сеансе.</div>');
 if(selection!==null){$('#list-search')?.focus();$('#list-search')?.setSelectionRange(selection,selection);}
}
function detailsBody(p){
 if(state.detailTab==='ask')return askBody(p);
 if(state.detailTab==='history')return `<div class="card-sub">${p.history_label||(p.id==='iek-loop'?'Архивный демо-кейс · апрель — ноябрь 2025':'Демо · апрель — ноябрь 2026; сентябрь — неполный месяц')} · ${p.unit}<br>График базового сценария</div>${chart(p,{detail:true,spike:state.showSpike})}${p.excluded_events.map(e=>`<div class="event-card"><strong>Разовая крупная продажа скорректирована</strong><br>${e.date} · накладная ${e.doc}<br>Продажа: <strong>${unit(e.qty,p)}</strong> → учтено ${unit(e.threshold,p)}<br>Из регулярного спроса исключено ${unit(e.excess,p)}</div>`).join('')}${p.lost_qty?`<div class="notice"><div>${icon('info')}</div><div><strong>Восстановлен упущенный спрос</strong>${p.restored_text?p.restored_text+'<br>':'Июль: факт 4 шт. → скорректированный спрос 52 шт.<br>'}Восстановлено ${unit(p.lost_qty,p)} из-за отсутствия товара.</div></div>`:''}<details class="disclosure"><summary>Посмотреть значения по месяцам</summary><table class="history-table"><thead><tr><th>Месяц</th><th class="num">Факт</th><th class="num">Скорр.</th><th class="num">Прогноз</th></tr></thead><tbody>${p.history.map(s=>`<tr><td>${s.month}</td><td class="num">${s.raw===null?'—':fmt(s.raw)}</td><td class="num">${s.clean===null?'—':fmt(s.clean)}</td><td class="num">${s.forecast===null?'—':fmt(s.forecast)}</td></tr>`).join('')}</tbody></table></details>`;
 if(state.detailTab==='data')return `<div class="notice"><div>${icon('alert')}</div><div><strong>${p.quality}</strong>${p.warnings.map(esc).join('<br>')}</div></div><div class="details-grid"><div class="detail-tile">Код 1С<strong>${p.sku}</strong></div><div class="detail-tile">Единица измерения<strong>${p.unit}</strong></div><div class="detail-tile">Срок поставки<strong>${p.lead_time_days} дней</strong></div><div class="detail-tile">Кратность заказа<strong>${unit(p.moq,p)}</strong></div></div><p class="reason">Все значения этой карточки заданы в демо-наборе. Они не являются результатом обработки реальных файлов.</p><details class="disclosure" open><summary>Источники и актуальность</summary><p>${demo.live?`Продажи и транзит: выгрузка 1С на ${demo.asOf.split('-').reverse().join('.')}.<br>${p.warnings.map(esc).join('<br>')}`:`Продажи и транзит: демо-снимок на 22.09.2026.<br>Остатки: демонстрационная оценка.<br>${p.id==='iek-loop'?'График LOOP показывает отдельный архивный кейс 2025 года.':''}`}</p></details>`;
 const rounded=Math.max(0,p.need);
 return `<div class="recommendation-hero"><div class="label">Рекомендуем ${p.recommended_qty?'заказать':'сохранить текущий запас'}</div><div class="value">${fmt(p.recommended_qty)} <span>${p.unit}</span></div><p>${p.recommended_qty?`На горизонт ${days(p.horizon_days??demo.horizon)} · кратность ${unit(p.moq,p)}`:'Дополнительный заказ не требуется'}${saved.scenario==='growth'?' · сценарий +20%':''}</p></div><div class="section-label">Почему именно столько</div><div class="breakdown">${[['forecast','Прогноз на горизонт',p.forecast_horizon,''],['safety','Страховой запас',p.safety_stock,'+'],['stock','Текущий остаток',p.stock_current,'−'],['transit','Товар в пути',p.in_transit_in_horizon,'−']].map(([k,label,n,sign])=>bdRow(p,k,label,`${sign} ${unit(n,p)}`)).join('')}${bdRow(p,'need','Потребность до округления',unit(rounded,p))}${bdRow(p,'moq',p.need<=0?'Запаса достаточно':'С учётом кратности',unit(p.recommended_qty,p),'total')}</div><div class="bd-hint">${icon('info')} Нажмите на строку, чтобы увидеть, из чего она сложилась</div>${demo.live?`<div class="explain-row">${btn('<span class="ai-badge">ИИ</span> Объяснить простыми словами','explain-product','small')}</div>`:''}${reasonList(p)}<div class="details-grid"><div class="detail-tile">Запаса хватит<strong>${days(p.cover_days)}</strong></div><div class="detail-tile">Срок поставки<strong>${days(p.lead_time_days)}</strong>${leadSource(p)}</div>${p.horizon_days?`<div class="detail-tile">Горизонт расчёта<strong>${days(p.horizon_days)}</strong><small>срок поставки + ${demo.reviewDays} дн. до следующего заказа</small></div>`:''}${p.abc?`<div class="detail-tile">Категория важности<strong>${esc(p.abc)}</strong><small>${abcHint(p.abc)}</small></div>`:''}</div>${whatIfBlock(p)}${saved.scenario==='growth'?'<div class="notice info">Срочность, покрытие и графики показаны для базового сценария. '+(demo.live?'Количество +20% пересчитано движком.':'Количество +20% задано заранее.')+'</div>':''}<details class="disclosure"><summary>${p.quality} · посмотреть оговорки</summary><p>${p.warnings.map(esc).join('<br>')}</p></details><div class="edit-form"><label for="detail-qty">Ваше количество к заказу</label><div class="qty"><input id="detail-qty" type="number" min="0" step="1" value="${quantity(p)}">${p.unit}</div><div class="field-hint">Рекомендация ${unit(p.recommended_qty,p)} останется видимой. Кратность: ${unit(p.moq,p)}</div><label for="detail-comment" style="margin-top:14px">Комментарий к решению</label><textarea id="detail-comment" maxlength="500" placeholder="Например: согласовано с менеджером проекта">${esc(comment(p))}</textarea><div id="detail-error" class="field-error" role="alert"></div>${btn('Сохранить правку','save-edit','small')}</div>`;
}

function leadSource(p){const src=(demo.leadText?.[p.supplier]||'').split('·')[1];return src?`<small>${esc(src.trim())}</small>`:'';}
function abcHint(c){return ({'1':'ядро ассортимента · сервис 98%','A':'ядро ассортимента · сервис 98%','2':'сервис 95%','B':'сервис 95%','5':'сервис 95%','3':'сервис 90%','C':'сервис 90%','7':'новинка или выведен · без автозаказа'})[c]||'';}

function coverCell(p){
 if(p.cover_days==null)return '<span class="muted">—</span>';
 if(!p.stock_current&&p.urgency!=='OK')return '<span class="cover-none">Нет в наличии</span>';
 const cls=p.cover_days<(p.lead_time_days||30)?'cover-low':'', text=p.cover_days<1?'меньше дня':days(p.cover_days);
 return cls?`<span class="${cls}">${text}</span>`:text;
}
function reasonList(p){
 const facts=String(p.reason_text||'').split(/\.\s+(?=[А-ЯЁA-Z])/).map(x=>x.replace(/\.$/,'').trim()).filter(Boolean);
 if(facts.length<2)return `<p class="reason">${esc(p.reason_text)}</p>`;
 return `<ul class="reason-list">${facts.map(f=>`<li>${esc(f)}</li>`).join('')}</ul>`;
}

// --- сервис расчёта (api.py): what-if и помощник работают поверх сохранённого расчёта run_id
const whatIfs={}, chats={};
let runPromise=null;
function ensureRun(){
 if(!runPromise)runPromise=fetch('./runs',{method:'POST'}).then(async r=>{const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(apiError(d,r));return d.run_id;}).catch(e=>{runPromise=null;throw e;});
 return runPromise;
}
function apiError(d,r){const x=d?.detail;return typeof x==='string'?x:Array.isArray(x)?x.map(e=>e.msg).join('; '):`Сервис ответил ${r.status}`;}
async function apiPost(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(apiError(d,r));return d;}
async function aiStatus(){try{const r=await fetch('./health');const d=await r.json();demo.aiChat=!!d.ai?.chat;}catch{demo.aiChat=false;}}
function whatIfBlock(p){
 if(!demo.live)return '';
 const w=whatIfs[p.id]||{};
 const res=w.loading?'<span class="muted">Пересчитываем…</span>':w.error?`<span class="cover-none">${esc(w.error)}</span>`:w.after!=null?`Заказ <strong>${unit(w.before,p)}</strong> → <strong>${unit(w.after,p)}</strong> <span class="${w.delta>0?'cover-low':w.delta<0?'delta-down':'muted'}">(${w.delta>0?'+':''}${fmt(w.delta)} ${esc(p.unit)})</span>`:'<span class="muted">Пересчёт по той же модели. Ничего не сохраняет и не меняет рекомендацию.</span>';
 return `<details class="disclosure whatif" ${w.open?'open':''}><summary>Что если…</summary><div class="whatif-form"><label>Срок поставки, дн.<input id="wi-lead" type="number" min="0" max="365" step="1" value="${w.lead??p.lead_time_days??''}"></label><label>Ещё в пути, ${esc(p.unit)}<input id="wi-extra" type="number" min="0" step="1" value="${w.extra??0}"></label>${btn('Пересчитать','what-if','small')}</div><div class="whatif-result">${res}</div></details>`;
}
function askBody(p){
 if(demo.aiChat===undefined){aiStatus().then(()=>{if(state.detailTab==='ask')renderProduct();});return '<div class="notice info">Проверяем, подключён ли помощник…</div>';}
 if(!demo.aiChat)return `<div class="notice"><div>${icon('info')}</div><div><strong>Помощник выключен</strong>Не задан ключ API языковой модели. Расчёт, рекомендации, «что если» и экспорт работают без него. Чтобы включить — добавьте OPENAI_API_KEY в .env и перезапустите сервер.</div></div>`;
 const c=chats[p.id]||{};const hist=c.history||[];
 const qs=[`Почему по этому товару рекомендовано ${fmt(p.recommended_qty)} ${p.unit}?`,'Что будет, если поставка задержится на 2 недели?',`Какие ещё критичные позиции ${p.supplier} без товара в пути?`];
 const thread=hist.length||c.loading?`<div class="ask-thread">${hist.map(t=>`<div class="ask-q">${esc(t.q)}</div>${t.error?`<div class="notice">${esc(t.error)}</div>`:`<div class="ask-answer"><div class="ask-check ${t.numbers_ok?'ok':'warn'}">${t.numbers_ok?'✓ Числа сверены с расчётом':'⚠ Есть числа не из данных: '+esc((t.unsupported||[]).join(', '))}</div><div class="ask-text">${esc(t.answer).replace(/\n/g,'<br>')}</div></div>`}`).join('')}${c.loading?`<div class="ask-q">${esc(c.pending)}</div><div class="ask-answer ask-pending">Думаю…</div>`:''}</div>`:'';
 return `<div class="ask">${thread}<div class="ask-suggest">${qs.map(q=>`<button class="chip" data-action="ask-fill" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div><textarea id="ask-input" maxlength="2000" placeholder="${hist.length?'Уточните или задайте следующий вопрос…':'Спросите о расчёте по этому товару…'}">${esc(c.draft||'')}</textarea><div class="ask-actions">${btn(c.loading?'Думаю…':'Спросить','ask-send','primary',c.loading?'disabled':'')}</div><div class="context-note">${icon('info')} Помощник отвечает только по результатам расчёта и не может отправить заказ поставщику.</div></div>`;
}

// --- ИИ (api.py → agent/): сводка, помощник на всех экранах, объяснение в карточке
const ai={brief:null,briefLoading:false,thread:'global',log:[],busy:false};
function aiOff(){return `<div class="ai-off">${icon('info')}<span>Помощник выключен: не задан ключ OpenAI. Добавьте <code>OPENAI_API_KEY</code> в файл <code>.env</code> и перезапустите сервер — расчёт и всё остальное работают без него.</span></div>`;}
function aiBriefBlock(){
 if(!demo.live)return '';
 if(demo.aiChat===undefined){aiStatus().then(()=>{if(state.page==='Сегодня')render();});return '';}
 if(!demo.aiChat)return `<section class="ai-brief off">${aiOff()}</section>`;
 if(!ai.brief&&!ai.briefLoading)loadBrief();
 const body=ai.briefLoading?'<div class="ai-loading"><i></i><i></i><i></i> Помощник читает расчёт…</div>':ai.brief?.error?`<div class="ai-off">${icon('alert')}<span>${esc(ai.brief.error)}</span></div>`:(ai.brief||[]).map(b=>`<div class="ai-brief-item"><div class="ai-brief-sup">${esc(supplierName(b.supplier))}</div><strong>${esc(b.headline)}</strong>${(b.top_risks||[]).length?`<ul>${b.top_risks.map(r=>`<li>${esc(r)}</li>`).join('')}</ul>`:''}</div>`).join('');
 return `<section class="ai-brief"><div class="ai-brief-head"><span class="ai-badge">ИИ</span><h2>Коротко от помощника</h2>${btn('Спросить','assistant-open','small')}</div><div class="ai-brief-body">${body}</div></section>`;
}
async function loadBrief(){
 ai.briefLoading=true;
 try{const run=await ensureRun();ai.brief=await Promise.all(suppliers.map(async s=>{const r=await fetch(`./runs/${run}/brief/${encodeURIComponent(s)}`);const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(apiError(d,r));return {supplier:s,...d};}));}
 catch(e){ai.brief={error:`Сводка не получилась: ${e.message}`};}
 ai.briefLoading=false;if(state.page==='Сегодня')render();
}
function assistantSuggestions(){
 const p=state.page;
 if(p==='Сегодня')return ['С чего начать сегодня?','Какие критичные позиции ИЭК без товара в пути самые важные?','Сколько денег нужно на срочный заказ Systeme Electric?'];
 if(p==='Закупки')return ['Какие позиции в черновике стоит перепроверить?','Где округление до кратности сильно увеличило заказ?','Что будет, если ИЭК задержит поставку на 2 недели?'];
 if(p==='Данные')return ['Каким данным в расчёте можно доверять меньше всего?','Почему остаток ИЭК — оценка?','Как считается упущенный спрос?'];
 return ['Какие риски дефицита сейчас главные?','Сколько позиций у каждого поставщика нужно заказать?','Почему «Петля LOOP» не заказывается?'];
}
function pageContext(){
 const c={'Сегодня':'Экран «Что делать сегодня»: срочные заказы — критичные позиции без товара в пути, сначала категории A (ИЭК) и 1 (Systeme Electric). Отвечай конкретно: какие товары и сколько заказать, используя инструменты.',
  'Закупки':'Экран «Закупки»: список рекомендаций и черновик заказа.','Обзор':'Экран «Обзор»: сводка по поставщикам.',
  'Товары':'Экран «Товары»: весь каталог.','Данные':'Экран «Данные»: источники данных и допущения расчёта.'}[state.page]||'';
 return c?`[Контекст: ${c}]`:'';
}
function renderAssistant(){
 let d=$('#assistant-dialog');
 if(!d){d=document.createElement('dialog');d.id='assistant-dialog';d.className='assistant-dialog';document.body.append(d);}
 const body=demo.aiChat===undefined?'<div class="ai-loading"><i></i><i></i><i></i> Проверяем помощника…</div>':!demo.aiChat?aiOff():`${ai.log.length?'':`<div class="ask-suggest">${assistantSuggestions().map(q=>`<button class="chip" data-action="assistant-fill" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div>`}<div class="assistant-log">${ai.log.map(m=>m.role==='user'?`<div class="msg user">${esc(m.text)}</div>`:`<div class="msg bot">${m.error?`<span class="cover-none">${esc(m.error)}</span>`:`<div class="ask-check ${m.numbers_ok?'ok':'warn'}">${m.numbers_ok?'✓ Числа сверены с расчётом':'⚠ Есть числа не из данных: '+esc((m.unsupported||[]).join(', '))}</div>${esc(m.text).replace(/\n/g,'<br>')}`}</div>`).join('')}${ai.busy?'<div class="msg bot"><div class="ai-loading"><i></i><i></i><i></i> Думаю…</div></div>':''}</div><div class="assistant-input"><textarea id="assistant-input" maxlength="2000" placeholder="Спросите о закупках, остатках, рисках…"></textarea>${btn('Отправить','assistant-send','primary',ai.busy?'disabled':'')}</div>`;
 d.innerHTML=`<div class="modal-heading"><div><span class="ai-badge">ИИ</span> <strong>Помощник закупщика</strong><div class="card-sub">Отвечает по результатам расчёта. Заказы не отправляет.</div></div><button class="icon-btn" data-action="assistant-close" aria-label="Закрыть">${icon('close')}</button></div>${body}`;
 const log=d.querySelector('.assistant-log');if(log)log.scrollTop=log.scrollHeight;
 return d;
}
async function openAssistant(q){
 const d=renderAssistant();if(!d.open)d.showModal();
 if(demo.aiChat===undefined){await aiStatus();renderAssistant();}
 if(q&&demo.aiChat){$('#assistant-input').value=q;}
 $('#assistant-input')?.focus();
}
async function sendAssistant(){
 const q=$('#assistant-input')?.value.trim();if(!q||ai.busy)return;
 ai.log.push({role:'user',text:q});ai.busy=true;renderAssistant();
 try{const run=await ensureRun();const d=await apiPost(`./runs/${run}/chat`,{message:`${pageContext()}\n${q}`,thread_id:ai.thread});ai.log.push({role:'bot',text:d.answer,numbers_ok:d.numbers_ok,unsupported:d.unsupported_numbers});}
 catch(e){ai.log.push({role:'bot',error:e.message});}
 ai.busy=false;renderAssistant();$('#assistant-input')?.focus();
}

// --- детализация строк разбора
function bdRow(p,k,label,value,extra=''){
 const open=state.bdOpen===k;
 return `<button class="breakdown-row clickable ${extra} ${open?'open':''}" data-action="bd-toggle" data-k="${k}" aria-expanded="${open}"><span>${label}</span><strong>${value}</strong><i class="bd-caret">${icon('chevron')}</i></button>${open?`<div class="bd-detail">${bdDetail(p,k)}</div>`:''}`;
}
function bdDetail(p,k){
 const u=esc(p.unit), pct=v=>`${Math.round(v*100)}%`;
 if(k==='forecast'){
  const fm=p.forecast_months||[];
  const head=`Регулярный спрос ≈ <strong>${fmt(p.base_monthly??0)} ${u}/мес</strong>${p.trend&&Math.abs(p.trend-1)>=.05?` · тренд ${p.trend>1?'рост':'спад'} ${p.trend>1?'+':''}${Math.round((p.trend-1)*100)}% за полгода`:''}. Горизонт — ${days(p.horizon_days??demo.horizon)}: срок поставки ${days(p.lead_time_days)} + ${demo.reviewDays} дн. до следующего заказа.`;
  if(!fm.length)return `<p>${head}</p>`;
  return `<p>${head}</p><table class="bd-table"><thead><tr><th>Месяц</th><th class="num hide-m">Дней в горизонте</th><th class="num">Сезонность</th><th class="num">Прогноз за месяц</th><th class="num">В горизонт</th></tr></thead><tbody>${fm.map(m=>`<tr><td>${monthName(m.month)}</td><td class="num hide-m">${m.days}</td><td class="num ${m.season>=1.2?'cover-low':''}">×${m.season.toFixed(2)}</td><td class="num">${unit(m.forecast,p)}</td><td class="num"><strong>${unit(m.horizon_qty,p)}</strong></td></tr>`).join('')}</tbody><tfoot><tr><td colspan="3">Итого на горизонт</td><td class="hide-m"></td><td class="num"><strong>${unit(p.forecast_horizon,p)}</strong></td></tr></tfoot></table>`;
 }
 if(k==='safety')return `<p>Запас на случай, если спрос окажется выше прогноза. ${p.abc?`Категория важности <strong>${esc(p.abc)}</strong> — `:''}уровень сервиса <strong>${p.service_level?pct(p.service_level):'95%'}</strong>: вероятность, что товар не закончится до следующей поставки. Чем важнее товар и чем сильнее колеблется спрос, тем больше запас.</p>`;
 if(k==='stock')return p.stock_source==='оценка'?`<p><strong>Оценка.</strong> Остаток на 1-е число месяца минус продажи с начала месяца. Приходы текущего месяца в выгрузке не видны, поэтому реальный остаток может быть больше — сверьте с 1С перед заказом.</p>`:`<p><strong>Из выгрузки 1С</strong> на ${esc(dateLabel().split(' · ')[0])}: свободный остаток + витрина + ТЗ + розничный склад — как в модели менеджера.</p>`;
 if(k==='transit'){
  const tl=p.transit_lines||[];
  if(!tl.length)return '<p>По этому товару в пути ничего нет.</p>';
  return `<table class="bd-table"><thead><tr><th>Документ</th><th class="num">Количество</th><th class="num">Приход до</th><th>В расчёте</th></tr></thead><tbody>${tl.map(t=>`<tr><td>${esc(t.doc)}</td><td class="num">${unit(t.qty,p)}</td><td class="num">${esc(t.eta)}</td><td>${t.in_horizon?'<span class="delta-down">учтено</span>':'<span class="muted">позже горизонта — не вычитаем</span>'}</td></tr>`).join('')}</tbody></table>`;
 }
 if(k==='need')return `<p>Прогноз + страховой запас − остаток − в пути = <strong>${unit(Math.max(0,p.need),p)}</strong>.${p.need<=0?' Отрицательная потребность значит, что запаса хватает.':''}</p>`;
 if(k==='moq')return p.need<=0?'<p>Текущий запас и поставки в пути покрывают прогноз — заказывать не нужно.</p>':`<p>Поставщик отгружает кратно <strong>${unit(p.moq,p)}</strong>, поэтому потребность ${unit(Math.max(0,p.need),p)} округлена вверх до <strong>${unit(p.recommended_qty,p)}</strong>.${(p.warnings||[]).some(w=>w.startsWith('Кратности нет'))?' Кратности нет в справочнике — взята 1, уточните у поставщика.':''}</p>`;
 return '';
}
const MONTHS_RU=['январь','февраль','март','апрель','май','июнь','июль','август','сентябрь','октябрь','ноябрь','декабрь'];
function monthName(ym){const [y,m]=String(ym).split('-');return m?`${MONTHS_RU[Number(m)-1]} ${y}`:ym;}
function sourceDetail(i){
 const s=sources[i];if(!s)return;
 const d=$('#confirm-dialog');
 const warn=(demo.warnings||[]).filter(w=>/ИЭК|Systeme|продаж|остат|пути|кратн|сезон/i.test(w));
 d.innerHTML=`<div class="modal-heading"><h2>${esc(s.name)}</h2><button class="icon-btn" data-action="close-modal" aria-label="Закрыть">${icon('close')}</button></div><div class="breakdown"><div class="breakdown-row"><span>Что это</span><strong>${esc(s.type)}</strong></div><div class="breakdown-row"><span>Дата данных</span><strong>${esc(s.date)}</strong></div><div class="breakdown-row"><span>Статус</span><strong>${esc(s.status)}</strong></div></div><p class="reason" style="margin-top:14px">${esc(s.description)}</p>${warn.length?`<div class="notice"><div>${icon('alert')}</div><div><strong>Оговорки при загрузке</strong>${warn.map(esc).join('<br>')}</div></div>`:''}<div class="modal-actions">${btn('Товары с оговорками','source-products','primary')}${btn('Закрыть','close-modal')}</div>`;
 d.showModal();
}
function renderProduct(){
 const p=find(state.productId);if(!p)return;
 const d=$('#product-dialog');
 d.innerHTML=`<header class="drawer-header"><div><div class="eyebrow">${p.supplier} / ${p.category}</div><h2>${esc(p.name)}</h2><p>${esc(p.article)} · код 1С ${p.sku}</p><div style="margin-top:12px">${badge(p)} ${saved.rejected.includes(p.id)?'<span class="mode-pill">Отклонено вами</span>':''}</div></div><button class="icon-btn" data-action="close-product" aria-label="Закрыть карточку">${icon('close')}</button></header><div class="drawer-body"><div class="tabs">${[['recommendation','Рекомендация'],['history','История спроса'],['data','Данные и допущения'],...(demo.live?[['ask','Спросить помощника']]:[])].map(([k,v])=>`<button class="tab ${state.detailTab===k?'active':''}" data-action="detail-tab" data-tab="${k}">${v}</button>`).join('')}</div>${detailsBody(p)}</div><footer class="drawer-footer">${saved.rejected.includes(p.id)?btn('Вернуть рекомендацию','restore','small'):btn('Отклонить','reject','text small')}${btn('Изменить','edit-focus','small')}${btn(saved.draft.includes(p.id)?icon('check')+' В черновике':icon('plus')+' В черновик','add-product','primary')}</footer>`;
}
function openProduct(id,tab='recommendation'){state.productId=id;state.detailTab=tab;state.bdOpen=null;state.showSpike=false;renderProduct();$('#product-dialog').showModal();}
function validQty(input){const value=Number(input.value);return input.value.trim()!==''&&Number.isSafeInteger(value)&&value>=0&&value<=100000000?value:null;}
function saveProductEdit({notify=false}={}){
 const p=find(state.productId),input=$('#detail-qty');
 if(!input)return true;
 const q=validQty(input);
 if(q===null){$('#detail-error').textContent='Введите целое количество от 0 до 100 000 000.';input.focus();return false;}
 const nextComment=$('#detail-comment').value.trim();
 if(q!==quantity(p)||nextComment!==comment(p)){saved.edits[p.id]={qty:q,comment:nextComment};invalidate();persist();render();}
 $('#detail-error').textContent=q%p.moq?`Количество не кратно ${p.moq} ${p.unit}. Перед подтверждением потребуется проверить эту правку.`:'';
 if(notify)toast('Количество и комментарий сохранены локально');return true;
}
function addToDraft(ids){let added=0;ids.forEach(id=>{const p=find(id);if(p&&quantity(p)>0){if(!saved.draft.includes(id)){saved.draft.push(id);added++;}saved.rejected=saved.rejected.filter(x=>x!==id);}});if(added){invalidate();persist();}render();return added;}
function showConfirm(){
 const rows=saved.draft.map(find).filter(p=>p&&quantity(p)>0);
 if(!rows.length){toast('Добавьте хотя бы одну позицию с количеством больше нуля');return;}
 const mismatch=rows.filter(p=>quantity(p)%p.moq!==0);
 const d=$('#confirm-dialog');
 d.innerHTML=`<div class="modal-heading"><h2>Подтвердить заказ?</h2><button class="icon-btn" data-action="close-modal" aria-label="Закрыть">${icon('close')}</button></div><p>Проверьте итог. После подтверждения можно скачать XLSX. ${demo.live?'Заказ сохраняется локально, поставщику ничего не отправляется.':'Это локальный демонстрационный заказ.'}</p><div class="breakdown"><div class="breakdown-row"><span>Позиций с количеством &gt; 0</span><strong>${rows.length}</strong></div><div class="breakdown-row"><span>Изменено вручную</span><strong>${rows.filter(changed).length}</strong></div><div class="breakdown-row"><span>Поставщиков</span><strong>${new Set(rows.map(p=>p.supplier)).size}</strong></div></div><div class="notice" style="margin-top:18px;margin-bottom:0"><div>${icon('alert')}</div><div><strong>Расчёт содержит допущения</strong>Проверьте оценочные остатки и сроки поставки.</div></div>${mismatch.length?`<div class="notice danger" style="margin-top:12px;margin-bottom:0"><div>${icon('alert')}</div><div><strong>Количество не кратно партии — подтвердить нельзя</strong>${mismatch.map(p=>`${esc(p.name)}: ${fmt(quantity(p))} → ${fmt(Math.ceil(quantity(p)/p.moq)*p.moq)} ${p.unit} (кратность ${fmt(p.moq)})`).join('<br>')}<div style="margin-top:10px">${btn('Округлить вверх до кратности','round-moq','small')}</div></div></div>`:''}<label for="approver" style="display:block;margin-top:16px">Утвердил (ФИО)</label><input id="approver" class="text-input" maxlength="80" autocomplete="name" placeholder="Например, Иванов И. И." value="${esc(saved.approver||'')}"><label class="scenario-option"><input type="checkbox" id="acknowledge"><span>Я проверил количества и оговорки</span></label><div class="modal-actions">${btn('Вернуться','close-modal')}${btn('Подтвердить','approve','primary',`disabled id="approve-button"${mismatch.length?' data-blocked="1"':''}`)}</div>`;d.showModal();
}
function settings(){
 const d=$('#settings-dialog');d.innerHTML=`<div class="modal-heading"><h2>Параметры демо</h2><button class="icon-btn" data-action="close-settings" aria-label="Закрыть">${icon('close')}</button></div><p>Два заранее подготовленных сценария. Переключение не запускает расчётный движок.</p><label class="scenario-option"><input type="radio" name="scenario" value="base" ${saved.scenario==='base'?'checked':''}><span>Базовый сценарий<small>План прироста 0% · фиксированные рекомендации</small></span></label><label class="scenario-option"><input type="radio" name="scenario" value="growth" ${saved.scenario==='growth'?'checked':''}><span>План прироста +20%<small>Подготовленные значения прогноза и заказа</small></span></label><details class="disclosure"><summary>Допущения планирования</summary><p>Период до следующего заказа: ${demo.reviewDays} дней.<br>${demo.live?`Горизонт = срок поставки + период до следующего заказа; у каждого товара свой.<br>Срок поставки IEK: ${demo.leadText?.IEK||'—'}; Systeme Electric: ${demo.leadText?.['Systeme Electric']||'—'}.`:`Горизонт демо-потребности: ${demo.horizon} дней.<br>Срок поставки IEK: 24–30 дней; Systeme Electric: 45 дней.<br>Горизонт зафиксирован для показа интерфейса и не вычисляется из сроков поставки.`}</p></details><p>Ручные количества сохранятся. Смена сценария отменит подтверждение черновика.</p><div class="modal-actions">${btn('Закрыть','close-settings')}${btn('Применить сценарий','apply-settings','primary')}</div><details class="disclosure"><summary>Локальные данные</summary><p>Правки хранятся в этом браузере. Сброс удаляет только демо-черновик, комментарии и ручные количества.</p>${btn('Сбросить демо-правки','reset-prompt','danger small')}</details>`;d.showModal();
}

function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);}
function exportCsv(){
 if(!saved.approved||staleApproval())return;
 const cell=v=>{const t=String(v??'');return /[;"\n]/.test(t)?`"${t.replace(/"/g,'""')}"`:t;};
 const head=['Код 1С','Артикул','Наименование','Ед.','Количество','Кратность','Поставщик','Срочность','Обоснование'];
 const lines=[head,...saved.approved.rows].map(r=>r.map(cell).join(';'));
 if(saved.approved.by)lines.push('',`Утвердил;${cell(saved.approved.by)};${new Date(saved.approved.at).toLocaleString('ru-RU')}`);
 download(new Blob(['﻿'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'}),`QadamSupply-${saved.approved.at.slice(0,10)}.csv`);
 toast('CSV сформирован из утверждённых количеств');
}
function staleApproval(){if(saved.approved?.rows?.[0]?.length===9)return false;saved.approved=null;persist();render();toast('Формат выгрузки обновился — подтвердите заказ ещё раз');return true;}
function exportApproved(){
 if(!saved.approved||staleApproval())return;
 download(buildWorkbook(saved.approved.rows,{by:saved.approved.by,at:saved.approved.at}),`QadamSupply-${saved.approved.at.slice(0,10)}.xlsx`);toast('XLSX сформирован из утверждённых количеств');
}
const actions={
 'recommendations':()=>navigate('Закупки'), 'catalog':()=>navigate('Товары'),
 'high-priority':()=>{navigate('Закупки');state.risk='Срочные';render();},
 'urgent':b=>{navigate('Закупки');state.risk='CRITICAL';if(b?.dataset?.supplier)state.supplier=b.dataset.supplier;render();},
 'transit':()=>{navigate('Закупки');state.risk='В пути';render();},
 'category-trend':b=>{navigate('Закупки');state.category=b.dataset.category;render();},
 'risk':b=>{navigate('Закупки');state.risk=b.dataset.risk;render();},
 'filter-risk':b=>{state.risk=b.dataset.risk;state.selected.clear();render();},
 'clear-filters':()=>{state.query='';state.supplier='Все';state.category='Все';state.risk='Все';state.transit='Все';state.oneoff='Все';state.abc='Все';state.review=false;state.restored=false;state.selected.clear();render();},
 'clear-restored':()=>{state.restored=false;render();},
 'clear-review':()=>{state.review=false;render();},
 'urgent-bare':b=>{navigate('Закупки');Object.assign(state,{risk:'CRITICAL',transit:'Нет',supplier:b.dataset.supplier||'Все'});render();},
 'oneoff':b=>{navigate('Закупки');Object.assign(state,{risk:'Все',oneoff:'Есть',supplier:b.dataset.supplier||'Все'});render();},
 'product':b=>openProduct(b.dataset.id), 'product-data':b=>openProduct(b.dataset.id,'data'),
 'close-product':()=>{if(saveProductEdit())$('#product-dialog').close();},
 'detail-tab':b=>{if(saveProductEdit()){state.detailTab=b.dataset.tab;renderProduct();}},
 'edit-focus':()=>{if(!saveProductEdit())return;state.detailTab='recommendation';renderProduct();$('#detail-qty').focus();$('#detail-qty').scrollIntoView({block:'center'});},
 'save-edit':()=>saveProductEdit({notify:true}),
 'spike':()=>{state.showSpike=!state.showSpike;renderProduct();},
 'purchase-tab':b=>{state.tab=b.dataset.tab;state.selected.clear();render();},
 'unselect':()=>{state.selected.clear();render();},
 'add-selected':()=>{const count=addToDraft([...state.selected]);state.selected.clear();state.tab='draft';render();toast(count?'Позиции добавлены в черновик':'В черновике только позиции с количеством больше нуля');},
 'add-product':()=>{if(!saveProductEdit())return;const p=find(state.productId);if(quantity(p)<=0){toast('Для добавления укажите количество больше нуля');return;}addToDraft([p.id]);$('#product-dialog').close();navigate('Закупки','draft');toast('Товар добавлен в черновик');},
 'remove-draft':b=>{saved.draft=saved.draft.filter(id=>id!==b.dataset.id);invalidate();persist();render();},
 'reject':()=>{if(!saveProductEdit())return;const p=find(state.productId);const d=$('#confirm-dialog');d.innerHTML=`<div class="modal-heading"><h2>Отклонить рекомендацию</h2><button class="icon-btn" data-action="close-modal" aria-label="Закрыть">${icon('close')}</button></div><p>${esc(p.name)} будет отмечен как отклонённый и убран из черновика. Решение можно отменить.</p><label for="reject-comment">Комментарий</label><textarea id="reject-comment" maxlength="500" placeholder="Причина отклонения">${esc(comment(p))}</textarea><div class="modal-actions">${btn('Вернуться','close-modal')}${btn('Отклонить','confirm-reject','danger')}</div>`;d.showModal();},
 'confirm-reject':()=>{const p=find(state.productId);saved.edits[p.id]={qty:quantity(p),comment:$('#reject-comment').value.trim()};if(!saved.rejected.includes(p.id))saved.rejected.push(p.id);saved.draft=saved.draft.filter(id=>id!==p.id);state.selected.delete(p.id);invalidate();persist();$('#confirm-dialog').close();render();renderProduct();toast('Рекомендация отклонена');},
 'restore':()=>{saved.rejected=saved.rejected.filter(id=>id!==state.productId);persist();render();renderProduct();},
 'confirm':showConfirm,
 'close-modal':()=>$('#confirm-dialog').close(),
 'round-moq':()=>{saved.approver=$('#approver')?.value.trim()||saved.approver;saved.draft.map(find).filter(p=>p&&quantity(p)%p.moq!==0).forEach(p=>{saved.edits[p.id]={qty:Math.ceil(quantity(p)/p.moq)*p.moq,comment:comment(p)};});invalidate();persist();render();showConfirm();},
 'approve':()=>{const by=$('#approver')?.value.trim();if(!$('#acknowledge')?.checked||!by)return;saved.approver=by;const rows=saved.draft.map(find).filter(p=>p&&quantity(p)>0);if(rows.some(p=>quantity(p)%p.moq!==0)){toast('Сначала приведите количества к кратности партии');return;}saved.approved={at:new Date().toISOString(),by,rows:rows.map(p=>[p.sku,p.article,p.name,p.unit,quantity(p),p.moq,p.supplier,urgency[p.urgency].label,rationale(p)])};persist();$('#confirm-dialog').close();render();toast('Заказ утверждён локально. XLSX готов к скачиванию.');},
 'what-if':async()=>{const p=find(state.productId);const lead=Math.round(Number($('#wi-lead').value)),extra=Math.round(Number($('#wi-extra').value)||0);
   whatIfs[p.id]={open:true,loading:true,lead,extra};renderProduct();
   try{const run=await ensureRun();const d=await apiPost(`./runs/${run}/what-if`,{supplier:p.supplier,sku:p.sku,lead_time_days:Number.isFinite(lead)&&lead!==p.lead_time_days?lead:null,extra_transit:extra>0?extra:null});
     const c=d.changes?.[0];whatIfs[p.id]=c?{open:true,lead,extra,before:c.recommended_qty_before,after:c.recommended_qty_after,delta:c.delta}:{open:true,lead,extra,error:'Сервис не вернул результат'};}
   catch(e){whatIfs[p.id]={open:true,lead,extra,error:e.message};}
   if(state.productId===p.id)renderProduct();},
 'ask-fill':b=>{chats[state.productId]={...(chats[state.productId]||{}),draft:b.dataset.q};renderProduct();$('#ask-input')?.focus();},
 'ask-send':async()=>{const p=find(state.productId);const q=$('#ask-input').value.trim();if(!q)return;const c=chats[p.id]||(chats[p.id]={});c.history=c.history||[];if(c.loading)return;c.loading=true;c.pending=q;c.draft='';renderProduct();
   try{const run=await ensureRun();const d=await apiPost(`./runs/${run}/chat`,{message:`Товар ${p.sku} (${p.supplier}): ${q}`,thread_id:p.sku.slice(0,60)});c.history.push({q,answer:d.answer,numbers_ok:d.numbers_ok,unsupported:d.unsupported_numbers});}
   catch(e){c.history.push({q,error:e.message});}
   c.loading=false;c.pending='';
   if(state.productId===p.id&&state.detailTab==='ask')renderProduct();},
 'bd-toggle':b=>{state.bdOpen=state.bdOpen===b.dataset.k?null:b.dataset.k;const y=$('#product-dialog .drawer-body')?.scrollTop;renderProduct();const body=$('#product-dialog .drawer-body');if(body&&y!=null)body.scrollTop=y;},
 'source':b=>sourceDetail(Number(b.dataset.i)),
 'source-products':()=>{$('#confirm-dialog').close();navigate('Закупки');state.review=false;render();},
 'go-data':()=>navigate('Данные'),
 'today-add':b=>{const t=todayTasks().orders.find(x=>x.key===b.dataset.task);if(!t)return;const n=addToDraft(t.items.map(p=>p.id));toast(n?`Добавлено в черновик: ${positions(n)}`:'Эти позиции уже в черновике');},
 'today-open':b=>{navigate('Закупки');Object.assign(state,{risk:'CRITICAL',transit:'Нет',supplier:b.dataset.supplier||'Все',abc:b.dataset.important?(b.dataset.supplier==='IEK'?'A':'1'):'Все'});render();},
 'today-review':()=>{navigate('Закупки');state.review=true;render();},
 'today-coming':()=>{navigate('Закупки');Object.assign(state,{risk:'CRITICAL',transit:'Есть'});render();},
 'today-example':b=>openProduct(b.dataset.id,'history'),
 'open-draft':()=>navigate('Закупки','draft'),
 'go-overview':()=>navigate('Обзор'),
 'today-restored':()=>{navigate('Закупки');state.restored=true;render();},
 'assistant-open':()=>openAssistant(),
 'assistant-close':()=>$('#assistant-dialog')?.close(),
 'assistant-fill':b=>{$('#assistant-input').value=b.dataset.q;sendAssistant();},
 'assistant-send':()=>sendAssistant(),
 'explain-product':async()=>{if(!saveProductEdit())return;const p=find(state.productId);state.detailTab='ask';chats[p.id]={q:'Объясни простыми словами, почему рекомендовано именно такое количество и что будет, если не заказать.'};renderProduct();if(demo.aiChat===undefined)await aiStatus();renderProduct();if(demo.aiChat)actions['ask-send']();},
 'export':exportApproved,'export-csv':exportCsv,'settings':settings,'close-settings':()=>$('#settings-dialog').close(),
 'apply-settings':()=>{const scenario=$('input[name="scenario"]:checked').value;if(saved.scenario!==scenario){saved.scenario=scenario;invalidate();persist();}$('#settings-dialog').close();render();toast('Демонстрационный сценарий применён');},
 'reset-prompt':()=>{const d=$('#confirm-dialog');d.innerHTML=`<div class="modal-heading"><h2>Сбросить демо-правки?</h2></div><p>Будут удалены ручные количества, комментарии и черновик в этом браузере. Исходные демо-товары останутся.</p><div class="modal-actions">${btn('Отмена','close-modal')}${btn('Сбросить','reset','danger')}</div>`;d.showModal();},
 'reset':()=>{Object.assign(saved,{edits:{},draft:[],rejected:[],approved:null,scenario:'base'});persist();$('#confirm-dialog').close();$('#settings-dialog').close();state.selected.clear();render();toast('Исходное демо восстановлено');},
};
document.addEventListener('click',e=>{
 const nav=e.target.closest('[data-nav]');if(nav){navigate(nav.dataset.nav);return;}
 const b=e.target.closest('[data-action]');if(b){actions[b.dataset.action]?.(b);return;}
 const row=e.target.closest('[data-row]');if(row&&!e.target.closest('input,button,a'))openProduct(row.dataset.row);
});
document.addEventListener('input',e=>{if(e.target.id==='list-search'){state.query=e.target.value;state.selected.clear();render();}if(e.target.id==='approver')syncApprove();});
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){if(e.target.id==='assistant-input')sendAssistant();if(e.target.id==='ask-input')actions['ask-send']();}});
function syncApprove(){const b=$('#approve-button');if(b)b.disabled=b.dataset.blocked==='1'||!($('#acknowledge')?.checked&&$('#approver')?.value.trim());}
document.addEventListener('change',e=>{
 const t=e.target;
 if(t.id==='supplier-filter'){state.supplier=t.value;state.selected.clear();render();}
 if(t.id==='category-filter'){state.category=t.value;state.selected.clear();render();}
 if(['transit-filter','oneoff-filter','abc-filter'].includes(t.id)){state[t.id.split('-')[0]]=t.value;state.selected.clear();render();}
 if(t.id==='chart-product'){state.chartId=t.value;render();}
 if(t.dataset.select){t.checked?state.selected.add(t.dataset.select):state.selected.delete(t.dataset.select);render();}
 if(t.id==='select-all'){let rows=filtered().filter(p=>state.risk==='Все'||(state.risk==='В пути'?p.in_transit_in_horizon>0:state.risk==='Срочные'?['CRITICAL','HIGH'].includes(p.urgency):p.urgency===state.risk)).filter(p=>!saved.rejected.includes(p.id));rows.forEach(p=>t.checked?state.selected.add(p.id):state.selected.delete(p.id));render();}
 if(t.dataset.draftQty){const p=find(t.dataset.draftQty),q=validQty(t);if(q===null){t.value=quantity(p);toast('Введите целое неотрицательное количество');return;}saved.edits[p.id]={qty:q,comment:comment(p)};invalidate();persist();render();}
 if(t.id==='acknowledge')syncApprove();
});
function showChartPoint(e){const t=e.target.closest('[data-chart-point]');if(!t)return;const p=find(t.dataset.product),s=p.history[Number(t.dataset.chartPoint)],tip=t.closest('.plot').querySelector('.plot-tooltip');tip.textContent=`${s.month} · ${s.raw!==null?`Факт: ${unit(s.raw,p)} · Скорр.: ${unit(s.clean,p)}`:`Прогноз: ${unit(s.forecast,p)}`}`;tip.classList.add('visible');}
document.addEventListener('mouseover',showChartPoint);
document.addEventListener('click',e=>{const t=e.target.closest('[data-chart-point]');if(t&&!e.target.closest('#product-dialog'))openProduct(t.dataset.product,'history');});
document.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.matches('[role=button][data-action]'))e.target.click();});document.addEventListener('focusin',showChartPoint);
document.addEventListener('mouseout',e=>{if(e.target.closest('[data-chart-point]'))e.target.closest('.plot')?.querySelector('.plot-tooltip')?.classList.remove('visible');});
document.addEventListener('focusout',e=>{if(e.target.closest('[data-chart-point]'))e.target.closest('.plot')?.querySelector('.plot-tooltip')?.classList.remove('visible');});
$('#product-dialog').addEventListener('cancel',e=>{if(!saveProductEdit())e.preventDefault();});
document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'&&!$('dialog[open]')){e.preventDefault();if(state.page==='Обзор'||state.page==='Данные')navigate('Товары');$('#list-search')?.focus();}});
for(const d of document.querySelectorAll('dialog')){d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom){if(d.id==='product-dialog'&&!saveProductEdit())return;d.close();}}});}
async function start(){
 $('#page').innerHTML=empty('Загружаем данные','Подготавливаем рекомендации и историю спроса.');
 try{
  [allProducts,sources]=await Promise.all([dataProvider.getProducts(),dataProvider.getSources()]);
  const ids=new Set(allProducts.map(p=>p.id));saved.draft=saved.draft.filter(id=>ids.has(id));saved.rejected=(saved.rejected||[]).filter(id=>ids.has(id));
  for(const [id,edit] of Object.entries(saved.edits)){if(!ids.has(id)||!Number.isSafeInteger(edit?.qty)||edit.qty<0||edit.qty>100000000)delete saved.edits[id];}
  render();
 }catch{ $('#page').innerHTML=empty('Не удалось открыть данные','Проверьте доступность демо-файла и перезагрузите страницу.'); }
}
start();
