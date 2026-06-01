"""
Tool for integrating with Cloudera AI Synthetic Data Studio (SDS).
Generates and evaluates synthetic tabular datasets from schema descriptions and example rows.
Supports custom tabular data generation via the SDS freeform endpoint.
"""

import json
import os
import argparse
import tempfile
from typing import Literal, Optional, List, Dict, Any
import requests
from pydantic import BaseModel, Field


class UserParameters(BaseModel):
    """
    Args:
        base_url (str): SDS application URL (e.g. 'https://sds-app.cml.cloudera.site').
        api_key (str): CDSW/CML API v2 key for Bearer authentication.
        model_id (str): LLM model ID to use for generation/evaluation.
        inference_type (str): 'aws_bedrock', 'CAII', or 'OpenAI'.
        caii_endpoint (str): Required when inference_type is 'CAII'.
        temperature (float): Generation temperature (0.0–1.0).
        max_tokens (int): Maximum tokens per generation call.
        timeout_seconds (int): HTTP request timeout.
    """
    base_url: str = Field(description="SDS application base URL")
    api_key: str = Field(description="CML API v2 key (Bearer token)")
    model_id: str = Field(
        default="us.anthropic.claude-3-5-haiku-20241022-v1:0",
        description="LLM model ID for generation and evaluation"
    )
    inference_type: str = Field(
        default="aws_bedrock",
        description="Inference provider: 'aws_bedrock', 'CAII', or 'OpenAI'"
    )
    caii_endpoint: Optional[str] = Field(
        default=None,
        description="CAII endpoint URL (required when inference_type is 'CAII')"
    )
    temperature: float = Field(default=0.3, ge=0.0, le=2.0, description="Generation temperature")
    max_tokens: int = Field(default=8192, ge=1, description="Maximum tokens per call")
    timeout_seconds: int = Field(default=300, description="HTTP timeout in seconds")


