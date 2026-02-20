"""
Tool template for CrewAI agentic key information extraction (KIE) workflow.

Extracts invoice/receipt fields using multi-agent workflow (Paddle OCR + RolmOCR + Master).
Supports open-schema (dynamic discovery) and closed-schema (fixed target fields) modes.

Input: image path or HTTP/HTTPS URL
Output: JSON with discovered_fields, canonical, extras, decision_log
"""

import argparse
import json
import os
import tempfile
from typing import Any, Dict, Literal, Optional

import requests
from pydantic import BaseModel, Field

# Import from bundled lib (relative to this tool's directory)
try:
    from lib.workflow import CrewAIRetrievalWorkflow
except ImportError:
    # Fallback for when run from different cwd
    import sys
    _tool_dir = os.path.dirname(os.path.abspath(__file__))
    if _tool_dir not in sys.path:
        sys.path.insert(0, _tool_dir)
    from lib.workflow import CrewAIRetrievalWorkflow


class UserParameters(BaseModel):
    """
    Tool configuration (set once per deployment).

    Args:
        paddle_url: Paddle OCR endpoint URL (e.g. https://.../paddle-ocr/v1/infer)
        rolm_url: RolmOCR OpenAI-compatible endpoint URL
        jwt_token: Bearer token for OCR APIs (Paddle + Rolm)
        rolm_model: RolmOCR model name (default: reducto/RolmOCR)
        llm_model: Master agent LLM (e.g. gpt-4.1-mini)
        llm_base_url: OpenAI-compatible base URL for Master agent
        llm_api_key: API key for Master agent
        timeout_seconds: Max runtime per image (default 300)
    """

    paddle_url: str = Field(description="Paddle OCR endpoint URL")
    rolm_url: str = Field(description="RolmOCR OpenAI-compatible endpoint URL")
    jwt_token: str = Field(description="Bearer token for OCR APIs")
    rolm_model: str = Field(default="reducto/RolmOCR", description="RolmOCR model name")
    llm_model: str = Field(default="gpt-4.1-mini", description="Master agent LLM model")
    llm_base_url: str = Field(description="OpenAI-compatible base URL for Master agent")
    llm_api_key: str = Field(description="API key for Master agent")
    timeout_seconds: int = Field(default=300, description="Max runtime per image (seconds)")


class ToolParameters(BaseModel):
    action: Literal["extract"] = Field(
        default="extract",
        description="Action to perform (currently only 'extract')",
    )
    image_source: str = Field(
        description="Local file path or HTTP/HTTPS URL to invoice/receipt image",
    )
    open_schema: bool = Field(
        default=True,
        description="Enable pass-1 dynamic field discovery (default). If false, use target_fields.",
    )
    target_fields: Optional[str] = Field(
        default=None,
        description="Comma-separated target fields for closed-schema mode (e.g. total,subtotal,tax)",
    )
    paddle_threshold: float = Field(
        default=0.80,
        description="Confidence threshold for Paddle candidates in closed-schema mode",
    )
    output_mode: Literal["full", "deterministic"] = Field(
        default="deterministic",
        description="Return 'deterministic' (discovered_fields/canonical/extras) or 'full' result",
    )


def _ensure_local_image(image_source: str) -> str:
    """Download URL to temp file if needed; return local path."""
    if image_source.startswith(("http://", "https://")):
        response = requests.get(image_source, timeout=60)
        response.raise_for_status()
        suffix = ".png"
        if "content-type" in response.headers:
            ct = response.headers["content-type"].lower()
            if "jpeg" in ct or "jpg" in ct:
                suffix = ".jpg"
        fd, path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(response.content)
            return path
        except Exception:
            os.close(fd)
            raise
    if not os.path.exists(image_source):
        raise FileNotFoundError(f"Image file not found: {image_source}")
    return image_source


def run_tool(config: UserParameters, args: ToolParameters) -> str:
    try:
        if args.action != "extract":
            return f"Error: Unsupported action '{args.action}'."

        image_path = _ensure_local_image(args.image_source)
        temp_file = image_path != args.image_source

        try:
            target_fields = (
                [x.strip() for x in args.target_fields.split(",") if x.strip()]
                if args.target_fields
                else ["total", "subtotal", "tax", "date", "currency", "items"]
            )

            workflow = CrewAIRetrievalWorkflow(
                paddle_url=config.paddle_url,
                rolm_url=config.rolm_url,
                jwt_token=config.jwt_token,
                rolm_model=config.rolm_model,
                llm_model=config.llm_model,
                llm_base_url=config.llm_base_url,
                llm_api_key=config.llm_api_key,
            )

            result: Dict[str, Any] = workflow.run(
                image_path=image_path,
                target_fields=target_fields,
                paddle_threshold=args.paddle_threshold,
                open_schema=args.open_schema,
            )

            if args.output_mode == "full":
                output = json.dumps(result, indent=2, ensure_ascii=False)
            else:
                output = json.dumps(
                    result.get("deterministic_output", {}),
                    indent=2,
                    ensure_ascii=False,
                )

            return output

        finally:
            if temp_file and os.path.exists(image_path):
                try:
                    os.unlink(image_path)
                except OSError:
                    pass

    except FileNotFoundError as e:
        return f"Error: {e}"
    except requests.exceptions.RequestException as e:
        return f"Error fetching image from URL: {e}"
    except Exception as e:
        return f"Tool execution failed: {e}"


OUTPUT_KEY = "tool_output"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-params", required=True, help="JSON string for tool configuration")
    parser.add_argument("--tool-params", required=True, help="JSON string for tool arguments")
    cli_args = parser.parse_args()

    user_params_dict = json.loads(cli_args.user_params)
    tool_params_dict = json.loads(cli_args.tool_params)

    config = UserParameters(**user_params_dict)
    params = ToolParameters(**tool_params_dict)

    output = run_tool(config, params)
    print(OUTPUT_KEY, output)
