#!/usr/bin/env python3
"""
CrewAI-based retrieval workflow for invoice/receipt key information extraction.
Bundled from multi-page-invoice-recognition/scripts/crewai_retrieval_workflow.py



Master agent
  -> Paddle retrieval agent (tool-wrapped PaddleOCR)
  -> Rolm retrieval agent (tool-wrapped RolmOCR)
  -> Merge into final key-value pairs

This script is designed for single-image testing first, then can be used as
the foundation for dataset-level benchmarking.
"""

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from crewai import Agent, Crew, LLM, Process, Task
from .common import safe_json_loads
from .paddle_tools import create_paddle_retrieve_tool


def _truncate_text(value: Any, limit: int = 1200) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _extract_first_json_object(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    direct = safe_json_loads(text, None)
    if isinstance(direct, dict):
        return direct

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return {}
    parsed = safe_json_loads(match.group(0), None)
    return parsed if isinstance(parsed, dict) else {}


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _collect_scalar_strings(value: Any) -> List[str]:
    scalars: List[str] = []
    if isinstance(value, dict):
        for v in value.values():
            scalars.extend(_collect_scalar_strings(v))
    elif isinstance(value, list):
        for v in value:
            scalars.extend(_collect_scalar_strings(v))
    elif value is not None:
        text = str(value).strip()
        if text:
            scalars.append(text)
    return scalars


def _has_rolm_evidence(field: str, field_value: Any, rolm_text: str) -> bool:
    rolm_norm = _normalize_text(rolm_text)
    if not rolm_norm:
        return False

    field_norm = _normalize_text(field)
    if field_norm and field_norm in rolm_norm:
        return True

    # For scalar values, require exact normalized value presence.
    if isinstance(field_value, (str, int, float)):
        value_norm = _normalize_text(field_value)
        return bool(value_norm) and value_norm in rolm_norm

    # For nested values, require at least one meaningful scalar evidence token.
    scalar_values = _collect_scalar_strings(field_value)
    for scalar in scalar_values:
        scalar_norm = _normalize_text(scalar)
        if len(scalar_norm) >= 3 and scalar_norm in rolm_norm:
            return True

    return False


class CrewAIRetrievalWorkflow:
    def __init__(
        self,
        paddle_url: str,
        rolm_url: str,
        jwt_token: str,
        rolm_model: str,
        llm_model: str,
        llm_base_url: str,
        llm_api_key: str,
    ):
        self.paddle_url = paddle_url.rstrip("/")
        self.rolm_url = rolm_url.rstrip("/")
        self.jwt_token = jwt_token
        self.rolm_model = rolm_model
        self.rolm_llm = LLM(
            model=f"openai/{self.rolm_model}",
            api_base=self.rolm_url,
            api_key=self.jwt_token,
            temperature=0.0,
        )

        # CrewAI agents use this OpenAI-compatible LLM endpoint.
        os.environ["OPENAI_MODEL_NAME"] = llm_model
        os.environ["OPENAI_API_BASE"] = llm_base_url.rstrip("/")
        os.environ["OPENAI_API_KEY"] = llm_api_key
        # Avoid interactive trace prompt during CLI runs.
        os.environ["CREWAI_TRACING_ENABLED"] = "false"

    def build_tools(self):
        return create_paddle_retrieve_tool(
            paddle_url=self.paddle_url,
            jwt_token=self.jwt_token,
        )

    def run(
        self,
        image_path: str,
        target_fields: List[str],
        paddle_threshold: float,
        open_schema: bool = True,
    ) -> Dict[str, Any]:
        paddle_tool = self.build_tools()

        if open_schema:
            paddle_agent = Agent(
                role="Paddle Retrieval Agent",
                goal=(
                    "Extract OCR evidence and candidate fields from image. "
                    "You MUST call the paddle_retrieve_tool first and return structured output from it."
                ),
                backstory="OCR retrieval specialist using PaddleOCR tool output.",
                verbose=False,
                tools=[paddle_tool],
                allow_delegation=False,
            )

            rolm_agent = Agent(
                role="Rolm Discovery Agent",
                goal="Discover as many meaningful key-value fields from the document image as possible.",
                backstory=(
                    "Vision extraction specialist using RolmOCR model directly. "
                    "Focus on field discovery and values, not rigid schema."
                ),
                verbose=False,
                llm=self.rolm_llm,
                multimodal=True,
                allow_delegation=False,
            )

            master_agent = Agent(
                role="Retrieval Master Agent",
                goal="Merge Paddle and Rolm evidence into open-schema discovered fields JSON.",
                backstory="Orchestrator that consolidates field discovery with traceable sources.",
                verbose=False,
                allow_delegation=True,
            )

            paddle_task = Task(
                description=(
                    "MANDATORY: Invoke paddle_retrieve_tool exactly once with this image path:\n"
                    f"{image_path}\n\n"
                    "Return JSON with keys: ocr_line_count, candidates, lines."
                ),
                expected_output="JSON from Paddle tool",
                agent=paddle_agent,
            )

            rolm_task = Task(
                description=(
                    "Analyze the image and discover key-value fields in natural language.\n"
                    "Do not limit to a fixed schema. Include amounts, dates, identifiers, vendors, payment info, "
                    "line items, and any useful metadata when present.\n"
                    "If uncertain, say uncertain."
                ),
                expected_output="Natural-language list of discovered fields and values",
                agent=rolm_agent,
                context=[paddle_task],
                input_files={"image": image_path},
            )

            merge_task = Task(
                description=(
                    "Merge Paddle + Rolm outputs into strict JSON ONLY.\n"
                    "You MUST extract structured fields from Rolm natural-language output.\n"
                    "Output schema:\n"
                    "{\n"
                    '  "discovered_fields": {\n'
                    '    "<field_name>": {"value": <any>, "source": "paddle|rolm|both", "confidence": <0..1|null>}\n'
                    "  },\n"
                    '  "routing": {"from_paddle":[...], "from_rolm":[...], "from_both":[...]}\n'
                    "}\n"
                    "No markdown, no prose."
                ),
                expected_output="Strict JSON with discovered_fields and routing",
                agent=master_agent,
                context=[paddle_task, rolm_task],
                markdown=False,
            )

            crew = Crew(
                agents=[paddle_agent, rolm_agent, master_agent],
                tasks=[paddle_task, rolm_task, merge_task],
                process=Process.sequential,
                verbose=False,
            )

            crew_output = crew.kickoff()
            raw_output = str(crew_output)
            parsed = safe_json_loads(raw_output, {"raw_output": raw_output})

            paddle_output_raw = str(paddle_task.output)
            rolm_output_raw = str(rolm_task.output)
            merge_output_raw = str(merge_task.output)

            paddle_json = _extract_first_json_object(paddle_output_raw)
            merge_json = _extract_first_json_object(merge_output_raw)
            discovered_fields_raw = (
                merge_json.get("discovered_fields", {})
                if isinstance(merge_json.get("discovered_fields"), dict)
                else {}
            )
            paddle_candidates = (
                paddle_json.get("candidates", {})
                if isinstance(paddle_json.get("candidates"), dict)
                else {}
            )

            discovered_fields: Dict[str, Dict[str, Any]] = {}

            for key, value in discovered_fields_raw.items():
                if isinstance(value, dict):
                    discovered_fields[key] = {
                        "value": value.get("value"),
                        "source": value.get("source", "rolm"),
                        "confidence": value.get("confidence"),
                    }
                else:
                    discovered_fields[key] = {
                        "value": value,
                        "source": "rolm",
                        "confidence": None,
                    }

            # Ensure Paddle candidates are always preserved in pass-1 discovery.
            for key, cand in paddle_candidates.items():
                if not isinstance(cand, dict):
                    continue
                existing = discovered_fields.get(key)
                if existing is None:
                    discovered_fields[key] = {
                        "value": cand.get("value"),
                        "source": "paddle",
                        "confidence": cand.get("confidence"),
                    }
                else:
                    existing_source = str(existing.get("source", "rolm"))
                    existing["source"] = "both" if existing_source != "paddle" else "paddle"
                    if existing.get("confidence") is None and isinstance(cand.get("confidence"), (int, float)):
                        existing["confidence"] = cand.get("confidence")
                    if existing.get("value") in (None, "", []):
                        existing["value"] = cand.get("value")

            known_canonical_fields = {
                "total",
                "subtotal",
                "tax",
                "date",
                "currency",
                "items",
                "invoice_number",
                "payment_method",
                "vendor",
            }
            canonical = {k: v for k, v in discovered_fields.items() if k in known_canonical_fields}
            extras = {k: v for k, v in discovered_fields.items() if k not in known_canonical_fields}

            deterministic = {
                "mode": "open_schema_pass1",
                "discovered_fields": discovered_fields,
                "canonical": canonical,
                "extras": extras,
                "decision_log": {
                    "paddle_summary": {
                        "ocr_line_count": paddle_json.get("ocr_line_count"),
                        "candidate_fields": sorted(list(paddle_candidates.keys())),
                    },
                    "counts": {
                        "discovered_total": len(discovered_fields),
                        "canonical_count": len(canonical),
                        "extras_count": len(extras),
                    },
                    "raw_task_outputs": {
                        "paddle_task_output": _truncate_text(paddle_output_raw),
                        "rolm_task_output": _truncate_text(rolm_output_raw),
                        "merge_task_output": _truncate_text(merge_output_raw),
                    },
                },
            }

            return {
                "crew_output": parsed,
                "deterministic_output": deterministic,
            }

        paddle_agent = Agent(
            role="Paddle Retrieval Agent",
            goal=(
                "Extract OCR evidence and high-confidence candidate fields from image. "
                "You MUST call the paddle_retrieve_tool first and base your answer only on tool output."
            ),
            backstory=(
                "Expert OCR retrieval specialist. Never fabricate OCR content. "
                "Always invoke paddle_retrieve_tool and return its parsed JSON result."
            ),
            verbose=False,
            tools=[paddle_tool],
            allow_delegation=False,
        )

        rolm_agent = Agent(
            role="Rolm Retrieval Agent",
            goal=(
                "Extract unresolved fields from the image with strict JSON output using RolmOCR directly."
            ),
            backstory=(
                "Expert focused extraction specialist using RolmOCR vision model directly (no tools). "
                "Return only JSON and include only requested unresolved keys."
            ),
            verbose=False,
            llm=self.rolm_llm,
            multimodal=True,
            allow_delegation=False,
        )

        master_agent = Agent(
            role="Retrieval Master Agent",
            goal="Coordinate retrieval outputs and merge into final key-value pairs.",
            backstory="Orchestrator that routes fields by confidence and merges outputs safely.",
            verbose=False,
            allow_delegation=True,
        )

        paddle_task = Task(
            description=(
                "MANDATORY: Invoke paddle_retrieve_tool exactly once with this image path:\n"
                f"{image_path}\n\n"
                "If tool invocation fails, return JSON: {\"error\":\"paddle_tool_call_failed\"}.\n"
                "Return only JSON with keys: ocr_line_count, candidates, lines."
            ),
            expected_output="JSON object with OCR line count and candidate field values from Paddle.",
            agent=paddle_agent,
        )

        rolm_task = Task(
            description=(
                "Use the previous task output to identify unresolved fields from this target field list:\n"
                f"{json.dumps(target_fields)}\n"
                f"Threshold: {paddle_threshold}\n\n"
                f"Image path: {image_path}\n\n"
                "MANDATORY RULES:\n"
                "- If unresolved fields list is non-empty, extract values directly from the image.\n"
                "- If unresolved fields list is empty, say exactly: 'No unresolved fields'.\n"
                "- You can answer in natural language with evidence snippets.\n"
                "- Focus only on unresolved fields; do not repeat already-resolved Paddle fields."
            ),
            expected_output="Natural-language extraction notes for unresolved fields with candidate values.",
            agent=rolm_agent,
            context=[paddle_task],
            input_files={"image": image_path},
        )

        merge_task = Task(
            description=(
                "Merge paddle and rolm outputs into final JSON.\n"
                "Rules:\n"
                "- Keep Paddle candidate if confidence >= threshold\n"
                "- Read Rolm output text and extract field/value pairs from it\n"
                "- Normalize extracted keys to target fields only\n"
                "- If Rolm text is noisy/non-JSON, you must still recover structured values when possible\n"
                "- Output MUST be valid JSON only (no markdown, no prose)\n"
                "- final schema:\n"
                "{\n"
                '  "paddle": {...},\n'
                '  "rolm": {...},\n'
                '  "final": {...},\n'
                '  "routing": {"accepted_from_paddle":[...], "from_rolm":[...], "unresolved":[...]}\n'
                "}"
            ),
            expected_output="Strict JSON following the required schema, with rolm values parsed from rolm text output.",
            agent=master_agent,
            context=[paddle_task, rolm_task],
            markdown=False,
        )

        crew = Crew(
            agents=[paddle_agent, rolm_agent, master_agent],
            tasks=[paddle_task, rolm_task, merge_task],
            process=Process.sequential,
            verbose=False,
        )

        crew_output = crew.kickoff()
        raw_output = str(crew_output)
        parsed = safe_json_loads(raw_output, {"raw_output": raw_output})

        # Deterministic post-check fallback from task outputs.
        # Crew may return markdown/extra prose; ensure machine-usable output.
        paddle_json = safe_json_loads(str(paddle_task.output), {})
        rolm_output_raw = str(rolm_task.output)
        rolm_json = safe_json_loads(rolm_output_raw, {})
        if not isinstance(rolm_json, dict):
            rolm_json = _extract_first_json_object(rolm_output_raw)
        paddle_output_raw = str(paddle_task.output)
        merge_output_raw = str(merge_task.output)
        merge_json = _extract_first_json_object(merge_output_raw)
        if not isinstance(paddle_json, dict):
            paddle_json = {}
        if not isinstance(rolm_json, dict):
            rolm_json = {}
        if not isinstance(merge_json, dict):
            merge_json = {}

        candidates = paddle_json.get("candidates", {}) if isinstance(paddle_json.get("candidates"), dict) else {}
        merge_rolm_values = merge_json.get("rolm", {}) if isinstance(merge_json.get("rolm"), dict) else {}
        evidence_rejected_fields: List[str] = []
        if merge_rolm_values:
            merge_rolm_values = {k: v for k, v in merge_rolm_values.items() if k in target_fields}
            rolm_values = {}
            for k, v in merge_rolm_values.items():
                if _has_rolm_evidence(k, v, rolm_output_raw):
                    rolm_values[k] = v
                else:
                    evidence_rejected_fields.append(k)
        elif isinstance(rolm_json.get("values"), dict):
            rolm_values = rolm_json.get("values", {})
        else:
            rolm_values = {
                k: v
                for k, v in rolm_json.items()
                if isinstance(k, str) and k in target_fields
            }

        final_values: Dict[str, Any] = {}
        accepted_from_paddle: List[str] = []
        from_rolm: List[str] = []
        unresolved: List[str] = []
        field_decisions: List[Dict[str, Any]] = []
        for field in target_fields:
            cand = candidates.get(field)
            if isinstance(cand, dict) and isinstance(cand.get("confidence"), (int, float)) and cand["confidence"] >= paddle_threshold:
                final_values[field] = cand.get("value")
                accepted_from_paddle.append(field)
                field_decisions.append(
                    {
                        "field": field,
                        "decision": "accept_paddle",
                        "paddle_value": cand.get("value"),
                        "paddle_confidence": cand.get("confidence"),
                        "threshold": paddle_threshold,
                        "reason": "paddle_confidence_gte_threshold",
                    }
                )
            elif field in rolm_values:
                final_values[field] = rolm_values[field]
                from_rolm.append(field)
                field_decisions.append(
                    {
                        "field": field,
                        "decision": "use_rolm",
                        "rolm_value": rolm_values[field],
                        "paddle_confidence": cand.get("confidence") if isinstance(cand, dict) else None,
                        "threshold": paddle_threshold,
                        "reason": "paddle_missing_or_below_threshold_and_rolm_available",
                    }
                )
            else:
                unresolved.append(field)
                field_decisions.append(
                    {
                        "field": field,
                        "decision": "unresolved",
                        "paddle_value": cand.get("value") if isinstance(cand, dict) else None,
                        "paddle_confidence": cand.get("confidence") if isinstance(cand, dict) else None,
                        "threshold": paddle_threshold,
                        "reason": (
                            "rolm_not_returned_value_for_unresolved_field"
                            if isinstance(cand, dict)
                            else "no_paddle_candidate_and_no_rolm_value"
                        ),
                    }
                )

        tool_invocation_signals = {
            "paddle_output_has_candidates_dict": isinstance(paddle_json.get("candidates"), dict),
            "paddle_output_has_lines_nonzero": int(paddle_json.get("ocr_line_count") or 0) > 0,
            "rolm_output_has_values_dict": isinstance(rolm_json.get("values"), dict),
            "rolm_output_has_any_value": len(rolm_values) > 0,
            "master_output_has_rolm_dict": isinstance(merge_json.get("rolm"), dict),
            "rolm_evidence_guard_active": True,
        }
        possible_tool_skip = (
            int(paddle_json.get("ocr_line_count") or 0) == 0
            and not isinstance(paddle_json.get("candidates"), dict)
            and len(rolm_values) == 0
        )

        rolm_involvement_reason = (
            "rolm_returned_values"
            if from_rolm
            else (
                "not_needed_all_fields_resolved_by_paddle"
                if not unresolved
                else (
                    "possible_tool_not_invoked_or_empty_stub_output"
                    if possible_tool_skip
                    else "rolm_returned_no_usable_values"
                )
            )
        )

        deterministic = {
            "paddle": candidates,
            "rolm": rolm_values,
            "final": final_values,
            "routing": {
                "accepted_from_paddle": accepted_from_paddle,
                "from_rolm": from_rolm,
                "unresolved": unresolved,
            },
            "decision_log": {
                "target_fields": target_fields,
                "paddle_threshold": paddle_threshold,
                "paddle_summary": {
                    "ocr_line_count": paddle_json.get("ocr_line_count"),
                    "candidate_fields": sorted(list(candidates.keys())),
                },
                "field_decisions": field_decisions,
                "rolm_involvement": {
                    "involved": bool(from_rolm),
                    "reason": rolm_involvement_reason,
                    "rolm_values_count": len(rolm_values),
                    "rolm_error": rolm_json.get("error") if isinstance(rolm_json, dict) else None,
                },
                "rolm_evidence_guard": {
                    "accepted_fields": sorted(list(rolm_values.keys())),
                    "rejected_fields": sorted(evidence_rejected_fields),
                },
                "tool_invocation_signals": tool_invocation_signals,
                "raw_task_outputs": {
                    "paddle_task_output": _truncate_text(paddle_output_raw),
                    "rolm_task_output": _truncate_text(rolm_output_raw),
                    "merge_task_output": _truncate_text(merge_output_raw),
                },
            },
        }

        return {
            "crew_output": parsed,
            "deterministic_output": deterministic,
        }