class ToolParameters(BaseModel):
    action: Literal["generate", "evaluate", "generate_and_evaluate", "health_check"] = Field(
        description=(
            "Action to perform: "
            "'generate' — generate synthetic rows for a table from schema+examples; "
            "'evaluate' — evaluate a JSON file of generated rows for quality; "
            "'generate_and_evaluate' — generate then immediately evaluate; "
            "'health_check' — verify SDS is reachable"
        )
    )
    table_name: Optional[str] = Field(
        default=None,
        description="Name of the table to generate data for (required for generate/generate_and_evaluate)"
    )
    schema_description: Optional[str] = Field(
        default=None,
        description=(
            "Text description of the table schema: column names, types, constraints, "
            "value ranges, and any FK relationships. The richer this is, the better the output."
        )
    )
    example_rows: Optional[str] = Field(
        default=None,
        description="JSON array string of 2–5 example rows (list of dicts). Guides the generator on format and value style."
    )
    num_rows: int = Field(
        default=100,
        ge=1,
        description="Number of synthetic rows to generate"
    )
    custom_instructions: Optional[str] = Field(
        default=None,
        description="Additional generation instructions, e.g. 'Avoid repeating IDs', 'Use ISO 8601 dates', 'All amounts in USD'"
    )
    output_path: Optional[str] = Field(
        default=None,
        description="If set, save generated rows as a JSON file at this path. Required for standalone 'evaluate' action."
    )
    eval_instructions: Optional[str] = Field(
        default=None,
        description="Custom evaluation criteria, e.g. 'Check that all foreign keys are consistent', 'Verify no real names appear'"
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _headers(api_key: str) -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


def _post(url: str, payload: Dict, api_key: str, timeout: int) -> Dict:
    resp = requests.post(url, headers=_headers(api_key), json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _get(url: str, api_key: str, timeout: int) -> Dict:
    resp = requests.get(url, headers=_headers(api_key), timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def handle_health_check(base_url: str, api_key: str, timeout: int) -> str:
    try:
        result = _get(f"{base_url}/health", api_key, timeout)
        return f"SDS health check passed. Response: {json.dumps(result, indent=2)}"
    except requests.exceptions.RequestException as e:
        return f"SDS health check failed: {e}"


def _build_freeform_payload(
    config: UserParameters,
    table_name: str,
    schema_description: Optional[str],
    example_rows_parsed: Optional[List[Dict]],
    num_rows: int,
    custom_instructions: Optional[str],
) -> Dict[str, Any]:
    """Build the /synthesis/freeform request payload."""
    base_prompt = (
        f"Generate realistic synthetic data for the database table '{table_name}'. "
        "The data must respect the schema, value ranges, and constraints described. "
        "Do NOT use real names, personal identifiers, or any actual customer data. "
        "Return each row as a JSON object with exactly the columns defined in the schema."
    )
    if custom_instructions:
        base_prompt += f"\n\nAdditional instructions: {custom_instructions}"

    payload: Dict[str, Any] = {
        "use_case": "custom",
        "technique": "freeform",
        "model_id": config.model_id,
        "inference_type": config.inference_type,
        "topics": [table_name],
        "num_questions": num_rows,
        "is_demo": True,
        "custom_prompt": base_prompt,
        "model_params": {
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "top_p": 1.0,
            "top_k": 150,
        },
    }

    if schema_description:
        payload["schema"] = schema_description

    if example_rows_parsed:
        payload["example_custom"] = example_rows_parsed

    if config.caii_endpoint:
        payload["caii_endpoint"] = config.caii_endpoint

    return payload


def handle_generate(
    config: UserParameters,
    args: ToolParameters,
) -> tuple[str, Optional[str]]:
    """
    Call /synthesis/freeform and return (summary_string, saved_file_path_or_None).
    """
    if not args.table_name:
        return "Error: 'table_name' is required for the 'generate' action.", None

    example_rows_parsed: Optional[List[Dict]] = None
    if args.example_rows:
        try:
            example_rows_parsed = json.loads(args.example_rows)
            if not isinstance(example_rows_parsed, list):
                return "Error: 'example_rows' must be a JSON array of objects.", None
        except json.JSONDecodeError as e:
            return f"Error: Could not parse 'example_rows' as JSON: {e}", None

    payload = _build_freeform_payload(
        config,
        args.table_name,
        args.schema_description,
        example_rows_parsed,
        args.num_rows,
        args.custom_instructions,
    )

    response = _post(f"{config.base_url.rstrip('/')}/synthesis/freeform", payload, config.api_key, config.timeout_seconds)

    # The freeform endpoint returns data in various shapes depending on SDS version.
    # Normalise to a list of rows.
    rows = _extract_rows(response, args.table_name)

    saved_path: Optional[str] = None
    if args.output_path:
        os.makedirs(os.path.dirname(os.path.abspath(args.output_path)), exist_ok=True)
        with open(args.output_path, "w") as f:
            json.dump(rows, f, indent=2)
        saved_path = args.output_path

    preview = json.dumps(rows[:3], indent=2) if rows else "(no rows returned)"
    summary_lines = [
        f"Table: {args.table_name}",
        f"Rows generated: {len(rows)}",
        f"Columns: {list(rows[0].keys()) if rows else 'N/A'}",
        f"Preview (first 3 rows):\n{preview}",
    ]
    if saved_path:
        summary_lines.append(f"Saved to: {saved_path}")

    return "\n".join(summary_lines), saved_path


def _extract_rows(response: Any, table_name: str) -> List[Dict]:
    """Normalise SDS freeform response to a flat list of row dicts."""
    if isinstance(response, list):
        return response

    if isinstance(response, dict):
        # Some SDS versions wrap in {"data": [...]} or {"<table_name>": [...]}
        for key in ("data", "rows", "results", table_name, "generated"):
            if key in response and isinstance(response[key], list):
                return response[key]
        # Flat dict of topic→rows: {"<table_name>": [...]}
        for v in response.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        # Single row returned as a dict
        return [response]

    return []


def handle_evaluate(
    config: UserParameters,
    import_path: str,
    eval_instructions: Optional[str],
) -> str:
    """
    Call /synthesis/evaluate_freeform with an existing JSON file.
    """
    if not os.path.exists(import_path):
        return f"Error: File not found at '{import_path}'. Generate the data first."

    base_eval_prompt = (
        "Evaluate the quality of this synthetic tabular dataset. "
        "Assess: (1) schema adherence — do all rows have expected columns and types? "
        "(2) value realism — are values plausible for a financial/banking context? "
        "(3) PII safety — confirm no real names, IDs, or contact details appear. "
        "(4) referential integrity — are FK values self-consistent within the dataset?"
    )
    if eval_instructions:
        base_eval_prompt += f"\n\nAdditional criteria: {eval_instructions}"

    payload: Dict[str, Any] = {
        "use_case": "custom",
        "technique": "freeform",
        "model_id": config.model_id,
        "inference_type": config.inference_type,
        "import_path": import_path,
        "is_demo": True,
        "custom_prompt": base_eval_prompt,
        "model_params": {
            "temperature": 0.0,
            "max_tokens": config.max_tokens,
            "top_p": 1.0,
            "top_k": 150,
        },
    }
    if config.caii_endpoint:
        payload["caii_endpoint"] = config.caii_endpoint

    response = _post(
        f"{config.base_url.rstrip('/')}/synthesis/evaluate_freeform",
        payload,
        config.api_key,
        config.timeout_seconds,
    )

    if isinstance(response, dict):
        return f"Evaluation result:\n{json.dumps(response, indent=2)}"
    return f"Evaluation result:\n{response}"


def handle_generate_and_evaluate(config: UserParameters, args: ToolParameters) -> str:
    """Generate rows, save to a temp file, evaluate, return combined report."""
    # Determine where to save generated data
    if args.output_path:
        save_path = args.output_path
        cleanup = False
    else:
        tmp = tempfile.NamedTemporaryFile(
            suffix=".json",
            prefix=f"sds_{args.table_name or 'table'}_",
            delete=False,
        )
        save_path = tmp.name
        tmp.close()
        cleanup = True

    # Override output_path so handle_generate saves to that location
    gen_args = args.model_copy(update={"output_path": save_path})
    gen_summary, saved = handle_generate(config, gen_args)

    if saved is None:
        # Generation failed — gen_summary contains the error
        if cleanup and os.path.exists(save_path):
            os.unlink(save_path)
        return gen_summary

    eval_summary = handle_evaluate(config, save_path, args.eval_instructions)

    if cleanup:
        os.unlink(save_path)

    return "\n\n---\n\n".join([gen_summary, eval_summary])


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_tool(config: UserParameters, args: ToolParameters) -> str:
    try:
        base_url = config.base_url.rstrip("/")

        if args.action == "health_check":
            return handle_health_check(base_url, config.api_key, config.timeout_seconds)

        elif args.action == "generate":
            summary, _ = handle_generate(config, args)
            return summary

        elif args.action == "evaluate":
            if not args.output_path:
                return "Error: 'output_path' must point to an existing JSON file for the 'evaluate' action."
            return handle_evaluate(config, args.output_path, args.eval_instructions)

        elif args.action == "generate_and_evaluate":
            return handle_generate_and_evaluate(config, args)

        else:
            return f"Error: Unknown action '{args.action}'."

    except requests.exceptions.ConnectionError:
        return f"Error: Could not connect to SDS at '{config.base_url}'. Verify the URL and that the application is running."
    except requests.exceptions.Timeout:
        return f"Error: Request to SDS timed out after {config.timeout_seconds}s. Try increasing timeout_seconds or reducing num_rows."
    except requests.exceptions.HTTPError as e:
        return f"Error: SDS returned HTTP {e.response.status_code}: {e.response.text[:500]}"
    except Exception as e:
        return f"Tool execution failed: {e}"


OUTPUT_KEY = "tool_output"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-params", required=True, help="JSON string for tool configuration")
    parser.add_argument("--tool-params", required=True, help="JSON string for tool arguments")
    cli_args = parser.parse_args()

    config = UserParameters(**json.loads(cli_args.user_params))
    params = ToolParameters(**json.loads(cli_args.tool_params))

    output = run_tool(config, params)
    print(OUTPUT_KEY, output)
