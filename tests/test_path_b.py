import pytest

from provenance_demo import config, db
from provenance_demo.activities.codegen import InvalidGeneratedCode, read_trailers, validate_transform
from provenance_demo.llm import MOCK_TRANSFORM
from provenance_demo.workflows import CodegenWorkflow, JobParams, RiskScoreJobWorkflow

from .conftest import wf_id


async def _codegen(client):
    h = await client.start_workflow(CodegenWorkflow.run, "test spec", id=wf_id("codegen"),
                                    task_queue=config.TASK_QUEUE)
    return h, await h.result()


async def test_commit_trailers_point_to_agent_run(client):
    h, res = await _codegen(client)
    t = read_trailers(config.generated_repo(), res.commit)
    assert t["Temporal-Workflow-Id"] == h.id
    assert t["Temporal-Run-Id"] == h.result_run_id
    assert t["Agent"] == "codegen@1"
    assert t["Agent-Commit"]
    assert t["Agent-Model"] == "mock"


async def test_job_records_carry_code_commit_and_old_commits_stay_reproducible(client):
    _, first = await _codegen(client)
    _, second = await _codegen(client)  # a later agent run -> a newer commit
    assert first.commit != second.commit

    jh = await client.start_workflow(RiskScoreJobWorkflow.run, JobParams(commit=first.commit),
                                     id=wf_id("riskjob"), task_queue=config.TASK_QUEUE)
    job = await jh.result()
    assert job.rows_written == 4

    rows = db.fetch_all("SELECT * FROM claim_risk_scores WHERE id = ANY(%s) ORDER BY claim_id",
                        (job.row_ids,))
    for r in rows:
        p = r["_provenance"]
        assert p["code"]["commit"] == first.commit  # the code that actually ran
        assert p["code"]["path"] == "transforms/risk_score.py"
        assert p["wf"] == jh.id and p["run"] == jh.result_run_id
        assert f"claims:{r['claim_id']}" in p["sources"]
    claim4 = rows[-1]
    assert claim4["_provenance"]["sources"] == [
        "claims:4", "orders:1200", "orders:1201", "orders:1202", "patients:79"]
    assert float(claim4["score"]) > 0.5


@pytest.mark.parametrize("code", [
    "import os\ndef transform(rows): return []\n",
    "def transform(rows): return open('/etc/passwd').read()\n",
    "def transform(rows): return ().__class__.__bases__\n",
    "def not_transform(rows): return []\n",
])
def test_validator_rejects_unsafe_or_wrong_code(code):
    with pytest.raises(InvalidGeneratedCode):
        validate_transform(code)


def test_validator_accepts_mock_transform():
    validate_transform(MOCK_TRANSFORM)
