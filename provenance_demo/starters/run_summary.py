"""Path A: ask the summarizer agent to summarize one or more claims.

    uv run python -m provenance_demo.starters.run_summary 1 2 4
"""

import asyncio
import json
import sys

from .. import config
from ..temporal_client import connect
from ..workflows import ClaimSummaryWorkflow


async def main(claim_ids: list[int]) -> None:
    client = await connect()
    handles = [
        await client.start_workflow(
            ClaimSummaryWorkflow.run, cid,
            id=f"claims-{cid}", task_queue=config.TASK_QUEUE,
        )
        for cid in claim_ids
    ]
    for h in handles:
        res = await h.result()
        print(f"claim_summaries.id={res.summary_id}  wf={h.id}  run={h.result_run_id}")
        print(f"  _provenance: {json.dumps(res.provenance)}")
        print(f"  summary: {res.summary}\n")


if __name__ == "__main__":
    ids = [int(a) for a in sys.argv[1:]] or [1]
    asyncio.run(main(ids))
