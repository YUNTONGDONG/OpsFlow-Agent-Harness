"""Common contract implemented by every business skill."""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Generic, Mapping, TypeVar

from pydantic import BaseModel, Field


SkillInput = TypeVar("SkillInput", bound=BaseModel)


class SkillResult(BaseModel):
    """Normalized result returned by a skill."""

    success: bool
    data: Any | None = None
    error_code: str | None = None
    message: str | None = None
    trace_attributes: dict[str, Any] = Field(default_factory=dict)


class SkillOperationType(str, Enum):
    READ = "read"
    IDEMPOTENT_WRITE = "idempotent_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"
    HUMAN_CONFIRMATION_REQUIRED = "human_confirmation_required"
    MIXED = "mixed"


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=1, ge=1)
    backoff_seconds: float = Field(default=0, ge=0)


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class VerificationResult(BaseModel):
    status: VerificationStatus
    external_resource_id: str | None = None
    data: Any | None = None


class BaseSkill(ABC, Generic[SkillInput]):
    """Metadata, input validation, and execution contract for a skill."""

    name: str
    description: str
    input_schema: type[SkillInput]
    output_schema: type[BaseModel] | None = None
    operation_type: SkillOperationType = SkillOperationType.READ
    retry_policy: RetryPolicy = RetryPolicy()
    timeout_seconds: float = 30
    concurrency_limit: int = 1

    def validate_input(self, arguments: Mapping[str, Any]) -> SkillInput:
        """Validate raw model/tool arguments against the skill schema."""

        return self.input_schema.model_validate(arguments)

    def validate_output(self, output: Any) -> Any:
        if self.output_schema is None:
            return output
        return self.output_schema.model_validate(output).model_dump(mode="json")

    def operation_type_for(
        self,
        arguments: Mapping[str, Any],
    ) -> SkillOperationType:
        return self.operation_type

    def verify(
        self,
        arguments: SkillInput,
        output: Any,
    ) -> VerificationResult:
        if self.operation_type_for(arguments.model_dump(mode="python")) == SkillOperationType.READ:
            return VerificationResult(status=VerificationStatus.VERIFIED)
        return VerificationResult(status=VerificationStatus.UNKNOWN)

    @abstractmethod
    def execute(self, arguments: SkillInput) -> SkillResult:
        """Execute the skill with already validated arguments."""

