import requests
import json
import argparse
import os
from pydantic import BaseModel, Field
from typing import Optional, Any

class UserParameters(BaseModel):
    """
    Configuration for the YOLO API. 
    Since your endpoint is public, these are optional.
    """
    api_url: str = "https://xray-yolo-api.ml-e54c7b5e-fcc.qzhong-1.a465-9q4k.cloudera.site/v1/detect"

class ToolParameters(BaseModel):
    """
    Arguments passed by the Agent.
    """
    image_path: str = Field(description="The relative path to the image file in the workspace (e.g., 'scan.png')")

def run_tool(config: UserParameters, args: ToolParameters) -> Any:
    """
    Calls the YOLO detection API and returns the results to the agent.
    """
    
    if not os.path.exists(args.image_path):
        return {"error": f"File not found: {args.image_path}"}

    try:
        # Prepare the file for multipart/form-data upload
        with open(args.image_path, 'rb') as f:
            files = {
                'file': (os.path.basename(args.image_path), f, 'image/png')
            }
            
            # Use the URL from UserParameters
            response = requests.post(config.api_url, files=files, timeout=60)
            response.raise_for_status()
            
            return response.json()

    except Exception as e:
        return {"error": f"API call failed: {str(e)}"}

OUTPUT_KEY = "tool_output"

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-params", required=True)
    parser.add_argument("--tool-params", required=True)
    args = parser.parse_args()
    
    user_dict = json.loads(args.user_params)
    tool_dict = json.loads(args.tool_params)
    
    config = UserParameters(**user_dict)
    params = ToolParameters(**tool_dict)
    
    output = run_tool(config, params)
    print(OUTPUT_KEY, json.dumps(output))