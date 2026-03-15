#!/usr/bin/env python
"""
agent_runner.py — DeepResearchAgent RCA 评测接口 (v2.0 框架)

使用 v2.0 框架的 ToolCallingAgent + RCA 工具集，通过 stdin/stdout 与 RolloutRunner 对接。

stdin:  JSON { question, system_prompt, user_prompt,
               compress_system_prompt, compress_user_prompt, data_dir }
stdout: JSON { output (CausalGraph JSON), trajectory (OpenAI 格式) }
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, "/home/nn/SOTA-agents/RolloutRunner")
from src.usage_tracker import UsageTracker

_tracker = UsageTracker()
_tracker.install_openai_hooks()

# 清理 RolloutRunner 路径和 src 模块缓存，避免与本项目的 src 包冲突
sys.path.remove("/home/nn/SOTA-agents/RolloutRunner")
for _mod in list(sys.modules):
    if _mod == "src" or _mod.startswith("src."):
        del sys.modules[_mod]


from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# Add project root to path for v2.0 framework imports
root = str(Path(__file__).resolve().parent)
sys.path.insert(0, root)

from src.config import config
from src.logger import logger
from src.model import model_manager
from src.model.types import ModelConfig
from src.version import version_manager
from src.prompt import prompt_manager
from src.memory import memory_manager
from src.tool import tcp
from src.skill import scp
from src.environment import ecp
from src.agent import acp
from src.session.types import SessionContext
from src.message.types import SystemMessage, HumanMessage

# ── Constants ────────────────────────────────────────────────────────────────

MODEL_NAME = os.getenv("DRA_MODEL_NAME", "claude-sonnet-4-6")
MODEL_KEY = f"openai/{MODEL_NAME}"  # Must match model_name in rca_agent.py config

RCA_THINK_PROMPT = """<Reflection Methodology — MANDATORY>
**CRITICAL: You MUST reflect in your thinking after every round of data queries. Never skip reflection.**

Failing to reflect leads to misidentifying symptoms as root causes. You MUST follow this discipline:

1. **After discovering available data (list_tables_in_directory + get_schema)** → reflect and plan:
   - Which data sources are most relevant to this incident?
   - What should I query first and why?

2. **After EVERY round of SQL queries** → reflect and analyze:
   - What anomalies or patterns did I find?
   - Which services show abnormal behavior?
   - What's the timeline of events?
   - What evidence is still missing to confirm root cause?
   - Am I looking at a symptom or the actual origin?

3. **Before concluding** → verify your hypothesis:
   - Does the evidence clearly point to a single root cause service?
   - Can I trace the full propagation path from root cause to all affected services?
   - The root cause is the UPSTREAM service that INITIATED the failure, not the downstream service with the most errors.

