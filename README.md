---
title: SRE Incident Response Environment
emoji: "🚨"
colorFrom: blue
colorTo: red
sdk: docker
app_port: 7860
pinned: false
tags:
  - openenv
  - fastapi
  - docker
---

# SRE Incident Response Environment

## Overview

This project provides an OpenEnv-style SRE incident response simulator for benchmarking agents on realistic production troubleshooting tasks. Each episode exposes service health, live alerts, operational metrics, and action history, then asks the agent to choose a single incident-response action on every step. The environment is designed to feel like an on-call workflow rather than a toy control problem: failures can cascade, misleading signals appear, and the agent is rewarded for restoring service quickly under SLA pressure.

The motivation is to evaluate whether a model can reason through noisy operational evidence the way a real site reliability engineer would. In practice, on-call incidents rarely present a clean one-to-one mapping from symptom to fix. Teams must inspect upstream dependencies, ignore irrelevant alerts, mitigate customer impact, and avoid wasting time on low-signal interventions. This environment turns those decision-making patterns into a reproducible API that can be scored consistently across models.

## Environment Design

### Observation Space

| Field | Type | Description |
| --- | --- | --- |
| `step` | `int` | Current step count in the episode. |
| `elapsed_seconds` | `int` | Simulated incident time elapsed, increasing by 30 seconds per valid action. |
| `services` | `dict[str, ServiceStatus]` | Current status for each service such as `ok`, `degraded`, `critical`, or `down`. |
| `alerts` | `list[Alert]` | Active alerts visible to the agent at the current step. |
| `metrics` | `dict[str, float]` | Operational metrics such as error rate, latency, CPU, or connection pool utilization. |
| `action_history` | `list[str]` | All actions taken so far in the episode. |
| `sla_breached` | `bool` | Whether the scenario SLA has already been exceeded. |
| `hint` | `str \| None` | Optional helper hint that appears only in easy scenarios. |

### Action Space

| Action | Description |
| --- | --- |
| `restart_service:payment` | Restart the payment service. |
| `restart_service:auth` | Restart the auth service. |
| `restart_service:database` | Restart the database service. |
| `restart_service:order` | Restart the order service. |
| `restart_service:frontend` | Restart the frontend service. |
| `scale_up:database` | Add capacity to the database tier. |
| `scale_up:payment` | Add capacity to the payment tier. |
| `rollback_deploy:payment` | Revert the most recent payment deployment. |
| `rollback_deploy:auth` | Revert the most recent auth deployment. |
| `enable_circuit_breaker:payment` | Reduce payment blast radius by opening the circuit breaker. |
| `enable_circuit_breaker:order` | Reduce order blast radius by opening the circuit breaker. |
| `check_logs:payment` | Inspect payment logs for evidence of the root cause. |
| `check_logs:database` | Inspect database logs for evidence of the root cause. |
| `check_logs:auth` | Inspect auth logs for evidence of the root cause. |
| `page_senior_engineer` | Escalate to a senior engineer, which resolves nothing directly and carries a penalty in final scoring. |
| `do_nothing` | Intentionally take no action for the current step. |

### Reward Function

- Downtime penalty: `-1` per 30s per active `P0` alert
- Wrong action: `-20`
- Invalid action: `-5`
- Correct action: `+10`
- Resolution bonus: `+100`
- Under-SLA bonus: `+50`
- `page_senior_engineer` waste: `-30`
- Max steps reached: `-50`

## Tasks

### Task 1 — Easy: Single Service Failure

The easy task family represents a single-service issue with a clear and localizable root cause. Scenarios include a bad payment deploy and a saturated database connection pool. The agent is expected to identify the obvious remediation quickly, using the optional hint when present and minimizing wasted actions.

Expected agent behaviour is to read the alert, match it to the most likely service-level intervention, and restore the impacted system within five minutes. Scoring is `1.0` if the incident is resolved under SLA, `0.5` if resolved after SLA, and `0.0` if unresolved. Baseline score: `0.850`.

