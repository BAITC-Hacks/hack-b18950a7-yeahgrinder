"""Synthetic integration checks for the browser/API boundary.

Run: .venv/bin/python -m pytest tests/test_web_data.py -q
No supplier archives, external network calls, or AI credentials are needed.
"""
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pandas as pd
import pytest

import api
from engine.load import DataError
from engine.models import Params
from engine.run import compute
import web_data
from tests.conftest import set_lines


@pytest.fixture
def ui_client(dataset, monkeypatch):
    """Exercise the real adapter and engine using only the shared synthetic data."""
    web_data.payload.cache_clear()
    params = Params(as_of=dataset.as_of.date())
    monkeypatch.setattr(web_data, "load", lambda: dataset)
    monkeypatch.setattr(Params, "from_yaml", classmethod(lambda cls: params))
    # TestClient without a lifespan context avoids the production cache warmer.
    client = TestClient(api.app)
    yield client
    client.close()
    web_data.payload.cache_clear()


def test_ui_data_matches_engine_and_frontend_contract(ui_client, dataset):
    response = ui_client.get("/ui/data")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"meta", "products", "sources"}
    assert body["meta"]["asOf"] == "2026-09-22"
    assert body["meta"]["live"] is True
    assert body["meta"]["growthPct"] == 20
    assert set(body["meta"]["leadText"]) == {"IEK", "Systeme Electric"}
    assert len(body["sources"]) == 4
    assert all({"name", "type", "date", "status", "description"} <= s.keys()
               for s in body["sources"])

    expected = compute(dataset, Params()).orders.set_index("sku")
    grown = compute(dataset, Params(growth_pct=20)).orders.set_index("sku")
    assert {p["id"] for p in body["products"]} == set(expected.index)
    required = {
        "id", "sku", "article", "name", "supplier", "category", "unit",
        "stock_current", "in_transit_in_horizon", "cover_days", "lead_time_days",
        "horizon_days", "forecast_horizon", "safety_stock", "moq", "recommended_qty",
        "urgency", "need", "order_value", "warnings", "quality", "reason_text",
        "excluded_events", "lost_qty", "restored_text", "history", "history_label",
        "growthScenario",
    }
    for product in body["products"]:
        assert required <= product.keys()
        order = expected.loc[product["sku"]]
        assert product["id"] == product["sku"]
        assert product["stock_current"] == round(order.free_stock, 1)
        assert product["in_transit_in_horizon"] == round(order.in_transit_in_horizon, 1)
        assert product["recommended_qty"] == order.recommended_qty
        assert product["forecast_horizon"] == round(order.forecast_horizon, 1)
        assert product["unit"] == order.unit
        assert product["reason_text"] == order.reason_text
        assert product["growthScenario"]["recommended_qty"] == grown.loc[product["sku"], "recommended_qty"]
        assert isinstance(product["recommended_qty"], int)
        assert product["recommended_qty"] % product["moq"] == 0
    # JSON must be safe for browser JSON.parse, including all nested chart values.
    json.dumps(body, allow_nan=False)


def test_history_separates_actual_months_and_future(ui_client):
    history = ui_client.get("/ui/data").json()["products"][0]["history"]
    assert [r["month"] for r in history] == [
        "Мар 26", "Апр 26", "Май 26", "Июн 26", "Июл 26", "Авг 26", "Сен 26", "Окт 26",
    ]
    assert all(r["raw"] == 300 and r["clean"] == 300 for r in history[:6])
    assert all(r["forecast"] is None for r in history[:5])
    assert history[5]["forecast"] == history[5]["clean"]  # continuous forecast line
    assert all(r["raw"] is None and r["clean"] is None for r in history[6:])
    assert all(r["forecast"] is not None and r["forecast"] > 0 for r in history[6:])


def test_growth_query_does_not_replace_baseline_recommendation(ui_client):
    no_growth = ui_client.get("/ui/data", params={"growth_pct": 0}).json()
    growth = ui_client.get("/ui/data", params={"growth_pct": 40}).json()
    assert no_growth["meta"]["growthPct"] == 0
    assert growth["meta"]["growthPct"] == 40
    for base, larger in zip(no_growth["products"], growth["products"]):
        assert base["sku"] == larger["sku"]
        assert base["recommended_qty"] == larger["recommended_qty"]
        assert base["growthScenario"]["recommended_qty"] == base["recommended_qty"]
        assert larger["growthScenario"]["forecast_horizon"] == pytest.approx(
            base["growthScenario"]["forecast_horizon"] * 1.4, abs=0.2)
        assert larger["growthScenario"]["recommended_qty"] > base["growthScenario"]["recommended_qty"]


