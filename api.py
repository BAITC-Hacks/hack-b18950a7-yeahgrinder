"""HTTP API over the same service Streamlit calls. Run: .venv/bin/uvicorn api:app --reload"""
from functools import lru_cache
from typing import Literal

from pathlib import Path
import threading

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.graph import AgentUnavailable, ask, explain_run
from agent import tracing
from agent.providers import status as ai_status
from engine.load import DataError
from engine.models import Params, Supplier, Urgency
from service import ProcurementService
import web_data

app = FastAPI(title="QadamSupply API", version="1.0")
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def revalidate_web_assets(request, call_next):
    # Без этого браузер держит старые app.js/styles.css после обновления; no-cache = сверка по ETag (304).
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.endswith((".html", ".js", ".css")):
        response.headers["Cache-Control"] = "no-cache"
    return response


WEB = Path(__file__).resolve().parent / "web"


@lru_cache
def service() -> ProcurementService:
    return ProcurementService()


def guarded(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'\"")) from exc
    except (DataError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


class DecisionIn(BaseModel):
    supplier: Supplier
    decision: Literal["approve", "edit", "reject"]
    edits: dict[str, int] = Field(default_factory=dict)
    reason: str = ""


class RuleIn(BaseModel):
    supplier: Supplier
    sku: str
    min_qty: int = Field(ge=0)


class WhatIfIn(BaseModel):
    supplier: Supplier | None = None
    sku: str | None = None
    lead_time_days: int | None = Field(default=None, ge=0, le=365)
    review_days: int | None = Field(default=None, ge=1, le=365)
    service_z: float | None = Field(default=None, ge=0, le=4)
    extra_transit: int | None = Field(default=None, ge=0)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str = Field(default="default", max_length=64)


def records(frame):
    frame = frame.astype(object).where(frame.notna(), None)
    return frame.to_dict("records")


@app.get("/health")
def health():
    return {"status": "ok", "ai": ai_status(), "tracing": {"langfuse": tracing.enabled()}}


@app.post("/runs")
def create_run(params: Params | None = None):
    result = guarded(service().calculate, params)
    return {"run_id": result.run_id, "summary": result.summary, "warnings": result.warnings}


@app.get("/runs")
def list_runs(limit: int = Query(30, ge=1, le=100)):
    return service().memory.list_runs(limit)


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    result = guarded(service().get_result, run_id)
    return {"run_id": run_id, "params": result.params.model_dump(mode="json"),
            "summary": result.summary, "warnings": result.warnings}


@app.get("/runs/{run_id}/orders")
def get_orders(run_id: str, supplier: Supplier | None = None, urgency: Urgency | None = None,
               only_orders: bool = False, limit: int = Query(200, ge=1, le=5000), offset: int = Query(0, ge=0)):
    orders = guarded(service().get_result, run_id).orders
    if supplier:
        orders = orders[orders.supplier == supplier]
    if urgency:
        orders = orders[orders.urgency == urgency]
    if only_orders:
        orders = orders[orders.recommended_qty > 0]
    return {"total": len(orders), "rows": records(orders.iloc[offset:offset + limit])}


@app.get("/runs/{run_id}/orders/{supplier}/{sku}")
def get_order(run_id: str, supplier: Supplier, sku: str):
    result = guarded(service().get_result, run_id)
    row = result.orders[(result.orders.supplier == supplier) & (result.orders.sku == sku)]
    if row.empty:
        raise HTTPException(404, "Позиция не найдена")
    h = result.history[(result.history.supplier == supplier) & (result.history.sku == sku)]
    return {"order": records(row)[0], "history": records(h.assign(month=h.month.astype(str)))}


@app.post("/runs/{run_id}/what-if")
def what_if(run_id: str, body: WhatIfIn):
    return guarded(service().what_if, run_id, **body.model_dump())


@app.post("/runs/{run_id}/decisions")
def decide(run_id: str, body: DecisionIn):
    approval_id = guarded(service().decide, run_id, body.supplier, body.decision, body.edits, body.reason)
    return {"approval_id": approval_id, "decision": body.decision}


@app.get("/approvals/{approval_id}/export")
def export(approval_id: str):
    content = guarded(service().export, approval_id)
    decision = service().memory.get_decision(approval_id)
    name = f"orders_{decision['supplier'].replace(' ', '_')}_{approval_id[:8]}.xlsx"
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/runs/{run_id}/rules")
def save_rule(run_id: str, body: RuleIn):
    guarded(service().save_rule, run_id, body.supplier, body.sku, body.min_qty)
    return {"saved": True}


@app.post("/runs/{run_id}/chat")
def chat(run_id: str, body: ChatIn):
    try:
        return guarded(ask, service(), run_id, body.message, body.thread_id)
    except AgentUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/runs/{run_id}/brief/{supplier}")
def brief(run_id: str, supplier: Supplier):
    try:
        return guarded(explain_run, service(), run_id, supplier)
    except AgentUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


# ---------------------------------------------------------------- веб-интерфейс (web/, QadamSupply)

@app.on_event("startup")
def warm_ui_cache():
    # первый расчёт для интерфейса ~12 с — греем в фоне, чтобы первый заход не ждал
    threading.Thread(target=lambda: guarded(web_data.payload), daemon=True).start()


@app.get("/ui/data")
def ui_data(growth_pct: float = Query(20.0, ge=-50, le=100)):
    """Товары, источники и сводка в форме web/mockDashboard.js — из того же расчёта, что /runs."""
    return guarded(web_data.payload, growth_pct)


if WEB.exists():  # последним: иначе перехватит маршруты API
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
