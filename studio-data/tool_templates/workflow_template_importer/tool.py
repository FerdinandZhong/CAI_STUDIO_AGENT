"""
Workflow Template Importer Tool

Imports Agent Studio workflow templates from ZIP files.
This tool uses the Agent Studio gRPC API to import pre-packaged workflow templates
and optionally create new workflows from them.

Usage:
- Provide path to a workflow template ZIP file
- Optionally create a new workflow from the imported template
"""

from pydantic import BaseModel, Field
from typing import Optional, Any, Dict, List
import json
import argparse
import os


class UserParameters(BaseModel):
    """
    Configuration parameters for the workflow template importer.
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
    Arguments for the workflow template import operation.
    """
    template_zip_path: str = Field(
        description="Absolute path to the workflow template ZIP file"
    )
    create_workflow: bool = Field(
        default=True,
        description="Whether to create a workflow from the imported template"
    )
    workflow_name: Optional[str] = Field(
        default=None,
        description="Optional name for the created workflow (uses template name if not provided)"
    )
    list_templates: bool = Field(
        default=False,
        description="List all available workflow templates before import"
    )


def list_workflow_templates(client) -> List[Dict]:
    """List all available workflow templates."""
    try:
        from studio.api import ListWorkflowTemplatesRequest
        resp = client.stub.ListWorkflowTemplates(ListWorkflowTemplatesRequest())
        templates = []
        for t in resp.workflow_templates:
            templates.append({
                "id": t.id,
                "name": t.name,
                "description": getattr(t, 'description', ''),
            })
        return templates
    except Exception as e:
        return [{"error": str(e)}]


def run_tool(config: UserParameters, args: ToolParameters) -> Any:
    """
    Import workflow template from ZIP file.
    """
    try:
        from studio.client import AgentStudioClient
        from studio.api import (
            ImportWorkflowTemplateRequest,
            AddWorkflowRequest,
            ListWorkflowTemplatesRequest,
            GetWorkflowTemplateRequest,
        )
    except ImportError as e:
        return {
            "success": False,
            "error": f"Failed to import Agent Studio modules: {e}. This tool must run within CAI environment."
        }

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
        "template_id": None,
        "workflow_id": None,
        "templates": None,
        "warnings": []
    }

    # List templates if requested
    if args.list_templates:
        results["templates"] = list_workflow_templates(client)

    # Validate file path
    if not os.path.exists(args.template_zip_path):
        return {"success": False, "error": f"Template ZIP file not found: {args.template_zip_path}"}

    if not args.template_zip_path.endswith('.zip'):
        results["warnings"].append("File does not have .zip extension, proceeding anyway...")

    try:
        # 1. Import the workflow template
        import_resp = client.stub.ImportWorkflowTemplate(ImportWorkflowTemplateRequest(
            file_path=args.template_zip_path
        ))
        template_id = import_resp.id
        results["template_id"] = template_id
        results["message"] = f"Successfully imported workflow template: {template_id}"

        # 2. Get template details
        try:
            template_resp = client.stub.GetWorkflowTemplate(GetWorkflowTemplateRequest(
                id=template_id
            ))
            results["template_details"] = {
                "id": template_resp.workflow_template.id,
                "name": template_resp.workflow_template.name,
                "description": getattr(template_resp.workflow_template, 'description', ''),
            }
        except Exception as e:
            results["warnings"].append(f"Could not get template details: {e}")

        # 3. Create workflow from template if requested
        if args.create_workflow:
            try:
                workflow_name = args.workflow_name
                if not workflow_name:
                    # Use template name if available
                    if "template_details" in results:
                        workflow_name = results["template_details"].get("name", f"workflow_{template_id}")
                    else:
                        workflow_name = f"workflow_{template_id}"

                workflow_resp = client.stub.AddWorkflow(AddWorkflowRequest(
                    name=workflow_name,
                    workflow_template_id=template_id,
                ))
                results["workflow_id"] = workflow_resp.workflow_id
                results["message"] += f"\nCreated workflow '{workflow_name}' with ID: {workflow_resp.workflow_id}"

            except Exception as e:
                results["warnings"].append(f"Failed to create workflow from template: {e}")

    except Exception as e:
        results["success"] = False
        results["error"] = str(e)

    return results


def export_template(client, template_id: str) -> Dict:
    """
    Export a workflow template to a ZIP file.
    Helper function for creating shareable templates.
    """
    try:
        from studio.api import ExportWorkflowTemplateRequest
        resp = client.stub.ExportWorkflowTemplate(ExportWorkflowTemplateRequest(
            id=template_id
        ))
        return {
            "success": True,
            "file_path": resp.file_path,
            "message": f"Template exported to: {resp.file_path}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


OUTPUT_KEY = "tool_output"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Workflow Template Importer")
    parser.add_argument("--user-params", required=True, help="User configuration JSON")
    parser.add_argument("--tool-params", required=True, help="Tool arguments JSON")
    args = parser.parse_args()

    user_dict = json.loads(args.user_params)
    tool_dict = json.loads(args.tool_params)

    config = UserParameters(**user_dict)
    params = ToolParameters(**tool_dict)

    output = run_tool(config, params)
    print(OUTPUT_KEY, json.dumps(output, indent=2))
