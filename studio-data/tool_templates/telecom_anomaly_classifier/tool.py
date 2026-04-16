"""
Mock anomaly classifier tool — replaces XGBoost model for demo.

Agent Studio tool pattern: UserParameters + ToolParameters + run_tool().
In production, replace the hardcoded logic with a real model loaded from
the CAI model registry or a CAI Inference predictive endpoint call.
"""

import json
import argparse
from pydantic import BaseModel, Field
from typing import Optional


class UserParameters(BaseModel):
    """Configuration parameters (set at deployment time)."""
    model_path: Optional[str] = Field(
        default=None,
        description="Path to trained model file (unused in mock)"
    )


class ToolParameters(BaseModel):
    """Runtime parameters (passed by the agent)."""
    kpi_features: str = Field(
        description="JSON string: list of {kpi, value, baseline, z_score} dicts"
    )
    alarm_context: str = Field(
        default="[]",
        description="JSON string: list of {alarm_type, severity} dicts from correlated alarms"
    )


def run_tool(config: UserParameters, args: ToolParameters) -> dict:
    """
    Classify the fault type based on KPI features and alarm context.

    Mock logic:
    - If any alarm_type contains 'VSWR' -> RF_HARDWARE (0.91)
    - If any alarm_type contains 'HIGH_CPU' or 'HIGH_MEMORY' -> SOFTWARE (0.75)
    - If any alarm_type contains 'BACKHAUL' -> TRANSPORT (0.80)
    - If KPI count >= 4 with no matching alarm -> UNKNOWN_MULTI_KPI (0.60)
    - Default -> UNKNOWN (0.50)
    """
    try:
        alarms = json.loads(args.alarm_context)
    except (json.JSONDecodeError, TypeError):
        alarms = []

    try:
        kpis = json.loads(args.kpi_features)
    except (json.JSONDecodeError, TypeError):
        kpis = []

    alarm_types = [a.get("alarm_type", "") for a in alarms]
    alarm_str = " ".join(alarm_types).upper()

    if "VSWR" in alarm_str:
        return {
            "fault_class": "RF_HARDWARE",
            "confidence": 0.91,
            "reasoning": "VSWR alarm detected — indicates antenna feed mismatch or cable fault",
            "alternatives": [
                {"hypothesis": "BACKHAUL_CONGESTION", "confidence": 0.05},
                {"hypothesis": "TRAFFIC_OVERLOAD", "confidence": 0.04},
            ],
        }
    elif "HIGH_CPU" in alarm_str or "HIGH_MEMORY" in alarm_str:
        return {
            "fault_class": "SOFTWARE",
            "confidence": 0.75,
            "reasoning": "High CPU/memory alarm — indicates software issue or resource exhaustion",
            "alternatives": [
                {"hypothesis": "RF_HARDWARE", "confidence": 0.15},
                {"hypothesis": "TRAFFIC_OVERLOAD", "confidence": 0.10},
            ],
        }
    elif "BACKHAUL" in alarm_str:
        return {
            "fault_class": "TRANSPORT",
            "confidence": 0.80,
            "reasoning": "Backhaul alarm — indicates transport layer degradation",
            "alternatives": [
                {"hypothesis": "RF_HARDWARE", "confidence": 0.12},
                {"hypothesis": "SOFTWARE", "confidence": 0.08},
            ],
        }
    elif len(kpis) >= 4:
        return {
            "fault_class": "UNKNOWN_MULTI_KPI",
            "confidence": 0.60,
            "reasoning": "Multiple KPIs degraded without a clear alarm correlation — requires further investigation",
            "alternatives": [
                {"hypothesis": "RF_HARDWARE", "confidence": 0.20},
                {"hypothesis": "SOFTWARE", "confidence": 0.10},
                {"hypothesis": "TRANSPORT", "confidence": 0.10},
            ],
        }
    else:
        return {
            "fault_class": "UNKNOWN",
            "confidence": 0.50,
            "reasoning": "Insufficient evidence for classification",
            "alternatives": [],
        }


OUTPUT_KEY = "tool_output"

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-params", required=True)
    parser.add_argument("--tool-params", required=True)
    cli_args = parser.parse_args()

    config = UserParameters(**json.loads(cli_args.user_params))
    params = ToolParameters(**json.loads(cli_args.tool_params))

    output = run_tool(config, params)
    print(OUTPUT_KEY, json.dumps(output))
