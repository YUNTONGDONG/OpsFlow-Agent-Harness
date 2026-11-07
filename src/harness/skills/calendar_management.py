"""Calendar management skill backed by the existing Calendar tools."""

from collections.abc import Callable
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from ..skill import (
    BaseSkill,
    RetryPolicy,
    SkillOperationType,
    SkillResult,
    VerificationResult,
)


class CalendarAction(str, Enum):
    CHECK_AVAILABILITY = "check_availability"
    SEARCH = "search"
    CREATE = "create"
    MODIFY = "modify"
    REQUEST_DELETE = "request_delete"
    CONFIRM_DELETE = "confirm_delete"


class CalendarTime(BaseModel):
    date: str | None = None
    date_time: str | None = None
    time_zone: str | None = None


class CalendarManagementInput(BaseModel):
    action: CalendarAction
    summary: str | None = None
    description: str | None = None
    start: CalendarTime | None = None
    end: CalendarTime | None = None
    is_all_day: bool = False
    time_min: str | None = None
    time_max: str | None = None
    event_id: str | None = None

    @model_validator(mode="after")
    def validate_action_fields(self) -> "CalendarManagementInput":
        if self.action == CalendarAction.CHECK_AVAILABILITY:
            self._require("time_min", "time_max")
        elif self.action == CalendarAction.CREATE:
            self._require("summary", "start", "end")
        elif self.action == CalendarAction.MODIFY:
            self._require("summary", "start", "end")
        elif self.action == CalendarAction.REQUEST_DELETE:
            self._require("summary")
        elif self.action == CalendarAction.CONFIRM_DELETE:
            self._require("event_id")
        return self

    def _require(self, *field_names: str) -> None:
        missing = [name for name in field_names if getattr(self, name) is None]
        if missing:
            raise ValueError(
                f"Calendar action {self.action.value} requires: "
                + ", ".join(missing)
            )


class CalendarManagementOutput(BaseModel):
    result: Any


CalendarBackend = Callable[[CalendarManagementInput], Any]


def _verify_existing_calendar_operation(
    arguments: CalendarManagementInput,
    output: Any,
) -> VerificationResult:
    if arguments.action != CalendarAction.CONFIRM_DELETE or not arguments.event_id:
        return VerificationResult(status="unknown")

    from googleapiclient.errors import HttpError
    from src.Tools import GoogleClient

    try:
        GoogleClient().calendar_service.events().get(
            calendarId="primary",
            eventId=arguments.event_id,
        ).execute()
    except HttpError as exc:
        if exc.resp.status in {404, 410}:
            return VerificationResult(
                status="verified",
                external_resource_id=arguments.event_id,
            )
        return VerificationResult(status="unknown")
    except Exception:
        return VerificationResult(status="unknown")
    return VerificationResult(
        status="rejected",
        external_resource_id=arguments.event_id,
    )


def _calendar_payload(arguments: CalendarManagementInput) -> dict[str, Any]:
    start = arguments.start
    end = arguments.end
    return {
        "summary": arguments.summary,
        "description": arguments.description,
        "start": (
            {
                "date": start.date,
                "dateTime": start.date_time,
                "timeZone": start.time_zone,
            }
            if start
            else None
        ),
        "end": (
            {
                "date": end.date,
                "dateTime": end.date_time,
                "timeZone": end.time_zone,
            }
            if end
            else None
        ),
        "isAllDay": arguments.is_all_day,
        "timeMin": arguments.time_min,
        "timeMax": arguments.time_max,
        "eventid": arguments.event_id,
    }


