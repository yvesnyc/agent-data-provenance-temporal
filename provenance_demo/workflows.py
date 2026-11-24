"""Temporal workflows. Each passes its own workflow ID and run ID to the
activities that write data, so every record links back to this run's history."""

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .activities.codegen import (
        TRANSFORM_PATH, CommitInput, CommitResult, commit_transform, generate_transform_code,
    )
    from .activities.job_runner import JobInput, JobResult, run_transform
    from .activities.summarizer import SummarizeInput, SummarizeResult, summarize_claim

NON_RETRYABLE = ["LLMRefusal", "InvalidGeneratedCode", "ValueError"]
LLM_RETRY = RetryPolicy(maximum_attempts=3, non_retryable_error_types=NON_RETRYABLE)


# --- Path A: agent answering a request ------------------------------------ #

@workflow.defn
class ClaimSummaryWorkflow:
    @workflow.run
    async def run(self, claim_id: int) -> SummarizeResult:
        info = workflow.info()
        return await workflow.execute_activity(
            summarize_claim,
            SummarizeInput(claim_id=claim_id, workflow_id=info.workflow_id, run_id=info.run_id),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=LLM_RETRY,
        )


# --- Path B: agent writes code; the code writes data later ---------------- #

@workflow.defn
class CodegenWorkflow:
    @workflow.run
    async def run(self, spec: str) -> CommitResult:
        info = workflow.info()
        generated = await workflow.execute_activity(
            generate_transform_code, spec,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=LLM_RETRY,
        )
        return await workflow.execute_activity(
            commit_transform,
            CommitInput(code=generated.code, model=generated.model, spec=spec,
                        workflow_id=info.workflow_id, run_id=info.run_id),
            start_to_close_timeout=timedelta(seconds=30),
        )


@dataclass
class JobParams:
    commit: str
    path: str = TRANSFORM_PATH


@workflow.defn
class RiskScoreJobWorkflow:
    @workflow.run
    async def run(self, params: JobParams) -> JobResult:
        info = workflow.info()
        return await workflow.execute_activity(
            run_transform,
            JobInput(commit=params.commit, path=params.path,
                     workflow_id=info.workflow_id, run_id=info.run_id),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3, non_retryable_error_types=NON_RETRYABLE),
        )


ALL_WORKFLOWS = [ClaimSummaryWorkflow, CodegenWorkflow, RiskScoreJobWorkflow]
ALL_ACTIVITIES = [summarize_claim, generate_transform_code, commit_transform, run_transform]
