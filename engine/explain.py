def explain(line, params):
    r = line
    unit = r["unit"]
    text = (f"Заказ {r['recommended_qty']:,} {unit} (кратно {r['moq']}). "
            f"Прогноз на {r['horizon_days']} дн.: {r['forecast_horizon']:.1f} {unit}; "
            f"база {r['base_monthly']:.1f}/мес., тренд ×{r['trend']:.2f}, прирост {params.growth_pct:+g}%. "
            f"Страховой запас {r['safety_stock']:.1f}. Свободный остаток {r['free_stock']:.1f}, "
            f"в пути в горизонте {r['in_transit_in_horizon']:.1f}, позже {r['in_transit_later']:.1f}. "
            f"Потребность до округления {r['need']:.1f}. "
            f"Допущения: срок поставки {r['lead_time_days']} дн., период заказа {params.review_days} дн., z={params.service_z:g}. ")
    text += "Сезонность поставщика: " + ", ".join(f"{m['month']} ×{m['season']:.2f}" for m in r['forecast_months']) + ". "
    for e in r['excluded_events']:
        if e['doc_no']:
            text += f"Разовая накладная №{e['doc_no']} от {e['date']} на {e['invoice_qty']:g}: "
        else:
            text += f"Статистический всплеск {e['month']}: "
        text += f"из месячного спроса исключено {e['qty']:.1f} {unit}. "
    for e in r['restored_events']:
        text += f"Дефицит {e['month']}: восстановлен спрос {e['clean_qty']:.1f} вместо {e['raw_qty']:.1f}. "
    notes = {"STOCK_ESTIMATED": "Остаток IEK оценочный: поступления неизвестны, подтвердить в 1С.",
             "MOQ_MISSING": "Кратность отсутствует: принято 1.",
             "COIL_305": "Кабель закупается бухтами по 305 м.",
             "SHORT_HISTORY": "Меньше трёх месяцев с продажами: проверить вручную.",
             "NO_DEMAND": "За последние 12 полных месяцев продаж нет; заказ 0.",
             "PARTIAL_MONTH": "Неполный месяц исключён из обучения; масштабирование только для графика.",
             "MOQ_OVERSHOOT": "Округление увеличивает потребность более чем на 50%; проверить вручную.",
             "SANITY_HIGH": "Количество превышает проверочный предел; проверить вручную.",
             "MONTHLY_MISSING": "Нет месячного отчёта для SKU; использованы доступные накладные."}
    text += " ".join(notes[c] for c in r['reason_codes'] if c in notes)
    return text.strip()