**NEVER output your final CausalGraph without first verifying your conclusion.**
</Reflection Methodology — MANDATORY>
"""


# ── Helper functions ─────────────────────────────────────────────────────────

def strip_markdown_json(text: str) -> str:
    """Strip ```json ... ``` code blocks, extract pure JSON.

    Also attempts to repair truncated JSON by closing unmatched brackets/braces.
    """
    # Try greedy match first (handles nested braces)
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    # Extract everything after ```json opening
    m = re.search(r"```(?:json)?\s*(\{.*)", text, re.DOTALL)
    if m:
        raw = m.group(1)
        # Remove trailing backticks and whitespace
        raw = re.sub(r"[\s`]*$", "", raw)
        # Remove trailing backslashes (LLM artifacts)
        raw = re.sub(r"\\+\s*$", "", raw)
        # Stack-based repair: track nesting to close in correct order
        raw = raw.rstrip().rstrip(",").rstrip("\\").rstrip()
        # Remove any trailing incomplete key-value (after last complete value)
        # by trimming to last }, ] or complete string value
        for _ in range(5):
            raw = raw.rstrip().rstrip(",").rstrip("\\").rstrip()
            # Build nesting stack
            stack = []
            in_string = False
            escape = False
            for ch in raw:
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "{":
                    stack.append("}")
                elif ch == "[":
                    stack.append("]")
                elif ch in "}]":
                    if stack:
                        stack.pop()
            # Close in reverse order
            suffix = "".join(reversed(stack))
            repaired = raw + suffix
            try:
                json.loads(repaired)
                return repaired
            except json.JSONDecodeError:
                # Trim to last complete entry (last } or ] before trailing junk)
                pos = len(raw) - 1
                while pos > 0 and raw[pos] not in "}]":
                    pos -= 1
                if pos <= 0:
                    break
                raw = raw[:pos + 1]

    # No markdown wrapper — try direct JSON parse
    stripped = text.strip()
    try:
        json.loads(stripped)
        return stripped
    except json.JSONDecodeError:
        pass

    return stripped


def tracer_records_to_trajectory(records: list) -> list:
    """Convert v2.0 tracer records to OpenAI-format trajectory.

    Each record has:
      record["tool"] = {thinking, evaluation_previous_goal, memory, next_goal,
                        actions: [{type, name, args, output}, ...]}

    We convert each step to assistant + tool messages.
    """
    trajectory = []

    for record in records:
        tool_data = record.get("tool") if isinstance(record, dict) else None
        if not tool_data:
            continue

        thinking = tool_data.get("thinking", "")
        next_goal = tool_data.get("next_goal", "")
        actions = tool_data.get("actions", [])

        # Build assistant message content
        content_parts = []
        if thinking:
            content_parts.append(f"Thinking: {thinking}")
        if next_goal:
            content_parts.append(f"Next Goal: {next_goal}")
        assistant_content = "\n".join(content_parts)

        # Build tool_calls from actions
        tool_calls = []
        for i, action in enumerate(actions):
            action_name = action.get("name", "")
            action_args = action.get("args", "{}")
            if isinstance(action_args, dict):
                action_args = json.dumps(action_args, ensure_ascii=False)
            record_id = record.get("id", 0)
            tool_call_id = f"call_{record_id}_{i}"
            tool_calls.append({
                "id": tool_call_id,
                "type": "function",
                "function": {
                    "name": action_name,
                    "arguments": action_args,
                },
            })

        assistant_msg = {"role": "assistant", "content": assistant_content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        trajectory.append(assistant_msg)

        # Add tool result messages
        for i, action in enumerate(actions):
            action_output = action.get("output", "")
            record_id = record.get("id", 0)
            tool_call_id = f"call_{record_id}_{i}"
            trajectory.append({
                "role": "tool",
                "content": str(action_output),
                "tool_call_id": tool_call_id,
            })

    return trajectory


async def register_kimi_model():
    """Register kimi-k2 as a custom model in model_manager."""
    kimi_config = ModelConfig(
        model_name=MODEL_KEY,
        model_id=MODEL_NAME,
        model_type="chat/completions",
        provider="openai",
        api_base=os.getenv("OPENAI_API_BASE", "https://api.shubiaobiao.cn/v1"),
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.7,
        max_completion_tokens=32000,
        supports_streaming=False,
        supports_functions=True,
        supports_vision=False,
        fallback_model=None,
    )
    await model_manager.register_model(kimi_config)
    print(f"[RCA] Registered model: {MODEL_KEY}", file=sys.stderr)


async def compress_findings(
    rca_system_prompt: str,
    trajectory: list,
    compress_sp: str,
    compress_up: str,
) -> str:
    """Call LLM to compress conversation trajectory into CausalGraph JSON."""
    # Build conversation history summary for compression
    conversation_summary = []
    for msg in trajectory:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if content:
            conversation_summary.append(f"[{role}] {content[:2000]}")

    conversation_text = "\n\n".join(conversation_summary)

    messages = [
        SystemMessage(content=compress_sp),
        HumanMessage(
            content=(
                f"## RCA Analysis Context\n\n{rca_system_prompt}\n\n"
                f"## Investigation History\n\n{conversation_text}\n\n"
                f"---\n\n{compress_up}"
            )
        ),
    ]

    print("[Compress] calling LLM to synthesize CausalGraph...", file=sys.stderr)

    response = await model_manager(
        model=MODEL_KEY,
        messages=messages,
    )

    return response.message


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    payload = json.loads(sys.stdin.read())

    system_prompt = payload["system_prompt"]
    user_prompt = payload["user_prompt"]
    compress_sp = payload["compress_system_prompt"]
    compress_up = payload["compress_user_prompt"]
    data_dir = payload.get("data_dir", "")

    # 过滤 think_tool 相关内容（v2.0 框架用原生 ThinkOutput 替代，无需 think_tool）
    system_prompt = re.sub(r"  4\. \*\*think_tool\*\*.*\n", "", system_prompt)
    system_prompt = system_prompt.replace("four tools", "three tools")

    # Build the task: combine RCA domain instructions + user prompt + data_dir
    task_parts = [
        "## RCA Domain Instructions\n\n",
        system_prompt,
        "\n\n---\n\n",
        RCA_THINK_PROMPT,
        "\n\n---\n\n",
        "## Incident Analysis Task\n\n",
        user_prompt,
    ]

    if data_dir:
        task_parts.extend([
            "\n\n## Data Location\n\n",
            f"The telemetry data for this incident is located at: `{data_dir}`\n\n",
            f'Start by calling `list_tables_in_directory` with directory="{data_dir}" ',
            "to discover available parquet files.",
        ])

    task = "".join(task_parts)

    # Initialize v2.0 framework
    config_path = os.path.join(root, "configs", "rca_agent.py")
    print(f"[RCA] Initializing v2.0 framework with config: {config_path}", file=sys.stderr)

    # Create a minimal args namespace for config.initialize
    # Must use argparse.Namespace (supports `in` operator), not a plain class
    from argparse import Namespace
    args = Namespace(config=config_path, cfg_options=None)

    config.initialize(config_path=args.config, args=args)
    logger.initialize(config=config)

    # Initialize model manager and register kimi
    print("[RCA] Initializing model manager...", file=sys.stderr)
    await model_manager.initialize()
    await register_kimi_model()

    # Initialize prompt manager
    print("[RCA] Initializing prompt manager...", file=sys.stderr)
    await prompt_manager.initialize()

    # Initialize memory manager
    print("[RCA] Initializing memory manager...", file=sys.stderr)
    await memory_manager.initialize(memory_names=config.memory_names)

    # Initialize tools (RCA tools + done)
    print("[RCA] Initializing tools...", file=sys.stderr)
    await tcp.initialize(tool_names=config.tool_names)
    tool_list = await tcp.list()
    print(f"[RCA] Tools initialized: {tool_list}", file=sys.stderr)

    # Initialize skills (none for RCA)
    skill_names = getattr(config, "skill_names", None)
    await scp.initialize(skill_names=skill_names)

    # Initialize environments
    print("[RCA] Initializing environments...", file=sys.stderr)
    await ecp.initialize(config.env_names)

    # Initialize agents
    print("[RCA] Initializing agents...", file=sys.stderr)
    await acp.initialize(agent_names=config.agent_names)

    # Initialize version manager
    await version_manager.initialize()

    # Clean up stale tracer.json from previous runs to avoid polluting
    # memory/history with old done calls and other records
    tracer_cleanup_path = os.path.join(config.workdir, "tracer.json")
    if os.path.exists(tracer_cleanup_path):
        os.remove(tracer_cleanup_path)
        print(f"[RCA] Cleaned up stale tracer: {tracer_cleanup_path}", file=sys.stderr)

    # Create session context
    ctx = SessionContext()

    # Run the ToolCallingAgent via ACP
    print(f"[RCA] Running ToolCallingAgent with task length={len(task)}", file=sys.stderr)

    agent_input = {
        "name": "tool_calling",
        "input": {
            "task": task,
            "files": [],
        },
        "ctx": ctx,
    }

    agent_response = await acp(**agent_input)
    print(f"[RCA] Agent completed: success={agent_response.success}", file=sys.stderr)

    # Extract trajectory from tracer
    # The tracer is saved per-agent in workdir/tracer.json
    tracer_path = os.path.join(config.workdir, "tracer.json")
    trajectory = []

    if os.path.exists(tracer_path):
        with open(tracer_path, "r") as f:
            tracer_data = json.load(f)
        # Extract records from all sessions
        all_records = []
        for session_id, records in tracer_data.get("sessions", {}).items():
            all_records.extend(records)
        trajectory = tracer_records_to_trajectory(all_records)
    else:
        print("[RCA] Warning: tracer.json not found, empty trajectory", file=sys.stderr)

    # Compress findings into CausalGraph JSON
    compressed = await compress_findings(
        rca_system_prompt=system_prompt,
        trajectory=trajectory,
        compress_sp=compress_sp,
        compress_up=compress_up,
    )

    # Build output
    result = {
        "output": strip_markdown_json(compressed),
        "trajectory": trajectory,
        "usage": _tracker.get_usage(),
    }

    # Single-line JSON output (runner parses last line)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
