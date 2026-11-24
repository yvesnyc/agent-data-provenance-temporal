"""Runtime configuration. Read lazily so tests can override via env vars."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASK_QUEUE = "provenance-demo"

# Agent identities. `version` is the agent's declared version (prompt/tooling
# contract); the git commit of the agent's code is resolved at runtime.
SUMMARIZER = {"name": "summarizer", "version": "3"}
CODEGEN = {"name": "codegen", "version": "1"}

CLAUDE_MODEL = "claude-opus-5-5"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://localhost/provenance_demo")


def temporal_address() -> str:
    return os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")


def temporal_namespace() -> str:
    return os.environ.get("TEMPORAL_NAMESPACE", "default")


def generated_repo() -> Path:
    """Git repo that holds agent-authored code (separate from this project)."""
    return Path(os.environ.get("GENERATED_REPO", PROJECT_ROOT / "generated_repo")).resolve()


def llm_mode() -> str:
    """'claude' if credentials are configured, else 'mock'. Override with LLM_MODE."""
    forced = os.environ.get("LLM_MODE")
    if forced in ("claude", "mock"):
        return forced
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "claude"
    return "mock"
