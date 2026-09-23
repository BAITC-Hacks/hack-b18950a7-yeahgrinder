"""Validated public contracts shared by the engine, UI and API."""
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
Supplier = Literal["IEK", "Systeme Electric"]
Urgency = Literal["CRITICAL", "HIGH", "PLANNED", "OK"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class OutlierConfig(Model):
    k_mad: float = Field(default=3.5, gt=0, le=20)
    min_ratio: float = Field(default=2, gt=1)
    min_abs: float = Field(default=20, ge=0)
    line_share: float = Field(default=0.5, gt=0, le=1)


class StockoutConfig(Model):
    low_stock_ratio: float = Field(default=0.25, ge=0, le=1)
    sales_drop_ratio: float = Field(default=0.5, gt=0, le=1)


class Params(Model):
    as_of: date = date(2026, 9, 22)
    history_months: int = Field(default=18, ge=12, le=60)
    review_days: int = Field(default=30, ge=1, le=365)
    # None — срок из данных (ИЭК: даты заказов в пути, SE: допущение 45 дн.); число — переопределение
    lead_time_days: dict[Supplier, int | None] = Field(default_factory=lambda: {"IEK": None, "Systeme Electric": None})
    # None — уровень сервиса по категории товара (1/A 98%, 2/B/5 95%, 3/C 90%); число — z для всех
    service_z: float | None = Field(default=None, ge=0, le=4)
    growth_pct: float = Field(default=0, ge=-50, le=100)
    outlier: OutlierConfig = Field(default_factory=OutlierConfig)
    stockout: StockoutConfig = Field(default_factory=StockoutConfig)
    trend_clip: tuple[float, float] = (0.7, 1.5)
    suppliers: list[Supplier] | None = None
    groups: list[str] | None = None
    restore_stockouts: bool = True
    clean_outliers: bool = True

    @model_validator(mode="after")
    def valid_ranges(self):
        if set(self.lead_time_days) != {"IEK", "Systeme Electric"}:
            raise ValueError("Нужны сроки поставки для обоих поставщиков")
        if any(v is not None and (isinstance(v, bool) or not 0 <= v <= 365) for v in self.lead_time_days.values()):
            raise ValueError("Срок поставки должен быть от 0 до 365 дней")
        if not 0 < self.trend_clip[0] <= 1 <= self.trend_clip[1] <= 3:
            raise ValueError("Некорректные границы тренда")
        return self

    @classmethod
    def from_yaml(cls, path: Path = ROOT / "config.yaml"):
        config = yaml.safe_load(path.read_text())
        config.pop("ai", None)
        return cls.model_validate(config)


class OrderLine(Model):
    supplier: Supplier
    sku: str
    article: str | None = None
    name: str
    unit: str
    group: str
    free_stock: float = Field(ge=0)
    in_transit_in_horizon: float = Field(ge=0)
    in_transit_later: float = Field(ge=0)
    base_monthly: float = Field(ge=0)
    trend: float = Field(gt=0)
    forecast_horizon: float = Field(ge=0)
    safety_stock: float = Field(ge=0)
    need: float
    moq: int = Field(ge=1)
    recommended_qty: int = Field(ge=0)
    urgency: Urgency
    cover_days: float | None
    horizon_days: int = Field(gt=0)
    lead_time_days: int = Field(ge=0)
    max_monthly_raw: float = Field(ge=0)
    reason_codes: list[str] = Field(default_factory=list)
    reason_text: str = Field(min_length=1)
    excluded_events: list[dict] = Field(default_factory=list)
    restored_events: list[dict] = Field(default_factory=list)
    forecast_months: list[dict] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def multiple(self):
        if self.recommended_qty % self.moq:
            raise ValueError("Количество должно быть кратно MOQ")
        return self
