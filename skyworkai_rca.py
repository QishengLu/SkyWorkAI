import argparse
import os
import sys
import asyncio
from pathlib import Path
from mmengine import DictAction

root = str(Path(__file__).resolve().parents[0])
sys.path.append(root)

from src.logger import logger
from src.config import config
from src.models import model_manager
from src.agent import create_agent

def parse_args():
    parser = argparse.ArgumentParser(description='main')
    parser.add_argument("--config", default=os.path.join(root, "configs", "config_main.py"), help="config file path")

    parser.add_argument(
        '--cfg-options',
        nargs='+',
        action=DictAction,
        help='override some settings in the used config, the key-value pair '
        'in xxx=yyy format will be merged into config file. If the value to '
        'be overwritten is a list, it should be like key="[a,b]" or key=a,b '
        'It also allows nested list/tuple values, e.g. key="[(a,b),(c,d)]" '
        'Note that the quotation marks are necessary and that no white space '
        'is allowed.')
    args = parser.parse_args()
    return args


async def main():
    # Parse command line arguments
    args = parse_args()

    # Initialize the configuration
    config.init_config(args.config, args)

    # Initialize the logger
    logger.init_logger(log_path=config.log_path)
    logger.info(f"| Logger initialized at: {config.log_path}")
    logger.info(f"| Config:\n{config.pretty_text}")

    # Registed models
    model_manager.init_models(use_local_proxy=False)
    logger.info("| Registed models: %s", ", ".join(model_manager.registed_models.keys()))

    # Create agent
    agent = await create_agent(config)
    logger.visualize_agent_tree(agent)

    # Root Cause Analysis Task  
    task = """Based on the observability data collected during the fault injection period in namespace ts0 from '2025-07-23 14:10:23' to '2025-07-23 14:14:23' UTC, in contrast, the normal time is between '2025-07-23 14:06:23' to '2025-07-23 14:10:23' UTC, analyze the span metrics, trace data, logs to identify which service is the root cause. Perform Root Cause Analysis (RCA) on the parquet files in the question_3 directory.

Create a detailed plan that delegates to the deep_analyzer_agent for comprehensive RCA:

**Analysis Workflow:**

Step 1: Discover and Understand Data Structure
- Use list_tables_in_directory with directory='./question_3' to discover all available parquet files  
- Use get_schema to examine the structure of each key file (logs, traces, metrics, etc.)
- Document the column names, data types, and row counts for reference

Step 2: Understand the High-Level Problem Overview
- Use query_parquet_files to read the conclusion.parquet file
- Extract and summarize the high-level problem description
- Identify the time range, affected services, and initial symptoms

Step 3: Analyze Anomalous Data
- Based on the problem overview, use query_parquet_files to extract anomalous data
- Focus on error logs, failed requests, high latency metrics, or abnormal traces
- Generate code to filter and aggregate anomalous patterns
- Document specific anomalies with timestamps and affected components
- Query Example for Anomalous Period:
  ```sql
  SELECT service_name, level, COUNT(*) as count 
  FROM abnormal_logs 
  WHERE time >= TIMESTAMP '2025-07-23 14:10:23' 
    AND time <= TIMESTAMP '2025-07-23 14:14:23'
  GROUP BY service_name, level 
  ORDER BY count DESC 
  LIMIT 50
  ```

Step 4: Compare with Normal Data  
- Use query_parquet_files to extract baseline/normal data from the same time period or similar conditions
- Generate code to compare metrics, error rates, and patterns between normal and anomalous states
- Identify significant deviations and correlations
- Query Example for Normal Period:
  ```sql
  SELECT service_name, COUNT(*) as error_count 
  FROM normal_logs 
  WHERE level = 'ERROR' 
    AND time >= TIMESTAMP '2025-07-23 14:06:23' 
    AND time < TIMESTAMP '2025-07-23 14:10:23'
  GROUP BY service_name 
  ORDER BY error_count DESC 
  LIMIT 20
  ```

Step 5: Iterative Multi-Round Analysis
- Based on findings from Steps 3-4, generate additional queries to investigate deeper
- Follow the chain of causality across services and components
- Use query_parquet_files iteratively to drill down into specific time windows or service interactions
- Correlate events across different data sources (logs, traces, metrics)

Step 6: Determine Root Cause
- Synthesize all findings from previous steps
- Identify the service or component that initiated the problem
- Provide clear evidence supporting the root cause determination

**Final Answer Requirements:**
You MUST provide the final answer in the following exact format:
Root cause service: [service-name]

For example:
Root cause service: ts-food-service

**Important Requirements:**
- Always use reasonable LIMIT values in SQL queries (≤100 rows recommended)
- Document your reasoning at each step
- If a query returns too much data, refine it with more specific filters
- The final answer MUST be in the format: "Root cause service: [service-name]"
- Do not include any other text in the final answer line, only the service name after the colon

**SQL Query Best Practices:**
- Always use reasonable LIMIT values in SQL queries (≤100 rows recommended)
- When querying with timestamps in parquet files:
  * The 'time' column is stored as datetime64[ns, UTC] type
- If a query returns too much data, refine it with more specific filters

Delegate this comprehensive RCA analysis task to the deep_analyzer_agent which has the required MCP tools."""
    res = await agent.run(task)
    logger.info(f"| Result: {res}")

if __name__ == '__main__':
    asyncio.run(main())