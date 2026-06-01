#!/usr/bin/env python3
"""
NL-to-SQL Feasibility Evaluator Tool — CAI Studio Agent tool template.

Follows the agentic_kie_tool pattern:
  UserParameters  — LLM connection config (set once per deployment)
  ToolParameters  — per-invocation natural language question
  run_tool()      — instantiates NLToSQLEvaluator and calls evaluate()
  OUTPUT_KEY      — key used by CAI Studio Agent to extract tool output

The tool returns a JSON string with:
  verdict         — FEASIBLE | NOT_FEASIBLE | CLARIFY
  message         — one-sentence explanation for the calling agent
  suggested_tables — tables likely needed to answer the question
  schema_context  — full schema text to pass to the SQL Generator agent

Usage (CLI / CAI Studio Agent invocation):
  python tool.py \\
    --user-params '{"cai_url":"https://...","cai_model":"...","cai_api_key":"..."}' \\
    --tool-params '{"question":"Which customers have a DELINQUENT loan?"}'
"""

import argparse
import json
import os
import sys

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Make lib importable when running as a script from the tool directory
# ---------------------------------------------------------------------------
_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOL_DIR not in sys.path:
    sys.path.insert(0, _TOOL_DIR)

from lib.workflow import NLToSQLEvaluator  # noqa: E402

# ---------------------------------------------------------------------------
# Output key consumed by CAI Studio Agent runtime
# ---------------------------------------------------------------------------
OUTPUT_KEY = "tool_output"


# ---------------------------------------------------------------------------
# Parameter models
# ---------------------------------------------------------------------------

class UserParameters(BaseModel):
    """LLM connection configuration — set once per CAI Studio deployment."""

    cai_url: str = Field(
        default='',
        description='Cloudera AI Inference endpoint (full URL including /v1)',
    )
    cai_model: str = Field(
        default='meta-llama/Meta-Llama-3-8B-Instruct',
        description='Model ID served by Cloudera AI Inference',
    )
    cai_api_key: str = Field(
        default='',
        description='API key / CDP JWT token for Cloudera AI Inference',
    )


class ToolParameters(BaseModel):
    """Per-invocation parameters — provided by the Query Evaluator Agent each call."""

    question: str = Field(
        description='Natural language question to evaluate for SQL feasibility',
    )


# ---------------------------------------------------------------------------
# run_tool — entry point called by CAI Studio Agent runtime
# ---------------------------------------------------------------------------

def run_tool(config: UserParameters, args: ToolParameters) -> str:
    """
    Evaluate whether the question can be translated to SQL against banking_chatbot_db.
    Returns a JSON string with verdict, message, suggested_tables, and schema_context.
    """
    evaluator = NLToSQLEvaluator(
        cai_url=config.cai_url or os.getenv('CAI_URL', ''),
        cai_model=config.cai_model,
        cai_api_key=config.cai_api_key or os.getenv('CDP_TOKEN', ''),
    )
    result = evaluator.evaluate(args.question)
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='NL-to-SQL Feasibility Evaluator Tool — CAI Studio Agent'
    )
    parser.add_argument('--user-params', required=True,
                        help='JSON string with UserParameters fields')
    parser.add_argument('--tool-params', required=True,
                        help='JSON string with ToolParameters fields')
    parsed = parser.parse_args()

    config = UserParameters(**json.loads(parsed.user_params))
    args   = ToolParameters(**json.loads(parsed.tool_params))

    output = run_tool(config, args)
    print(OUTPUT_KEY, output)


if __name__ == '__main__':
    main()
