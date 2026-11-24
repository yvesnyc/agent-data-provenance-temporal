"""Path A: an agent answering a request writes records during its own run.

The workflow passes its workflow ID and run ID in; the agent stamps them on
the record together with its identity and the source rows it actually read.
"""

from dataclasses import dataclass

from temporalio import activity

from .. import config, db, llm, provenance


@dataclass
class SummarizeInput:
    claim_id: int
    workflow_id: str
    run_id: str


@dataclass
class SummarizeResult:
    summary_id: int
    summary: str
    provenance: dict
    request_ids: list[str]  # Anthropic API request IDs: kept in Temporal history, not on the record


@activity.defn
def summarize_claim(inp: SummarizeInput) -> SummarizeResult:
    sources: list[str] = []  # every row the agent reads is recorded here

    def get_claim(claim_id: int) -> dict:
        row = db.fetch_one(
            "SELECT id, patient_id, order_ids, status, submitted_at FROM claims WHERE id = %s",
            (claim_id,),
        )
        if row is None:
            raise ValueError(f"claim {claim_id} not found")
        sources.append(f"claims:{claim_id}")
        return row

    def get_patient(patient_id: int) -> dict:
        # Name deliberately not selected: the model doesn't need it.
        row = db.fetch_one("SELECT id, dob, plan FROM patients WHERE id = %s", (patient_id,))
        if row is None:
            raise ValueError(f"patient {patient_id} not found")
        sources.append(f"patients:{patient_id}")
        return row

    def get_order(order_id: int) -> dict:
        row = db.fetch_one(
            "SELECT id, patient_id, code, description, amount, ordered_at FROM orders WHERE id = %s",
            (order_id,),
        )
        if row is None:
            raise ValueError(f"order {order_id} not found")
        sources.append(f"orders:{order_id}")
        return row

    activity.logger.info("summarizer running for claim %s (llm=%s)", inp.claim_id, config.llm_mode())
    summary, call = llm.run_summarizer(
        inp.claim_id, {"get_claim": get_claim, "get_patient": get_patient, "get_order": get_order}
    )

    prov = provenance.for_agent_run(config.SUMMARIZER, call.model, inp.workflow_id, inp.run_id,
                                    sources)
    with db.connect() as conn:
        # Upsert on (claim_id, run): an activity retry within the same run
        # replaces its own row instead of adding a duplicate.
        row = conn.execute(
            """
            INSERT INTO claim_summaries (claim_id, summary, _provenance)
            VALUES (%s, %s, %s)
            ON CONFLICT (claim_id, prov_run)
            DO UPDATE SET summary = EXCLUDED.summary, _provenance = EXCLUDED._provenance
            RETURNING id
            """,
            (inp.claim_id, summary, db.jsonb(prov)),
        ).fetchone()
    return SummarizeResult(summary_id=row["id"], summary=summary, provenance=prov,
                           request_ids=call.request_ids)