def _call_existing_calendar_tool(arguments: CalendarManagementInput) -> Any:
    from src.Tools import (
        ConfirmDelSchedule,
        DelSchedule,
        ModifySchedule,
        SearchSchedule,
        SetSchedule,
        checkSchedule,
    )

    payload = _calendar_payload(arguments)
    if arguments.action == CalendarAction.CHECK_AVAILABILITY:
        return checkSchedule.invoke(
            {
                "schedule": {
                    "startTime": arguments.time_min,
                    "endTime": arguments.time_max,
                }
            }
        )
    if arguments.action == CalendarAction.SEARCH:
        return SearchSchedule.invoke(
            {
                "search": {
                    "timeMin": arguments.time_min,
                    "timeMax": arguments.time_max,
                }
            }
        )
    if arguments.action == CalendarAction.CREATE:
        return SetSchedule.invoke(
            {
                "sets": {
                    key: payload[key]
                    for key in (
                        "summary",
                        "description",
                        "start",
                        "end",
                        "isAllDay",
                    )
                }
            }
        )
    if arguments.action == CalendarAction.MODIFY:
        return ModifySchedule.invoke(
            {
                "search": {
                    key: payload[key]
                    for key in (
                        "timeMin",
                        "timeMax",
                        "description",
                        "start",
                        "end",
                        "summary",
                    )
                }
            }
        )
    if arguments.action == CalendarAction.REQUEST_DELETE:
        return DelSchedule.invoke(
            {
                "query": {
                    "summary": arguments.summary,
                    "description": arguments.description,
                }
            }
        )
    return ConfirmDelSchedule.invoke(
        {"query": {"eventid": arguments.event_id}}
    )


class CalendarManagementSkill(BaseSkill[CalendarManagementInput]):
    name = "calendar_management"
    description = (
        "Search, create, modify, or safely delete Google Calendar events. "
        "create requires summary, start, and end; modify requires summary, "
        "start, and end; check_availability requires time_min and time_max; "
        "Use request_delete to find an event and request user confirmation; "
        "request_delete requires summary; "
        "use confirm_delete only in a later request after explicit confirmation "
        "provides an event_id. Ask for missing required values instead of "
        "guessing them."
    )
    input_schema = CalendarManagementInput
    output_schema = CalendarManagementOutput
    operation_type = SkillOperationType.MIXED
    retry_policy = RetryPolicy(max_attempts=1)
    timeout_seconds = 20
    concurrency_limit = 1

    def operation_type_for(
        self,
        arguments: dict[str, Any],
    ) -> SkillOperationType:
        action = arguments.get("action")
        if action in {
            CalendarAction.CHECK_AVAILABILITY,
            CalendarAction.CHECK_AVAILABILITY.value,
            CalendarAction.SEARCH,
            CalendarAction.SEARCH.value,
            CalendarAction.REQUEST_DELETE,
            CalendarAction.REQUEST_DELETE.value,
        }:
            return SkillOperationType.READ
        if action in {
            CalendarAction.CONFIRM_DELETE,
            CalendarAction.CONFIRM_DELETE.value,
        }:
            return SkillOperationType.HUMAN_CONFIRMATION_REQUIRED
        return SkillOperationType.NON_IDEMPOTENT_WRITE

    def __init__(
        self,
        backend: CalendarBackend | None = None,
        verify_fn: Callable[
            [CalendarManagementInput, Any], VerificationResult
        ]
        | None = None,
    ) -> None:
        self._backend = backend or _call_existing_calendar_tool
        self._verify_fn = (
            verify_fn
            if verify_fn is not None
            else (
                _verify_existing_calendar_operation
                if backend is None
                else None
            )
        )

    def execute(self, arguments: CalendarManagementInput) -> SkillResult:
        try:
            result = self._backend(arguments)
        except Exception:
            return SkillResult(
                success=False,
                error_code="CALENDAR_OPERATION_FAILED",
                message="The calendar operation could not be completed.",
            )

        if isinstance(result, str) and "失败" in result:
            return SkillResult(
                success=False,
                error_code="CALENDAR_OPERATION_FAILED",
                message="The calendar provider rejected the operation.",
            )
        return SkillResult(success=True, data={"result": result})

    def verify(
        self,
        arguments: CalendarManagementInput,
        output: Any,
    ) -> VerificationResult:
        if self._verify_fn is not None:
            return self._verify_fn(arguments, output)
        return super().verify(arguments, output)
