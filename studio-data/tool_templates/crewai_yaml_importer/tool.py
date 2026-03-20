"""
CrewAI YAML Importer Tool

Converts CrewAI agents.yaml and tasks.yaml configuration files into an Agent Studio workflow.
This tool uses the Agent Studio gRPC API to programmatically create workflows, agents, and tasks.

Usage:
- Provide paths to CrewAI agents.yaml and tasks.yaml files
- Specify workflow name and LLM model ID
- The tool creates a complete workflow in Agent Studio
"""

from pydantic import BaseModel, Field
from typing import Optional, Any, Dict, List
import json
import argparse
import yaml
import os


class UserParameters(BaseModel):
    """
    Configuration parameters for the CrewAI importer.
    """
    agent_studio_ip: Optional[str] = Field(
        default=None,
        description="Agent Studio gRPC service IP (defaults to AGENT_STUDIO_SERVICE_IP env var)"
    )
    agent_studio_port: Optional[str] = Field(
        default=None,
        description="Agent Studio gRPC service port (defaults to AGENT_STUDIO_SERVICE_PORT env var)"
    )


class ToolParameters(BaseModel):
    """
    Arguments for the CrewAI YAML import operation.
    """
    agents_yaml_path: str = Field(
        description="Absolute path to CrewAI agents.yaml file"
    )
    tasks_yaml_path: str = Field(
        description="Absolute path to CrewAI tasks.yaml file"
    )
    workflow_name: str = Field(
        description="Name for the new Agent Studio workflow"
    )
    llm_model_id: str = Field(
        description="Agent Studio LLM model provider ID (get from ListLLMProviderModels API)"
    )
    process_type: str = Field(
        default="sequential",
        description="Workflow process type: 'sequential' or 'hierarchical'"
    )
    is_conversational: bool = Field(
        default=False,
        description="Whether the workflow is conversational (chat-based)"
    )
    workflow_description: Optional[str] = Field(
        default=None,
        description="Optional description for the workflow"
    )


