import json
import os
import time
from typing import Any

import requests
from openai import OpenAI


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
SYSTEM_PROMPT = """You are an expert SRE engineer responding to a
production incident. Diagnose and resolve the incident as fast as
possible with the fewest actions.

You must respond with EXACTLY one action string from this list and
nothing else — no explanation, no punctuation, just the action string:

restart_service:payment
restart_service:auth
restart_service:database
restart_service:order
restart_service:frontend
scale_up:database
scale_up:payment
rollback_deploy:payment
rollback_deploy:auth
enable_circuit_breaker:payment
enable_circuit_breaker:order
check_logs:payment
check_logs:database
check_logs:auth
page_senior_engineer
do_nothing

Strategy:
- Always check_logs on the most critical service first
- If a deploy happened recently, rollback_deploy is likely the fix
- If connection pool is exhausted, scale_up the database
- Do not restart services before checking logs
- Ignore alerts that seem unrelated to the main failure
- Never page_senior_engineer unless all else fails"""
MAX_STEPS = 20
TASKS = ["easy", "medium", "hard"]


def build_user_message(obs: dict) -> str:
    alerts_str = "\n".join([
        f"  - {a['name']} ({a['severity']}) on {a['service']}: {a['message']}"
        for a in obs.get("alerts", [])
    ])
    metrics_str = "\n".join([
        f"  - {k}: {v}"
        for k, v in obs.get("metrics", {}).items()
    ])
    services_str = "\n".join([
        f"  - {svc}: {status}"
        for svc, status in obs.get("services", {}).items()
    ])
    history_str = (
        "\n".join([f"  - {a}" for a in obs.get("action_history", [])])
        or "  (none yet)"
    )
    hint = obs.get("hint")
    hint_str = f"\nHINT: {hint}" if hint else ""

    return f"""INCIDENT STATUS — Step {obs.get('step', 0)} |
Elapsed: {obs.get('elapsed_seconds', 0)}s |
SLA breached: {obs.get('sla_breached', False)}
{hint_str}

SERVICES:
{services_str}

ACTIVE ALERTS:
{alerts_str}

METRICS:
{metrics_str}

ACTIONS TAKEN SO FAR:
{history_str}

What is your single next action?"""


def log_line(kind: str, **fields: Any) -> None:
    rendered = [f"{key}={json.dumps(value, separators=(',', ':'))}" for key, value in fields.items()]
    print(f"[{kind}] {' '.join(rendered)}", flush=True)


def request_with_retry(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            if method == "post":
                response = requests.post(url, timeout=30, **kwargs)
            elif method == "get":
                response = requests.get(url, timeout=30, **kwargs)
            else:
                raise RuntimeError(f"Unsupported HTTP method: {method}")
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2)
    raise RuntimeError(f"Request failed after 3 attempts: {url}") from last_error


def wait_for_environment(base_url: str, timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            health = request_with_retry("get", f"{base_url}/health")
            if health.get("status") == "ok":
                return
        except Exception as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Environment did not become healthy at {base_url}") from last_error


def main() -> int:
    api_base_url = os.environ.get("API_BASE_URL", "https://api.openai.com/v1")
    openai_api_key = os.environ.get("OPENAI_API_KEY", "")
    model_name = os.environ.get("MODEL_NAME", "gpt-4o-mini")
    env_base_url = os.environ.get("ENV_BASE_URL", "http://127.0.0.1:7860")
    hf_token = os.environ.get("HF_TOKEN", "")

    if not openai_api_key and hf_token:
        openai_api_key = hf_token

    client = OpenAI(
        api_key=openai_api_key,
        base_url=api_base_url
    )

    overall_start = time.time()
    scores = {task: 0.0 for task in TASKS}

    try:
        wait_for_environment(env_base_url.rstrip("/"))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        for task in TASKS:
            log_line(
                "START",
                task=task,
                session_id=None,
                model_name=model_name,
                api_base_url=api_base_url,
                env_base_url=env_base_url,
                initial_step=None,
                initial_elapsed_seconds=None,
            )
            log_line(
                "END",
                task=task,
                score=0.0,
                resolved=False,
                elapsed_seconds=0,
                wrong_actions=0,
                llm_failures=0,
                error=error,
            )
        log_line(
            "END",
            average_score=0.0,
            easy_score=0.0,
            medium_score=0.0,
            hard_score=0.0,
            runtime_seconds=round(time.time() - overall_start, 3),
            error=error,
        )
        return 0

    for task in TASKS:
        reset_data = request_with_retry(
            "post",
            f"{env_base_url.rstrip('/')}/reset",
            json={"task": task},
        )
        session_id = reset_data["session_id"]
        obs = reset_data["observation"]
        final_info = {
            "task_score": 0.0,
            "resolved": False,
            "elapsed_seconds": obs["elapsed_seconds"],
            "wrong_actions": 0,
        }
        llm_failures = 0

        log_line(
            "START",
            task=task,
            session_id=session_id,
            model_name=model_name,
            api_base_url=api_base_url,
            env_base_url=env_base_url,
            initial_step=obs["step"],
            initial_elapsed_seconds=obs["elapsed_seconds"],
        )

        for step in range(1, MAX_STEPS + 1):
            user_message = build_user_message(obs)

            proposed_action = ""
            llm_error = None
            try:
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_message}
                    ],
                    max_tokens=20,
                    temperature=0.0
                )
                content = response.choices[0].message.content if response.choices else ""
                content = content.strip() if content else ""
                proposed_action = content.splitlines()[0].strip() if content else ""
            except Exception as exc:
                llm_error = str(exc)
                proposed_action = ""
                llm_failures += 1

            if proposed_action in VALID_ACTIONS:
                action = proposed_action
            else:
                action = "do_nothing"

            step_data = request_with_retry(
                "post",
                f"{env_base_url.rstrip('/')}/step",
                json={"session_id": session_id, "action": {"command": action}},
            )

            reward = step_data["reward"]
            done = step_data["done"]
            final_info = step_data["info"]
            obs = step_data["observation"]

            log_line(
                "STEP",
                task=task,
                step=step,
                action=action,
                proposed_action=proposed_action,
                llm_error=llm_error,
                reward_immediate=reward["immediate"],
                reward_cumulative=reward["cumulative"],
                reward_final=reward.get("final"),
                done=done,
                elapsed_seconds=final_info["elapsed_seconds"],
                wrong_actions=final_info["wrong_actions"],
            )

            if done:
                break

        scores[task] = float(final_info["task_score"])
        log_line(
            "END",
            task=task,
            score=final_info["task_score"],
            resolved=final_info["resolved"],
            elapsed_seconds=final_info["elapsed_seconds"],
            wrong_actions=final_info["wrong_actions"],
            llm_failures=llm_failures,
        )

    average_score = sum(scores.values()) / len(TASKS)
    log_line(
        "END",
        average_score=average_score,
        easy_score=scores["easy"],
        medium_score=scores["medium"],
        hard_score=scores["hard"],
        runtime_seconds=round(time.time() - overall_start, 3),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
