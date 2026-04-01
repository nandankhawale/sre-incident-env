import json
import os
import time

import requests


TASKS = ["easy", "medium", "hard"]


def request_json(method, url, **kwargs):
    if method == "get":
        response = requests.get(url, timeout=30, **kwargs)
    elif method == "post":
        response = requests.post(url, timeout=30, **kwargs)
    else:
        raise RuntimeError(f"Unsupported method: {method}")
    response.raise_for_status()
    return response.json()


def main():
    base_url = os.environ.get("ENV_BASE_URL", "http://localhost:7860").rstrip("/")
    start = time.time()

    root = request_json("get", f"{base_url}/")
    health = request_json("get", f"{base_url}/health")
    tasks = request_json("get", f"{base_url}/tasks")

    print("Root:", json.dumps(root, indent=2))
    print("Health:", json.dumps(health, indent=2))
    print("Tasks:", json.dumps(tasks, indent=2))

    for task_name in TASKS:
        reset_payload = request_json("post", f"{base_url}/reset", json={"task": task_name})
        session_id = reset_payload["session_id"]
        observation = reset_payload["observation"]
        state = request_json("get", f"{base_url}/state", params={"session_id": session_id})

        print(
            f"[{task_name}] session={session_id} "
            f"obs_step={observation['step']} state_step={state['step']}"
        )

        step_response = request_json(
            "post",
            f"{base_url}/step",
            json={"session_id": session_id, "action": {"command": "do_nothing"}},
        )
        print(
            f"[{task_name}] reward={step_response['reward']['immediate']:.2f} "
            f"done={step_response['done']} info={json.dumps(step_response['info'])}"
        )

    print(f"Validation completed in {time.time() - start:.1f}s")


if __name__ == "__main__":
    main()
