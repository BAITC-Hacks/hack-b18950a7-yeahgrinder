from math import ceil, sqrt


def replenish(forecast, sigma, mean_season, stock, transit, later, moq, lead, params, no_demand):
    horizon = lead + params.review_days
    safety = params.service_z * sigma * mean_season * sqrt(horizon / 30)
    daily = forecast / horizon
    position = stock + transit
    need = forecast + safety - position
    qty = int(ceil(max(0, need) / moq - 1e-10) * moq) if need > 0 else 0
    if no_demand:
        qty, forecast, safety, need, daily = 0, 0.0, 0.0, -position, 0.0
    cover = stock / daily if daily > 0 else None
    if daily > 0 and (stock == 0 or position / daily < lead):
        urgency = "CRITICAL"
    elif daily > 0 and position / daily < lead + params.review_days / 2:
        urgency = "HIGH"
    else:
        urgency = "PLANNED" if qty else "OK"
    return dict(free_stock=stock, in_transit_in_horizon=transit, in_transit_later=later,
                forecast_horizon=forecast, safety_stock=safety, need=need, moq=moq,
                recommended_qty=qty, urgency=urgency, cover_days=cover,
                horizon_days=horizon, lead_time_days=lead)
