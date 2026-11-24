"""Runs all workflows and activities on one task queue."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.worker import Worker

from . import config, provenance
from .temporal_client import connect
from .workflows import ALL_ACTIVITIES, ALL_WORKFLOWS


async def main() -> None:
    # Pin the agent's code commit now, while the loaded code and the files on disk
    # are known to match. Every record this worker writes carries this value.
    agent_commit = provenance.agent_code_commit()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = await connect()
    logging.info("worker on %s queue=%s llm=%s db=%s agent_commit=%s", config.temporal_address(),
                 config.TASK_QUEUE, config.llm_mode(), config.database_url(), agent_commit)
    if agent_commit.endswith("-dirty") or agent_commit == "uncommitted":
        logging.warning("agent code has uncommitted changes; records will carry %r", agent_commit)
    with ThreadPoolExecutor(max_workers=10) as pool:
        worker = Worker(client, task_queue=config.TASK_QUEUE, workflows=ALL_WORKFLOWS,
                        activities=ALL_ACTIVITIES, activity_executor=pool)
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
