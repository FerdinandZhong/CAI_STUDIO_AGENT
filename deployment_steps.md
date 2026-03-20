

## Step 1: Fork and Structure the Repo


```bash
# Fork CAI_STUDIO_AGENT
git clone https://github.com/your-org/CAI_STUDIO_AGENT.git
cd CAI_STUDIO_AGENT

# Create directory for workflow templates
mkdir -p workflow_templates

# Add your exported workflow template
cp /path/to/my_workflow_template.zip workflow_templates/

# Create deployment configuration
cat > workflow_templates/deployment_config.json << 'EOF'
{
"workflows": [
{
"template_zip": "workflow_templates/my_workflow_template.zip",
"workflow_name": "Production Workflow",
"auto_deploy": true,
"tool_configs": {
"tool-id-placeholder": {
"api_key": "${TOOL_API_KEY}",
"endpoint": "${TOOL_ENDPOINT}"
}
},
"mcp_configs": {
"mcp-id-placeholder": {
"API_KEY": "${MCP_API_KEY}"
}
},
"llm_config": {
"model_id": "gpt-4",
"api_key": "${OPENAI_API_KEY}",
"base_url": "https://api.openai.com/v1"
},
"deployment_config": {
"cpu": 2,
"memory": 4,
"replicas": 1
}
}
]
}
EOF

git add workflow_templates/
git commit -m "Add workflow templates for automated deployment"
git push
```

## Step 2: Create Auto-Deployment Script

Create startup_scripts/auto_deploy_workflows.py:


