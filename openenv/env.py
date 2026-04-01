import json
import random
from copy import deepcopy
from pathlib import Path

from openenv.models import Action, Alert, Observation, Reward, ServiceStatus, VALID_ACTIONS


STATUS_ORDER = [
    ServiceStatus.DOWN,
    ServiceStatus.CRITICAL,
    ServiceStatus.DEGRADED,
    ServiceStatus.OK,
]


class SREIncidentEnv:
    def __init__(self) -> None:
        self.scenario: dict = {}
        self.current_obs: Observation | None = None
        self.step_count: int = 0
        self.elapsed_seconds: int = 0
        self.cumulative_reward: float = 0.0
        self.actions_taken: list[str] = []
        self.resolved: bool = False
        self.sla_breached: bool = False
        self.wrong_action_count: int = 0
        self.applied_correct_actions: set[str] = set()
        self._sla_penalty_applied: bool = False

    def reset(self, task: str = "easy", scenario_id: str | None = None) -> Observation:
        scenarios = self._load_scenarios()

        if scenario_id is not None:
            matches = [item for item in scenarios if item["id"] == scenario_id]
        else:
            matches = [item for item in scenarios if item["task"] == task]

        if not matches:
            raise ValueError("No matching scenario found")

        if scenario_id is None:
            ordered = sorted(matches, key=lambda item: item["id"])
            selector_seed = sum(ord(char) for char in task) + sum(
                item["seed"] for item in ordered
            )
            selector = random.Random(selector_seed)
            scenario = selector.choice(ordered)
        else:
            scenario = matches[0]

        random.seed(scenario["seed"])
        self.scenario = deepcopy(scenario)
        self.step_count = 0
        self.elapsed_seconds = 0
        self.cumulative_reward = 0.0
        self.actions_taken = []
        self.resolved = False
        self.sla_breached = False
        self.wrong_action_count = 0
        self.applied_correct_actions = set()
        self._sla_penalty_applied = False

        services = {
            name: ServiceStatus(status)
            for name, status in self.scenario["initial_services"].items()
        }
        self.current_obs = Observation(
            step=0,
            elapsed_seconds=0,
            services=services,
            alerts=self._build_alerts(services),
            metrics=self._build_metrics(services),
            action_history=[],
            sla_breached=False,
            hint=self.scenario.get("hint") if self.scenario["task"] == "easy" else None,
        )
        return self.current_obs

    def step(self, action: Action) -> tuple[Observation, Reward, bool, dict]:
        if self.current_obs is None:
            raise ValueError("Environment must be reset before stepping")

        if action.command not in VALID_ACTIONS:
            self.cumulative_reward += -5.0
            reward = Reward(
                immediate=-5.0,
                cumulative=self.cumulative_reward,
                final=None,
                breakdown={"invalid_action": -5.0},
            )
            info = {
                "task_score": self._compute_task_score(),
                "resolved": self.resolved,
                "wrong_actions": self.wrong_action_count,
                "elapsed_seconds": self.elapsed_seconds,
            }
            return self.current_obs, reward, False, info

        breakdown: dict[str, float] = {}
        immediate = 0.0
        final_value: float | None = None
        projected_elapsed = self.elapsed_seconds + 30

        self.actions_taken.append(action.command)

        if action.command == "page_senior_engineer":
            breakdown["senior_engineer_used"] = 0.0

        if action.command in self.scenario["correct_actions"] and self._is_action_effective(
            action.command
        ):
            immediate += 10.0
            breakdown["correct_action"] = 10.0
            services = self._apply_action_effects(action.command, self.current_obs.services)
            self.applied_correct_actions.add(action.command)
        elif action.command in self.scenario["correct_actions"]:
            immediate += -20.0
            breakdown["redundant_action"] = -20.0
            self.wrong_action_count += 1
            services = dict(self.current_obs.services)
        else:
            immediate += -20.0
            breakdown["wrong_action"] = -20.0
            self.wrong_action_count += 1
            services = dict(self.current_obs.services)

        alerts = self._build_alerts(services)
        active_p0_alerts = sum(
            1 for alert in alerts if alert.severity == "P0" and not alert.is_red_herring
        )
        if active_p0_alerts:
            downtime_penalty = -1.0 * active_p0_alerts
            immediate += downtime_penalty
            breakdown["downtime_penalty"] = downtime_penalty

        if self._resolution_met(services):
            self.resolved = True
            final_value = 100.0
            breakdown["resolution_bonus"] = 100.0
            if projected_elapsed <= self.scenario["sla_threshold_seconds"]:
                final_value += 50.0
                breakdown["sla_bonus"] = 50.0
            if self.wrong_action_count:
                wrong_penalty = -20.0 * self.wrong_action_count
                final_value += wrong_penalty
                breakdown["wrong_action_total_penalty"] = wrong_penalty
            if "page_senior_engineer" in self.actions_taken:
                final_value += -30.0
                breakdown["senior_engineer_penalty"] = -30.0

        if (
            projected_elapsed > self.scenario["sla_threshold_seconds"]
            and not self._sla_penalty_applied
        ):
            self.sla_breached = True
            self._sla_penalty_applied = True
            immediate += -50.0
            breakdown["sla_breach_penalty"] = -50.0

        self.elapsed_seconds = projected_elapsed
        self.step_count += 1

        metrics = self._build_metrics(services)
        self.current_obs = Observation(
            step=self.step_count,
            elapsed_seconds=self.elapsed_seconds,
            services=services,
            alerts=alerts,
            metrics=metrics,
            action_history=list(self.actions_taken),
            sla_breached=self.sla_breached,
            hint=self.scenario.get("hint") if self.scenario["task"] == "easy" else None,
        )

        done = self.resolved
        if self.step_count >= 20 and not done:
            done = True
            final_value = -50.0
            breakdown["max_steps_penalty"] = -50.0

        total_step_reward = immediate + (final_value or 0.0)
        self.cumulative_reward += total_step_reward

        reward = Reward(
            immediate=immediate,
            cumulative=self.cumulative_reward,
            final=final_value,
            breakdown=breakdown,
        )
        info = {
            "task_score": self._compute_task_score(),
            "resolved": self.resolved,
            "wrong_actions": self.wrong_action_count,
            "elapsed_seconds": self.elapsed_seconds,
        }
        return self.current_obs, reward, done, info

    def state(self) -> dict:
        return {
            "scenario_id": self.scenario.get("id"),
            "step": self.step_count,
            "elapsed_seconds": self.elapsed_seconds,
            "resolved": self.resolved,
            "sla_breached": self.sla_breached,
            "cumulative_reward": self.cumulative_reward,
            "wrong_action_count": self.wrong_action_count,
            "actions_taken": list(self.actions_taken),
            "applied_correct_actions": sorted(self.applied_correct_actions),
            "current_observation": self.current_obs.model_dump(mode="json")
            if self.current_obs is not None
            else None,
        }

    def _compute_task_score(self) -> float:
        if not self.scenario:
            return 0.0

        task = self.scenario["task"]
        threshold = self.scenario["sla_threshold_seconds"]

        if task == "easy":
            if self.resolved and self.elapsed_seconds <= threshold:
                return 1.0
            if self.resolved:
                return 0.5
            return 0.0

        if task == "medium":
            if (
                self.resolved
                and self.elapsed_seconds <= threshold
                and self.wrong_action_count <= 1
            ):
                return 1.0
            if self.resolved:
                return 0.5
            return 0.0

        if (
            self.resolved
            and self.elapsed_seconds <= threshold
            and self.wrong_action_count == 0
        ):
            return 1.0
        if self.resolved and self.wrong_action_count > 0:
            return 0.3
        return 0.0

    def _load_scenarios(self) -> list[dict]:
        scenario_path = Path(__file__).resolve().parent.parent / "data" / "scenarios.json"
        with scenario_path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _build_alerts(self, services: dict[str, ServiceStatus]) -> list[Alert]:
        alerts: list[Alert] = []
        for raw_alert in self.scenario["initial_alerts"]:
            alert = Alert(**raw_alert)
            service_status = services.get(alert.service, ServiceStatus.OK)
            if alert.is_red_herring or service_status != ServiceStatus.OK:
                alerts.append(alert)
        return alerts

    def _build_metrics(self, services: dict[str, ServiceStatus]) -> dict[str, float]:
        metrics = {
            key: float(value)
            for key, value in self.scenario["initial_metrics"].items()
        }

        if "payment_error_rate" in metrics:
            metrics["payment_error_rate"] = self._status_value(
                services["payment"], 0.01, 0.22, 0.76, 0.98
            )
        if "payment_latency_p99" in metrics:
            metrics["payment_latency_p99"] = self._status_value(
                services["payment"], 0.4, 1.4, 7.8, 12.5
            )
        if "database_cpu_pct" in metrics:
            metrics["database_cpu_pct"] = self._status_value(
                services["database"], 38.0, 68.0, 92.0, 99.0
            )
        if "auth_latency_p99" in metrics:
            metrics["auth_latency_p99"] = self._status_value(
                services["auth"], 0.2, 0.8, 2.8, 6.4
            )
        if "database_conn_pool_pct" in metrics:
            metrics["database_conn_pool_pct"] = self._status_value(
                services["database"], 42.0, 84.0, 97.0, 100.0
            )
        if "order_latency_p99" in metrics:
            metrics["order_latency_p99"] = self._status_value(
                services["order"], 0.3, 1.2, 4.1, 8.2
            )
        if "frontend_error_rate" in metrics:
            metrics["frontend_error_rate"] = self._status_value(
                services["frontend"], 0.01, 0.08, 0.31, 0.72
            )
        if self.scenario.get("id") == "scenario_005" and "frontend_error_rate" in metrics:
            metrics["frontend_error_rate"] = 0.0

        return metrics

    def _status_value(
        self,
        status: ServiceStatus,
        ok: float,
        degraded: float,
        critical: float,
        down: float,
    ) -> float:
        if status == ServiceStatus.OK:
            return ok
        if status == ServiceStatus.DEGRADED:
            return degraded
        if status == ServiceStatus.CRITICAL:
            return critical
        return down

    def _apply_action_effects(
        self, command: str, services: dict[str, ServiceStatus]
    ) -> dict[str, ServiceStatus]:
        updated = dict(services)
        for service_name in self._action_service_map().get(command, []):
            updated[service_name] = self._improve_status(updated[service_name])

        return updated

    def _action_service_map(self) -> dict[str, list[str]]:
        scenario_id = self.scenario["id"]
        if scenario_id == "scenario_001":
            return {
                "check_logs:payment": ["payment"],
                "rollback_deploy:payment": ["payment"],
            }
        if scenario_id == "scenario_002":
            return {
                "scale_up:database": ["database"],
            }
        if scenario_id == "scenario_003":
            return {
                "check_logs:database": ["database", "payment"],
                "scale_up:database": ["database", "payment"],
            }
        if scenario_id == "scenario_004":
            return {
                "check_logs:auth": ["auth", "payment", "order"],
                "rollback_deploy:auth": ["auth", "payment", "order"],
            }
        if scenario_id == "scenario_005":
            return {
                "check_logs:database": ["database", "payment", "order"],
                "scale_up:database": ["database", "payment", "order"],
                "enable_circuit_breaker:payment": ["payment"],
            }
        return {}

    def _is_action_effective(self, command: str) -> bool:
        return command not in self.applied_correct_actions

    def _improve_status(self, status: ServiceStatus) -> ServiceStatus:
        current_index = STATUS_ORDER.index(status)
        improved_index = min(current_index + 1, len(STATUS_ORDER) - 1)
        return STATUS_ORDER[improved_index]

    def _resolution_met(self, services: dict[str, ServiceStatus]) -> bool:
        for service_name, expected in self.scenario["resolution_condition"].items():
            if services.get(service_name) != ServiceStatus(expected):
                return False
        return True