def run_tool(config: UserParameters, args: ToolParameters) -> Any:
    """
    Convert CrewAI YAML files to Agent Studio workflow.
    """
    try:
        from studio.client import AgentStudioClient
        from studio.api import (
            AddWorkflowRequest,
            AddAgentRequest,
            AddTaskRequest,
            UpdateWorkflowRequest,
            CrewAIWorkflowMetadata,
            CrewAIAgentMetadata,
            AddCrewAITaskRequest,
        )
    except ImportError as e:
        return {
            "success": False,
            "error": f"Failed to import Agent Studio modules: {e}. This tool must run within CAI environment."
        }

    # Validate file paths
    if not os.path.exists(args.agents_yaml_path):
        return {"success": False, "error": f"agents.yaml not found: {args.agents_yaml_path}"}
    if not os.path.exists(args.tasks_yaml_path):
        return {"success": False, "error": f"tasks.yaml not found: {args.tasks_yaml_path}"}

    # Load YAML files
    try:
        with open(args.agents_yaml_path, 'r') as f:
            agents_config = yaml.safe_load(f)
        with open(args.tasks_yaml_path, 'r') as f:
            tasks_config = yaml.safe_load(f)
    except Exception as e:
        return {"success": False, "error": f"Failed to parse YAML files: {e}"}

    if not agents_config:
        return {"success": False, "error": "agents.yaml is empty or invalid"}
    if not tasks_config:
        return {"success": False, "error": "tasks.yaml is empty or invalid"}

    # Connect to Agent Studio
    try:
        client = AgentStudioClient(
            server_ip=config.agent_studio_ip,
            server_port=config.agent_studio_port
        )
    except Exception as e:
        return {"success": False, "error": f"Failed to connect to Agent Studio: {e}"}

    results = {
        "success": True,
        "workflow_id": None,
        "agents_created": [],
        "tasks_created": [],
        "warnings": []
    }

    try:
        # 1. Create workflow
        workflow_resp = client.stub.AddWorkflow(AddWorkflowRequest(
            name=args.workflow_name,
            is_conversational=args.is_conversational,
            description=args.workflow_description or f"Imported from CrewAI: {args.agents_yaml_path}",
            crew_ai_workflow_metadata=CrewAIWorkflowMetadata(
                process=args.process_type,
            )
        ))
        workflow_id = workflow_resp.workflow_id
        results["workflow_id"] = workflow_id

        # 2. Create agents
        agent_map = {}  # crewai_name -> agent_studio_id
        for agent_name, agent_config in agents_config.items():
            if not isinstance(agent_config, dict):
                results["warnings"].append(f"Skipping invalid agent config: {agent_name}")
                continue

            try:
                resp = client.stub.AddAgent(AddAgentRequest(
                    name=agent_name,
                    workflow_id=workflow_id,
                    llm_provider_model_id=args.llm_model_id,
                    crew_ai_agent_metadata=CrewAIAgentMetadata(
                        role=agent_config.get('role', agent_name),
                        goal=agent_config.get('goal', ''),
                        backstory=agent_config.get('backstory', ''),
                    ),
                ))
                agent_map[agent_name] = resp.agent_id
                results["agents_created"].append({
                    "name": agent_name,
                    "id": resp.agent_id
                })
            except Exception as e:
                results["warnings"].append(f"Failed to create agent '{agent_name}': {e}")

        # 3. Create tasks
        task_ids = []
        for task_name, task_config in tasks_config.items():
            if not isinstance(task_config, dict):
                results["warnings"].append(f"Skipping invalid task config: {task_name}")
                continue

            # Find assigned agent
            assigned_agent = task_config.get('agent', '')
            agent_id = agent_map.get(assigned_agent, '')

            if not agent_id and assigned_agent:
                results["warnings"].append(
                    f"Task '{task_name}' references unknown agent '{assigned_agent}'. "
                    "Task will be created without agent assignment."
                )

            try:
                resp = client.stub.AddTask(AddTaskRequest(
                    name=task_name,
                    workflow_id=workflow_id,
                    add_crew_ai_task_request=AddCrewAITaskRequest(
                        description=task_config.get('description', ''),
                        expected_output=task_config.get('expected_output', ''),
                        assigned_agent_id=agent_id,
                    )
                ))
                task_ids.append(resp.task_id)
                results["tasks_created"].append({
                    "name": task_name,
                    "id": resp.task_id,
                    "assigned_agent": assigned_agent
                })
            except Exception as e:
                results["warnings"].append(f"Failed to create task '{task_name}': {e}")

        # 4. Update workflow with agent and task IDs
        if agent_map or task_ids:
            try:
                client.stub.UpdateWorkflow(UpdateWorkflowRequest(
                    workflow_id=workflow_id,
                    crew_ai_workflow_metadata=CrewAIWorkflowMetadata(
                        agent_id=list(agent_map.values()),
                        task_id=task_ids,
                        process=args.process_type,
                    )
                ))
            except Exception as e:
                results["warnings"].append(f"Failed to update workflow metadata: {e}")

        results["message"] = (
            f"Successfully created workflow '{args.workflow_name}' with "
            f"{len(results['agents_created'])} agents and {len(results['tasks_created'])} tasks"
        )

    except Exception as e:
        results["success"] = False
        results["error"] = str(e)

    return results


OUTPUT_KEY = "tool_output"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CrewAI YAML to Agent Studio Importer")
    parser.add_argument("--user-params", required=True, help="User configuration JSON")
    parser.add_argument("--tool-params", required=True, help="Tool arguments JSON")
    args = parser.parse_args()

    user_dict = json.loads(args.user_params)
    tool_dict = json.loads(args.tool_params)

    config = UserParameters(**user_dict)
    params = ToolParameters(**tool_dict)

    output = run_tool(config, params)
    print(OUTPUT_KEY, json.dumps(output, indent=2))
