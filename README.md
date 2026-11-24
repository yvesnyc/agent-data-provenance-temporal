# Agent data provenance with Temporal + Postgres

On LinkedIn I posted about two types of agent harness generated events that relate to data record creation.

Coding agent harnesses coordinate around GitHub commits at a scale of ~10 agents. (at least that's my level of tolerance)
General agent harnesses can scale around ~10000 agents on Temporal.

Coding agents generate code that create records. The general agents produce data records as part of a request response.

We want to keep track of which agents produced records in a table as part of the data provenance. Temporal is a natural
platform to leverage because of the durable execution management. The addition of a provenance column ("_provenance") holding this information as a compact JSONB makes it a useful for debugging as well as audit in regulated domains.

A runnable demo of this pattern: every row an agent writes carries a compact
`_provenance` JSONB column (IDs and hashes, not copies), and Temporal's event
history is the detailed record. Lineage is traced offline, at audit time.

Agents create records in two ways, and both are covered here:

| Path | Who writes the row | `_provenance` |
|---|---|---|
| **A. Agent answering a request** | `summarizer` agent, during its Temporal run | `{"agent":{"name":"summarizer","version":"3","commit":"a1b9e0c","model":"claude-opus-5-5"},"wf":"claims-4","run":"4e1d…","sources":["claims:4","orders:1200",…,"patients:79"]}` |
| **B. Agent-written code** | `transforms/risk_score.py`, written by the `codegen` agent and run later by a scheduled job | `{"code":{"repo":"generated_repo","commit":"7b3e…","path":"transforms/risk_score.py"},"wf":"riskjob-…","run":"…","sources":[…]}` |

In path B, the commit itself records the agent run that wrote the code, using git trailers:

```
codegen: write transforms/risk_score.py

Spec: Score claims for manual-review priority...

Agent: codegen@1
Agent-Commit: <sha of this project>
Agent-Model: claude-opus-5-5
Temporal-Workflow-Id: codegen-risk-score-1a2b3c
Temporal-Run-Id: 0a9c0bf7-...
```

## Layout

```
sql/schema.sql, sql/seed.sql        source tables + agent-written tables with _provenance (synthetic data)
scripts/setup_db.py                 create DB from DATABASE_URL, apply schema + seed (--reset to rebuild)
provenance_demo/
  provenance.py                     builds the _provenance value for each path
  llm.py                            Claude (claude-opus-5-5) if credentials are set, else a deterministic mock
  activities/summarizer.py          Path A: tool-using agent; every row it reads becomes a source
  activities/codegen.py             Path B1: generate transform(rows), validate, commit with trailers
  activities/job_runner.py          Path B2: load code via `git show <sha>:path`, run it, stamp rows
  workflows.py                      ClaimSummaryWorkflow, CodegenWorkflow, RiskScoreJobWorkflow
  worker.py
  starters/                         run_summary, run_codegen, schedule_job
  lineage.py                        offline audit CLI
tests/                              pytest + Temporal time-skipping test server (mock LLM)
```

## Run it

Prereqs: Postgres (local trust auth works as-is), [uv](https://docs.astral.sh/uv/), and the
Temporal CLI (`brew install temporal`).

```bash
uv sync
uv run python scripts/setup_db.py                  # DATABASE_URL defaults to postgresql://localhost/provenance_demo

temporal server start-dev                           # terminal 1 (UI at http://localhost:8233)
uv run python -m provenance_demo.worker             # terminal 2

# Path A: the agent answers requests and writes records during its run
uv run python -m provenance_demo.starters.run_summary 1 2 4

# Path B: the agent writes code; the code writes records later
uv run python -m provenance_demo.starters.run_codegen
uv run python -m provenance_demo.starters.schedule_job --commit <sha> --once
#   or as a Temporal Schedule:  ... schedule_job --commit <sha> --every 60

# Audit (offline): follow any record back to what produced it
uv run python -m provenance_demo.lineage claim_summaries 1
uv run python -m provenance_demo.lineage claim_risk_scores 1 --json
```

### LLM (and how to use your Anthropic API key)

- Without a key, the agents use a deterministic mock and records say `"model": "mock"`.
- With a key, the **worker** calls `claude-opus-5-5`. Only the worker needs the key; the
  starters and `lineage.py` never call Claude.

```bash
cp .env.example .env          # .env is gitignored; put your key in it
uv run --env-file .env python -m provenance_demo.worker
# log line should read: ... llm=claude ...
```

Or set it for one shell: `export ANTHROPIC_API_KEY=sk-ant-...` before starting the worker.

- Run only one worker at a time. A mock-mode worker on the same task queue would take some of
  the jobs and write mock rows.
- Force a mode with `LLM_MODE=mock|claude`.
- Server-side refusal fallback is enabled (`fallbacks="default"`).

**Which model produced a row:**
- **Agent answering a request:** `_provenance.agent.model` holds the model that actually
  answered. That's `resp.model`, so it shows the fallback model if one ran.
- **Agent-written code:** the commit has an `Agent-Model:` trailer.
- **API request IDs** for each call are kept in the activity result in Temporal history, not
  on the record.

### Config (env vars)

`DATABASE_URL`, `TEMPORAL_ADDRESS` (default `localhost:7233`), `TEMPORAL_NAMESPACE`,
`GENERATED_REPO` (default `./generated_repo`), `LLM_MODE`.

## Design notes

- **The workflow passes its own IDs.** It hands `workflow.info().workflow_id` / `.run_id` to the
  activity that writes. All LLM calls and DB writes happen in activities, never in workflow code.
- **Sources are what the agent actually read.** The summarizer's tools record each row they
  return, so the `sources` list reflects real tool calls, not a guess.
- **Retries don't duplicate rows.** `prov_run` is a generated column from `_provenance->>'run'`,
  with `UNIQUE (claim_id, prov_run)`, so a retried activity upserts its own row. Each new run
  writes a new row. `commit_transform` is idempotent per run: it checks HEAD's `Temporal-Run-Id`
  trailer.
- **Code runs at an exact commit.** The job loads the transform with `git show <sha>:path`, not
  from the working tree, so the commit on each record is the code that actually ran. Old commits
  stay reproducible after newer codegen runs.
- **Generated code is constrained.** Claude writes only `transform(rows) -> list[dict]`, and an
  AST check limits imports to `math`/`statistics` and blocks `open`/`exec`/dunder access.
  - This is a guardrail, **not a sandbox**.
  - The runner does all DB access and provenance stamping.
  - The runner also validates output (known `claim_id`, score in [0, 1]).
- **Agent identity.** Each record carries name + declared version + the git SHA of this project
  (`-dirty` if there are uncommitted changes, `uncommitted` if there are no commits yet).
  Commit the project for meaningful SHAs.
- **Retention and archival.** Temporal deletes histories after the namespace retention period,
  and audits can come years later. In production, enable
  [Temporal archival](https://docs.temporal.io/clusters#archival). `lineage.py` reports when a
  run's history is no longer on the server; that is where you'd read the archive instead.
- **The audit query is plain SQL**, for example "everything a given run wrote":
  `SELECT * FROM claim_summaries WHERE _provenance @> '{"run": "<run_id>"}'` (GIN-indexed).

## Tests

```bash
uv run pytest -q
```

- Uses the Temporal time-skipping test server (downloaded automatically by `temporalio`) and a
  separate `provenance_demo_test` database, rebuilt on each run.
- Set `TEST_DATABASE_URL` to override the database. Tests are skipped if Postgres is unreachable.

## License

MIT. See [LICENSE](LICENSE). The sample data is synthetic; this is a demo, not a validated system for regulated use.
