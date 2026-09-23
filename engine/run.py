"""One deterministic batch over all SKUs. No API calls or LLM in this module."""
from dataclasses import dataclass, field
from datetime import timedelta

import pandas as pd

from engine.anomalies import clean_series
from engine.explain import explain
from engine.forecast import forecast
from engine.loader import TABLES, Dataset, load
from engine.models import OrderLine, Params
from engine.prepare import prepare_series
from engine.replenish import replenish
from engine.validate import summarize, validate_orders


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


def compute(ds: Dataset, params: Params | None = None, rules=None):
    params = params or Params.from_yaml()
    if params.as_of != ds.as_of:
        raise ValueError("Дата расчёта должна совпадать с датой снимка данных; загрузите данные заново")
    rules = rules or {}
    master = ds.sku_master
    if params.suppliers is not None:
        master = master[master.supplier.isin(params.suppliers)]
    if params.groups is not None:
        master = master[master.group.isin(params.groups)]
    grouped = {name: {key: part for key, part in getattr(ds, name).groupby(["supplier", "sku"], sort=False)}
               for name in ["sales_monthly", "sales_lines", "stock_monthly", "transit"]}
    stock_map = ds.stock_current.set_index(["supplier", "sku"]).free_stock.to_dict()
    seasons = {s: dict(zip(p.month_num, p.coef)) for s, p in ds.seasonality.groupby("supplier")}
    rows, histories = [], []
    for item in master.to_dict("records"):
        supplier, sku = item['supplier'], item['sku']
        key = (supplier, sku)
        def table(name):
            return grouped[name].get(key, pd.DataFrame(columns=TABLES[name]))
        season = seasons[supplier]
        h = prepare_series(table("sales_monthly"), table("stock_monthly"), season, params)
        h, events, restored = clean_series(h, table("sales_lines"), params)
        full = h[~h.is_partial]
        no_demand = full.qty.tail(12).sum() == 0
        lead = params.lead_time_days[supplier]
        horizon = lead + params.review_days
        level, trend, sigma, fcst, mean_season, months = forecast(h, season, params, horizon)
        if no_demand:
            level, fcst, sigma = 0.0, 0.0, 0.0
            for month in months:
                month["forecast"] = month["horizon_qty"] = 0.0
        transit = table("transit")
        in_horizon = pd.to_datetime(transit.eta) <= pd.Timestamp(params.as_of + timedelta(days=horizon))
        now, later = float(transit.loc[in_horizon, "qty"].sum()), float(transit.loc[~in_horizon, "qty"].sum())
        if key not in stock_map:
            raise ValueError(f"Нет текущего остатка для {supplier}/{sku}")
        r = {k: item[k] for k in ["supplier", "sku", "name", "article", "unit", "group"]}
        r.update(replenish(fcst, sigma, mean_season, float(stock_map[key]), now, later, int(item['moq']), lead, params, no_demand))
        flags = list(item['flags'])
        codes = flags + [e['type'] for e in events]
        if restored:
            codes.append("STOCKOUT_RESTORED")
        if h.is_partial.any():
            codes.append("PARTIAL_MONTH")
        if no_demand:
            codes.append("NO_DEMAND")
        elif (full.qty > 0).sum() < 3:
            codes.append("SHORT_HISTORY")
        if max(m['season'] for m in months) >= 1.2:
            codes.append("SEASON_PEAK")
        if trend > 1:
            codes.append("TREND_UP")
        elif trend < 1:
            codes.append("TREND_DOWN")
        if r['recommended_qty'] > max(r['need'], 0) + 1e-8:
            codes.append("MOQ_ROUNDED")
        if r['need'] > 0 and r['recommended_qty'] > 1.5 * r['need']:
            codes.append("MOQ_OVERSHOOT")
        rule = rules.get(key)
        if rule is not None and not no_demand:
            r['recommended_qty'] = max(r['recommended_qty'], ((int(rule) + r['moq'] - 1) // r['moq']) * r['moq'])
            codes.append("MANAGER_RULE")
            if r['recommended_qty'] and r['urgency'] == 'OK':
                r['urgency'] = 'PLANNED'
        max_raw = float(full.qty.max())
        if r['recommended_qty'] > max(3 * max_raw * horizon / 30, r['moq']):
            codes.append("SANITY_HIGH")
        r.update(base_monthly=level, trend=trend, max_monthly_raw=max_raw,
                 reason_codes=list(dict.fromkeys(codes)), flags=list(dict.fromkeys(flags + [c for c in codes if c in {"SANITY_HIGH", "MOQ_OVERSHOOT", "SHORT_HISTORY"}])),
                 excluded_events=events, restored_events=restored, forecast_months=months)
        r['reason_text'] = explain(r, params)
        if "MANAGER_RULE" in codes:
            r['reason_text'] += f" Правило менеджера: минимум {rule} {r['unit']} при наличии спроса."
        rows.append(OrderLine.model_validate(r).model_dump())
        h = h.reset_index()
        h['supplier'], h['sku'] = supplier, sku
        h['forecast'] = None
        future = pd.DataFrame([{'supplier': supplier, 'sku': sku, 'month': pd.Timestamp(m['month']), 'forecast': m['forecast']} for m in months])
        histories.extend([h, future])
    orders = pd.DataFrame(rows, columns=OrderLine.model_fields)
    errors = validate_orders(orders)
    if errors:
        raise ValueError("; ".join(errors))
    history = pd.concat(histories, ignore_index=True) if histories else pd.DataFrame(columns=['supplier', 'sku', 'month'])
    return Result(orders, history, params, ds.warnings + ["Сроки поставки, период заказа и уровень сервиса — допущения из параметров."])


def run_all(params=None, paths=None):
    from service import ProcurementService
    params = params or Params.from_yaml()
    return ProcurementService(paths=paths).calculate(params).run_id
