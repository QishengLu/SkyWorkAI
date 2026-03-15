"""RCA Agent Configuration - uses v2.0 ToolCallingAgent with RCA tools + kimi model."""

from mmengine.config import read_base

with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.tool_calling import tool_calling_agent
    from .tools.rca_list_tables import list_tables_in_directory_tool
    from .tools.rca_get_schema import get_schema_tool
    from .tools.rca_query_parquet import query_parquet_files_tool
    from .environments.file_system import environment as file_system_environment
    from .memory.general_memory_system import memory_system as general_memory_system
    from .memory.optimizer_memory_system import memory_system as optimizer_memory_system

tag = "rca_agent"
workdir = f"workdir/{tag}"
log_path = "rca_agent.log"

use_local_proxy = False
version = "0.1.0"

# Model: kimi-k2 via Moonshot API (OpenAI-compatible)
# The actual model is registered dynamically in agent_runner.py
model_name = "openai/claude-sonnet-4-6"

env_names = [
    "file_system",
]
memory_names = [
    "general_memory_system",
    "optimizer_memory_system",
]
agent_names = [
    "tool_calling",
]
# RCA tools: 3 parquet tools + done (v2.0 ThinkOutput handles reflection natively)
tool_names = [
    "list_tables_in_directory",
    "get_schema",
    "query_parquet_files",
    "done",
]
skill_names = []

# ── Tool configs ──
list_tables_in_directory_tool.update(require_grad=False)
get_schema_tool.update(require_grad=False)
query_parquet_files_tool.update(require_grad=False)

# ── Memory configs ──
general_memory_system.update(
    base_dir="memory/general_memory_system",
    model_name=model_name,
    max_summaries=10,
    max_insights=10,
    require_grad=False,
)
optimizer_memory_system.update(
    base_dir="memory/optimizer_memory_system",
    model_name=model_name,
    max_records_per_session=10,
    require_grad=False,
)

# ── Environment config ──
file_system_environment.update(
    base_dir="environment/file_system",
    require_grad=False,
)

# ── Agent config ──
tool_calling_agent.update(
    workdir=workdir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,   # Must be True: agent_history tracks tool results across steps
    use_todo=False,    # No todo needed for RCA
    max_steps=50,      # 放宽至 50 steps
)
