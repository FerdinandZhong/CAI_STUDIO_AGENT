"""
CrewAI to Agent Studio Converter SDK

Provides functions to programmatically convert CrewAI configurations
to Agent Studio workflows using the gRPC API.

Usage:
    from studio.sdk.crewai_converter import (
        convert_crewai_yaml,
        list_available_models,
        import_workflow_template,
    )

    # List available LLM models
    models = list_available_models()
    print(models)

    # Convert CrewAI YAML files to Agent Studio workflow
    result = convert_crewai_yaml(
        agents_yaml_path="config/agents.yaml",
        tasks_yaml_path="config/tasks.yaml",
        workflow_name="my_workflow",
        llm_model_id="your-model-id",
    )
    print(f"Created workflow: {result['workflow_id']}")

    # Import a workflow template from ZIP
    result = import_workflow_template(
        template_path="/path/to/template.zip",
        create_workflow=True,
        workflow_name="imported_workflow",
    )
"""

from typing import Dict, List, Optional, Any
import yaml
import os


def get_client():
    """Get Agent Studio gRPC client."""
    from studio.client import AgentStudioClient
    return AgentStudioClient()


def list_available_models() -> List[Dict[str, str]]:
    """
    List all available LLM provider models.

    Returns:
        List of dicts with 'id' and 'display_name' keys
    """
    from studio.api import ListLLMProviderModelsRequest

    client = get_client()
    resp = client.stub.ListLLMProviderModels(ListLLMProviderModelsRequest())

    models = []
    for model in resp.llm_provider_models:
        models.append({
            "id": model.id,
            "display_name": model.display_name,
            "provider": getattr(model, 'provider', 'unknown'),
        })
    return models


def list_workflows() -> List[Dict[str, Any]]:
    """
    List all workflows in Agent Studio.

    Returns:
        List of workflow dictionaries
    """
    from studio.api import ListWorkflowsRequest

    client = get_client()
    resp = client.stub.ListWorkflows(ListWorkflowsRequest())

    workflows = []
    for w in resp.workflows:
        workflows.append({
            "id": w.workflow_id,
            "name": w.name,
            "is_conversational": w.is_conversational,
            "is_valid": w.is_valid,
            "is_ready": w.is_ready,
            "description": w.description,
        })
    return workflows


def list_workflow_templates() -> List[Dict[str, Any]]:
    """
    List all workflow templates in Agent Studio.

    Returns:
        List of template dictionaries
    """
    from studio.api import ListWorkflowTemplatesRequest

    client = get_client()
    resp = client.stub.ListWorkflowTemplates(ListWorkflowTemplatesRequest())

    templates = []
    for t in resp.workflow_templates:
        templates.append({
            "id": t.id,
            "name": t.name,
            "description": getattr(t, 'description', ''),
        })
    return templates


