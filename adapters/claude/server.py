#!/usr/bin/env python
"""
MCP Server — wraps DataPipelineCleaner v2 for Claude Desktop / MiMo Code.

Usage:
    pip install mcp pandas pyarrow openpyxl

    # Claude Desktop config (claude_desktop_config.json):
    {
      "mcpServers": {
        "data-cleaning": {
          "command": "python",
          "args": ["adapters/claude/server.py"],
          "cwd": "/path/to/data-cleaning"
        }
      }
    }
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_SKILL_ROOT))

from scripts.clean import DataPipelineCleaner  # noqa: E402

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Data Cleaner v2 — Industrial-Grade Pipeline")


def _safe_json(label: str, raw: str, default=None):
    raw = (raw or "").strip()
    if not raw or raw == "{}":
        return default
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else default
    except json.JSONDecodeError:
        return None


@mcp.tool()
def clean_data(
    file_path: str,
    schema_rules: str = "{}",
    business_rules: str = "{}",
    semantic_rules: str = "{}",
    missing_rules: str = "{}",
    outlier_rules: str = "{}",
    outlier_method: str = "iqr",
    iqr_k: float = 1.5,
    expand_nested: bool = False,
    db_table: str = "",
) -> str:
    """Run the v2 data cleaning pipeline with full parameter control.

    Args:
        file_path: Path to data file (csv/tsv/xlsx/xls/json/parquet/feather/html/xml/yaml/db/pkl).
        schema_rules: JSON. Column->type mapping. Example: '{"age": "int", "salary": "float"}'
        business_rules: JSON. Legacy replace_values. Example: '{"height_cm": {"replace_values": [0], "fill": "median"}}'
        semantic_rules: JSON. Semantic tagging. Example: '{"age": {"invalid": [-5,-1], "suspicious": [150]}}'
        missing_rules: JSON. Sentinel->NaN. Example: '{"income": {"sentinel": [-999]}}'
        outlier_rules: JSON. Per-column outlier method. Example: '{"income": {"method": "percentile"}}'
        outlier_method: Global outlier method: iqr/percentile/zscore/none (default iqr).
        iqr_k: IQR sensitivity (default 1.5).
        expand_nested: Auto-expand nested JSON/list columns.
        db_table: SQLite table name (for multi-table .db files).

    Returns:
        JSON string — full audit report with semantic_audit, cell_actions, outlier_winsorizing.
    """
    schema = _safe_json("schema_rules", schema_rules, {})
    business = _safe_json("business_rules", business_rules)
    semantic = _safe_json("semantic_rules", semantic_rules)
    missing = _safe_json("missing_rules", missing_rules)
    outlier_r = _safe_json("outlier_rules", outlier_rules)

    if schema is None:
        return json.dumps({"error": "schema_rules must be a JSON object"}, ensure_ascii=False)
    if outlier_method not in ("iqr", "percentile", "zscore", "none"):
        return json.dumps({"error": f"Invalid outlier_method: {outlier_method}"}, ensure_ascii=False)

    engine_kwargs = {"table": db_table} if db_table else None

    cleaner = DataPipelineCleaner()
    try:
        _, audit = cleaner.execute(
            file_path=file_path,
            schema_rules=schema,
            business_rules=business,
            semantic_rules=semantic,
            missing_rules=missing,
            outlier_rules=outlier_r,
            outlier_method=outlier_method,
            iqr_k=iqr_k,
            expand_nested=expand_nested,
            engine_kwargs=engine_kwargs,
        )
    except FileNotFoundError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Pipeline failed: {type(e).__name__}: {e}"}, ensure_ascii=False)

    return json.dumps(audit, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
def profile_data(file_path: str) -> str:
    """Profile a data file: shape, dtypes, nulls, column name mapping, samples.

    Args:
        file_path: Path to any supported data file.

    Returns:
        JSON string with per-column statistics and standardized name mapping.
    """
    import pandas as pd

    try:
        p = Path(file_path)
        if not p.exists():
            return json.dumps({"error": f"File not found: {file_path}"}, ensure_ascii=False)

        suff = p.suffix.lower()
        if suff == ".json":
            df = pd.read_json(p, dtype=str)
        elif suff == ".csv":
            df = pd.read_csv(p, dtype=str, na_filter=False, keep_default_na=False)
        elif suff in {".xlsx", ".xls"}:
            df = pd.read_excel(p, dtype=str, na_filter=False)
        elif suff == ".parquet":
            df = pd.read_parquet(p)
        elif suff == ".feather":
            df = pd.read_feather(p)
        elif suff in {".pkl", ".pickle"}:
            data = pd.read_pickle(p)
            if isinstance(data, dict) and "columns" in data and "data" in data:
                df = pd.DataFrame(data["data"], columns=data["columns"])
            elif isinstance(data, pd.DataFrame):
                df = data
            else:
                df = pd.DataFrame(data)
        else:
            return json.dumps({"error": f"Unsupported: {suff}"}, ensure_ascii=False)

        total_rows = len(df)
        profile = {"file": str(p.name), "rows": total_rows, "columns": {}}
        for col in df.columns:
            ser = df[col]
            null_count = int(ser.isna().sum())
            profile["columns"][str(col)] = {
                "dtype": str(ser.dtype),
                "null_count": null_count,
                "null_pct": round(null_count / total_rows * 100, 1) if total_rows else 0,
                "unique": int(ser.nunique(dropna=False)),
                "samples": ser.dropna().head(5).tolist() if null_count < total_rows else [],
            }

        return json.dumps(profile, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        return json.dumps({"error": f"Profile failed: {type(e).__name__}: {e}"}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