```python
#!/usr/bin/env python3
"""
Auto-deploy workflows from templates on project initialization.
This script runs after Agent Studio startup.
"""

import os
import sys
import json
import time
from pathlib import Path


# Add studio to path
app_dir = os.getenv("APP_DIR")
if app_dir:
sys.path.insert(0, app_dir)

from studio.client import AgentStudioClient
from studio.api import *


def wait_for_studio_ready(max_wait=300):
"""Wait for Agent Studio gRPC service to be ready."""
print("Waiting for Agent Studio to be ready...")
start = time.time()
while time.time() - start < max_wait:
try:
studio = AgentStudioClient()
studio.stub.ListWorkflows(ListWorkflowsRequest())
print("✓ Agent Studio is ready")
return True
except Exception as e:
print(f"Waiting... ({int(time.time() - start)}s)")
time.sleep(5)
return False


def resolve_env_vars(obj):
"""Recursively resolve ${ENV_VAR} in config."""
if isinstance(obj, dict):
return {k: resolve_env_vars(v) for k, v in obj.items()}
elif isinstance(obj, list):
return [resolve_env_vars(item) for item in obj]
elif isinstance(obj, str) and obj.startswith("${") and obj.endswith("}"):
env_var = obj[2:-1]
value = os.getenv(env_var)
if not value:
raise ValueError(f"Environment variable {env_var} not set")
return value
return obj


def deploy_workflow_from_template(studio, workflow_config):
"""Deploy a single workflow from template configuration."""

template_zip = workflow_config["template_zip"]
workflow_name = workflow_config["workflow_name"]


print(f"\n{'='*60}")
print(f"Deploying workflow: {workflow_name}")
print(f"{'='*60}")


# Resolve environment variables in configs
tool_configs = resolve_env_vars(workflow_config.get("tool_configs", {}))
mcp_configs = resolve_env_vars(workflow_config.get("mcp_configs", {}))
llm_config = resolve_env_vars(workflow_config.get("llm_config", {}))


# 1. Check if template already imported
print("Checking existing templates...")
templates_resp = studio.stub.ListWorkflowTemplates(ListWorkflowTemplatesRequest())
existing_template = next(
(t for t in templates_resp.workflow_templates if t.name == workflow_name),
None
)


if existing_template:
print(f"✓ Template already exists: {existing_template.id}")
template_id = existing_template.id
else:
# 2. Import template
print(f"Importing template from {template_zip}...")
abs_path = os.path.abspath(template_zip)
if not os.path.exists(abs_path):
raise FileNotFoundError(f"Template not found: {abs_path}")

import_resp = studio.stub.ImportWorkflowTemplate(
ImportWorkflowTemplateRequest(file_path=abs_path)
)
template_id = import_resp.workflow_template_id

print(f"✓ Template imported: {template_id}")


# 3. Get template details to extract tool/MCP IDs
print("Fetching template details...")
template = studio.stub.GetWorkflowTemplate(
GetWorkflowTemplateRequest(id=template_id)
).workflow_template


# 4. Map tool configs (if template has tools)
tool_params = {}
if template.tool_template_ids:
print(f"Configuring {len(template.tool_template_ids)} tools...")
for i, tool_template_id in enumerate(template.tool_template_ids):
# Get tool template details
tool_template = studio.stub.GetToolTemplate(
GetToolTemplateRequest(id=tool_template_id)
).tool_template


# Find matching config by name or use first available
tool_config = None
for config_key, config_value in tool_configs.items():
if config_key in tool_template.name.lower():
tool_config = config_value
break


if not tool_config and i == 0:
tool_config = list(tool_configs.values())[0] if tool_configs else {}

tool_params[tool_template_id] = KeyValuePairs(
parameters=tool_config or {}
)

print(f"  ✓ Tool: {tool_template.name}")


# 5. Map MCP configs
mcp_params = {}
if template.mcp_template_ids:
print(f"Configuring {len(template.mcp_template_ids)} MCP instances...")
for mcp_template_id in template.mcp_template_ids:
mcp_template = studio.stub.GetMCPTemplate(
GetMCPTemplateRequest(id=mcp_template_id)
).mcp_template


# Find matching config
mcp_config = None
for config_key, config_value in mcp_configs.items():
if config_key in mcp_template.name.lower():
mcp_config = config_value
break

mcp_params[mcp_template_id] = KeyValuePairs(
parameters=mcp_config or {}
)

print(f"  ✓ MCP: {mcp_template.name}")


# 6. Check if workflow already exists
workflows_resp = studio.stub.ListWorkflows(ListWorkflowsRequest())
existing_workflow = next(
(w for w in workflows_resp.workflows if w.name == workflow_name),
None
)


if existing_workflow:
print(f"✓ Workflow already exists: {existing_workflow.workflow_id}")
workflow_id = existing_workflow.workflow_id
else:
# 7. Create workflow from template
print("Creating workflow instance...")
workflow_resp = studio.stub.AddWorkflow(
AddWorkflowRequest(
name=workflow_name,
workflow_template_id=template_id,
tool_user_parameters=tool_params,
mcp_instance_env_vars=mcp_params
)
)
workflow_id = workflow_resp.workflow.workflow_id
print(f"✓ Workflow created: {workflow_id}")

# 8. Deploy if auto_deploy is enabled
if workflow_config.get("auto_deploy", False):
# Check if already deployed
deployed_resp = studio.stub.ListDeployedWorkflows(ListDeployedWorkflowsRequest())
already_deployed = any(
d.workflow_id == workflow_id for d in deployed_resp.deployed_workflows
)


if already_deployed:
print("✓ Workflow already deployed")
else:
print("Deploying workflow...")

generation_config = json.dumps({

"temperature": 0.7,
"max_new_tokens": 4096,
"do_sample": True
})

studio.stub.DeployWorkflow(
DeployWorkflowRequest(
workflow_id=workflow_id,
generation_config=generation_config,
tool_user_parameters=tool_params,
mcp_instance_env_vars=mcp_params,
env_variable_overrides=workflow_config.get("env_overrides", {})
)
)

print("✓ Deployment initiated")

# Monitor deployment
print("Monitoring deployment (this may take a few minutes)...")
for _ in range(60):  # 10 minutes max
time.sleep(10)
deployed_resp = studio.stub.ListDeployedWorkflows(
ListDeployedWorkflowsRequest()
)
deployed = next(
(d for d in deployed_resp.deployed_workflows
if d.workflow_id == workflow_id),
None
)
if deployed:
status = deployed.application_status
print(f"  Status: {status}")
if status == "deployed":
print(f"\n✓✓✓ DEPLOYMENT COMPLETE ✓✓✓")
print(f"Application URL: {deployed.application_url}")
return True
elif "fail" in status.lower():
print(f"\n✗ Deployment failed with status: {status}")
return False

return True


def main():
"""Main deployment orchestrator."""
print("\n" + "="*60)
print("Agent Studio Workflow Auto-Deployment")
print("="*60 + "\n")

# Wait for Studio to be ready
if not wait_for_studio_ready():
print("✗ Agent Studio failed to start in time")
sys.exit(1)


# Load deployment configuration
config_path = "workflow_templates/deployment_config.json"
if not os.path.exists(config_path):
print(f"No deployment config found at {config_path}")
print("Skipping auto-deployment")
return

with open(config_path, 'r') as f:
config = json.load(f)

workflows = config.get("workflows", [])

if not workflows:
print("No workflows configured for deployment")
return

print(f"Found {len(workflows)} workflow(s) to deploy\n")


# Connect to Studio
studio = AgentStudioClient()


# Deploy each workflow
success_count = 0
for workflow_config in workflows:
try:
if deploy_workflow_from_template(studio, workflow_config):
success_count += 1
except Exception as e:
print(f"\n✗ Error deploying workflow: {e}")
import traceback
traceback.print_exc()

print("\n" + "="*60)
print(f"Deployment Summary: {success_count}/{len(workflows)} successful")
print("="*60 + "\n")


if __name__ == "__main__":
main()


## Step 3: Hook into Startup Process

Edit .project-metadata.yaml to run after Studio starts:

```yaml
name: Agent Studio with Auto-Deploy
description: Agent Studio with pre-configured workflows

