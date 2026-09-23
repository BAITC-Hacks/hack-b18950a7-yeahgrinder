"""Мост: параметры и схема OrderLine сервиса/API/агента поверх расчёта engine/calc.py.

Все количества считает engine/calc.py (тот же расчёт, что видит интерфейс). Этот модуль
только переводит параметры (engine.models.Params → engine.calc.Params), результат —
в строки OrderLine и применяет правила менеджера. LLM и API здесь нет.
"""
from dataclasses import dataclass, field
from hashlib import sha256
from math import ceil
from statistics import NormalDist

import pandas as pd

from engine import calc
from engine.load import Dataset
from engine.models import OrderLine, Params
from engine.validate import summarize, validate_orders

SUPPLIER_OUT = {"ИЭК": "IEK", "Systeme Electric": "Systeme Electric"}  # имя в данных → имя в API
SUPPLIER_IN = {v: k for k, v in SUPPLIER_OUT.items()}
URGENCY = {"критично": "CRITICAL", "высокая": "HIGH", "плановая": "PLANNED", "не нужно": "OK"}
CODE_MAP = {"ONE_OFF_EXCLUDED": "ONE_OFF_INVOICE"}
FLAG_CODES = {"SANITY_HIGH", "MOQ_OVERSHOOT", "SHORT_HISTORY", "STOCK_ESTIMATED", "MOQ_MISSING",
              "LEAD_ASSUMED", "CATEGORY_NO_AUTO"}
UI_DEFAULT_K, SPEC_K_MAD = 6.0, 3.5  # config.yaml хранит k_mad в шкале спеки (3.5 ≈ 6 в calc)


@dataclass
class Result:
    orders: pd.DataFrame
    history: pd.DataFrame
    params: Params
    warnings: list[str] = field(default_factory=list)
    run_id: str | None = None

    @property
    def summary(self):
        return summarize(self.orders)


def fingerprint(ds: Dataset) -> str:
    """Короткий отпечаток снимка данных — для аудита расчётов."""
    parts = [str(ds.as_of), len(ds.lines), float(ds.lines["qty"].sum()), len(ds.items),
             len(ds.transit), float(ds.transit["qty"].sum()) if len(ds.transit) else 0.0]
    return sha256(repr(parts).encode()).hexdigest()[:16]


def calc_params(p: Params) -> calc.Params:
    lead = {SUPPLIER_IN.get(k, k): v for k, v in p.lead_time_days.items() if v is not None}
    service = {}
    if p.service_z is not None:  # явный z — один уровень сервиса для всех категорий
        service = dict(service_by_category={}, service_level=NormalDist().cdf(p.service_z))
    return calc.Params(
        review_days=p.review_days, growth_pct=p.growth_pct,
        oneoff_k=p.outlier.k_mad * UI_DEFAULT_K / SPEC_K_MAD,
        groups=p.groups or None,
        suppliers=[SUPPLIER_IN.get(s, s) for s in p.suppliers] if p.suppliers else None,
        lead_by_supplier=lead or None,
        clean_oneoffs=p.clean_outliers, restore_stockouts=p.restore_stockouts, **service)


