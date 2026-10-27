"""Modelos de respuesta. Los importes son ``Decimal`` (se serializan como texto decimal)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class Page[T](BaseModel):
    items: list[T]
    limit: int
    offset: int
    total: int


class Health(BaseModel):
    status: str
    database: str | None = None


class KpiSummary(BaseModel):
    date_from: date | None
    date_to: date | None
    revenue: Decimal
    orders: int
    units: int
    average_order_value: Decimal | None
    active_customers: int
    cancelled_or_returned_orders: int


class DailySales(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_date: date
    category: str
    orders: int
    units: int
    revenue: Decimal
    cancelled_or_returned_orders: int


class CategorySales(BaseModel):
    category: str
    orders: int
    units: int
    revenue: Decimal


class TopProduct(BaseModel):
    product_id: int
    title: str | None
    category: str | None
    units: int
    revenue: Decimal


class TaskRun(BaseModel):
    task_name: str
    status: str
    attempt: int
    started_at: datetime
    finished_at: datetime | None
    duration_s: float | None
    rows_in: int | None
    rows_out: int | None
    error_message: str | None


class PipelineRun(BaseModel):
    run_id: uuid.UUID
    flow_name: str
    source_name: str | None
    status: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    duration_s: float | None
    rows_read: int
    rows_loaded: int
    rows_rejected: int
    error_type: str | None
    error_message: str | None


class PipelineRunDetail(PipelineRun):
    orchestrator_run_id: uuid.UUID | None
    params: dict[str, Any]
    tasks: list[TaskRun]
    quality_failures: int


class QualitySummary(BaseModel):
    layer: str
    table_name: str
    rule_name: str
    severity: str
    checks: int
    rows_evaluated: int
    rows_failed: int
    last_checked_at: datetime
    last_passed: bool


class QualityResult(BaseModel):
    id: int
    run_id: uuid.UUID
    batch_id: uuid.UUID | None
    layer: str
    table_name: str
    rule_name: str
    severity: str
    rows_evaluated: int
    rows_failed: int
    passed: bool
    details: dict[str, Any]
    checked_at: datetime


class Batch(BaseModel):
    batch_id: uuid.UUID
    source_name: str
    source_ref: str
    content_sha256: str
    status: str
    row_count: int
    rejected_count: int
    run_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class DailyVolume(BaseModel):
    day: date
    flow_name: str
    runs: int
    failed_runs: int
    rows_read: int
    rows_loaded: int
    rows_rejected: int
    avg_duration_s: float | None
    max_duration_s: float | None


class TriggerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_refresh: bool = False


class TriggerResponse(BaseModel):
    deployment: str
    flow_run_id: uuid.UUID
    state: str
