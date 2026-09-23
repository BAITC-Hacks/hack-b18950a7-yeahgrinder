"""The UI contract from docs/PLAN.md on top of the new engine (both suppliers).

Switching app.py is a two-line import change:
    from engine.compat import Params, compute
    from engine.compat import DataError, load
"""
from dataclasses import dataclass, field
from statistics import NormalDist

import pandas as pd

from engine import run
from engine.loader import DataError, load as load_dataset  # noqa: F401  (DataError is re-exported)
from engine.models import Params as EngineParams

URGENCY_RU = {"CRITICAL": "критично", "HIGH": "высокая", "PLANNED": "плановая", "OK": "не нужно"}
SPEC_K_MAD = 3.5
UI_DEFAULT_K = 6.0


@dataclass
class Params:
    review_days: int = 30
    lead_days: int = 30
    service_level: float = 0.95
    growth_pct: float = 0.0
    oneoff_k: float = UI_DEFAULT_K
    groups: list[str] | None = None


@dataclass
class Result:
    orders: pd.DataFrame
    history: pd.DataFrame
    oneoffs: pd.DataFrame
    warnings: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def load():
    ds = load_dataset()
    items = ds.sku_master.rename(columns={"sku": "code"}).copy()
    first_word = items.name.str.strip().str.split().str[0].fillna("")
    items["group_name"] = items.group.map(first_word.groupby(items.group).agg(lambda x: x.value_counts().index[0]))
    ds.items = items.set_index("code")
    return ds


def engine_params(p: Params) -> EngineParams:
    settings = EngineParams.from_yaml().model_dump()
    settings.update(review_days=p.review_days, growth_pct=p.growth_pct, groups=p.groups or None,
                    service_z=round(NormalDist().inv_cdf(p.service_level), 3),
                    lead_time_days={s: p.lead_days for s in settings["lead_time_days"]})
    # The UI slider is centred on 6; the engine's calibrated MAD multiplier is 3.5.
    settings["outlier"]["k_mad"] = p.oneoff_k * SPEC_K_MAD / UI_DEFAULT_K
    return EngineParams.model_validate(settings)


def compute(ds, p: Params | None = None) -> Result:
    res = run.compute(ds, engine_params(p or Params()))
    o = res.orders
    names = ds.items.group_name if hasattr(ds, "items") else pd.Series(dtype=str)
    orders = pd.DataFrame({
        "code": o.sku, "article": o.article, "name": o.name, "unit": o.unit,
        "group": o.group, "group_name": o.group.map(names), "supplier": o.supplier,
        "base_month": o.base_monthly, "forecast_need": o.forecast_horizon, "safety_stock": o.safety_stock,
        "stock_now": o.free_stock, "in_transit": o.in_transit_in_horizon, "moq": o.moq,
        "order_qty": o.recommended_qty, "days_cover": o.cover_days, "lead_days": o.lead_time_days,
        "urgency": o.urgency.map(URGENCY_RU), "reason": o.reason_text,
        "n_oneoff": o.excluded_events.map(len),
        "lost_qty": o.restored_events.map(lambda events: sum(e["qty"] for e in events)),
    })
    h = res.history
    history = pd.DataFrame({"code": h.sku, "supplier": h.supplier, "month": h.month,
                            "raw_qty": h.get("qty"), "oneoff_qty": h.get("excluded_qty"),
                            "lost_qty": h.get("restored_qty"), "clean_qty": h.get("clean_qty"),
                            "forecast": h.forecast})
    oneoffs = pd.DataFrame([{"date": pd.Timestamp(e["date"]) if e["date"] else pd.Timestamp(e["month"]),
                             "doc": e["doc_no"], "code": sku, "qty": e["invoice_qty"] or e["raw_qty"],
                             "threshold": e["clean_qty"], "type": e["type"]}
                            for sku, events in zip(o.sku, o.excluded_events) for e in events],
                           columns=["date", "doc", "code", "qty", "threshold", "type"])
    lead_note = f"Срок поставки {p.lead_days if p else Params().lead_days} дн. из настройки применён ко всем поставщикам."
    return Result(orders, history, oneoffs, res.warnings + [lead_note])
