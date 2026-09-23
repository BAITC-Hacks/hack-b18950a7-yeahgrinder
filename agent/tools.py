"""Read-only tools for the chat agent. Every number the agent says must come from here."""
import json
from typing import Literal

import duckdb
from langchain_core.tools import tool
import pandas as pd

from agent.security import UnsafeQuery, safe_select, wrap_untrusted
from engine.run import agent_tables

URGENCY_ORDER = {"CRITICAL": 0, "HIGH": 1, "PLANNED": 2, "OK": 3}
SHORT = ["supplier", "sku", "article", "name", "unit", "recommended_qty", "moq", "urgency", "cover_days",
         "free_stock", "in_transit_in_horizon", "base_monthly", "forecast_horizon", "safety_stock"]


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def rounded(record: dict) -> dict:
    out = {}
    for k, v in record.items():
        if isinstance(v, float):
            out[k] = None if v != v else round(v, 1)
        elif k == "name":
            out[k] = wrap_untrusted(v)
        else:
            out[k] = v
    return out


def make_tools(service, run_id: str):
    result = service.get_result(run_id)
    orders = result.orders
    snapshot = agent_tables(service.snapshot(run_id))
    tables = {"orders": orders.drop(columns=["excluded_events", "restored_events", "forecast_months"])
                              .assign(reason_codes=orders.reason_codes.map(", ".join), flags=orders["flags"].map(", ".join)),
              **snapshot}

    def find(sku: str) -> pd.DataFrame:
        hit = orders[orders.sku == sku.strip()]
        if hit.empty:
            hit = orders[orders.article.fillna("").str.lower() == sku.strip().lower()]
        return hit

    @tool
    def get_run_summary(supplier: Literal["IEK", "Systeme Electric"] | None = None) -> str:
        """Сводка расчёта: число позиций к заказу, критичных, суммарные количества по единицам и оговорки."""
        summary = [s for s in result.summary if supplier is None or s["supplier"] == supplier]
        return dump({"run_id": run_id, "as_of": str(result.params.as_of), "summary": summary,
                     "lead_time_days": result.params.lead_time_days, "review_days": result.params.review_days,
                     "service_z": result.params.service_z, "warnings": result.warnings})

    @tool
    def list_orders(supplier: Literal["IEK", "Systeme Electric"] | None = None,
                    urgency: Literal["CRITICAL", "HIGH", "PLANNED", "OK"] | None = None,
                    limit: int = 20, sort: Literal["qty", "urgency", "cover_days"] = "urgency") -> str:
        """Список рекомендованных позиций с фильтром по поставщику и срочности (не больше 50 строк)."""
        part = orders
        if supplier:
            part = part[part.supplier == supplier]
        if urgency:
            part = part[part.urgency == urgency]
        if sort == "qty":
            part = part.sort_values("recommended_qty", ascending=False)
        elif sort == "cover_days":
            part = part.sort_values("cover_days", na_position="last")
        else:
            part = part.assign(_u=part.urgency.map(URGENCY_ORDER)).sort_values(["_u", "cover_days"], na_position="last")
        limit = max(1, min(int(limit), 50))
        return dump({"total": len(part), "rows": [rounded(r) for r in part[SHORT].head(limit).to_dict("records")]})

    @tool
    def get_sku_explanation(sku: str) -> str:
        """Полное объяснение по позиции: формула, коэффициенты, исключённые разовые продажи, восстановленный спрос, ряд продаж."""
        hit = find(sku)
        if hit.empty:
            return dump({"error": f"Позиция {sku} не найдена в расчёте"})
        row = hit.iloc[0].to_dict()
        h = result.history[(result.history.supplier == row["supplier"]) & (result.history.sku == row["sku"])]
        cols = [c for c in ["month", "qty", "clean_qty", "excluded_qty", "restored_qty", "opening", "forecast"] if c in h]
        series = h[cols].assign(month=pd.to_datetime(h.month).dt.strftime("%Y-%m")).tail(24)
        facts = rounded({k: row[k] for k in SHORT + ["trend", "need", "lead_time_days", "horizon_days", "in_transit_later"]})
        facts.update(reason_codes=list(row["reason_codes"]), reason_text=row["reason_text"],
                     excluded_events=row["excluded_events"], restored_events=row["restored_events"],
                     forecast_months=row["forecast_months"], history=[rounded(r) for r in series.to_dict("records")])
        return dump(facts)

    @tool
    def get_schema() -> str:
        """Таблицы для query_data: имена, колонки и 3 примера строк."""
        return dump({name: {"columns": list(df.columns), "rows": len(df),
                            "sample": [rounded(r) for r in df.head(3).to_dict("records")]} for name, df in tables.items()})

    @tool
    def query_data(sql: str) -> str:
        """Только SELECT (DuckDB) по таблицам orders, sales_monthly, stock_monthly, transit, sku_master; максимум 100 строк."""
        try:
            query = safe_select(sql)
        except UnsafeQuery as exc:
            return dump({"error": str(exc)})
        con = duckdb.connect(config={"enable_external_access": False})
        try:
            for name, df in tables.items():
                con.register(name, df)
            frame = con.execute(query).fetchdf()
        except duckdb.Error as exc:
            return dump({"error": f"Ошибка SQL: {exc}"})
        finally:
            con.close()
        return dump({"rows": [rounded(r) for r in frame.to_dict("records")]})

    @tool
    def what_if(sku: str | None = None, supplier: Literal["IEK", "Systeme Electric"] | None = None,
                lead_time_days: int | None = None, review_days: int | None = None,
                service_z: float | None = None, extra_transit: int | None = None) -> str:
        """Пересчёт «что если» без сохранения: срок поставки, период заказа, уровень сервиса, добавочный транзит. Возвращает дельту заказа."""
        if sku:
            hit = find(sku)
            if hit.empty:
                return dump({"error": f"Позиция {sku} не найдена"})
            sku, supplier = hit.iloc[0].sku, supplier or hit.iloc[0].supplier
        try:
            res = service.what_if(run_id, supplier=supplier, sku=sku, lead_time_days=lead_time_days,
                                  review_days=review_days, service_z=service_z, extra_transit=extra_transit)
        except ValueError as exc:
            return dump({"error": str(exc)})
        changed = [c for c in res["changes"] if c["delta"]]
        return dump({"saved": False, "summary": res["summary"], "changed_count": len(changed),
                     "changes": sorted(changed, key=lambda c: -abs(c["delta"]))[:20]})

    @tool
    def propose_rule(sku: str, min_qty: int, comment: str = "") -> str:
        """Только ПРЕДЛАГАЕТ правило «минимальный заказ» по позиции. Сохраняет его менеджер кнопкой в интерфейсе."""
        hit = find(sku)
        if hit.empty:
            return dump({"error": f"Позиция {sku} не найдена"})
        row = hit.iloc[0]
        return dump({"proposal": {"supplier": row.supplier, "sku": row.sku, "min_qty": int(min_qty),
                                  "moq": int(row.moq), "comment": comment},
                     "saved": False, "note": "Нужно подтверждение менеджера в интерфейсе"})

    return [get_run_summary, list_orders, get_sku_explanation, get_schema, query_data, what_if, propose_rule]
