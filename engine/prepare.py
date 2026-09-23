"""A complete calendar, with incomplete months explicitly excluded from fitting."""
import numpy as np
import pandas as pd


def prepare_series(monthly, stock, season, params):
    cutoff = pd.Timestamp(params.as_of)
    current = cutoff.to_period("M")
    last_complete = current if cutoff.is_month_end else current - 1
    months = pd.period_range(last_complete - params.history_months + 1, current, freq="M").to_timestamp()
    h = pd.DataFrame(index=pd.DatetimeIndex(months, name="month"))
    h["qty_raw"] = monthly.set_index("month").qty_raw.reindex(months, fill_value=0)
    h["qty"] = h.qty_raw.clip(lower=0)
    h["season"] = [float(season[m.month]) for m in months]
    h["is_partial"] = (h.index.to_period("M") == current) & (not cutoff.is_month_end)
    h["display_qty"] = np.where(h.is_partial, h.qty * cutoff.days_in_month / cutoff.day, h.qty)
    s = stock.set_index("month")
    h["opening"] = s.opening.reindex(months)
    h["closing"] = s.closing.reindex(months)
    return h
