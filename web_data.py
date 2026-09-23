"""Данные для веб-интерфейса web/ (QadamSupply, фронт Аслана) из расчёта engine/.

Форма товара совпадает с демо-набором web/mockDashboard.js, поэтому интерфейс работает на
реальных данных без переделки. Все числа — из engine/run.py (тот же расчёт, что у API и агента).
"""
import copy
from functools import lru_cache

import pandas as pd

from agent.memory import Memory
from engine import calc
from engine.load import load
from engine.models import Params
from engine.run import SUPPLIER_OUT, compute

USER = "local"


def effective_params() -> Params:
    """Параметры из config.yaml + то, что менеджер сохранил в админке."""
    settings = Params.from_yaml().model_dump(mode="json")
    settings.update(Memory().preferences(USER))
    return Params.model_validate(settings)


@lru_cache(maxsize=1)
def backtest() -> dict:
    """Проверка на истории: расчёт на срезе «4 месяца назад» против того, что случилось потом.

    Реальный дефицит — остаток на 1-е число ≤ 0 в один из двух месяцев после среза у товара,
    который продавался в три месяца до среза. Долю ложных тревог не считаем: менеджер в эти
    месяцы заказывал сам, и часть «критичных» он просто спас.
    """
    ds = load()
    cur = ds.as_of.to_period("M").to_timestamp()
    cut = cur - pd.DateOffset(months=3)          # срез: конец месяца за 3 месяца до текущего
    check = [cut + pd.DateOffset(months=1), cut + pd.DateOffset(months=2)]
    d = copy.copy(ds)
    d.lines = ds.lines[ds.lines["date"] < cut]
    d.stock = ds.stock[ds.stock["month"] <= cut]
    d.transit = ds.transit.iloc[0:0]
    d.stock_current = pd.Series(dtype=float)
    d.as_of = d.lines["date"].max()
    orders = calc.compute(d, calc.Params()).orders.set_index("code")
    st = ds.stock.pivot_table(index="code", columns="month", values="stock", aggfunc="sum")
    sales = (ds.lines.assign(m=ds.lines["date"].dt.to_period("M").dt.to_timestamp())
             .pivot_table(index="code", columns="m", values="qty", aggfunc="sum").reindex(st.index).fillna(0))
    before = [c for c in sales.columns if cut - pd.DateOffset(months=3) <= c < cut]
    active = sales[before].sum(axis=1) > 0
    zero = pd.Series(False, index=st.index)
    for m in check:
        if m in st.columns:
            zero |= st[m] <= 0
    real = active & zero
    urg = orders.reindex(st.index)["urgency"].fillna("не нужно")[real]
    counts = urg.value_counts().to_dict()
    flagged = {k: int(counts.get(k, 0)) for k in ["критично", "высокая", "плановая"]}
    warned = sum(flagged.values())
    hits = orders[orders.index.isin(real[real].index) & (orders["urgency"] == "критично")]
    hits = hits.sort_values("order_qty", ascending=False).head(5)
    months_ru = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре",
                 "октябре", "ноябре", "декабре"]
    return {
        "as_of": f"{d.as_of:%d.%m.%Y}", "window": f"{months_ru[check[0].month - 1]}–{months_ru[check[-1].month - 1]} {check[-1].year}",
        "real": int(real.sum()), "warned": int(warned), "warned_pct": round(warned / max(int(real.sum()), 1) * 100),
        "flagged": flagged, "missed": int(counts.get("не нужно", 0)),
        "examples": [{"sku": c, "name": r["name"], "supplier": SUPPLIER_OUT.get(r["supplier"], r["supplier"]),
                      "order_qty": float(r["order_qty"]), "unit": r["unit"]} for c, r in hits.iterrows()],
    }

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
    transit = {k: g for k, g in ds.transit.groupby("code")}
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
            # для раскрытия строк разбора в карточке
            "forecast_months": [{"month": m["month"], "days": m["days"], "season": round(m["season"], 2),
                                 "forecast": round(m["forecast"], 1), "horizon_qty": round(m["horizon_qty"], 1)}
                                for m in o["forecast_months"]],
            "transit_lines": [{"doc": str(t.doc), "qty": float(t.qty),
                               "eta": pd.Timestamp(t.arrival_date).strftime("%d.%m.%Y"),
                               "in_horizon": bool(pd.Timestamp(t.arrival_date) <= ds.as_of + pd.Timedelta(days=int(o["horizon_days"])))}
                              for t in transit[sku].itertuples()] if sku in transit else [],
            "stock_source": o["stock_source"], "lead_source": o["lead_source"],
            "service_level": o["service_level"], "base_monthly": round(o["base_monthly"], 1),
            "trend": o["trend"],
            "growthScenario": {"forecast_horizon": round(float(g.at[sku, "forecast_horizon"]), 1),
                               "recommended_qty": int(g.at[sku, "recommended_qty"])} if sku in g.index else
                              {"forecast_horizon": round(o["forecast_horizon"], 1), "recommended_qty": o["recommended_qty"]},
        })
    return out


@lru_cache(maxsize=4)
def payload(growth_pct: float = 20.0) -> dict:
    """Всё, что нужно интерфейсу, одним ответом. Кэшируется до перезапуска сервера."""
    ds = load()
    params = effective_params()
    rules = Memory().rules(USER)
    res = compute(ds, params, rules)
    growth = compute(ds, params.model_copy(update={"growth_pct": growth_pct}), rules)
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
                 "backtest": backtest(), "rulesCount": len(rules),
                 "live": True, "leadText": {s: f"{_days(d)} · {src}" for s, (d, src) in lead.items()},
                 "warnings": res.warnings, "orderValue": {s: float(v) for s, v in
                                                          res.orders.groupby("supplier")["order_value"].sum().items()
                                                          if v}},
        "products": _clean(_products(res, growth, ds)),
        "sources": sources,
    }
