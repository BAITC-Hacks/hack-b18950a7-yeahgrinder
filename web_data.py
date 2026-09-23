"""Данные для веб-интерфейса web/ (QadamSupply, фронт Аслана) из расчёта engine/.

Форма товара совпадает с демо-набором web/mockDashboard.js, поэтому интерфейс работает на
реальных данных без переделки. Все числа — из engine/run.py (тот же расчёт, что у API и агента).
"""
from functools import lru_cache

import pandas as pd

from engine.load import load
from engine.models import Params
from engine.run import compute

MONTHS = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря"]
MONTHS_FULL = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
               "октябрь", "ноябрь", "декабрь"]
WARNINGS = {
    "STOCK_ESTIMATED": "Остаток оценочный: приходы текущего месяца в выгрузке не видны — сверить с 1С.",
    "LEAD_ASSUMED": "Срок поставки принят как допущение: дат заказов у поставщика нет.",
    "MOQ_MISSING": "Кратности нет в справочнике — округлено до 1.",
    "MOQ_OVERSHOOT": "Округление до кратности добавило больше 50% к потребности.",
    "SANITY_HIGH": "Заказ заметно больше обычных продаж — проверить вручную.",
    "CATEGORY_NO_AUTO": "Категория 7 (новинка или выведен) — автозаказа нет, решает менеджер.",
    "NO_DEMAND": "За 12 месяцев продаж нет — заказ не рекомендуется.",
    "SHORT_HISTORY": "Короткая история продаж — прогноз менее надёжен.",
}
REVIEW = {"ONE_OFF_INVOICE", "STAT_SPIKE", "STOCKOUT_RESTORED", "SANITY_HIGH", "MOQ_OVERSHOOT"}


def _days(n: int) -> str:
    n = int(n)
    word = "день" if n % 10 == 1 and n % 100 != 11 else ("дня" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "дней")
    return f"{n} {word}"


def _label(ts) -> str:
    return f"{MONTHS[ts.month - 1]} {ts:%y}"


def _num(x):
    return None if x is None or pd.isna(x) else round(float(x), 1)


def _history(h: pd.DataFrame, as_of: pd.Timestamp, anchor: pd.Timestamp | None):
    """8 точек, как рассчитан график интерфейса: 6 месяцев факта + прогноз.
    Если разовая сделка или дефицит были раньше — окно на них (архивный кейс, без прогноза)."""
    cur = as_of.to_period("M").to_timestamp()
    last_full = cur - pd.DateOffset(months=1)
    by_month = h.set_index("month")
    if anchor is not None and anchor < last_full - pd.DateOffset(months=5):
        start = anchor - pd.DateOffset(months=2)
        months = pd.date_range(start, periods=8, freq="MS")
        rows = []
        for m in months:
            r = by_month.loc[m] if m in by_month.index and m < cur else None
            rows.append({"month": _label(m), "raw": _num(r["qty"]) if r is not None else None,
                         "clean": _num(r["clean_qty"]) if r is not None else None, "forecast": None})
        label = f"Архив: {MONTHS_FULL[months[0].month - 1]} {months[0]:%Y} — {MONTHS_FULL[months[-1].month - 1]} {months[-1]:%Y}"
        return rows, label
    hist = pd.date_range(last_full - pd.DateOffset(months=5), last_full, freq="MS")
    fut = pd.date_range(cur, periods=2, freq="MS")
    rows = []
    for i, m in enumerate(hist):
        r = by_month.loc[m] if m in by_month.index else None
        clean = _num(r["clean_qty"]) if r is not None else None
        rows.append({"month": _label(m), "raw": _num(r["qty"]) if r is not None else None, "clean": clean,
                     "forecast": clean if i == len(hist) - 1 else None})
    for m in fut:
        r = by_month.loc[m] if m in by_month.index else None
        rows.append({"month": _label(m), "raw": None, "clean": None,
                     "forecast": _num(r["forecast"]) if r is not None else None})
    label = f"{MONTHS_FULL[hist[0].month - 1].capitalize()} — {MONTHS_FULL[fut[-1].month - 1]} {fut[-1]:%Y} · прогноз с {MONTHS_GEN[cur.month - 1]}"
    return rows, label


def _restored_text(events, unit) -> str:
    if not events:
        return ""
    parts = [f"{MONTHS_FULL[int(e['month'][5:]) - 1].capitalize()} {e['month'][:4]}: факт {e['raw_qty']:,.0f} {unit} → "
             f"скорректированный спрос {e['clean_qty']:,.0f} {unit}".replace(",", " ") for e in events[:3]]
    if len(events) > 3:
        parts.append(f"и ещё {len(events) - 3} мес.")
    return "<br>".join(parts)


