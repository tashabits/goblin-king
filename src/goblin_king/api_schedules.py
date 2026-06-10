"""Schedule validation and construction helpers for the API control plane."""

from __future__ import annotations

from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, croniter
from fastapi import HTTPException

from goblin_king.api_models import ScheduleCreateRequest
from goblin_king.contracts import ScheduleRecord, utc_now
from goblin_king.registry import GoblinRegistry, RegistryError
from goblin_king.scheduler import next_run_after


def schedule_from_request(
    registry: GoblinRegistry,
    request: ScheduleCreateRequest,
) -> ScheduleRecord:
    """Validate and convert a schedule create body into a persisted record."""
    try:
        definition = registry.get(request.kind)
    except RegistryError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    validate_cron(request.cron)
    validate_timezone(request.timezone)
    created_at = utc_now()
    provisional = ScheduleRecord(
        id=str(uuid4()),
        kind=definition.kind,
        input=request.input,
        cron=request.cron,
        timezone=request.timezone,
        enabled=request.enabled,
        priority=request.priority,
        created_at=created_at,
        next_run_at=created_at,
        max_retries=request.max_retries,
        timeout_seconds=request.timeout_seconds,
    )
    return provisional.model_copy(
        update={
            "next_run_at": (
                created_at if request.due_now else next_run_after(provisional, created_at)
            )
        }
    )


def validate_cron(value: str) -> None:
    """Raise an HTTP 422 for invalid cron expressions."""
    try:
        croniter(value)
    except (CroniterBadCronError, ValueError) as error:
        raise HTTPException(status_code=422, detail=f"invalid cron expression: {value}") from error


def validate_timezone(value: str) -> None:
    """Raise an HTTP 422 for unknown timezones."""
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise HTTPException(status_code=422, detail=f"unknown timezone: {value}") from error
