// Fixed demo fixtures. These are not results from a connected calculation engine.
export const demo = { asOf: '2026-09-22', version: 2, horizon: 60, reviewDays: 30 };
export const suppliers = ['IEK', 'Systeme Electric'];
export const urgency = {
  CRITICAL: { label: 'Критично', className: 'critical', rank: 0 },
  HIGH: { label: 'Высокая', className: 'high', rank: 1 },
  PLANNED: { label: 'Плановая', className: 'planned', rank: 2 },
  OK: { label: 'В норме', className: 'low', rank: 3 },
};
const fixtures = [
  ['iek-1','300200428_','MVA20-1-016-C','Автомат ВА47-29 1P 16A','IEK','Автоматы','шт.',12,0,6,30,112,20,10,120,'CRITICAL', [34,38,40,43,46,49], [34,38,40,43,46,49], [54,58], 135,150],
  ['iek-2','300200429_','KKM22-025-230-10','Контактор КМИ-22510','IEK','Контакторы','шт.',20,40,10,30,120,20,10,80,'HIGH', [36,40,42,44,48,51], [36,40,42,44,48,51], [58,62],144,110],
  ['se-1','030200310_','NUC-UTP-5E-305','Кабель U/UTP Cat.5e','Systeme Electric','Кабели','м',30,305,2,45,800,100,305,610,'CRITICAL', [220,240,250,270,300,340], [220,240,250,270,300,340], [380,420],960,915],
  ['iek-3','300200430_','MDV10-2-040-030','УЗО ВД1-63 2P 40A 30мА','IEK','УЗО','шт.',35,0,18,30,120,15,10,100,'CRITICAL', [48,50,51,4,55,57], [48,50,51,52,55,57], [58,62],144,130],
  ['se-2','030200311_','SPP-36-4000K','Светильник ДПП 36 Вт','Systeme Electric','Светильники','шт.',420,0,168,45,150,30,1,0,'OK', [62,64,66,68,70,72], [62,64,66,68,70,72], [73,77],180,0],
  ['se-3','030200312_','EZ9E112S2SRU','Щит распределительный 12 модулей','Systeme Electric','Щиты','шт.',105,100,32,45,200,45,10,40,'PLANNED', [70,75,79,80,84,88], [70,75,79,80,84,88], [96,104],240,80],
  ['iek-4','300200431_','MVA51-3-063-C','Автомат ВА47-100 3P 63A','IEK','Автоматы','шт.',18,24,12,24,90,12,6,60,'HIGH', [28,30,33,35,37,40], [28,30,33,35,37,40], [43,47],108,78],
  ['iek-loop','130200305_','LOOP-METAL','Петля металлическая LOOP','IEK','Аксессуары','шт.',80,0,20,30,240,40,20,200,'CRITICAL', [90,95,210100,98,105,110], [90,95,220,98,105,110], [116,124],288,260],
  ['se-4','030200313_','ATN000141','Розетка AtlasDesign с заземлением','Systeme Electric','Розетки','шт.',64,120,24,45,160,24,10,0,'OK', [54,57,60,62,65,68], [54,57,60,62,65,68], [78,82],192,40],
  ['iek-5','300200432_','YKM40-02-65','Коробка монтажная КМ41212','IEK','Аксессуары','упак.',4,0,10,30,24,4,2,24,'CRITICAL', [8,9,9,10,10,11], [8,9,9,10,10,11], [11,13],29,30],
  ['se-5','030200314_','EZ9F34116','Автомат Easy9 1P 16A','Systeme Electric','Автоматы','шт.',180,0,60,45,180,40,10,40,'PLANNED', [64,66,68,70,74,79], [64,66,68,70,74,79], [86,94],216,80],
  ['iek-6','300200433_','LLE-230-40','Лампа светодиодная E27 10 Вт','IEK','Светильники','шт.',230,100,115,30,120,20,10,0,'OK', [42,44,46,48,49,51], [42,44,46,48,49,51], [58,62],144,0],
];
const months = ['Апр 26','Май 26','Июн 26','Июл 26','Авг 26','Сен 26','Окт 26','Ноя 26'];
export const products = fixtures.map(f => {
  const [id,sku,article,name,supplier,category,unit,stock_current,in_transit_in_horizon,cover_days,lead_time_days,forecast_horizon,safety_stock,moq,recommended_qty,risk,raw,clean,forecast,growthForecast,growthQty] = f;
  const loop = id === 'iek-loop', restored = id === 'iek-3';
  const warnings = supplier === 'IEK' ? ['Остаток оценочный — требуется сверка с 1С.'] : ['Срок поставки 45 дней принят как допущение.'];
  if (loop) warnings.push('Обнаружено расхождение между источниками продаж.');
  return {id,sku,article,name,supplier,category,unit,stock_current,in_transit_in_horizon,cover_days,lead_time_days,forecast_horizon,safety_stock,moq,recommended_qty,urgency:risk,
    need: forecast_horizon + safety_stock - stock_current - in_transit_in_horizon,
    warnings, quality: loop || restored ? 'Нужна проверка' : 'Есть допущения',
    reason_text: loop ? 'Разовая накладная на 210 000 шт. скорректирована до 120 шт. Обычный спрос сохранён; всплеск не переносится в регулярный заказ.' : restored ? 'В июле товар отсутствовал. Вместо 4 проданных единиц учтён скорректированный спрос 52 шт. Низкие продажи не приняты за падение спроса.' : recommended_qty ? `Запаса хватит примерно на ${cover_days} дн. В рекомендации учтены поставка, страховой запас и кратность ${moq} ${unit}` : 'Текущий запас и ожидаемые поступления покрывают демонстрационную потребность. Пополнение сейчас не требуется.',
    excluded_events: loop ? [{date:'09.06.2025',doc:'20000064179',qty:210000,threshold:120,excess:209880}] : [],
    lost_qty: restored ? 48 : 0,
    history: months.map((month,i)=>({month:loop?month.replace('26','25'):month,raw:i<6?raw[i]:null,clean:i<6?clean[i]:null,forecast:i>=5?(i===5?clean[5]:forecast[i-6]):null})),
    growthScenario: {forecast_horizon:growthForecast,recommended_qty:growthQty},
  };
});
export const sources = [
  {name:'Динамика продаж',type:'Строки накладных',date:'22.09.2026',status:'Демо-источник',description:'Основа для истории спроса и поиска разовых крупных продаж.'},
  {name:'Остатки по месяцам',type:'Остатки и периоды отсутствия',date:'01.09.2026',status:'Есть оговорки',description:'Остаток IEK оценочный. Периоды отсутствия используются для восстановления спроса.'},
  {name:'Товары в пути',type:'Ожидаемые поступления',date:'22.09.2026',status:'Демо-источник',description:'В карточках показан транзит, учитываемый в горизонте заказа.'},
  {name:'MOQ и кратность',type:'Условия заказа',date:'22.09.2026',status:'Демо-источник',description:'В наборе заполнено для всех товаров. Кабель заказывается бухтами по 305 м.'},
];
export const dataProvider = {
  async getProducts() { return products; },
  async getSources() { return sources; },
};
