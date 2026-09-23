from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

import api
from service import ProcurementService


@pytest.fixture
def client(dataset, tmp_path, monkeypatch):
    service = ProcurementService(storage=tmp_path, dataset=dataset)
    monkeypatch.setattr(api, "service", lambda: service)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return TestClient(api.app)


def test_run_approve_export_flow(client):
    assert client.get("/health").json()["status"] == "ok"
    run = client.post("/runs").json()
    run_id = run["run_id"]
    assert len(run["summary"]) == 2
    orders = client.get(f"/runs/{run_id}/orders", params={"supplier": "IEK"}).json()
    assert orders["total"] == 1
    card = client.get(f"/runs/{run_id}/orders/IEK/001_").json()
    assert card["order"]["reason_text"] and card["history"]
    assert client.post(f"/runs/{run_id}/decisions", json={"supplier": "IEK", "decision": "approve",
                                                          "edits": {"001_": 11}}).status_code == 422
    approval = client.post(f"/runs/{run_id}/decisions", json={"supplier": "IEK", "decision": "approve",
                                                              "edits": {"001_": 1000}}).json()["approval_id"]
    xlsx = client.get(f"/approvals/{approval}/export")
    assert xlsx.status_code == 200
    assert load_workbook(BytesIO(xlsx.content))["Заказ"]["E2"].value == 1000
    delta = client.post(f"/runs/{run_id}/what-if", json={"supplier": "IEK", "sku": "001_", "extra_transit": 500}).json()
    assert delta["saved"] is False and delta["changes"][0]["delta"] < 0


def test_errors_are_readable(client):
    assert client.get("/runs/unknown").status_code == 404
    run_id = client.post("/runs").json()["run_id"]
    assert client.post(f"/runs/{run_id}/chat", json={"message": "привет"}).status_code == 503
    rejected = client.post(f"/runs/{run_id}/decisions", json={"supplier": "IEK", "decision": "reject", "reason": "нет"}).json()
    assert client.get(f"/approvals/{rejected['approval_id']}/export").status_code == 422
