"""
Tool for executing Python code or script files in a subprocess.
Enables agents to write Python scripts (for data generation, evaluation, etc.)
and run them, capturing stdout, stderr, and exit code.

Designed for Direction 3 of the synthetic data workflow: agents write generation
and evaluation scripts, then execute them to produce and assess synthetic datasets.
"""

import json
import os
import sys
import argparse
import subprocess
import tempfile
import textwrap
from typing import Literal, Optional
from pydantic import BaseModel, Field


class UserParameters(BaseModel):
    """
    Args:
        python_executable (str): Python interpreter to use (default: 'python3').
        working_directory (str): Working directory for script execution.
        timeout_seconds (int): Max seconds before the process is killed.
        capture_files (str): Comma-separated file paths to read and include in output
            after execution (e.g. a generated CSV or evaluation report).
        max_output_chars (int): Truncate stdout/stderr beyond this length.
    """
    python_executable: str = Field(
        default="python3",
        description="Python interpreter path or command (e.g. 'python3', '/usr/bin/python3', 'uv run python')"
    )
    working_directory: str = Field(
        default="/tmp",
        description="Working directory for script execution. The script runs with cwd set to this path."
    )
    timeout_seconds: int = Field(
        default=300,
        description="Seconds before the subprocess is force-killed (default 300)"
    )
    capture_files: Optional[str] = Field(
        default=None,
        description="Comma-separated file paths to read and append to tool output after execution. "
                    "Useful for reading a generated CSV summary or evaluation report."
    )
    max_output_chars: int = Field(
        default=8000,
        description="Maximum combined stdout+stderr characters to return (truncated beyond this)"
    )


