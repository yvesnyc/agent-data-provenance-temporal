"""Path B, step 2: agent-written code runs (on a schedule, long after the agent
finished) and writes records stamped with the git commit of that code.

The generated code is loaded from git *at the exact commit*, never from the
working tree, so the commit on each record is the code that actually ran.
The runner, not the generated code, does all DB reads/writes and provenance.
"""

from dataclasses import dataclass
from datetime import date

from temporalio import activity

from .. import config, db, provenance
from .codegen import git, validate_transform

INPUT_SQL = """
SELECT c.id AS claim_id, c.patient_id, p.plan, p.dob, c.status, c.order_ids,
       array_agg(o.code ORDER BY o.id) AS order_codes,
       sum(o.amount)::float AS total_amount,
       count(o.id)::int AS n_orders
FROM claims c
JOIN patients p ON p.id = c.patient_id
JOIN orders o ON o.id = ANY (c.order_ids)
GROUP BY c.id, p.id
ORDER BY c.id
"""


@dataclass
class JobInput:
    commit: str
    path: str
    workflow_id: str
    run_id: str


@dataclass
class JobResult:
    commit: str
    rows_written: int
    row_ids: list[int]


def _age(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def load_transform(commit: str, path: str):
    repo = config.generated_repo()
    code = git(repo, "show", f"{commit}:{path}")
    validate_transform(code)
    namespace: dict = {"__name__": f"agent_transform_{commit[:12]}"}
    exec(compile(code, f"{commit[:12]}:{path}", "exec"), namespace)
    return namespace["transform"]


@activity.defn
def run_transform(inp: JobInput) -> JobResult:
    transform = load_transform(inp.commit, inp.path)

    rows = db.fetch_all(INPUT_SQL)
    today = date.today()
    inputs, sources_by_claim = [], {}
    for r in rows:
        inputs.append({
            "claim_id": r["claim_id"], "patient_id": r["patient_id"], "plan": r["plan"],
            "status": r["status"], "order_ids": list(r["order_ids"]),
            "order_codes": list(r["order_codes"]), "total_amount": r["total_amount"],
            "n_orders": r["n_orders"], "patient_age": _age(r["dob"], today),
        })
        sources_by_claim[r["claim_id"]] = (
            [f"claims:{r['claim_id']}", f"patients:{r['patient_id']}"]
            + [f"orders:{oid}" for oid in r["order_ids"]]
        )

    outputs = transform([dict(i) for i in inputs])  # copies: the transform can't mutate ours

    ids = []
    with db.connect() as conn:
        for out in outputs:
            claim_id = out["claim_id"]
            if claim_id not in sources_by_claim:
                raise ValueError(f"transform returned unknown claim_id {claim_id!r}")
            score = float(out["score"])
            if not 0.0 <= score <= 1.0:
                raise ValueError(f"score out of range for claim {claim_id}: {score}")
            prov = provenance.for_code_run(
                config.generated_repo().name, inp.commit, inp.path,
                inp.workflow_id, inp.run_id, sources_by_claim[claim_id],
            )
            row = conn.execute(
                """
                INSERT INTO claim_risk_scores (claim_id, score, reasons, _provenance)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (claim_id, prov_run)
                DO UPDATE SET score = EXCLUDED.score, reasons = EXCLUDED.reasons,
                              _provenance = EXCLUDED._provenance
                RETURNING id
                """,
                (claim_id, score, db.jsonb(list(out["reasons"])), db.jsonb(prov)),
            ).fetchone()
            ids.append(row["id"])
    activity.logger.info("transform @%s wrote %d rows", inp.commit[:12], len(ids))
    return JobResult(commit=inp.commit, rows_written=len(ids), row_ids=ids)
