"""LLM layer: Claude when credentials are configured, deterministic mock otherwise.

Only called from Temporal *activities* (never workflows): LLM calls are
non-deterministic, and Temporal records each activity's result in history.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config

ToolFn = Callable[..., Any]

# --------------------------------------------------------------------------- #
# Summarizer agent (Path A): a small tool-use loop. The tools read source rows;
# whatever the agent actually reads becomes the record's `sources`.
# --------------------------------------------------------------------------- #

SUMMARIZER_SYSTEM = (
    "You are a claims-review assistant at a health insurer. Use the tools to read the "
    "claim, the patient, and every order on the claim, then write a 3-5 sentence plain-"
    "English summary for a human reviewer: what was billed, total amount, anything that "
    "warrants review. Do not invent facts that are not in the tool results. Refer to the "
    "patient only by patient ID, never by name. Reply with the summary text only."
)

SUMMARIZER_TOOLS = [
    {
        "name": "get_claim",
        "description": "Fetch a claim by ID. Returns patient_id, order_ids, status, submitted_at.",
        "input_schema": {
            "type": "object",
            "properties": {"claim_id": {"type": "integer"}},
            "required": ["claim_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_patient",
        "description": "Fetch a patient by ID. Returns dob and insurance plan.",
        "input_schema": {
            "type": "object",
            "properties": {"patient_id": {"type": "integer"}},
            "required": ["patient_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_order",
        "description": "Fetch an order (billed procedure) by ID. Returns code, description, amount.",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "integer"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


class LLMRefusal(Exception):
    pass


MOCK_MODEL = "mock"


@dataclass
class LLMCall:
    """What produced an LLM output. `model` goes on the record (compact);
    `request_ids` go into Temporal history via the activity result."""
    model: str
    request_ids: list[str] = field(default_factory=list)


def _client():
    import anthropic

    return anthropic.Anthropic()


def _create(client, **kwargs):
    """messages.create on Opus 5.5 with server-side refusal fallbacks enabled."""
    resp = client.beta.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        **kwargs,
    )
    if resp.stop_reason == "refusal":
        cat = resp.stop_details.category if resp.stop_details else None
        raise LLMRefusal(f"model declined (category={cat}, request_id={resp._request_id})")
    return resp


def run_summarizer(claim_id: int, tools: dict[str, ToolFn]) -> tuple[str, LLMCall]:
    if config.llm_mode() == "mock":
        return _mock_summarizer(claim_id, tools), LLMCall(model=MOCK_MODEL)

    client = _client()
    request_ids: list[str] = []
    messages: list[dict] = [
        {"role": "user", "content": f"Summarize claim {claim_id} for the review queue."}
    ]
    for _ in range(12):
        resp = _create(
            client,
            system=SUMMARIZER_SYSTEM,
            tools=SUMMARIZER_TOOLS,
            output_config={"effort": "medium"},
            messages=messages,
        )
        request_ids.append(resp._request_id)
        # Pass content back unchanged (thinking blocks included).
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            if not text:
                raise RuntimeError(f"empty summary (stop_reason={resp.stop_reason})")
            # resp.model is the model that actually answered (the fallback model, if one ran).
            return text, LLMCall(model=resp.model, request_ids=request_ids)
        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            try:
                out = tools[block.name](**block.input)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out, default=str)})
            except Exception as e:  # report tool errors to the model, don't drop them
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": f"error: {e}", "is_error": True})
        messages.append({"role": "user", "content": results})
    raise RuntimeError("summarizer exceeded max tool iterations")


def _mock_summarizer(claim_id: int, tools: dict[str, ToolFn]) -> str:
    claim = tools["get_claim"](claim_id=claim_id)
    patient = tools["get_patient"](patient_id=claim["patient_id"])
    orders = [tools["get_order"](order_id=oid) for oid in claim["order_ids"]]
    total = sum(float(o["amount"]) for o in orders)
    items = "; ".join(f"{o['code']} {o['description']} (${float(o['amount']):.2f})" for o in orders)
    flag = " High total; recommend clinical review." if total > 1000 else ""
    return (
        f"[mock] Claim {claim_id} for patient {claim['patient_id']} ({patient['plan']}), "
        f"status {claim['status']}. Billed: {items}. Total ${total:.2f}.{flag}"
    )


# --------------------------------------------------------------------------- #
# Codegen agent (Path B): writes a single constrained `transform` function.
# --------------------------------------------------------------------------- #

TRANSFORM_CONTRACT = '''\
Write a Python module that defines exactly one function:

    def transform(rows: list[dict]) -> list[dict]:

Each input row describes one insurance claim and has keys:
    claim_id (int), patient_id (int), plan (str), status (str),
    order_ids (list[int]), order_codes (list[str]), total_amount (float),
    n_orders (int), patient_age (int)

Return one output dict per input row with keys:
    claim_id (int)            -- copied from input
    score (float)             -- between 0 and 1, higher = riskier
    reasons (list[str])       -- short human-readable reasons for the score

Rules: pure function, deterministic, no I/O, no network, no database access.
You may import only `math` and `statistics`. Output only the Python code, no
markdown fences, no explanation.'''

ALLOWED_IMPORTS = {"math", "statistics"}


def generate_transform(spec: str) -> tuple[str, LLMCall]:
    if config.llm_mode() == "mock":
        return MOCK_TRANSFORM, LLMCall(model=MOCK_MODEL)
    resp = _create(
        _client(),
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": f"{TRANSFORM_CONTRACT}\n\nScoring spec: {spec}"}],
    )
    code = "".join(b.text for b in resp.content if b.type == "text")
    return _strip_fences(code).strip() + "\n", LLMCall(model=resp.model, request_ids=[resp._request_id])


def _strip_fences(text: str) -> str:
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    return m.group(1) if m else text


MOCK_TRANSFORM = '''\
"""Claim risk score transform (mock codegen output)."""

import math

HIGH_RISK_CODES = {"99285", "70450", "78452"}


def transform(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        reasons = []
        score = 0.1
        if r["total_amount"] > 1000:
            score += 0.35
            reasons.append(f"total ${r['total_amount']:.0f} exceeds $1000")
        risky = sorted(set(r["order_codes"]) & HIGH_RISK_CODES)
        if risky:
            score += 0.15 * len(risky)
            reasons.append("high-cost procedure codes: " + ", ".join(risky))
        if r["n_orders"] >= 3:
            score += 0.1
            reasons.append(f"{r['n_orders']} orders on one claim")
        if r["patient_age"] >= 65:
            score += 0.05
            reasons.append("patient age 65+")
        out.append({
            "claim_id": r["claim_id"],
            "score": round(min(score, 1.0), 3),
            "reasons": reasons or ["no risk factors"],
        })
    return out
'''
