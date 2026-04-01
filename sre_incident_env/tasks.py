TASKS = {
    "easy": {
        "name": "Single Service Failure",
        "description": "One service is down with a clear root cause. Fix it within 5 minutes.",
        "scenario_ids": ["scenario_001", "scenario_002"],
        "sla_seconds": 300,
        "max_wrong_actions": 3,
        "scoring": "1.0 if resolved under SLA, 0.5 if resolved over SLA, 0.0 if not resolved",
    },
    "medium": {
        "name": "Cascading Failure with Red Herrings",
        "description": "Multiple services affected. Some alerts are misleading. Find root cause.",
        "scenario_ids": ["scenario_003", "scenario_004"],
        "sla_seconds": 420,
        "max_wrong_actions": 2,
        "scoring": "1.0 if resolved under SLA with ≤1 wrong action",
    },
    "hard": {
        "name": "Multi-Service Incident with Faulty Metrics",
        "description": "4 services degraded, 3 red herring alerts, one metric always reads 0. Zero mistakes required.",
        "scenario_ids": ["scenario_005"],
        "sla_seconds": 600,
        "max_wrong_actions": 0,
        "scoring": "1.0 only if resolved under SLA with zero wrong actions",
    },
}