def _events(res: calc.Result) -> tuple[dict, dict]:
    excluded, restored = {}, {}
    for r in res.oneoffs.itertuples():
        excluded.setdefault(r.code, []).append({
            "type": "ONE_OFF_INVOICE", "month": f"{r.date:%Y-%m}", "date": f"{r.date:%Y-%m-%d}",
            "doc_no": str(r.doc), "invoice_qty": float(r.qty), "raw_qty": float(r.qty),
            "clean_qty": float(r.typical), "excluded_qty": float(r.qty), "threshold": float(r.threshold)})
    h = res.history
    if len(h):
        lines_months = {(e["month"]) for ev in excluded.values() for e in ev}
        spikes = h[(h["oneoff_qty"] > 0)]
        for r in spikes.itertuples():  # месячные выбросы 2024 г. (накладных за 2024 нет)
            m = f"{r.month:%Y-%m}"
            if not any(e["month"] == m for e in excluded.get(r.code, [])) and m not in lines_months:
                excluded.setdefault(r.code, []).append({
                    "type": "STAT_SPIKE", "month": m, "date": None, "doc_no": None, "invoice_qty": None,
                    "raw_qty": float(r.raw_qty), "clean_qty": float(r.raw_qty - r.oneoff_qty),
                    "excluded_qty": float(r.oneoff_qty), "threshold": None})
        for r in h[h["lost_qty"] > 0].itertuples():
            restored.setdefault(r.code, []).append({
                "month": f"{r.month:%Y-%m}", "raw_qty": float(r.raw_qty), "qty": float(r.lost_qty),
                "clean_qty": float(r.clean_qty)})
    return excluded, restored


def compute(ds: Dataset, params: Params | None = None, rules=None) -> Result:
    params = params or Params.from_yaml()
    rules = rules or {}
    res = calc.compute(ds, calc_params(params))
    warnings = list(res.warnings)
    if params.as_of != ds.as_of.date():
        warnings.append(f"Дата в параметрах {params.as_of} не совпадает с датой данных {ds.as_of.date()} — "
                        "расчёт сделан на дату данных.")
    excluded, restored = _events(res)
    h = res.history
    hist_end = ds.as_of.to_period("M").to_timestamp()
    last12 = (h[(h["month"] < hist_end) & (h["month"] >= hist_end - pd.DateOffset(months=12))]
              .groupby("code")["raw_qty"].sum()) if len(h) else pd.Series(dtype=float)
    transit = ds.transit
    rows = []
    for o in res.orders.to_dict("records"):
        code, supplier = o["code"], SUPPLIER_OUT.get(o["supplier"], o["supplier"])
        horizon_end = ds.as_of + pd.Timedelta(days=int(o["horizon_days"]))
        t = transit[transit["code"] == code]
        later = float(t.loc[t["arrival_date"] > horizon_end, "qty"].sum())
        codes = [CODE_MAP.get(f, f) for f in o["flags"]]
        codes += [e["type"] for e in excluded.get(code, [])]
        no_demand = float(last12.get(code, 0.0)) == 0.0
        if no_demand:
            codes.append("NO_DEMAND")
        if any(m["season"] >= 1.2 for m in o["forecast_months"]):
            codes.append("SEASON_PEAK")
        qty = int(round(o["order_qty"]))
        moq = max(int(round(o["moq"])), 1)
        if qty > max(o["need"], 0) + 1e-8:
            codes.append("MOQ_ROUNDED")
        urgency = URGENCY[o["urgency"]]
        text = o["reason"]
        rule = rules.get((supplier, code))
        if rule is not None and not no_demand:
            qty = max(qty, ceil(int(rule) / moq) * moq)
            codes.append("MANAGER_RULE")
            text += f" Правило менеджера: минимум {rule} {o['unit']} при наличии спроса."
            if qty and urgency == "OK":
                urgency = "PLANNED"
        if qty > max(3 * o["max_monthly_raw"] * o["horizon_days"] / 30, moq):
            codes.append("SANITY_HIGH")
        codes = list(dict.fromkeys(codes))
        cover = o["days_cover"]
        rows.append({
            "supplier": supplier, "sku": code,
            "article": None if pd.isna(o["article"]) else str(o["article"]),
            "name": o["name"], "unit": str(o["unit"]), "group": str(o["group"]),
            "free_stock": float(o["stock_now"]), "in_transit_in_horizon": float(o["in_transit"]),
            "in_transit_later": later, "base_monthly": float(o["base_month"]),
            "trend": float(o["trend"]) if o["trend"] > 0 else 1.0,
            "forecast_horizon": float(o["forecast_need"]), "safety_stock": float(o["safety_stock"]),
            "need": float(o["need"]), "moq": moq, "recommended_qty": qty, "urgency": urgency,
            "cover_days": None if cover is None or cover != cover or cover == float("inf") else float(cover),
            "horizon_days": int(o["horizon_days"]), "lead_time_days": int(o["lead_days"]),
            "max_monthly_raw": float(o["max_monthly_raw"]), "reason_codes": codes, "reason_text": text,
            "excluded_events": excluded.get(code, []), "restored_events": restored.get(code, []),
            "forecast_months": o["forecast_months"], "flags": [c for c in codes if c in FLAG_CODES]})
    orders = pd.DataFrame(rows, columns=list(OrderLine.model_fields))
    orders = orders.sort_values("supplier", kind="stable").reset_index(drop=True)  # IEK, затем SE
    bad = validate_orders(orders)
    if bad:  # не роняем весь расчёт из-за одной строки — убираем её и сообщаем
        drop = {tuple(e.split(":", 1)[0].split("/", 1)) for e in bad if "/" in e.split(":", 1)[0]}
        orders = orders[~orders.apply(lambda r: (r.supplier, r.sku) in drop, axis=1)].reset_index(drop=True)
        warnings.append(f"Не прошли проверку и убраны из списка: {len(drop)} строк ({bad[0]})")
    history = pd.DataFrame({
        "supplier": h["code"].map(ds.items.set_index("code")["supplier"]).map(lambda s: SUPPLIER_OUT.get(s, s)),
        "sku": h["code"], "month": h["month"], "qty": h["raw_qty"], "clean_qty": h["clean_qty"],
        "excluded_qty": h["oneoff_qty"], "restored_qty": h["lost_qty"], "forecast": h["forecast"],
    }) if len(h) else pd.DataFrame(columns=["supplier", "sku", "month", "qty", "clean_qty",
                                            "excluded_qty", "restored_qty", "forecast"])
    return Result(orders, history, params, warnings)


