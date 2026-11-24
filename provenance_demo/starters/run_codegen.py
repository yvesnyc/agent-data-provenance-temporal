"""Path B, step 1: the codegen agent writes a risk-score transform and commits it.

    uv run python -m provenance_demo.starters.run_codegen ["scoring spec ..."]
"""

import asyncio
import sys
import uuid

from .. import config
from ..activities.codegen import read_trailers
from ..temporal_client import connect
from ..workflows import CodegenWorkflow

DEFAULT_SPEC = (
    "Score claims for manual-review priority. Weight high totals (> $1000), "
    "high-cost imaging/ED procedure codes, many orders on one claim, and age 65+."
)


async def main(spec: str) -> None:
    client = await connect()
    wf_id = f"codegen-risk-score-{uuid.uuid4().hex[:6]}"
    res = await client.execute_workflow(CodegenWorkflow.run, spec, id=wf_id,
                                        task_queue=config.TASK_QUEUE)
    print(f"committed {res.path} in {res.repo} at {res.commit}")
    for k, v in read_trailers(config.generated_repo(), res.commit).items():
        print(f"  {k}: {v}")
    print("\nnext: uv run python -m provenance_demo.starters.schedule_job "
          f"--commit {res.commit} --once")


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or DEFAULT_SPEC))
