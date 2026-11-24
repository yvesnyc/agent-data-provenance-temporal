"""Path B, step 1: an agent writes code and commits it.

The commit carries git trailers pointing back to the Temporal run of the agent
that wrote it, so any record later written by this code (which carries the
commit SHA) links back to the agent run.
"""

import ast
import subprocess
from dataclasses import dataclass
from pathlib import Path

from temporalio import activity

from .. import config, llm, provenance

TRANSFORM_PATH = "transforms/risk_score.py"
FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals",
                   "getattr", "setattr", "delattr", "vars", "input", "breakpoint"}


class InvalidGeneratedCode(Exception):
    pass


def validate_transform(code: str) -> None:
    """Static guardrail on agent-written code. Not a sandbox: it narrows what the
    generated function can do; the runner still owns all DB access."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise InvalidGeneratedCode(f"syntax error: {e}") from e
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad = [a.name for a in node.names if a.name not in llm.ALLOWED_IMPORTS]
            if bad:
                raise InvalidGeneratedCode(f"disallowed import(s): {bad}")
        elif isinstance(node, ast.ImportFrom):
            if node.module not in llm.ALLOWED_IMPORTS:
                raise InvalidGeneratedCode(f"disallowed import: {node.module}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise InvalidGeneratedCode(f"forbidden name: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise InvalidGeneratedCode(f"forbidden dunder attribute: {node.attr}")
    if not any(isinstance(n, ast.FunctionDef) and n.name == "transform" for n in tree.body):
        raise InvalidGeneratedCode("no top-level `def transform(rows)`")


@dataclass
class GeneratedCode:
    code: str
    model: str              # model that actually wrote the code
    request_ids: list[str]  # Anthropic API request IDs (kept in Temporal history)


@dataclass
class CommitInput:
    code: str
    model: str
    spec: str
    workflow_id: str
    run_id: str


@dataclass
class CommitResult:
    repo: str
    commit: str
    path: str


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def read_trailers(repo: Path, commit: str) -> dict[str, str]:
    raw = git(repo, "log", "-1", "--format=%(trailers:only,unfold)", commit)
    out = {}
    for line in raw.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return out


@activity.defn
def generate_transform_code(spec: str) -> GeneratedCode:
    activity.logger.info("codegen generating transform (llm=%s)", config.llm_mode())
    code, call = llm.generate_transform(spec)
    validate_transform(code)
    return GeneratedCode(code=code, model=call.model, request_ids=call.request_ids)


@activity.defn
def commit_transform(inp: CommitInput) -> CommitResult:
    repo = config.generated_repo()
    if not (repo / ".git").exists():
        repo.mkdir(parents=True, exist_ok=True)
        git(repo, "init", "-q")

    # Idempotency: if a previous attempt of this same run already committed, reuse it.
    try:
        head = git(repo, "rev-parse", "HEAD")
        if read_trailers(repo, head).get("Temporal-Run-Id") == inp.run_id:
            return CommitResult(repo=repo.name, commit=head, path=TRANSFORM_PATH)
    except subprocess.CalledProcessError:
        pass  # empty repo, no HEAD yet

    target = repo / TRANSFORM_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(inp.code)
    git(repo, "add", TRANSFORM_PATH)

    agent = provenance.agent_identity(config.CODEGEN)
    git(
        repo,
        "-c", "user.name=codegen-agent",
        "-c", "user.email=codegen-agent@provenance-demo.invalid",
        "commit", "-q", "--allow-empty",
        "-m", f"codegen: write {TRANSFORM_PATH}",
        "-m", f"Spec: {inp.spec}",
        "--trailer", f"Agent: {agent['name']}@{agent['version']}",
        "--trailer", f"Agent-Commit: {agent['commit']}",
        "--trailer", f"Agent-Model: {inp.model}",
        "--trailer", f"Temporal-Workflow-Id: {inp.workflow_id}",
        "--trailer", f"Temporal-Run-Id: {inp.run_id}",
    )
    sha = git(repo, "rev-parse", "HEAD")
    activity.logger.info("committed %s at %s", TRANSFORM_PATH, sha)
    return CommitResult(repo=repo.name, commit=sha, path=TRANSFORM_PATH)