class ToolParameters(BaseModel):
    action: Literal["run_code", "run_script", "install_requirements"] = Field(
        description=(
            "'run_code' — execute a Python code string directly (no file needed); "
            "'run_script' — execute an existing .py file from script_path; "
            "'install_requirements' — pip install packages listed in requirements"
        )
    )
    code: Optional[str] = Field(
        default=None,
        description="Python code string to execute (required for 'run_code'). "
                    "Write complete, self-contained scripts. Use print() to produce output."
    )
    script_path: Optional[str] = Field(
        default=None,
        description="Absolute path to a .py file to execute (required for 'run_script')"
    )
    script_args: Optional[str] = Field(
        default=None,
        description="Space-separated command-line arguments to pass to the script "
                    "(e.g. '--output /tmp/out.csv --rows 1000')"
    )
    requirements: Optional[str] = Field(
        default=None,
        description="Comma-separated Python packages to install before running "
                    "(e.g. 'pandas,faker,sdv'). Uses pip install."
    )
    save_code_to: Optional[str] = Field(
        default=None,
        description="If set, save the code string to this file path before executing it. "
                    "Useful for persisting generated scripts for later reuse."
    )
    capture_files: Optional[str] = Field(
        default=None,
        description="Comma-separated file paths to read and append to tool output after execution "
                    "(e.g. '/tmp/output.csv,/tmp/eval_report.json'). Overrides the config-level setting."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_subprocess(
    cmd: list[str],
    cwd: str,
    timeout: int,
    env: Optional[dict] = None,
) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env or os.environ.copy(),
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Process timed out after {timeout}s"
    except FileNotFoundError as e:
        return -1, "", f"Executable not found: {e}"


def _format_result(
    returncode: int,
    stdout: str,
    stderr: str,
    max_chars: int,
    label: str = "",
    captured_files: Optional[list[str]] = None,
) -> str:
    combined = stdout + ("\nSTDERR:\n" + stderr if stderr.strip() else "")
    if len(combined) > max_chars:
        combined = combined[:max_chars] + f"\n... [truncated at {max_chars} chars]"

    parts = []
    if label:
        parts.append(label)
    parts.append(f"Exit code: {returncode}")
    parts.append(f"Output:\n{combined}" if combined.strip() else "Output: (none)")

    if captured_files:
        for fpath in captured_files:
            if os.path.exists(fpath):
                try:
                    with open(fpath) as f:
                        content = f.read(4000)
                    parts.append(f"\nFile {fpath}:\n{content}")
                    if len(content) == 4000:
                        parts.append("... [file truncated at 4000 chars]")
                except Exception as e:
                    parts.append(f"\nCould not read {fpath}: {e}")
            else:
                parts.append(f"\nFile {fpath}: not found after execution")

    status = "SUCCESS" if returncode == 0 else f"FAILED (exit {returncode})"
    parts.insert(1, f"Status: {status}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def handle_install_requirements(
    config: UserParameters,
    requirements: Optional[str],
) -> str:
    if not requirements:
        return "Error: 'requirements' field is required for install_requirements action."

    packages = [p.strip() for p in requirements.split(",") if p.strip()]
    if not packages:
        return "Error: no valid package names found in 'requirements'."

    cmd = [config.python_executable, "-m", "pip", "install", "--quiet"] + packages
    rc, stdout, stderr = _run_subprocess(cmd, config.working_directory, config.timeout_seconds)

    if rc == 0:
        return f"Installed: {', '.join(packages)}\n{stdout.strip()}"
    return f"pip install failed (exit {rc}):\n{stderr[:2000]}"


def handle_run_code(config: UserParameters, args: ToolParameters) -> str:
    if not args.code:
        return "Error: 'code' is required for 'run_code' action."

    code = textwrap.dedent(args.code)

    # Optionally install requirements first
    if args.requirements:
        install_result = handle_install_requirements(config, args.requirements)
        if "failed" in install_result.lower():
            return f"Dependency installation failed:\n{install_result}"

    # Optionally save to a file for reuse
    if args.save_code_to:
        os.makedirs(os.path.dirname(os.path.abspath(args.save_code_to)), exist_ok=True)
        with open(args.save_code_to, "w") as f:
            f.write(code)

    # Write to a temp file and execute
    with tempfile.NamedTemporaryFile(
        suffix=".py", prefix="agent_script_", delete=False, mode="w"
    ) as tmp:
        tmp.write(code)
        script_file = tmp.name

    try:
        cmd = [config.python_executable, script_file]
        if args.script_args:
            cmd.extend(args.script_args.split())

        rc, stdout, stderr = _run_subprocess(
            cmd, config.working_directory, config.timeout_seconds
        )
    finally:
        os.unlink(script_file)

    captured = (
        [p.strip() for p in args.capture_files.split(",") if p.strip()]
        if args.capture_files
        else None
    )
    # fall back to config-level capture_files if tool-level not set
    if not captured and config.capture_files:
        captured = [p.strip() for p in config.capture_files.split(",") if p.strip()]

    label = f"Script saved to: {args.save_code_to}" if args.save_code_to else ""
    return _format_result(rc, stdout, stderr, config.max_output_chars, label, captured)


def handle_run_script(config: UserParameters, args: ToolParameters) -> str:
    if not args.script_path:
        return "Error: 'script_path' is required for 'run_script' action."

    if not os.path.exists(args.script_path):
        return f"Error: Script not found at '{args.script_path}'."

    # Optionally install requirements first
    if args.requirements:
        install_result = handle_install_requirements(config, args.requirements)
        if "failed" in install_result.lower():
            return f"Dependency installation failed:\n{install_result}"

    cmd = [config.python_executable, args.script_path]
    if args.script_args:
        cmd.extend(args.script_args.split())

    rc, stdout, stderr = _run_subprocess(
        cmd, config.working_directory, config.timeout_seconds
    )

    captured = (
        [p.strip() for p in args.capture_files.split(",") if p.strip()]
        if args.capture_files
        else None
    )
    if not captured and config.capture_files:
        captured = [p.strip() for p in config.capture_files.split(",") if p.strip()]

    return _format_result(
        rc, stdout, stderr, config.max_output_chars,
        f"Script: {args.script_path}", captured
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_tool(config: UserParameters, args: ToolParameters) -> str:
    try:
        os.makedirs(config.working_directory, exist_ok=True)

        if args.action == "install_requirements":
            return handle_install_requirements(config, args.requirements)
        elif args.action == "run_code":
            return handle_run_code(config, args)
        elif args.action == "run_script":
            return handle_run_script(config, args)
        else:
            return f"Error: Unknown action '{args.action}'."

    except PermissionError as e:
        return f"Permission error: {e}"
    except Exception as e:
        return f"Tool execution failed: {e}"


OUTPUT_KEY = "tool_output"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-params", required=True)
    parser.add_argument("--tool-params", required=True)
    cli_args = parser.parse_args()

    config = UserParameters(**json.loads(cli_args.user_params))
    params = ToolParameters(**json.loads(cli_args.tool_params))

    output = run_tool(config, params)
    print(OUTPUT_KEY, output)
