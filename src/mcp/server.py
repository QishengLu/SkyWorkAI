import os
import sys
import logging

from fastmcp import FastMCP
from dotenv import load_dotenv
import asyncio
from pathlib import Path

# 禁用所有日志输出到 stdout，防止干扰 JSONRPC 通信
logging.getLogger().setLevel(logging.CRITICAL)
logging.getLogger('mcp').setLevel(logging.CRITICAL)  
logging.getLogger('fastmcp').setLevel(logging.CRITICAL)

root = str(Path(__file__).resolve().parents[2])
sys.path.append(root)

from src.utils import assemble_project_path
# from src.logger import logger  # 注释掉以避免日志输出

# Load environment variables
load_dotenv(override=True)

# Initialize FastMCP
mcp = FastMCP("LocalMCP")
_mcp_tools_namespace = {}

async def register_tool_from_script(script_info):
    """
    Register a tool from a script content.
    """

    name = script_info.get("name", "UnnamedTool")
    description = script_info.get("description", "No description provided.")
    script_content = script_info.get("script_content", "")

    if script_content.startswith('```python'):
        script_content = script_content.replace('```python', '')
    if script_content.endswith('```'):
        script_content = script_content.replace('```', '')

    # Debug output to stderr (不会干扰 JSONRPC)
    if os.getenv("DEBUG"):
        print(f"Registering tool: {name}", file=sys.stderr)
    
    try:
        exec(script_content, _mcp_tools_namespace)
    except Exception as e:
        if os.getenv("DEBUG"):
            print(f"Error executing script for tool '{name}': {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
        return

    tool_function = _mcp_tools_namespace.get(name, None)
    if tool_function is None:
        if os.getenv("DEBUG"):
            print(f"Tool function '{name}' not found in script content.", file=sys.stderr)
        return
    else:
        try:
            # Check function signature for debugging
            import inspect
            sig = inspect.signature(tool_function)
            if os.getenv("DEBUG"):
                print(f"Function signature for {name}: {sig}", file=sys.stderr)
            
            mcp.tool(
                tool_function,
                name=name,
                description=description,
            )
            if os.getenv("DEBUG"):
                print(f"Tool '{name}' registered successfully.", file=sys.stderr)
        except Exception as e:
            if os.getenv("DEBUG"):
                print(f"Error registering tool '{name}': {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)

async def register_tools(script_info_path):
    """
    Register tools from a JSON file containing script information.
    """
    import json

    try:
        with open(script_info_path, 'r') as f:
            script_info_list = json.load(f)

        for script_info in script_info_list:
            await register_tool_from_script(script_info)

    except FileNotFoundError:
        if os.getenv("DEBUG"):
            print(f"Script info file not found: {script_info_path}", file=sys.stderr)
    except json.JSONDecodeError:
        if os.getenv("DEBUG"):
            print(f"Error decoding JSON from script info file: {script_info_path}", file=sys.stderr)
    except Exception as e:
        if os.getenv("DEBUG"):
            print(f"An unexpected error occurred while registering tools: {e}", file=sys.stderr)

    if os.getenv("DEBUG"):
        print("All tools registered successfully.", file=sys.stderr)

    mcp_tools = await mcp.get_tools()
    if os.getenv("DEBUG"):
        print(f"Registered tools: {', '.join([tool for tool in mcp_tools])}", file=sys.stderr)

if __name__ == "__main__":
    script_info_path = assemble_project_path(os.path.join("src", "mcp", "local", "mcp_tools_registry.json"))
    asyncio.run(register_tools(script_info_path))
    mcp.run()