@pytest.mark.parametrize("growth", [-51, 101, "invalid"])
def test_invalid_growth_is_rejected(ui_client, growth):
    response = ui_client.get("/ui/data", params={"growth_pct": growth})
    assert response.status_code == 422
    assert response.json()["detail"]


def test_missing_prices_and_no_demand_are_null_not_nan(ui_client, dataset):
    dataset.lines = dataset.lines[dataset.lines.code != "001_"]
    dataset.monthly_2024.loc[dataset.monthly_2024.code == "001_", "qty"] = 0.0
    dataset.items.loc[dataset.items.code == "001_", "article"] = None
    dataset.items.loc[dataset.items.code == "001_", "group_name"] = None
    response = ui_client.get("/ui/data")
    assert response.status_code == 200
    body = response.json()
    product = next(p for p in body["products"] if p["sku"] == "001_")
    assert product["article"] == "001_"
    assert product["category"] == "001"
    assert product["recommended_qty"] == 0
    assert product["cover_days"] is None
    assert product["order_value"] is None
    assert body["meta"]["orderValue"] == {}
    assert any("продаж нет" in warning for warning in product["warnings"])
    json.dumps(body, allow_nan=False)


def test_missing_history_does_not_invent_zero_sales():
    history = pd.DataFrame(columns=["month", "qty", "clean_qty", "forecast"])
    rows, label = web_data._history(history, pd.Timestamp("2026-09-22"), None)
    assert len(rows) == 8
    assert all(r["raw"] is None and r["clean"] is None and r["forecast"] is None for r in rows)
    assert "2026" in label


def test_archive_outlier_and_restored_demand_are_serialized(ui_client, dataset):
    dataset.lines = pd.concat([dataset.lines, pd.DataFrame([{
        "date": pd.Timestamp("2025-06-09"), "doc": "SPIKE/001", "code": "001_", "qty": 210000.0,
    }])], ignore_index=True)
    months = pd.date_range("2026-05-01", "2026-08-01", freq="MS")
    set_lines(dataset, "001_", months, 0)
    dataset.stock.loc[(dataset.stock.code == "001_") & dataset.stock.month.isin(months), "stock"] = 0.0
    response = ui_client.get("/ui/data")
    assert response.status_code == 200
    product = next(p for p in response.json()["products"] if p["sku"] == "001_")
    event = next(e for e in product["excluded_events"] if e["doc"] == "SPIKE")
    assert event["date"] == "09.06.2025"
    assert event["qty"] == 210000
    assert event["threshold"] + event["excess"] == event["qty"]
    assert product["quality"] == "Нужна проверка"
    assert product["lost_qty"] > 0
    assert "скорректированный спрос" in product["restored_text"]
    assert product["history_label"].startswith("Архив:")
    assert any(r["month"] == "Июн 25" and r["raw"] > r["clean"] for r in product["history"])
    assert all(r["forecast"] is None for r in product["history"])


def test_data_source_failure_has_readable_422(ui_client, monkeypatch):
    def unavailable():
        raise DataError("Нет тестовой выгрузки продаж")

    monkeypatch.setattr(web_data, "load", unavailable)
    response = ui_client.get("/ui/data")
    assert response.status_code == 422
    assert response.json()["detail"] == "Нет тестовой выгрузки продаж"


def test_home_page_is_served_by_same_api(ui_client):
    response = ui_client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "SupplyAI" in response.text
    assert 'src="./app.js"' in response.text


@pytest.mark.parametrize("asset", ["app.js", "mockDashboard.js", "export.js", "styles.css"])
def test_frontend_assets_are_reachable_with_browser_mime_types(ui_client, asset):
    response = ui_client.get(f"/{asset}")
    assert response.status_code == 200
    mime = response.headers["content-type"].split(";", 1)[0]
    assert mime in ({"text/css"} if asset.endswith(".css") else {"text/javascript", "application/javascript"})
    assert response.content == (Path(__file__).resolve().parents[1] / asset).read_bytes()


@pytest.mark.parametrize("path", ["/api.py", "/config.yaml", "/.env", "/ui/unknown"])
def test_static_serving_does_not_expose_backend_files(ui_client, path):
    assert ui_client.get(path).status_code == 404
