from provenance_demo import config, lineage
from provenance_demo.workflows import ClaimSummaryWorkflow, CodegenWorkflow, JobParams, RiskScoreJobWorkflow

from .conftest import wf_id


async def test_lineage_agent_run(client):
    res = await client.execute_workflow(ClaimSummaryWorkflow.run, 2, id=wf_id("claims-2"),
                                        task_queue=config.TASK_QUEUE)
    r = await lineage.trace("claim_summaries", res.summary_id, client=client)
    assert r["path"] == "agent-run"
    assert set(r["sources"]) == {"claims:2", "patients:77", "orders:1183"}
    assert all(r["sources"].values())
    run = r["writer_run"]
    assert run["workflow_type"] == "ClaimSummaryWorkflow"
    assert run["input"] == [2]
    assert [a["activity"] for a in run["activities"]] == ["summarize_claim"]
    assert run["final_event"] == "WORKFLOW_EXECUTION_COMPLETED"
    lineage.print_report(r)  # smoke-test the formatter


async def test_lineage_agent_written_code(client):
    cg_h = await client.start_workflow(CodegenWorkflow.run, "spec", id=wf_id("codegen"),
                                       task_queue=config.TASK_QUEUE)
    commit = (await cg_h.result()).commit
    job = await client.execute_workflow(RiskScoreJobWorkflow.run, JobParams(commit=commit),
                                        id=wf_id("riskjob"), task_queue=config.TASK_QUEUE)

    r = await lineage.trace("claim_risk_scores", job.row_ids[0], client=client)
    assert r["path"] == "agent-written-code"
    assert r["code_commit"]["commit"] == commit
    assert r["writer_run"]["workflow_type"] == "RiskScoreJobWorkflow"
    author = r["code_author_run"]
    assert author["workflow_type"] == "CodegenWorkflow"
    assert author["workflow_id"] == cg_h.id and author["run_id"] == cg_h.result_run_id
    assert [a["activity"] for a in author["activities"]] == ["generate_transform_code", "commit_transform"]
    lineage.print_report(r)
