"""
PaddleOCR-VL Tool — Vision-Language OCR via OpenAI-compatible endpoint.

Calls PaddleOCR-VL (a vision-language model) through a standard
/v1/chat/completions API. Supports:
  - ocr:       Extract all text from a single image.
  - ocr_batch: Extract text from multiple images in separate requests.
  - vqa:       Ask an arbitrary question about an image (visual QA).

Input images can be local file paths or HTTP/HTTPS URLs.
The endpoint must accept the OpenAI multimodal message format
(messages[].content[] with type "image_url" and optional "text").
"""

import argparse
import base64
import json
import mimetypes
import os
from typing import Any, Dict, List, Literal, Optional

import requests
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class UserParameters(BaseModel):
    """
    Args:
        endpoint_url (str): Full URL to the chat completions endpoint
            (e.g. https://.../paddleocr/v1/chat/completions).
        model (str): Model identifier served at the endpoint.
        api_key (Optional[str]): Bearer token for authentication.
        timeout_seconds (int): HTTP request timeout.
    """

    endpoint_url: str = Field(
        description=(
            "Full chat-completions URL, "
            "e.g. https://host/paddleocr/v1/chat/completions"
        ),
    )
    model: str = Field(
        default="/home/cdsw/models/PaddleOCR-VL-1.5",
        description="Model path or identifier served at the endpoint",
    )
    api_key: Optional[str] = Field(
        default=None,
        description="Bearer token (without the 'Bearer ' prefix)",
    )
    timeout_seconds: int = Field(
        default=120,
        description="HTTP timeout in seconds (vision requests can be slow)",
    )


# ---------------------------------------------------------------------------
# Tool parameters
# ---------------------------------------------------------------------------

class ToolParameters(BaseModel):
    """
    Arguments passed by the agent when invoking this tool.
    """

    action: Literal["ocr", "ocr_batch", "vqa"] = Field(
        default="ocr",
        description=(
            "Action to perform: "
            "'ocr' extracts all text from a single image, "
            "'ocr_batch' extracts text from multiple images, "
            "'vqa' asks a custom question about an image"
        ),
    )

    image_source: Optional[str] = Field(
        default=None,
        description="Image source for 'ocr' or 'vqa': local path or HTTP(S) URL",
    )

    image_sources: Optional[List[str]] = Field(
        default=None,
        description="List of image sources for 'ocr_batch'",
    )

    prompt: Optional[str] = Field(
        default=None,
        description=(
            "Text prompt sent alongside the image. "
            "For 'ocr'/'ocr_batch' defaults to a standard extraction prompt. "
            "For 'vqa' this is required — the question to ask about the image."
        ),
    )

    output_mode: Literal["raw", "text", "lines"] = Field(
        default="text",
        description=(
            "'text' returns the model's extracted text only; "
            "'lines' returns structured JSON with line_count and a lines array; "
            "'raw' returns the full JSON response from the endpoint"
        ),
    )


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

_DEFAULT_OCR_PROMPT = (
    "Please perform OCR on this image. "
    "Extract and return ALL text content exactly as it appears, "
    "preserving the original layout and structure as much as possible."
)


def _guess_mime(source: str) -> str:
    mime_type, _ = mimetypes.guess_type(source)
    return mime_type or "image/png"


def _load_image_bytes(source: str) -> bytes:
    if source.startswith(("http://", "https://")):
        resp = requests.get(source, timeout=30)
        resp.raise_for_status()
        return resp.content
    with open(source, "rb") as fh:
        return fh.read()


def _to_data_url(source: str) -> str:
    raw = _load_image_bytes(source)
    mime = _guess_mime(source)
    b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime};base64,{b64}"


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

def _build_headers(config: UserParameters) -> Dict[str, str]:
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "accept": "application/json",
    }
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    return headers


def _build_message_content(
    image_source: str,
    prompt: str,
) -> List[Dict[str, Any]]:
    """Build the multimodal content array for one user message."""
    content: List[Dict[str, Any]] = []

    # Text prompt (if provided)
    if prompt:
        content.append({"type": "text", "text": prompt})

    # Image
    content.append({
        "type": "image_url",
        "image_url": {"url": _to_data_url(image_source)},
    })

    return content


