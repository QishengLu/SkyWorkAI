"""RCA (Root Cause Analysis) tools for querying parquet telemetry data.

Provides 3 tools:
- list_tables_in_directory: List parquet files with metadata
- get_schema: Get schema of parquet files
- query_parquet_files: Query parquet files with SQL via DuckDB
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Union

from pydantic import Field

from src.registry import TOOL
from src.tool.types import Tool, ToolResponse

# ── Constants ────────────────────────────────────────────────────────────────

TOKEN_LIMIT = 5000

ALLOWED_STEMS = {
    "normal_logs", "abnormal_logs",
    "normal_traces", "abnormal_traces",
    "normal_metrics", "abnormal_metrics",
    "normal_metrics_histogram", "abnormal_metrics_histogram",
    "normal_metrics_sum", "abnormal_metrics_sum",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _import_duckdb():
    try:
        import duckdb
        return duckdb
    except ImportError:
        raise ImportError("duckdb is required. Install it with: pip install duckdb")


def _serialize_datetime(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {key: _serialize_datetime(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [_serialize_datetime(item) for item in obj]
    else:
        return obj


def _estimate_token_count(text: str) -> int:
    average_chars_per_token = 3
    return (len(text) + average_chars_per_token - 1) // average_chars_per_token


def _enforce_token_limit(payload: str, context: str) -> str:
    token_estimate = _estimate_token_count(payload)
    if token_estimate <= TOKEN_LIMIT:
        return payload

    current_size = len(json.loads(payload)) if payload.startswith("[") else None
    suggested_limit = None
    if current_size:
        ratio = TOKEN_LIMIT / token_estimate
        suggested_limit = max(1, int(current_size * ratio * 0.8))

    suggestion_parts = [
        "The query result is too large. Please adjust your query:",
        "  - Reduce the LIMIT value" + (f" (try LIMIT {suggested_limit})" if suggested_limit else ""),
        "  - Filter rows with WHERE clauses to reduce result size",
        "  - Select only necessary columns instead of SELECT *",
        "  - Use aggregation (COUNT, SUM, AVG) instead of retrieving raw rows",
    ]

    warning = {
        "error": "Result exceeds token budget",
        "context": context,
        "estimated_tokens": token_estimate,
        "token_limit": TOKEN_LIMIT,
        "rows_returned": current_size,
        "suggested_limit": suggested_limit,
        "suggestion": "\n".join(suggestion_parts),
    }
    return json.dumps(warning, ensure_ascii=False, indent=2)


def _sanitize_column_name(name: str) -> str:
    return name.replace(".", "_")


def _build_rename_select(parquet_path: str) -> str:
    duckdb = _import_duckdb()
    conn = duckdb.connect(":memory:")
    try:
        result = conn.execute(f"SELECT * FROM read_parquet('{parquet_path}') LIMIT 0")
        columns = [desc[0] for desc in result.description]
    finally:
        conn.close()

    needs_rename = any("." in col for col in columns)
    if not needs_rename:
        return "*"

    parts = []
    for col in columns:
        if "." in col:
            parts.append(f'"{col}" AS {_sanitize_column_name(col)}')
        else:
            parts.append(col)
    return ", ".join(parts)



# ── Tool Implementations ─────────────────────────────────────────────────────

_LIST_TABLES_DESCRIPTION = """List all parquet files in a directory with metadata including row count and column count.
Use this to discover available data files for RCA analysis.

Args:
- directory (str): Directory path to search for parquet files.

Example: {"name": "list_tables_in_directory", "args": {"directory": "/path/to/data"}}
"""


@TOOL.register_module(force=True)
class ListTablesInDirectoryTool(Tool):
    """A tool for listing parquet files in a directory with metadata."""

    name: str = "list_tables_in_directory"
    description: str = _LIST_TABLES_DESCRIPTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, directory: str, **kwargs) -> ToolResponse:
        """List all parquet files in a directory with metadata.

        Args:
            directory (str): Directory path to search for parquet files.
        """
        duckdb = _import_duckdb()

        dir_path = Path(directory)
        if not dir_path.exists():
            return ToolResponse(
                success=False,
                message=json.dumps({"error": f"Directory not found: {directory}"}),
            )
        if not dir_path.is_dir():
            return ToolResponse(
                success=False,
                message=json.dumps({"error": f"Path is not a directory: {directory}"}),
            )

        files_info = []

        for file_path in sorted(dir_path.rglob("*.parquet")):
            if file_path.stem not in ALLOWED_STEMS:
                continue
            file_path_str = str(file_path)

            try:
                conn = duckdb.connect(":memory:")
                try:
                    row_count_result = conn.execute(
                        f"SELECT COUNT(*) FROM read_parquet('{file_path}')"
                    ).fetchone()
                    row_count = row_count_result[0] if row_count_result else 0

                    result = conn.execute(
                        f"SELECT * FROM read_parquet('{file_path}') LIMIT 0"
                    )
                    column_count = len(result.description)
                finally:
                    conn.close()

                files_info.append({
                    "filename": file_path.name,
                    "path": file_path_str,
                    "row_count": row_count,
                    "column_count": column_count,
                })
            except Exception as e:
                files_info.append({
                    "filename": file_path.name,
                    "path": file_path_str,
                    "error": str(e),
                })

        result_json = json.dumps(files_info, ensure_ascii=False, indent=2)
        return ToolResponse(
            success=True,
            message=_enforce_token_limit(result_json, "list_tables_in_directory"),
        )


_GET_SCHEMA_DESCRIPTION = """Get schema information of parquet file(s) including column names, types, and row count.

