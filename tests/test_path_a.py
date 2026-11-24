from provenance_demo import config, db
from provenance_demo.workflows import ClaimSummaryWorkflow

from .conftest import wf_id


async def test_summary_record_carries_agent_run_provenance(client):
    handle = await client.start_workflow(ClaimSummaryWorkflow.run, 4, id=wf_id("claims-4"),
                                         task_queue=config.TASK_QUEUE)
    res = await handle.result()

    row = db.fetch_one("SELECT * FROM claim_summaries WHERE id = %s", (res.summary_id,))
    prov = row["_provenance"]
    assert prov["wf"] == handle.id
    assert prov["run"] == handle.result_run_id
    assert prov["agent"]["name"] == "summarizer" and prov["agent"]["version"] == "3"
    assert prov["agent"]["commit"]
    assert prov["agent"]["model"] == "mock"
    assert res.request_ids == []  # mock makes no API calls
    # Exactly the rows the agent read: the claim, its patient, its three orders.
    assert prov["sources"] == ["claims:4", "orders:1200", "orders:1201", "orders:1202", "patients:79"]
    assert row["claim_id"] == 4 and "Claim 4" in row["summary"]


async def test_each_run_gets_its_own_record(client):
    ids = []
    for _ in range(2):
        res = await client.execute_workflow(ClaimSummaryWorkflow.run, 1, id=wf_id("claims-1"),
                                            task_queue=config.TASK_QUEUE)
        ids.append(res.summary_id)
    rows = db.fetch_all("SELECT _provenance->>'run' AS run FROM claim_summaries WHERE id = ANY(%s)", (ids,))
    assert len({r["run"] for r in rows}) == 2
