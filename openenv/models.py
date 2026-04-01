from enum import Enum

from pydantic import BaseModel, Field


class ServiceStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    DOWN = "down"


VALID_ACTIONS = [
    "restart_service:payment",
    "restart_service:auth",
    "restart_service:database",
    "restart_service:order",
    "restart_service:frontend",
    "scale_up:database",
    "scale_up:payment",
    "rollback_deploy:payment",
    "rollback_deploy:auth",
    "enable_circuit_breaker:payment",
    "enable_circuit_breaker:order",
    "check_logs:payment",
    "check_logs:database",
    "check_logs:auth",
    "page_senior_engineer",
    "do_nothing",
]


class Alert(BaseModel):
    name: str
    severity: str
    service: str
    message: str
    is_red_herring: bool = Field(default=False, exclude=True)


class Observation(BaseModel):
    step: int
    elapsed_seconds: int
    services: dict[str, ServiceStatus]
    alerts: list[Alert]
    metrics: dict[str, float]
    action_history: list[str]
    sla_breached: bool
    hint: str | None = None


class Action(BaseModel):
    command: str


class Reward(BaseModel):
    immediate: float
    cumulative: float
    final: float | None = None
    breakdown: dict[str, float]
