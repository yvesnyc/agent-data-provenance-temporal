"""Shared fixtures: an isolated Postgres DB, a temp git repo for generated code,
the mock LLM, and an in-process Temporal test server + worker."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
import pytest_asyncio
from psycopg.conninfo import make_conninfo

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://localhost/provenance_demo_test")


@pytest.fixture(scope="session", autouse=True)
def test_env(tmp_path_factory):
    try:
        psycopg.connect(make_conninfo(TEST_DB_URL, dbname="postgres"), connect_timeout=3).close()
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not reachable: {e}", allow_module_level=True)
    os.environ["DATABASE_URL"] = TEST_DB_URL
    os.environ["LLM_MODE"] = "mock"
    os.environ["GENERATED_REPO"] = str(tmp_path_factory.mktemp("gen") / "generated_repo")
    from scripts.setup_db import apply
    apply(TEST_DB_URL, reset=True)
    yield


@pytest_asyncio.fixture(scope="session")
async def env():
    from temporalio.testing import WorkflowEnvironment
    async with await WorkflowEnvironment.start_time_skipping() as e:
        yield e


@pytest_asyncio.fixture(scope="session")
async def client(env):
    from temporalio.worker import Worker

    from provenance_demo import config
    from provenance_demo.workflows import ALL_ACTIVITIES, ALL_WORKFLOWS
    with ThreadPoolExecutor(max_workers=4) as pool:
        async with Worker(env.client, task_queue=config.TASK_QUEUE, workflows=ALL_WORKFLOWS,
                          activities=ALL_ACTIVITIES, activity_executor=pool):
            yield env.client


def wf_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"
