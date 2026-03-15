# CLAUDE.md — DeepResearchAgent / RCA Agent

## 项目概述

基于 SkyworkAI/DeepResearchAgent v2.0 框架，通过 `agent_runner.py` 接入 RolloutRunner 评测管线执行 RCA（根因分析）。使用 v2.0 的 ToolCallingAgent + MMEngine config + kimi-k2 模型。

**核心理念**：使用项目原本的框架和逻辑（ToolCallingAgent、ToolContextProtocol、ModelManager、PromptManager），只替换工具集和注入 RCA 领域 prompt，不绕过框架。

---

## RCA 评测接口

### 入口文件

| 文件 | 作用 |
|------|------|
| `agent_runner.py` | **RolloutRunner 接口**。v2.0 框架初始化 + ToolCallingAgent + compress |
| `src/tool/default_tools/rca_tools.py` | 3 个 RCA Tool 子类（v2.0 Tool 格式） |
| `configs/rca_agent.py` | MMEngine config：kimi 模型 + RCA 工具 + agent 参数 |

### agent_runner.py 流程

```
stdin → 解析 payload (6 字段)
  → 构建 task（system_prompt + rca_think_prompt + user_prompt + data_dir）
  → 初始化 v2.0 框架（config → model_manager → prompt_manager → tcp → acp）
  → 注册 kimi 模型到 model_manager
  → ToolCallingAgent ReAct 循环（Think → Parse → Execute → done → end）
  → 从 tracer.json 提取 trajectory
  → compress_findings（LLM 合成 CausalGraph JSON）
  → stdout 输出 {output, trajectory}
```

### v2.0 RCA 工具集

| Tool Class | name | 功能 |
|-----------|------|------|
| `ListTablesInDirectoryTool` | `list_tables_in_directory` | 列出目录中所有 parquet 文件及元数据 |
| `GetSchemaTool` | `get_schema` | 获取 parquet 文件的列名和类型 |
| `QueryParquetFilesTool` | `query_parquet_files` | 用 DuckDB SQL 查询 parquet 数据（token 限制 5000） |
| `DoneTool` | `done` | v2.0 内置，标记任务完成 |

> 注：v2.0 ToolCallingAgent 内置 ThinkOutput 结构化推理（thinking, evaluation, memory, next_goal, actions），无需额外 ThinkTool。
> agent_runner.py 会自动过滤 system_prompt 中的 think_tool 引用，避免 LLM 尝试调用不存在的工具。
> max_steps 已设为 50（configs/rca_agent.py）。

### stdin/stdout 协议

**stdin (6 字段):**
```json
{
  "question": "augmented_question 原文",
  "system_prompt": "RCA_ANALYSIS_SP (已 format date)",
  "user_prompt": "RCA_ANALYSIS_UP (已 format incident_description)",
  "compress_system_prompt": "COMPRESS_FINDINGS_SP",
  "compress_user_prompt": "COMPRESS_FINDINGS_UP",
  "data_dir": "/path/to/eval-data/<exp_id>/data_XXXXXXXX"
}
```

**stdout (最后一行 JSON):**
```json
{
  "output": "{\"nodes\": [...], \"edges\": [...], \"root_causes\": [...]}",
  "trajectory": [
    {"role": "assistant", "content": "...", "tool_calls": [...]},
    {"role": "tool", "content": "...", "tool_call_id": "..."}
  ]
}
```

---

## 与 thinkdepthai 的对比

| 维度 | thinkdepthai (Deep_Research) | DeepResearchAgent |
|------|-----|------|
| 框架 | LangGraph StateGraph | v2.0 ToolCallingAgent (ThinkOutput + ReAct) |
| 工具调用 | LangChain @tool + bind_tools | v2.0 Tool 子类 + ToolContextProtocol |
| 模型封装 | langchain init_chat_model | v2.0 ModelManager + ChatOpenAI |
| 推理格式 | 自由文本 + function calling | 结构化 ThinkOutput JSON (thinking, evaluation, memory, next_goal, actions) |
| 依赖 | langgraph, langchain | v2.0 framework (mmengine, openai, pydantic) |
| 配置系统 | 环境变量 | MMEngine Python config |

---

## 关键文件

```
DeepResearchAgent/
├── agent_runner.py                      # RolloutRunner stdin/stdout 接口 (v2.0 框架)
├── configs/
│   ├── rca_agent.py                     # RCA agent MMEngine config
│   ├── agents/tool_calling.py           # ToolCallingAgent 参数
│   └── tools/
│       ├── rca_think.py                 # ThinkTool config（未使用，v2.0 ThinkOutput 替代）
│       ├── rca_list_tables.py           # ListTablesInDirectoryTool config
│       ├── rca_get_schema.py            # GetSchemaTool config
│       └── rca_query_parquet.py         # QueryParquetFilesTool config
├── src/
│   └── tool/default_tools/
│       └── rca_tools.py                 # 4 个 RCA Tool 子类
├── rca_tools.py                         # 旧版独立工具（已不使用）
├── .env                                 # API 密钥（kimi）
└── workdir/rca_agent/                   # 运行时工作目录（tracer.json 等）
```

---

## 环境

```bash
# v2.0 框架依赖
pip install -r requirements.txt
# 额外 RCA 依赖
pip install duckdb

# 运行（由 RolloutRunner 调用）
python agent_runner.py
```

Python 要求：`>=3.11`

---

## 环境变量（.env）

```
OPENAI_API_KEY=sk-...                      # kimi API key
OPENAI_API_BASE=https://api.shubiaobiao.cn/v1 # kimi API base
DRA_MODEL_NAME=openai/claude-sonnet-4-5-20250929        # 可选，默认 openai/claude-sonnet-4-5-20250929
```

---

## RolloutRunner 配置

```yaml
# RolloutRunner/configs/agents/deepresearchagent.yaml
name: deepresearchagent
cmd: ["python", "agent_runner.py"]
cwd: /home/nn/SOTA-agents/DeepResearchAgent
exp_id: rollout_deepresearchagent
model_name: openai/claude-sonnet-4-5-20250929
agent_type: deepresearchagent
concurrency: 2
timeout: 600
data_dir: /home/nn/SOTA-agents/RolloutRunner/data
```

---

## 常用命令

```bash
# 冒烟测试（1 条）
cd /home/nn/SOTA-agents/RolloutRunner
python scripts/run_rollout.py --agent deepresearchagent --source_exp_id <exp_id> --limit 1

# 全量运行
nohup python -u scripts/run_rollout.py --agent deepresearchagent --source_exp_id <exp_id> \
  > rollout_dra.log 2>&1 &
```