environment_variables:
  # Workflow deployment configs
  TOOL_API_KEY: ""
  TOOL_ENDPOINT: "https://api.example.com"
  MCP_API_KEY: ""
  OPENAI_API_KEY: ""

  # Auto-deploy flag
  AUTO_DEPLOY_WORKFLOWS: "true"

  # Existing Studio variables
  AGENT_STUDIO_DEPLOY_MODE: "amp"
  # ... other vars

runtimes:
  - editor: Workbench
    kernel: Python 3.10
    edition: Standard
```

Modify startup_scripts/run-app.py to trigger auto-deployment:

```python
# At the end of startup_scripts/run-app.py, add:

# Auto-deploy workflows if enabled
if os.getenv("AUTO_DEPLOY_WORKFLOWS", "false").lower() == "true":
print("\n" + "="*60)
print("Starting workflow auto-deployment...")
print("="*60)


import subprocess
subprocess.Popen([
    sys.executable,
    os.path.join(app_dir, "startup_scripts", "auto_deploy_workflows.py")
])
```

## Step 4: Use CML API to Create Project

```python
#!/usr/bin/env python3
"""
Create a new CML project with Agent Studio and auto-deploy workflows.
Run this script from any environment with CML API access.
"""

import cmlapi
import os
import time


# Configuration
CML_HOST = "https://your-cml-instance.domain.com"
API_KEY = "your-cml-api-key"
PROJECT_NAME = "Production Agent Studio"
REPO_URL = "https://github.com/your-org/CAI_STUDIO_AGENT.git"
REPO_BRANCH = "main"


# Environment variables for the project
PROJECT_ENV_VARS = {
# Workflow deployment configs
"TOOL_API_KEY": "your-tool-api-key",
"TOOL_ENDPOINT": "https://api.example.com",
"MCP_API_KEY": "your-mcp-key",
"OPENAI_API_KEY": "sk-xxx",

# Enable auto-deployment
"AUTO_DEPLOY_WORKFLOWS": "true",

# Agent Studio configs
"AGENT_STUDIO_DEPLOY_MODE": "amp",
}


