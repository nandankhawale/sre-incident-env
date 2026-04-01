import json
import os
import time

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


def require_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def request_with_retry(method, url, **kwargs):
    last_error = None
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
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2)
    raise RuntimeError(f"Request failed after 3 attempts: {url}") from last_error


def parse_action(raw_text):
    action = (raw_text or "").strip().splitlines()[0].strip() if (raw_text or "").strip() else ""
    if action not in VALID_ACTIONS:
        print(f"  warning: invalid model action {json.dumps(action)}")
        return ""
    return action


def build_user_message(obs):
    hint_line = f"Hint: {obs['hint']}" if obs.get("hint") else ""
    return (
        f"Step {obs['step']} | Elapsed: {obs['elapsed_seconds']}s\n"
        f"Services: {obs['services']}\n"
        f"Active Alerts: {[a['name'] + ' (' + a['severity'] + ')' for a in obs['alerts']]}\n"
        f"Metrics: {obs['metrics']}\n"
        f"Actions taken so far: {obs['action_history']}\n"
        f"SLA breached: {obs['sla_breached']}\n"
        f"{hint_line}\n"
        "What is your next action?"
    )


def heuristic_action(obs):
    history = obs["action_history"]
    services = obs["services"]
    alerts = obs["alerts"]
    metrics = obs["metrics"]
    alert_names = [alert["name"] for alert in alerts]

    if "database_connection_pool_exhausted" in alert_names:
        return "scale_up:database"

    if obs.get("hint") and "payment service" in obs["hint"].lower():
        if "check_logs:payment" not in history:
            return "check_logs:payment"
        if "rollback_deploy:payment" not in history:
            return "rollback_deploy:payment"

    if services.get("auth") == "critical" or any(
        alert["service"] == "auth" and alert["severity"] == "P0" for alert in alerts
    ):
        if "check_logs:auth" not in history:
            return "check_logs:auth"
        if "rollback_deploy:auth" not in history:
            return "rollback_deploy:auth"

    database_is_suspicious = (
        services.get("database") in {"critical", "degraded"}
        or metrics.get("database_cpu_pct", 0.0) >= 90.0
        or metrics.get("database_conn_pool_pct", 0.0) >= 97.0
        or any(alert["service"] == "database" for alert in alerts)
    )
    payment_is_impacted = services.get("payment") in {"down", "critical", "degraded"}
    order_is_impacted = services.get("order") in {"critical", "degraded"}

    if database_is_suspicious and (payment_is_impacted or order_is_impacted):
        if "check_logs:database" not in history and any(
            alert["service"] == "database" for alert in alerts
        ):
            return "check_logs:database"
        if services.get("database") != "ok":
            return "scale_up:database"
        if services.get("payment") != "ok" and "enable_circuit_breaker:payment" not in history:
            return "enable_circuit_breaker:payment"

    if payment_is_impacted:
        if "check_logs:payment" not in history and any(
            alert["service"] == "payment" for alert in alerts
        ):
            return "check_logs:payment"
        if "rollback_deploy:payment" not in history:
            return "rollback_deploy:payment"
        if "enable_circuit_breaker:payment" not in history:
            return "enable_circuit_breaker:payment"

    return None


def is_low_signal_action(action):
    return (
        action.startswith("restart_service:")
        or action == "do_nothing"
        or action == "page_senior_engineer"
    )


def choose_action(obs, proposed_action):
    history = obs["action_history"]
    heuristic = heuristic_action(obs)

    if proposed_action not in VALID_ACTIONS:
        fallback = heuristic or "do_nothing"
        print(f"  warning: defaulting to {fallback}")
        return fallback

    if heuristic and heuristic != proposed_action:
        if is_low_signal_action(proposed_action):
            print(f"  note: overriding low-signal action {proposed_action} -> {heuristic}")
            return heuristic
        if history and proposed_action == history[-1]:
            print(f"  note: avoiding repeated action {proposed_action} -> {heuristic}")
            return heuristic
        if proposed_action in history and heuristic not in history:
            print(f"  note: preferring fresh action {heuristic} over repeated {proposed_action}")
            return heuristic

    return proposed_action


def main():
    api_base_url = require_env("API_BASE_URL").rstrip("/")
    require_env("OPENAI_API_KEY")
    model_name = require_env("MODEL_NAME")

    client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("API_BASE_URL_LLM", "https://api.openai.com/v1")
    )

    system_prompt = (
        "You are an expert SRE engineer responding to a production incident.\n"
        "You must diagnose and resolve the incident as fast as possible.\n\n"
        "Available actions (you must respond with EXACTLY one of these strings):\n"
        "restart_service:payment, restart_service:auth, restart_service:database,\n"
        "restart_service:order, restart_service:frontend,\n"
        "scale_up:database, scale_up:payment,\n"
        "rollback_deploy:payment, rollback_deploy:auth,\n"
        "enable_circuit_breaker:payment, enable_circuit_breaker:order,\n"
        "check_logs:payment, check_logs:database, check_logs:auth,\n"
        "page_senior_engineer, do_nothing\n\n"
        "Respond with ONLY the action string. Nothing else. No explanation."
    )

    scores = {}
    start_time = time.time()

    for task_name in ["easy", "medium", "hard"]:
        reset_data = request_with_retry(
            "post",
            f"{api_base_url}/reset",
            json={"task": task_name},
        )
        session_id = reset_data["session_id"]
        observation = reset_data["observation"]
        final_info = {
            "task_score": 0.0,
            "resolved": False,
            "elapsed_seconds": observation["elapsed_seconds"],
            "wrong_actions": 0,
        }

        for step in range(1, 21):
            user_message = build_user_message(observation)
            completion = client.chat.completions.create(
                model=os.environ["MODEL_NAME"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                max_tokens=20,
                temperature=0.0
            )
            raw_action = completion.choices[0].message.content if completion.choices else ""
            proposed_action = parse_action(raw_action)
            action = choose_action(observation, proposed_action)

            step_data = request_with_retry(
                "post",
                f"{api_base_url}/step",
                json={"session_id": session_id, "action": {"command": action}},
            )
            observation = step_data["observation"]
            reward = step_data["reward"]
            done = step_data["done"]
            final_info = step_data["info"]

            print(f"  step {step}: action={action}  reward={reward['immediate']:.2f}")

            if done:
                break

        scores[task_name] = final_info["task_score"]
        print(
            f"[{task_name.upper()}] score={final_info['task_score']:.3f}  "
            f"resolved={final_info['resolved']}  "
            f"elapsed={final_info['elapsed_seconds']}s  "
            f"wrong_actions={final_info['wrong_actions']}"
        )

    runtime = time.time() - start_time
    avg = (scores["easy"] + scores["medium"] + scores["hard"]) / 3.0

    print(
        f"""
══════════════════════════════════════
BASELINE RESULTS — {model_name}
══════════════════════════════════════
Easy   : {scores['easy']:.3f}
Medium : {scores['medium']:.3f}
Hard   : {scores['hard']:.3f}
Overall: {avg:.3f}
Total runtime: {runtime:.1f}s
══════════════════════════════════════
"""
    )


if __name__ == "__main__":
    main()