def run_all(params=None, paths=None):
    from service import ProcurementService
    return ProcurementService(paths=paths).calculate(params or Params.from_yaml()).run_id


def agent_tables(ds: Dataset) -> dict[str, pd.DataFrame]:
    """Снимок данных в виде таблиц для SQL-инструмента агента (имена как в спеке)."""
    sup = ds.items.set_index("code")["supplier"].map(lambda s: SUPPLIER_OUT.get(s, s))
    lines = ds.lines.assign(month=ds.lines["date"].dt.to_period("M").dt.to_timestamp())
    monthly = pd.concat([lines.groupby(["code", "month"], as_index=False)["qty"].sum(),
                         ds.monthly_2024[["code", "month", "qty"]]], ignore_index=True)
    return {
        "sku_master": pd.DataFrame({"supplier": ds.items["code"].map(sup), "sku": ds.items["code"],
                                    "name": ds.items["name"], "article": ds.items["article"],
                                    "unit": ds.items["unit"], "moq": ds.items["moq"], "group": ds.items["group"],
                                    "category": ds.items["category"], "unit_cost": ds.items["unit_cost"],
                                    "flags": ""}),
        "sales_monthly": pd.DataFrame({"supplier": monthly["code"].map(sup), "sku": monthly["code"],
                                       "month": monthly["month"], "qty_raw": monthly["qty"],
                                       "is_partial": monthly["month"] >= ds.as_of.to_period("M").to_timestamp()}),
        "stock_monthly": pd.DataFrame({"supplier": ds.stock["code"].map(sup), "sku": ds.stock["code"],
                                       "month": ds.stock["month"], "opening": ds.stock["stock"]}),
        "transit": pd.DataFrame({"supplier": ds.transit["code"].map(sup), "sku": ds.transit["code"],
                                 "order_id": ds.transit["doc"], "qty": ds.transit["qty"],
                                 "eta": ds.transit["arrival_date"]}),
    }