def create_project_from_git():
"""Create a new CML project from Git repository."""


# Initialize CML client
client = cmlapi.default_client(url=CML_HOST, cml_api_key=API_KEY)

print(f"Creating project: {PROJECT_NAME}")
print(f"From repository: {REPO_URL}")


# Create project
project = client.create_project(
body=cmlapi.CreateProjectRequest(
name=PROJECT_NAME,
description="Agent Studio with pre-configured workflows",
template="git",
git_url=REPO_URL,
default_project_engine_type="ml_runtime"
)
)

project_id = project.id

print(f"✓ Project created: {project_id}")

# Set environment variables
print("Setting environment variables...")
for key, value in PROJECT_ENV_VARS.items():
try:
client.create_project_environment_variable(
project_id=project_id,
body=cmlapi.CreateEnvironmentVariableRequest(
key=key,
value=value
)
)
print(f"  ✓ Set {key}")
except Exception as e:
print(f"  ✗ Failed to set {key}: {e}")

# Wait for project to initialize
print("\nWaiting for project initialization...")
for i in range(60):  # 10 minutes max
time.sleep(10)
proj = client.get_project(project_id)
if proj.creation_status == "success":
print("✓ Project initialized successfully")
break
elif proj.creation_status == "failed":
print("✗ Project initialization failed")
return None
print(f"  Status: {proj.creation_status} ({i*10}s)")

return project


def create_studio_application(project_id):
"""Create the Agent Studio application."""

client = cmlapi.default_client(url=CML_HOST, cml_api_key=API_KEY)


print("\nCreating Agent Studio application...")


# Get runtime
runtimes = client.list_runtimes(
search_filter=json.dumps({
"kernel": "Python 3.10",
"edition": "Standard"
})
)
runtime_id = runtimes.runtimes[0].image_identifier


# Create application
app = client.create_application(
project_id=project_id,
body=cmlapi.CreateApplicationRequest(
name="Agent Studio",
description="AI Agent Studio with Auto-Deploy",
subdomain=f"agent-studio-{project_id[:8]}",
script="startup_scripts/run-app.py",
cpu=2,
memory=8,
nvidia_gpu=0,
runtime_identifier=runtime_id,
bypass_authentication=False
)
)

print(f"✓ Application created: {app.id}")
print(f"✓ Application URL: {app.url}")

# Monitor application startup
print("\nMonitoring application startup...")
for i in range(120):  # 20 minutes max
time.sleep(10)
app = client.get_application(project_id, app.id)
status = app.status
print(f"  Status: {status} ({i*10}s)")

if status == "running":
print("\n✓✓✓ Agent Studio is running!")
print(f"URL: {app.url}")
print("\nWorkflows are being auto-deployed in the background.")
print("Check application logs for deployment progress.")
return app
elif status == "failed":
print("\n✗ Application failed to start")
return None

return None


def main():
"""Main orchestration."""
print("\n" + "="*80)
print("CML Project Creation with Agent Studio Auto-Deploy")
print("="*80 + "\n")


# Create project
project = create_project_from_git()
if not project:
print("\n✗ Failed to create project")
return

print(f"\n✓ Project ready: {project.name} (ID: {project.id})")


# Create Studio application
app = create_studio_application(project.id)
if not app:
print("\n✗ Failed to create application")
return

print("\n" + "="*80)
print("DEPLOYMENT COMPLETE")
print("="*80)
print(f"\nAgent Studio URL: {app.url}")
print(f"Project ID: {project.id}")
print("\nWorkflows will be automatically deployed in the background.")
print("Access the application to see deployment progress.")
print("="*80 + "\n")


if __name__ == "__main__":
    import json
    main()
```
