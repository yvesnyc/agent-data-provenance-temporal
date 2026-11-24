"""Builds the compact `_provenance` JSONB value stamped on every agent-written row.

Two shapes, one per way agents create data:

Path A, agent answering a request (written during the agent's Temporal run):
    {"agent": {"name", "version", "commit", "model"}, "wf", "run", "sources": ["table:id", ...]}

Path B, agent-authored code (written whenever the code runs, long after the agent):
    {"code": {"repo", "commit", "path"}, "wf", "run", "sources": [...]}
    The commit's git trailers point back to the Temporal run of the agent that wrote it,
    and name the model that wrote the code (Agent-Model).

`model` is the model that actually produced the output ("mock" when no API key is set),
so an auditor can tell LLM-written rows from mock rows and see when a fallback model ran.
"""

import subprocess
from functools import lru_cache
from pathlib import Path

from . import config


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@lru_cache(maxsize=1)
def agent_code_commit() -> str:
    """Short SHA of this project's code (the agent's own code). '-dirty' if uncommitted changes.

    Cached for the life of the process: Python loads the agent code once, so the
    commit must describe the code *as loaded*. worker.py calls this at startup,
    right after the imports, so edits made while the worker runs can't change it.
    """
    try:
        sha = _git(config.PROJECT_ROOT, "rev-parse", "--short", "HEAD")
    except subprocess.CalledProcessError:
        return "uncommitted"
    dirty = _git(config.PROJECT_ROOT, "status", "--porcelain", "--untracked-files=no")
    return f"{sha}-dirty" if dirty else sha


def agent_identity(agent: dict) -> dict:
    return {"name": agent["name"], "version": agent["version"], "commit": agent_code_commit()}


def for_agent_run(agent: dict, model: str, workflow_id: str, run_id: str,
                  sources: list[str]) -> dict:
    return {
        "agent": {**agent_identity(agent), "model": model},
        "wf": workflow_id,
        "run": run_id,
        "sources": sorted(set(sources)),
    }


def for_code_run(repo_name: str, commit: str, path: str, workflow_id: str, run_id: str,
                 sources: list[str]) -> dict:
    return {
        "code": {"repo": repo_name, "commit": commit, "path": path},
        "wf": workflow_id,
        "run": run_id,
        "sources": sorted(set(sources)),
    }
