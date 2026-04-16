"""
Mock KPI forecaster tool — replaces LSTM model for demo.

Returns baseline-based predictions for telecom KPIs.
In production, replace with a real LSTM model loaded from the CAI model
registry or a CAI Inference predictive endpoint call.
"""

import json
import argparse
from pydantic import BaseModel, Field
from typing import Optional


# Known KPI baseline statistics (from the synthetic data generator)
KPI_BASELINES = {
    "rrc_setup_success_rate": {"mean": 98.5, "std": 1.2, "unit": "%"},
    "handover_success_rate": {"mean": 97.0, "std": 2.0, "unit": "%"},
    "dl_throughput_mbps": {"mean": 85.0, "std": 30.0, "unit": "Mbps"},
    "ul_throughput_mbps": {"mean": 25.0, "std": 12.0, "unit": "Mbps"},
    "rab_drop_rate": {"mean": 0.8, "std": 0.5, "unit": "%"},
    "rrc_connected_users": {"mean": 350.0, "std": 150.0, "unit": "users"},
    "volte_drop_rate": {"mean": 0.3, "std": 0.2, "unit": "%"},
    "prb_utilization_pct": {"mean": 55.0, "std": 20.0, "unit": "%"},
}


class UserParameters(BaseModel):
    """Configuration parameters (set at deployment time)."""
    model_path: Optional[str] = Field(
        default=None,
        description="Path to trained LSTM model file (unused in mock)"
    )


class ToolParameters(BaseModel):
    """Runtime parameters (passed by the agent)."""
    kpi_name: str = Field(description="KPI metric name to forecast")
    current_value: float = Field(description="Current observed KPI value")
    cell_id: str = Field(default="unknown", description="Cell ID for context")


def run_tool(config: UserParameters, args: ToolParameters) -> dict:
    """
    Predict next-hour KPI value based on baseline statistics.

    Mock logic: returns the baseline mean as the prediction, with confidence
    interval based on 1 standard deviation. Trend is determined by comparing
    current value to baseline.
    """
    baseline = KPI_BASELINES.get(args.kpi_name)

    if not baseline:
        return {
            "kpi_name": args.kpi_name,
            "predicted_value": args.current_value,
            "confidence_interval": [args.current_value * 0.9, args.current_value * 1.1],
            "trend": "unknown",
            "note": f"No baseline data for {args.kpi_name} — returning current value",
        }

    mean = baseline["mean"]
    std = baseline["std"]

    # Determine trend based on how far current value is from baseline
    deviation = abs(args.current_value - mean) / std if std > 0 else 0

    if deviation < 0.5:
        trend = "stable"
        predicted = mean
    elif args.current_value > mean and args.kpi_name in ("rab_drop_rate", "volte_drop_rate", "prb_utilization_pct"):
        # For "higher is worse" KPIs, above baseline = degrading
        trend = "degrading"
        predicted = mean + std * 0.5  # predict partial recovery
    elif args.current_value < mean and args.kpi_name in ("dl_throughput_mbps", "ul_throughput_mbps", "rrc_setup_success_rate", "handover_success_rate"):
        # For "lower is worse" KPIs, below baseline = degrading
        trend = "degrading"
        predicted = mean - std * 0.5  # predict partial recovery
    else:
        trend = "recovering"
        predicted = mean

    return {
        "kpi_name": args.kpi_name,
        "cell_id": args.cell_id,
        "current_value": args.current_value,
        "predicted_value": round(predicted, 2),
        "baseline_mean": mean,
        "confidence_interval": [round(mean - std, 2), round(mean + std, 2)],
        "trend": trend,
        "unit": baseline["unit"],
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