def convert_crewai_yaml(
    agents_yaml_path: str,
    tasks_yaml_path: str,
    workflow_name: str,
    llm_model_id: str,
    process_type: str = "sequential",
    is_conversational: bool = False,
    description: Optional[str] = None,
    tool_template_ids: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """
    Convert CrewAI YAML configuration files to an Agent Studio workflow.

    Args:
        agents_yaml_path: Path to CrewAI agents.yaml file
        tasks_yaml_path: Path to CrewAI tasks.yaml file
        workflow_name: Name for the new workflow
        llm_model_id: Agent Studio LLM model provider ID
        process_type: 'sequential' or 'hierarchical'
        is_conversational: Whether workflow is chat-based
        description: Optional workflow description
        tool_template_ids: Optional dict mapping agent names to tool template IDs
            Example: {"email_agent": ["gmail-tool-id", "calendar-tool-id"]}

    Returns:
        Dict with workflow_id, agents_created, tasks_created, and any warnings
    """
    from studio.api import (
        AddWorkflowRequest,
        AddAgentRequest,
        AddTaskRequest,
        UpdateWorkflowRequest,
        CrewAIWorkflowMetadata,
        CrewAIAgentMetadata,
        AddCrewAITaskRequest,
    )

    # Validate files exist
    if not os.path.exists(agents_yaml_path):
        raise FileNotFoundError(f"agents.yaml not found: {agents_yaml_path}")
    if not os.path.exists(tasks_yaml_path):
        raise FileNotFoundError(f"tasks.yaml not found: {tasks_yaml_path}")

    # Load YAML files
    with open(agents_yaml_path, 'r') as f:
        agents_config = yaml.safe_load(f)
    with open(tasks_yaml_path, 'r') as f:
        tasks_config = yaml.safe_load(f)

    if not agents_config:
        raise ValueError("agents.yaml is empty or invalid")
    if not tasks_config:
        raise ValueError("tasks.yaml is empty or invalid")

    client = get_client()
    tool_template_ids = tool_template_ids or {}

    result = {
        "workflow_id": None,
        "agents_created": [],
        "tasks_created": [],
        "warnings": [],
    }

    # 1. Create workflow
    workflow_resp = client.stub.AddWorkflow(AddWorkflowRequest(
        name=workflow_name,
        is_conversational=is_conversational,
        description=description or f"Imported from CrewAI YAML",
        crew_ai_workflow_metadata=CrewAIWorkflowMetadata(
            process=process_type,
        )
    ))
    workflow_id = workflow_resp.workflow_id
    result["workflow_id"] = workflow_id
    print(f"Created workflow: {workflow_name} ({workflow_id})")

    # 2. Create agents
    agent_map = {}
    for agent_name, agent_config in agents_config.items():
        if not isinstance(agent_config, dict):
            result["warnings"].append(f"Skipping invalid agent: {agent_name}")
            continue

        agent_tools = tool_template_ids.get(agent_name, [])

        try:
            resp = client.stub.AddAgent(AddAgentRequest(
                name=agent_name,
                workflow_id=workflow_id,
                llm_provider_model_id=llm_model_id,
                crew_ai_agent_metadata=CrewAIAgentMetadata(
                    role=agent_config.get('role', agent_name),
                    goal=agent_config.get('goal', ''),
                    backstory=agent_config.get('backstory', ''),
                ),
                tool_template_ids=agent_tools,
            ))
            agent_map[agent_name] = resp.agent_id
            result["agents_created"].append({
                "name": agent_name,
                "id": resp.agent_id,
                "role": agent_config.get('role', ''),
            })
            print(f"  Created agent: {agent_name} ({resp.agent_id})")
        except Exception as e:
            result["warnings"].append(f"Failed to create agent '{agent_name}': {e}")

    # 3. Create tasks
    task_ids = []
    for task_name, task_config in tasks_config.items():
        if not isinstance(task_config, dict):
            result["warnings"].append(f"Skipping invalid task: {task_name}")
            continue

        assigned_agent = task_config.get('agent', '')
        agent_id = agent_map.get(assigned_agent, '')

        if assigned_agent and not agent_id:
            result["warnings"].append(
                f"Task '{task_name}' references unknown agent '{assigned_agent}'"
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
            result["tasks_created"].append({
                "name": task_name,
                "id": resp.task_id,
                "assigned_agent": assigned_agent,
            })
            print(f"  Created task: {task_name} ({resp.task_id})")
        except Exception as e:
            result["warnings"].append(f"Failed to create task '{task_name}': {e}")

    # 4. Update workflow metadata
    if agent_map or task_ids:
        client.stub.UpdateWorkflow(UpdateWorkflowRequest(
            workflow_id=workflow_id,
            crew_ai_workflow_metadata=CrewAIWorkflowMetadata(
                agent_id=list(agent_map.values()),
                task_id=task_ids,
                process=process_type,
            )
        ))

    print(f"\nWorkflow '{workflow_name}' created with {len(result['agents_created'])} agents and {len(result['tasks_created'])} tasks")

    return result


def import_workflow_template(
    template_path: str,
    create_workflow: bool = True,
    workflow_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Import a workflow template from a ZIP file.

    Args:
        template_path: Absolute path to the workflow template ZIP file
        create_workflow: Whether to create a workflow from the template
        workflow_name: Optional name for the created workflow

    Returns:
        Dict with template_id, workflow_id (if created), and any warnings
    """
    from studio.api import (
        ImportWorkflowTemplateRequest,
        AddWorkflowRequest,
        GetWorkflowTemplateRequest,
    )

    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template file not found: {template_path}")

    client = get_client()

    result = {
        "template_id": None,
        "workflow_id": None,
        "template_details": None,
        "warnings": [],
    }

    # Import template
    resp = client.stub.ImportWorkflowTemplate(ImportWorkflowTemplateRequest(
        file_path=template_path
    ))
    template_id = resp.id
    result["template_id"] = template_id
    print(f"Imported template: {template_id}")

    # Get template details
    try:
        template_resp = client.stub.GetWorkflowTemplate(GetWorkflowTemplateRequest(
            id=template_id
        ))
        result["template_details"] = {
            "id": template_resp.workflow_template.id,
            "name": template_resp.workflow_template.name,
        }
    except Exception as e:
        result["warnings"].append(f"Could not get template details: {e}")

    # Create workflow if requested
    if create_workflow:
        name = workflow_name
        if not name and result["template_details"]:
            name = result["template_details"].get("name", f"workflow_{template_id}")
        elif not name:
            name = f"workflow_{template_id}"

        try:
            workflow_resp = client.stub.AddWorkflow(AddWorkflowRequest(
                name=name,
                workflow_template_id=template_id,
            ))
            result["workflow_id"] = workflow_resp.workflow_id
            print(f"Created workflow: {name} ({workflow_resp.workflow_id})")
        except Exception as e:
            result["warnings"].append(f"Failed to create workflow: {e}")

    return result


def export_workflow_template(template_id: str) -> str:
    """
    Export a workflow template to a ZIP file.

    Args:
        template_id: ID of the workflow template to export

    Returns:
        Path to the exported ZIP file
    """
    from studio.api import ExportWorkflowTemplateRequest

    client = get_client()
    resp = client.stub.ExportWorkflowTemplate(ExportWorkflowTemplateRequest(
        id=template_id
    ))
    print(f"Exported template to: {resp.file_path}")
    return resp.file_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CrewAI to Agent Studio Converter")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # List models command
    subparsers.add_parser("list-models", help="List available LLM models")

    # List workflows command
    subparsers.add_parser("list-workflows", help="List existing workflows")

    # List templates command
    subparsers.add_parser("list-templates", help="List workflow templates")

    # Convert command
    convert_parser = subparsers.add_parser("convert", help="Convert CrewAI YAML to workflow")
    convert_parser.add_argument("--agents", required=True, help="Path to agents.yaml")
    convert_parser.add_argument("--tasks", required=True, help="Path to tasks.yaml")
    convert_parser.add_argument("--name", required=True, help="Workflow name")
    convert_parser.add_argument("--model-id", required=True, help="LLM model ID")
    convert_parser.add_argument("--process", default="sequential", choices=["sequential", "hierarchical"])
    convert_parser.add_argument("--conversational", action="store_true")

    # Import command
    import_parser = subparsers.add_parser("import", help="Import workflow template")
    import_parser.add_argument("--path", required=True, help="Path to template ZIP")
    import_parser.add_argument("--name", help="Workflow name")
    import_parser.add_argument("--no-create", action="store_true", help="Don't create workflow")

    args = parser.parse_args()

    if args.command == "list-models":
        models = list_available_models()
        for m in models:
            print(f"{m['id']}: {m['display_name']}")

    elif args.command == "list-workflows":
        workflows = list_workflows()
        for w in workflows:
            status = "ready" if w['is_ready'] else "not ready"
            print(f"{w['id']}: {w['name']} ({status})")

    elif args.command == "list-templates":
        templates = list_workflow_templates()
        for t in templates:
            print(f"{t['id']}: {t['name']}")

    elif args.command == "convert":
        result = convert_crewai_yaml(
            agents_yaml_path=args.agents,
            tasks_yaml_path=args.tasks,
            workflow_name=args.name,
            llm_model_id=args.model_id,
            process_type=args.process,
            is_conversational=args.conversational,
        )
        if result["warnings"]:
            print("\nWarnings:")
            for w in result["warnings"]:
                print(f"  - {w}")

    elif args.command == "import":
        result = import_workflow_template(
            template_path=args.path,
            create_workflow=not args.no_create,
            workflow_name=args.name,
        )
        if result["warnings"]:
            print("\nWarnings:")
            for w in result["warnings"]:
                print(f"  - {w}")

    else:
        parser.print_help()
