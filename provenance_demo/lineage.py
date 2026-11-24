"""Offline lineage / audit tool. Not runtime work: run it when an auditor asks.

Given a record, follow its compact `_provenance` references:
    record -> source rows (by ID)
           -> Path A: agent identity + the Temporal run that wrote it (full event history)
           -> Path B: git commit of the code that wrote it -> commit trailers
                      -> the Temporal run of the agent that wrote the code
                      -> the Temporal run of the job that executed the code

    uv run python -m provenance_demo.lineage claim_summaries 1
    uv run python -m provenance_demo.lineage claim_risk_scores 3 [--json]
"""

import argparse
import asyncio
import json
import subprocess
from typing import Any

from temporalio.api.enums.v1 import EventType
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from . import config, db
from .activities.codegen import git, read_trailers
from .temporal_client import connect

AGENT_TABLES = {"claim_summaries", "claim_risk_scores"}


def _decode(payloads) -> list[Any]:
    out = []
    for p in payloads:
        try:
            out.append(json.loads(p.data))
        except (ValueError, UnicodeDecodeError):
            out.append(f"<{len(p.data)} bytes, encoding={p.metadata.get('encoding', b'?').decode()}>")
    return out


async def run_history(client: Client, workflow_id: str, run_id: str) -> dict | None:
    """Condensed view of a run's Temporal event history. None if the server no longer
    has it (past retention) -- in production you'd read the archived history instead."""
    try:
        history = await client.get_workflow_handle(workflow_id, run_id=run_id).fetch_history()
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            return None
        raise
    summary: dict[str, Any] = {"workflow_id": workflow_id, "run_id": run_id,
                               "events": len(history.events), "activities": []}
    by_sched: dict[int, dict] = {}
    for ev in history.events:
        t = ev.event_time.ToDatetime().isoformat(timespec="seconds") + "Z"
        if ev.HasField("workflow_execution_started_event_attributes"):
            a = ev.workflow_execution_started_event_attributes
            summary.update(workflow_type=a.workflow_type.name, started=t, input=_decode(a.input.payloads))
        elif ev.HasField("activity_task_scheduled_event_attributes"):
            a = ev.activity_task_scheduled_event_attributes
            by_sched[ev.event_id] = act = {"activity": a.activity_type.name, "scheduled": t,
                                           "attempts": 0, "status": "scheduled"}
            summary["activities"].append(act)
        elif ev.HasField("activity_task_started_event_attributes"):
            a = ev.activity_task_started_event_attributes
            by_sched[a.scheduled_event_id]["attempts"] = a.attempt
        elif ev.HasField("activity_task_completed_event_attributes"):
            a = ev.activity_task_completed_event_attributes
            by_sched[a.scheduled_event_id].update(status="completed", completed=t,
                                                  result=_decode(a.result.payloads))
        elif ev.HasField("activity_task_failed_event_attributes"):
            a = ev.activity_task_failed_event_attributes
            by_sched[a.scheduled_event_id].update(status="failed", failure=a.failure.message)
    last = history.events[-1]
    summary["final_event"] = EventType.Name(last.event_type).removeprefix("EVENT_TYPE_")
    summary["closed"] = last.event_time.ToDatetime().isoformat(timespec="seconds") + "Z"
    return summary


async def trace(table: str, row_id: int, client: Client | None = None) -> dict:
    if table not in AGENT_TABLES:
        raise SystemExit(f"table must be one of {sorted(AGENT_TABLES)}")
    row = db.fetch_one(f"SELECT * FROM {table} WHERE id = %s", (row_id,))
    if row is None:
        raise SystemExit(f"{table}:{row_id} not found")
    prov = row.pop("_provenance")
    row.pop("prov_run", None)
    client = client or await connect()

    report: dict[str, Any] = {"record": f"{table}:{row_id}", "row": row, "provenance": prov,
                              "sources": {ref: db.get_source_row(ref) for ref in prov["sources"]}}

    if "agent" in prov:  # Path A: written by an agent during its own run
        report["path"] = "agent-run"
        report["writer_run"] = await run_history(client, prov["wf"], prov["run"])
    elif "code" in prov:  # Path B: written by agent-authored code
        report["path"] = "agent-written-code"
        repo, commit = config.generated_repo(), prov["code"]["commit"]
        try:
            git(repo, "cat-file", "-e", f"{commit}:{prov['code']['path']}")
            trailers = read_trailers(repo, commit)
            report["code_commit"] = {
                "commit": commit, "path": prov["code"]["path"], "trailers": trailers,
                "subject": git(repo, "log", "-1", "--format=%s", commit),
                "committed": git(repo, "log", "-1", "--format=%cI", commit),
            }
        except subprocess.CalledProcessError:
            report["code_commit"] = {"commit": commit, "error": f"not found in {repo}"}
            trailers = {}
        report["writer_run"] = await run_history(client, prov["wf"], prov["run"])
        if "Temporal-Workflow-Id" in trailers:
            report["code_author_run"] = await run_history(
                client, trailers["Temporal-Workflow-Id"], trailers["Temporal-Run-Id"])
    return report


def _short(v: Any, n: int = 160) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s if len(s) <= n else s[: n - 3] + "..."


def _print_run(label: str, run: dict | None, wf: str, run_id: str) -> None:
    print(f"\n{label}")
    if run is None:
        print(f"  !! history for {wf}/{run_id} not on server (past retention?) -> read the archive")
        return
    print(f"  {run['workflow_type']}  wf={run['workflow_id']}  run={run['run_id']}")
    print(f"  {run['started']} -> {run['closed']}  ({run['final_event']}, {run['events']} events)")
    print(f"  input: {_short(run['input'])}")
    for a in run["activities"]:
        print(f"  - activity {a['activity']}: {a['status']} (attempt {a['attempts']})")
        if "result" in a:
            print(f"      result: {_short(a['result'])}")
        if "failure" in a:
            print(f"      failure: {a['failure']}")


def print_report(r: dict) -> None:
    prov = r["provenance"]
    print(f"RECORD {r['record']}  [{r['path']}]")
    for k, v in r["row"].items():
        print(f"  {k}: {_short(v)}")
    print(f"  _provenance: {json.dumps(prov)}")

    print("\nSOURCES")
    for ref, src in r["sources"].items():
        print(f"  {ref}: {_short(src) if src else '!! missing'}")

    if r["path"] == "agent-run":
        a = prov["agent"]
        print(f"\nAGENT  {a['name']} v{a['version']} @ commit {a['commit']}  model={a.get('model', '?')}")
        _print_run("WRITER RUN (agent run that wrote this record)", r["writer_run"], prov["wf"], prov["run"])
    else:
        c = r["code_commit"]
        print(f"\nCODE  {prov['code']['repo']}:{prov['code']['path']} @ {c['commit']}")
        if "error" in c:
            print(f"  !! {c['error']}")
        else:
            print(f"  {c['subject']}  ({c['committed']})")
            for k, v in c["trailers"].items():
                print(f"  {k}: {v}")
        _print_run("WRITER RUN (job that executed the code)", r["writer_run"], prov["wf"], prov["run"])
        t = c.get("trailers", {})
        if "Temporal-Workflow-Id" in t:
            _print_run("CODE AUTHOR RUN (agent run that wrote the code)", r.get("code_author_run"),
                       t["Temporal-Workflow-Id"], t["Temporal-Run-Id"])


async def _main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("table", choices=sorted(AGENT_TABLES))
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true", help="emit the full report as JSON")
    args = p.parse_args()
    report = await trace(args.table, args.id)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_report(report)


if __name__ == "__main__":
    asyncio.run(_main())