def _clean(v):
    if isinstance(v, float) and v != v:
        return None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def _products(res, growth, ds) -> list[dict]:
    hist = {k: g for k, g in res.history.groupby("sku")}
    g = growth.orders.set_index("sku")
    out = []
    for o in res.orders.to_dict("records"):
        sku, unit = o["sku"], o["unit"]
        events = o["excluded_events"] + o["restored_events"]
        anchor = min((pd.Timestamp(e["month"] + "-01") for e in events), default=None)
        rows, label = _history(hist.get(sku, pd.DataFrame(columns=res.history.columns)), ds.as_of, anchor)
        codes = set(o["reason_codes"])
        warnings = [WARNINGS[c] for c in o["reason_codes"] if c in WARNINGS]
        excluded = [{"date": pd.Timestamp(e["date"]).strftime("%d.%m.%Y") if e["date"] else e["month"],
                     "doc": (e["doc_no"] or "—").split("/")[0], "qty": e["raw_qty"],
                     "threshold": round(e["raw_qty"] - e["excluded_qty"], 1), "excess": e["excluded_qty"]}
                    for e in o["excluded_events"]]
        out.append({
            "id": sku, "sku": sku, "article": o["article"] or sku, "name": o["name"],
            "supplier": o["supplier"], "category": o["group_name"] or o["group"], "unit": unit,
            "abc": o["category"], "stock_current": round(o["free_stock"], 1),
            "in_transit_in_horizon": round(o["in_transit_in_horizon"], 1),
            "cover_days": None if o["cover_days"] is None or pd.isna(o["cover_days"]) else round(o["cover_days"]),
            "lead_time_days": o["lead_time_days"], "horizon_days": o["horizon_days"],
            "forecast_horizon": round(o["forecast_horizon"], 1), "safety_stock": round(o["safety_stock"], 1),
            "moq": o["moq"], "recommended_qty": o["recommended_qty"], "urgency": o["urgency"],
            "need": round(o["need"], 1), "order_value": o["order_value"],
            # себестоимость единицы: сумма считается и по ручному количеству, и при рекомендации 0
            "unit_cost": None if o["unit_cost"] is None or pd.isna(o["unit_cost"]) else float(o["unit_cost"]),
            "warnings": warnings or ["Данные полные, допущений нет."],
            "quality": "Нужна проверка" if codes & REVIEW else ("Есть допущения" if warnings else "Данные полные"),
            "reason_text": o["reason_text"], "excluded_events": excluded,
            "lost_qty": round(sum(e["qty"] for e in o["restored_events"]), 1),
            "restored_text": _restored_text(o["restored_events"], unit),
            "history": rows, "history_label": label,
            "growthScenario": {"forecast_horizon": round(float(g.at[sku, "forecast_horizon"]), 1),
                               "recommended_qty": int(g.at[sku, "recommended_qty"])} if sku in g.index else
                              {"forecast_horizon": round(o["forecast_horizon"], 1), "recommended_qty": o["recommended_qty"]},
        })
    return out


@lru_cache(maxsize=4)
def payload(growth_pct: float = 20.0) -> dict:
    """Всё, что нужно интерфейсу, одним ответом. Кэшируется до перезапуска сервера."""
    ds = load()
    params = Params.from_yaml()
    res = compute(ds, params)
    growth = compute(ds, params.model_copy(update={"growth_pct": growth_pct}))
    lead = {("IEK" if s == "ИЭК" else s): v for s, v in ds.lead_default.items()}
    sources = [
        {"name": "Динамика продаж", "type": "Строки накладных", "date": f"{ds.as_of:%d.%m.%Y}", "status": "Подключено",
         "description": f"{len(ds.lines):,} строк накладных — основа спроса и поиска разовых сделок.".replace(",", " ")},
        {"name": "Остатки по месяцам", "type": "Остатки на 1-е число", "date": f"01.{ds.as_of:%m.%Y}",
         "status": "Есть оговорки", "description": "Периоды без товара для досчёта спроса. Остаток ИЭК — оценка, "
                                                   "SE — из файла менеджера."},
        {"name": "Товары в пути", "type": "Ожидаемые поступления", "date": f"{ds.as_of:%d.%m.%Y}", "status": "Подключено",
         "description": f"{len(ds.transit)} строк в пути. Для ИЭК из дат заказов выведен срок поставки."},
        {"name": "MOQ и кратность", "type": "Условия заказа", "date": f"{ds.as_of:%d.%m.%Y}", "status": "Подключено",
         "description": f"Кратность известна для {int(ds.items['moq_known'].sum())} из {len(ds.items)} товаров."},
    ]
    return {
        "meta": {"asOf": f"{ds.as_of:%Y-%m-%d}", "reviewDays": params.review_days, "growthPct": growth_pct,
                 "live": True, "leadText": {s: f"{_days(d)} · {src}" for s, (d, src) in lead.items()},
                 "warnings": res.warnings, "orderValue": {s: float(v) for s, v in
                                                          res.orders.groupby("supplier")["order_value"].sum().items()
                                                          if v}},
        "products": _clean(_products(res, growth, ds)),
        "sources": sources,
    }
