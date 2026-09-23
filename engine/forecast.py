from calendar import monthrange
from datetime import timedelta

import numpy as np
import pandas as pd


def forecast(history, season, params, horizon):
    values = (history.loc[~history.is_partial, "clean_qty"] / history.loc[~history.is_partial, "season"]).to_numpy()
    recent = values[-6:]
    level = float(np.average(recent, weights=np.arange(1, len(recent) + 1))) if len(recent) else 0.0
    trend = 1.0
    if len(values) >= 12 and values[-12:-6].mean() > 0:
        previous = values[-12:-6].mean()
        q1, q2 = values[-6:-3].mean(), values[-3:].mean()
        if (q1 - previous) * (q2 - previous) > 0:
            trend = float(np.clip(recent.mean() / previous, *params.trend_clip))
    sigma = float(np.std(values)) if len(values) else 0.0
    # The stock snapshot includes as_of; forecast exactly the following H calendar days.
    start = params.as_of + timedelta(days=1)
    stop = start + timedelta(days=horizon)
    cursor = start
    months = []
    while cursor < stop:
        period = pd.Period(cursor, freq="M")
        next_month = (period + 1).start_time.date()
        days = (min(next_month, stop) - cursor).days
        step = max(1, period.ordinal - pd.Period(params.as_of, freq="M").ordinal + 1)
        coef = float(season[cursor.month])
        monthly = level * (1 + params.growth_pct / 100) * trend ** (step / 12) * coef
        months.append({"month": str(period), "season": coef, "days": days,
                       "forecast": monthly, "horizon_qty": monthly * days / monthrange(cursor.year, cursor.month)[1]})
        cursor = next_month
    total = sum(m["horizon_qty"] for m in months)
    mean_season = sum(m["season"] * m["days"] for m in months) / horizon
    return level, trend, sigma, total, mean_season, months
