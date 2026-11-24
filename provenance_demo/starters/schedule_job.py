"""Path B, step 2: run the agent-written code, independently of the agent.

    # run once now
    uv run python -m provenance_demo.starters.schedule_job --commit <sha> --once
    # or create a Temporal Schedule (default every 60 min) and trigger it immediately
    uv run python -m provenance_demo.starters.schedule_job --commit <sha> --every 60
"""

import argparse
import asyncio
import uuid
from datetime import timedelta

from temporalio.client import (
    Schedule, ScheduleActionStartWorkflow, ScheduleAlreadyRunningError,
    ScheduleIntervalSpec, ScheduleSpec, ScheduleUpdate,
)

from .. import config
from ..temporal_client import connect
from ..workflows import JobParams, RiskScoreJobWorkflow

SCHEDULE_ID = "risk-score-job"


async def main(args) -> None:
    client = await connect()
    params = JobParams(commit=args.commit)
    if args.once:
        wf_id = f"riskjob-{uuid.uuid4().hex[:6]}"
        res = await client.execute_workflow(RiskScoreJobWorkflow.run, params, id=wf_id,
                                            task_queue=config.TASK_QUEUE)
        print(f"{wf_id}: code @{res.commit[:12]} wrote {res.rows_written} rows: ids={res.row_ids}")
        return

    schedule = Schedule(
        action=ScheduleActionStartWorkflow(RiskScoreJobWorkflow.run, params,
                                           id="riskjob", task_queue=config.TASK_QUEUE),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(minutes=args.every))]),
    )
    try:
        handle = await client.create_schedule(SCHEDULE_ID, schedule)
        print(f"created schedule {SCHEDULE_ID} (every {args.every} min) for commit {args.commit[:12]}")
    except ScheduleAlreadyRunningError:
        handle = client.get_schedule_handle(SCHEDULE_ID)
        await handle.update(lambda _: ScheduleUpdate(schedule=schedule))
        print(f"updated schedule {SCHEDULE_ID} to commit {args.commit[:12]}")
    await handle.trigger()
    print("triggered one run now; see it in the Temporal UI or with `temporal schedule describe`")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--commit", required=True)
    p.add_argument("--once", action="store_true")
    p.add_argument("--every", type=int, default=60, help="schedule interval, minutes")
    asyncio.run(main(p.parse_args()))
