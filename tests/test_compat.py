from engine.compat import Params, compute

PLAN_ORDER_COLUMNS = {"code", "article", "name", "unit", "group", "group_name", "supplier", "base_month",
                      "forecast_need", "safety_stock", "stock_now", "in_transit", "moq", "order_qty",
                      "days_cover", "lead_days", "urgency", "reason", "n_oneoff", "lost_qty"}


def test_legacy_ui_contract(dataset):
    res = compute(dataset, Params())
    assert PLAN_ORDER_COLUMNS <= set(res.orders.columns)
    assert {"code", "month", "raw_qty", "oneoff_qty", "lost_qty", "clean_qty", "forecast"} <= set(res.history.columns)
    assert set(res.orders.urgency) <= {"критично", "высокая", "плановая", "не нужно"}
    assert list(res.oneoffs.columns)[:5] == ["date", "doc", "code", "qty", "threshold"]
