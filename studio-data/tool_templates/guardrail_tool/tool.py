"""
Jailbreak and input-safety guardrail for Agent Studio workflows.

Guards natural-language inputs against prompt injection, jailbreak attempts,
and domain-specific abuse (SQL DDL, privilege escalation, table access
violations, sensitive column access) before they reach downstream agents.

Place this as the first task in any workflow. A BLOCK verdict stops execution
with a safe, structured error message. An ALLOW verdict passes the input
unchanged to Agent 1.

Three detection layers (each independently configurable):
  Layer 1 — Regex heuristics       < 1 ms,  no dependencies
  Layer 2 — ML classifier          ~100 ms, transformers (local) or CAII (api)
  Layer 3 — Domain policy          < 5 ms,  no dependencies

Layers run in sequence; the first BLOCK short-circuits the rest.
"""

from __future__ import annotations

import json
import re
import argparse
from typing import Literal, Optional

from pydantic import BaseModel, Field

import layer1
import layer2
import layer3
from models import GuardrailResult, LayerResult


# ---------------------------------------------------------------------------
# Configuration models
# ---------------------------------------------------------------------------

class UserParameters(BaseModel):
    mode: Literal["local", "api"] = Field(
        default="local",
        description=(
            "'local' — Prompt-Guard-86M on CPU via transformers (no external service, ~100 ms); "
            "'api'   — remote Llama Guard 3 via CAII OpenAI-compatible endpoint (~30 ms on GPU)"
        ),
    )
    classifier_endpoint: Optional[str] = Field(
        default=None,
        description="CAII base URL for remote classifier, e.g. 'https://caii.example.com/v1' (required when mode='api')",
    )
    classifier_api_key: Optional[str] = Field(
        default=None,
        description="Bearer token for the remote classifier endpoint",
    )
    api_type: Literal["openai_compatible", "classification"] = Field(
        default="openai_compatible",
        description=(
            "'openai_compatible' — Llama Guard 3 via CAII chat/completions API; "
            "'classification'   — custom endpoint returning {label, score}"
        ),
    )
    domain: Literal["text_to_sql", "code_generation", "general"] = Field(
        default="general",
        description=(
            "Workflow domain — activates domain-specific patterns in Layer 1 "
            "and policy checks in Layer 3. "
            "'text_to_sql' adds SQL DDL, privilege escalation, and table allowlist checks."
        ),
    )
    classifier_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Minimum classifier confidence score to trigger a Layer 2 block (default 0.85)",
    )
    custom_block_patterns: Optional[str] = Field(
        default=None,
        description=(
            "Pipe-separated additional regex patterns to always block, applied before Layer 1. "
            "Example: 'internal_table_x|confidential_proc_y'"
        ),
    )


class ToolParameters(BaseModel):
    input_text: str = Field(
        description="The user's natural-language input to evaluate before passing to downstream agents.",
    )
    action_on_block: Literal["raise_error", "return_verdict"] = Field(
        default="return_verdict",
        description=(
            "'raise_error'    — raise a ValueError to halt the CrewAI task immediately; "
            "'return_verdict' — return the structured BLOCK verdict so the calling agent can handle it"
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_custom_patterns(input_text: str, patterns_str: str) -> LayerResult:
    for raw in patterns_str.split("|"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            if re.search(raw, input_text, re.IGNORECASE):
                return LayerResult(
                    blocked=True,
                    threat_categories=["custom_pattern"],
                    confidence=1.0,
                    reason=f"custom block pattern matched: {raw!r}",
                )
        except re.error:
            pass  # silently skip malformed patterns
    return LayerResult(blocked=False, threat_categories=[], confidence=1.0, reason=None)


def _serialise(
    layer_result: LayerResult,
    layer_num: Optional[int],
    action_on_block: str,
) -> str:
    gr = GuardrailResult(
        verdict="BLOCK" if layer_result.blocked else "ALLOW",
        threat_categories=layer_result.threat_categories,
        confidence=layer_result.confidence,
        layer_triggered=layer_num if layer_result.blocked else None,
        reason=layer_result.reason,
        safe_to_proceed=not layer_result.blocked,
    )
    output = json.dumps(gr.to_dict(), indent=2)
    if layer_result.blocked and action_on_block == "raise_error":
        raise ValueError(
            f"Guardrail BLOCK (layer {layer_num}): {layer_result.reason}\n{output}"
        )
    return output


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_tool(config: UserParameters, args: ToolParameters) -> str:
    input_text = args.input_text.strip()

    if not input_text:
        return _serialise(
            LayerResult(blocked=True, threat_categories=["empty_input"],
                        confidence=1.0, reason="empty input rejected"),
            layer_num=0,
            action_on_block=args.action_on_block,
        )

    strictness = "moderate" if config.domain == "text_to_sql" else "lenient"

    # Layer 0 — custom patterns (runs before everything else)
    if config.custom_block_patterns:
        r = _check_custom_patterns(input_text, config.custom_block_patterns)
        if r.blocked:
            return _serialise(r, layer_num=0, action_on_block=args.action_on_block)

    # Layer 1 — regex / heuristics
    r = layer1.check(input_text, config.domain, strictness)
    if r.blocked:
        return _serialise(r, layer_num=1, action_on_block=args.action_on_block)

    # Layer 2 — ML classifier
    r = layer2.check(
        input_text,
        mode=config.mode,
        threshold=config.classifier_threshold,
        classifier_endpoint=config.classifier_endpoint,
        classifier_api_key=config.classifier_api_key,
        api_type=config.api_type,
    )
    if r.blocked:
        return _serialise(r, layer_num=2, action_on_block=args.action_on_block)

    # Layer 3 — domain policy
    r = layer3.check(input_text, config.domain, None, strictness)
    if r.blocked:
        return _serialise(r, layer_num=3, action_on_block=args.action_on_block)

    # All layers passed
    return json.dumps(
        GuardrailResult(
            verdict="ALLOW",
            threat_categories=[],
            confidence=1.0,
            layer_triggered=None,
            reason=None,
            safe_to_proceed=True,
        ).to_dict(),
        indent=2,
    )


OUTPUT_KEY = "tool_output"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-params", required=True)
    parser.add_argument("--tool-params", required=True)
    cli_args = parser.parse_args()

    config = UserParameters(**json.loads(cli_args.user_params))
    params = ToolParameters(**json.loads(cli_args.tool_params))
    print(OUTPUT_KEY, run_tool(config, params))