Args:
- parquet_files (str or list): Path to a parquet file (string) or list of paths.

Example: {"name": "get_schema", "args": {"parquet_files": "/path/to/abnormal_logs.parquet"}}
"""


@TOOL.register_module(force=True)
class GetSchemaTool(Tool):
    """A tool for getting schema information of parquet files."""

    name: str = "get_schema"
    description: str = _GET_SCHEMA_DESCRIPTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, parquet_files: Union[str, List[str]], **kwargs) -> ToolResponse:
        """Get schema information of parquet file(s).

        Args:
            parquet_files (str or list): Path to a parquet file or list of paths.
        """
        duckdb = _import_duckdb()

        def _get_schema_one(parquet_file: str) -> dict:
            if not Path(parquet_file).exists():
                return {"error": f"Parquet file not found: {parquet_file}"}
            conn = duckdb.connect(":memory:")
            try:
                result = conn.execute(
                    f"SELECT * FROM read_parquet('{parquet_file}') LIMIT 0"
                )
                schema = [
                    {"name": _sanitize_column_name(desc[0]), "type": str(desc[1])}
                    for desc in result.description
                ]
                row_count_result = conn.execute(
                    f"SELECT COUNT(*) FROM read_parquet('{parquet_file}')"
                ).fetchone()
                row_count = row_count_result[0] if row_count_result else 0
                return {"file": parquet_file, "row_count": row_count, "columns": schema}
            except Exception as e:
                return {"error": f"Failed to extract schema: {str(e)}"}
            finally:
                conn.close()

        if isinstance(parquet_files, str):
            result_json = json.dumps(_get_schema_one(parquet_files), ensure_ascii=False, indent=2)
        else:
            result_json = json.dumps(
                [_get_schema_one(f) for f in parquet_files],
                ensure_ascii=False,
                indent=2,
            )

        return ToolResponse(
            success=True,
            message=_enforce_token_limit(result_json, "get_schema"),
        )


_QUERY_PARQUET_DESCRIPTION = """Query parquet files using SQL syntax for data analysis.
Each file becomes a queryable table named after its filename stem
(e.g., 'abnormal_logs.parquet' becomes table 'abnormal_logs').

Args:
- parquet_files (str or list): Path(s) to parquet file(s).
- query (str): SQL query to execute against the parquet files.
- limit (int, optional): Maximum number of records to return (default: 10).

Example: {"name": "query_parquet_files", "args": {"parquet_files": "/path/to/abnormal_traces.parquet", "query": "SELECT service_name, COUNT(*) as cnt FROM abnormal_traces GROUP BY service_name ORDER BY cnt DESC", "limit": 20}}
"""


@TOOL.register_module(force=True)
class QueryParquetFilesTool(Tool):
    """A tool for querying parquet files using SQL syntax via DuckDB."""

    name: str = "query_parquet_files"
    description: str = _QUERY_PARQUET_DESCRIPTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(
        self,
        parquet_files: Union[str, List[str]],
        query: str,
        limit: int = 10,
        **kwargs,
    ) -> ToolResponse:
        """Query parquet files using SQL syntax.

        Args:
            parquet_files (str or list): Path(s) to parquet file(s).
            query (str): SQL query to execute against the parquet files.
            limit (int): Maximum number of records to return (default: 10).
        """
        duckdb = _import_duckdb()

        try:
            if isinstance(parquet_files, str):
                parquet_files = [parquet_files]
            for fp in parquet_files:
                if not Path(fp).exists():
                    return ToolResponse(
                        success=False,
                        message=json.dumps({
                            "error": f"Parquet file not found: {fp}. "
                            "Use 'list_tables_in_directory' to discover available files."
                        }),
                    )
        except Exception as e:
            return ToolResponse(success=False, message=json.dumps({"error": str(e)}))

        conn = duckdb.connect(":memory:")
        table_names: set = set()

        try:
            for file_path in parquet_files:
                base_name = Path(file_path).stem
                table_name = base_name
                counter = 1
                while table_name in table_names:
                    table_name = f"{base_name}_{counter}"
                    counter += 1
                table_names.add(table_name)
                select_clause = _build_rename_select(file_path)
                conn.execute(
                    f"CREATE VIEW {table_name} AS SELECT {select_clause} FROM read_parquet('{file_path}')"
                )

            result = conn.execute(query).fetchall()
            columns = [desc[0] for desc in conn.description]

            rows = [dict(zip(columns, row)) for row in result]
            serialized_rows = _serialize_datetime(rows)

            if len(serialized_rows) > limit:
                serialized_rows = serialized_rows[:limit]

            result_json = json.dumps(serialized_rows, ensure_ascii=False, indent=2)
            return ToolResponse(
                success=True,
                message=_enforce_token_limit(result_json, "query_parquet_files"),
            )

        except Exception as e:
            error_msg = str(e)
            if "syntax error" in error_msg.lower() or "parser error" in error_msg.lower():
                err = {"error": f"SQL syntax error: {error_msg}", "query": query, "available_tables": list(table_names)}
            elif "catalog" in error_msg.lower() or "table" in error_msg.lower():
                err = {"error": f"Table reference error: {error_msg}", "query": query, "available_tables": list(table_names)}
            else:
                err = {"error": f"Query failed: {error_msg}", "query": query, "available_tables": list(table_names)}
            return ToolResponse(success=False, message=json.dumps(err))
        finally:
            conn.close()