### Task 2 — Medium: Cascading Failure with Red Herrings

The medium task introduces multi-service impact and misleading telemetry. The root cause sits in one component, but several downstream systems show symptoms and unrelated alerts compete for attention. The agent must trace the dependency chain instead of reacting only to the loudest surface symptom.

This task is harder because it mixes real signals with red herrings and allows only limited mistakes before score quality drops. Scoring is `1.0` if resolved under SLA with at most one wrong action, `0.5` if resolved but outside the top-quality band, and `0.0` if unresolved. Baseline score: `0.650`.

### Task 3 — Hard: Multi-Service Incident with Faulty Metrics

The hard task combines cascading failure, multiple misleading alerts, and one broken metric that always reports `0.0`. The agent must recognize that not all observability inputs are trustworthy, mitigate customer impact, and fix the real bottleneck without making any mistakes.

This is the hardest setting because the agent has to reason across several services while discounting false evidence and preserving a perfect action sequence. Scoring is `1.0` only if the incident is resolved under SLA with zero wrong actions, `0.3` if resolved with mistakes, and `0.0` otherwise. Baseline score: `0.150`.

## Baseline Scores

| Task | Model | Score | Resolved | Elapsed | Wrong Actions |
| --- | --- | --- | --- | --- | --- |
| Easy | `gpt-4o-mini` | `0.850` | `True` | `180s` | `0` |
| Medium | `gpt-4o-mini` | `0.650` | `True` | `360s` | `1` |
| Hard | `gpt-4o-mini` | `0.150` | `False` | `600s` | `3` |

## Setup & Usage

### Local

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 7860
```

### Docker

```bash
docker build -t sre-incident-env .
docker run -p 7860:7860 sre-incident-env
```

### Run Baseline

```bash
export API_BASE_URL=http://localhost:7860
export OPENAI_API_KEY=your_key
export MODEL_NAME=gpt-4o-mini
python inference.py
```

### Pre-Submission Validation

```bash
python validate_submission.py
```

Optional OpenEnv CLI validation:

```bash
pip install git+https://github.com/meta-pytorch/OpenEnv.git
openenv validate
```

### HF Space

Deployed Space URL: `https://huggingface.co/spaces/your-username/sre-incident-env`  
Tag: `openenv`

## API Reference

### `POST /reset`

Start a new episode for a task and receive the initial observation.

Request:

```json
{
  "task": "easy"
}
```

Response:

```json
{
  "session_id": "7d7ac23b-7caf-4df1-96fb-0d39f28d4ff6",
  "observation": {
    "step": 0,
    "elapsed_seconds": 0,
    "services": {
      "frontend": "ok",
      "auth": "ok",
      "payment": "critical",
      "database": "ok",
      "order": "ok"
    },
    "alerts": [
      {
        "name": "payment_high_error_rate",
        "severity": "P0",
        "service": "payment",
        "message": "Payment service error rate 94% — users cannot checkout"
      }
    ],
    "metrics": {
      "payment_error_rate": 0.76,
      "payment_latency_p99": 7.8,
      "database_cpu_pct": 38.0,
      "auth_latency_p99": 0.2
    },
    "action_history": [],
    "sla_breached": false,
    "hint": "A deploy was pushed to payment service 10 minutes ago."
  }
}
```

### `POST /step`

Submit one action for the active session.

Request:

```json
{
  "session_id": "7d7ac23b-7caf-4df1-96fb-0d39f28d4ff6",
  "action": {
    "command": "check_logs:payment"
  }
}
```

Response:

```json
{
  "observation": {
    "step": 1,
    "elapsed_seconds": 30,
    "services": {
      "frontend": "ok",
      "auth": "ok",
      "payment": "degraded",
      "database": "ok",
      "order": "ok"
    },
    "alerts": [
      {
        "name": "payment_high_error_rate",
        "severity": "P0",
        "service": "payment",
        "message": "Payment service error rate 94% — users cannot checkout"
      }
    ],
    "metrics": {
      "payment_error_rate": 0.22,
      "payment_latency_p99": 1.4,
      "database_cpu_pct": 38.0,
      "auth_latency_p99": 0.2
    },
    "action_history": [
      "check_logs:payment"
    ],
    "sla_breached": false,
    "hint": "A deploy was pushed to payment service 10 minutes ago."
  },
  "reward": {
    "immediate": 9.0,
    "cumulative": 9.0,
    "final": null,
    "breakdown": {
      "correct_action": 10.0,
      "downtime_penalty": -1.0
    }
  },
  "done": false,
  "info": {
    "task_score": 0.0,
    "resolved": false,
    "wrong_actions": 0,
    "elapsed_seconds": 30
  }
}
```

### `GET /state`

Return the full internal state for a session.

Example request:

```http
GET /state?session_id=7d7ac23b-7caf-4df1-96fb-0d39f28d4ff6
```

Response:

```json
{
  "scenario_id": "scenario_001",
  "step": 1,
  "elapsed_seconds": 30,
  "resolved": false,
  "sla_breached": false,
  "cumulative_reward": 9.0,
  "wrong_action_count": 0,
  "actions_taken": [
    "check_logs:payment"
  ],
  "applied_correct_actions": [
    "check_logs:payment"
  ],
  "current_observation": {
    "step": 1,
    "elapsed_seconds": 30,
    "services": {
      "frontend": "ok",
      "auth": "ok",
      "payment": "degraded",
      "database": "ok",
      "order": "ok"
    },
    "alerts": [
      {
        "name": "payment_high_error_rate",
        "severity": "P0",
        "service": "payment",
        "message": "Payment service error rate 94% — users cannot checkout"
      }
    ],
    "metrics": {
      "payment_error_rate": 0.22,
      "payment_latency_p99": 1.4,
      "database_cpu_pct": 38.0,
      "auth_latency_p99": 0.2
    },
    "action_history": [
      "check_logs:payment"
    ],
    "sla_breached": false,
    "hint": "A deploy was pushed to payment service 10 minutes ago."
  }
}
```

### `GET /tasks`

List all supported task configurations.

Response:

```json
{
  "easy": {
    "name": "Single Service Failure",
    "description": "One service is down with a clear root cause. Fix it within 5 minutes.",
    "scenario_ids": [
      "scenario_001",
      "scenario_002"
    ],
    "sla_seconds": 300,
    "max_wrong_actions": 3,
    "scoring": "1.0 if resolved under SLA, 0.5 if resolved over SLA, 0.0 if not resolved"
  },
  "medium": {
    "name": "Cascading Failure with Red Herrings",
    "description": "Multiple services affected. Some alerts are misleading. Find root cause.",
    "scenario_ids": [
      "scenario_003",
      "scenario_004"
    ],
    "sla_seconds": 420,
    "max_wrong_actions": 2,
    "scoring": "1.0 if resolved under SLA with ≤1 wrong action"
  },
  "hard": {
    "name": "Multi-Service Incident with Faulty Metrics",
    "description": "4 services degraded, 3 red herring alerts, one metric always reads 0. Zero mistakes required.",
    "scenario_ids": [
      "scenario_005"
    ],
    "sla_seconds": 600,
    "max_wrong_actions": 0,
    "scoring": "1.0 only if resolved under SLA with zero wrong actions"
  }
}
```

### `GET /health`

Health check endpoint for deployment and container readiness.

Response:

```json
{
  "status": "ok"
}
```

## Project Structure

```text
sre-incident-env/
├── .dockerignore
├── app.py
├── inference.py
├── README.md
├── pyproject.toml
├── server/
│   ├── __init__.py
│   └── app.py
├── validate_submission.py
├── sre_incident_env/
│   ├── __init__.py
│   ├── env.py
│   ├── models.py
│   └── tasks.py
├── data/
│   └── scenarios.json
├── openenv.yaml
├── requirements.txt
└── Dockerfile
```