def _call_endpoint(
    config: UserParameters,
    image_source: str,
    prompt: str,
) -> Dict[str, Any]:
    """Send a single chat-completions request for one image."""
    payload = {
        "model": config.model,
        "messages": [
            {
                "role": "user",
                "content": _build_message_content(image_source, prompt),
            }
        ],
    }

    resp = requests.post(
        config.endpoint_url,
        headers=_build_headers(config),
        json=payload,
        timeout=config.timeout_seconds,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _extract_text(response: Dict[str, Any]) -> str:
    """
    Pull the assistant's text from a standard chat-completions response.

    Expected shape:
        {"choices": [{"message": {"role": "assistant", "content": "..."}}]}
    """
    try:
        choices = response.get("choices", [])
        if choices:
            return choices[0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        pass

    # Fallback: return stringified response if shape is unexpected
    return json.dumps(response, indent=2, ensure_ascii=False)


def _text_to_lines(text: str) -> List[Dict[str, str]]:
    """Split extracted text into a structured lines array."""
    return [
        {"text": line.strip()}
        for line in text.split("\n")
        if line.strip()
    ]


def _format_output(response: Dict[str, Any], output_mode: str) -> str:
    if output_mode == "raw":
        return json.dumps(response, indent=2, ensure_ascii=False)

    text = _extract_text(response)

    if output_mode == "lines":
        lines = _text_to_lines(text)
        return json.dumps(
            {"line_count": len(lines), "lines": lines},
            indent=2, ensure_ascii=False,
        )

    return text


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_source(source: str) -> Optional[str]:
    """Return an error string if the source is invalid, else None."""
    if source.startswith(("http://", "https://")):
        return None
    if not os.path.exists(source):
        return f"Error: image file not found: {source}"
    return None


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run_tool(config: UserParameters, args: ToolParameters) -> str:
    try:
        prompt = args.prompt or _DEFAULT_OCR_PROMPT

        # ---- ocr (single image) ----
        if args.action == "ocr":
            if not args.image_source:
                return "Error: 'image_source' is required for action='ocr'."
            err = _validate_source(args.image_source)
            if err:
                return err

            response = _call_endpoint(config, args.image_source, prompt)
            return _format_output(response, args.output_mode)

        # ---- ocr_batch (multiple images) ----
        if args.action == "ocr_batch":
            if not args.image_sources:
                return "Error: 'image_sources' is required for action='ocr_batch'."

            for src in args.image_sources:
                err = _validate_source(src)
                if err:
                    return err

            results: List[Dict[str, Any]] = []
            for src in args.image_sources:
                response = _call_endpoint(config, src, prompt)
                text = _extract_text(response)
                entry: Dict[str, Any] = {"source": src, "text": text}
                if args.output_mode == "lines":
                    entry["lines"] = _text_to_lines(text)
                    entry["line_count"] = len(entry["lines"])
                results.append(entry)

            return json.dumps(results, indent=2, ensure_ascii=False)

        # ---- vqa (visual question answering) ----
        if args.action == "vqa":
            if not args.image_source:
                return "Error: 'image_source' is required for action='vqa'."
            if not args.prompt:
                return "Error: 'prompt' is required for action='vqa'."
            err = _validate_source(args.image_source)
            if err:
                return err

            response = _call_endpoint(config, args.image_source, args.prompt)
            return _format_output(response, args.output_mode)

        return f"Error: unsupported action '{args.action}'."

    except requests.exceptions.RequestException as exc:
        return f"PaddleOCR-VL request failed: {exc}"
    except Exception as exc:
        return f"Tool execution failed: {exc}"


OUTPUT_KEY = "tool_output"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-params", required=True, help="JSON: tool configuration")
    parser.add_argument("--tool-params", required=True, help="JSON: tool arguments")
    cli_args = parser.parse_args()

    config = UserParameters(**json.loads(cli_args.user_params))
    params = ToolParameters(**json.loads(cli_args.tool_params))

    output = run_tool(config, params)
    print(OUTPUT_KEY, output)
