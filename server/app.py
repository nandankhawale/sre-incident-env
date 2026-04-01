from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sre_incident_env.env import SREIncidentEnv
from sre_incident_env.models import Action
from sre_incident_env.tasks import TASKS


class ResetRequest(BaseModel):
    task: str = "easy"


class StepRequest(BaseModel):
    session_id: str
    action: Action


app = FastAPI(title="SRE Incident Response Environment")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSIONS: dict[str, SREIncidentEnv] = {}


@app.get("/")
def root() -> dict:
    return {
        "name": "sre-incident-env",
        "status": "ok",
        "tasks": list(TASKS.keys()),
    }


@app.post("/reset")
def reset_environment(payload: ResetRequest | None = None) -> dict:
    task = payload.task if payload is not None else "easy"
    if task not in TASKS:
        raise HTTPException(status_code=400, detail="Unknown task")

    env = SREIncidentEnv()
    observation = env.reset(task=task)
    session_id = str(uuid4())
    SESSIONS[session_id] = env
    return {
        "session_id": session_id,
        "observation": observation.model_dump(mode="json"),
    }


@app.post("/step")
def step_environment(payload: StepRequest) -> dict:
    env = SESSIONS.get(payload.session_id)
    if env is None:
        raise HTTPException(status_code=404, detail="Session not found")

    observation, reward, done, info = env.step(payload.action)
    return {
        "observation": observation.model_dump(mode="json"),
        "reward": reward.model_dump(mode="json"),
        "done": done,
        "info": info,
    }


@app.get("/state")
def get_state(session_id: str) -> dict:
    env = SESSIONS.get(session_id)
    if env is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return env.state()


@app.get("/tasks")
def get_tasks() -> dict:
    return TASKS


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def main() -> None:
    uvicorn.run("server.app:app", host="0.0.0.0", port=7860)


if __name__ == "__main__":
    main()
