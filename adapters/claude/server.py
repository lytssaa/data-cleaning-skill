#!/usr/bin/env python
"""
MCP Server — wraps DataPipelineCleaner for Claude Desktop.

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

mcp = FastMCP("Data Cleaner — Industrial-Grade Pipeline")


def _safe_parse_json(label: str, raw: str, default: dict | None = None) -> dict | None:
    raw = raw.strip()
    if not raw or raw == "{}":
        return default
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return None
        return parsed
    except json.JSONDecodeError:
        return None


@mcp.tool()
def clean_data(
    file_path: str,
    schema_rules: str = "{}",
    business_rules: str = "{}",
    outlier_method: str = "none",
    iqr_k: float = 1.5,
    expand_nested: bool = False,
) -> str:
    """Run the five-phase cleaning pipeline with full parameter control.

    Phases: safe ingest -> standardise -> type alignment -> missing trial -> outlier suppression.

    Args:
        file_path: Path to the data file (.csv, .tsv, .xlsx, .xls, .json, .parquet, .feather, .html, .xml, .yaml, .db, .pkl).
        schema_rules: JSON string. Column -> target type mapping.
            Example: '{"age": "int", "salary": "float"}'
            Supported types: int, float, str, datetime.
        business_rules: JSON string. Per-column semantic rules.
            Example: '{"height_cm": {"replace_values": [0], "fill": "median", "missing_means": "invalid data"}}'
            Keys: replace_values (list), fill (value or "median"/"mean"/"mode"), missing_means (str).
        outlier_method: 'iqr', 'percentile', 'zscore', or 'none' (default).
        iqr_k: IQR sensitivity coefficient (default 1.5).
        expand_nested: If true, auto-detect and expand nested JSON/list columns.

    Returns:
        JSON string — full audit report.
    """
    parsed_schema = _safe_parse_json("schema_rules", schema_rules, {})
    if parsed_schema is None:
        return json.dumps({"error": "schema_rules must be a JSON object"}, ensure_ascii=False)

    parsed_business = _safe_parse_json("business_rules", business_rules, {})
    if parsed_business is None:
        return json.dumps({"error": "business_rules must be a JSON object"}, ensure_ascii=False)

    if outlier_method not in ("iqr", "percentile", "zscore", "none"):
        return json.dumps(
            {"error": f"Invalid outlier_method '{outlier_method}'. Use: iqr, percentile, zscore, none"},
            ensure_ascii=False,
        )

    cleaner = DataPipelineCleaner()
    try:
        _, audit = cleaner.execute(
            file_path=file_path,
            schema_rules=parsed_schema,
            business_rules=parsed_business if parsed_business else None,
            outlier_method=outlier_method,
            iqr_k=iqr_k,
            expand_nested=expand_nested,
        )
    except FileNotFoundError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps(
            {"error": f"Pipeline failed: {type(e).__name__}: {e}"},
            ensure_ascii=False,
        )

    return json.dumps(audit, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
def profile_data(file_path: str) -> str:
    """Quick-look data profile: shape, dtypes, null counts, sample values.

    Args:
        file_path: Path to a data file.

    Returns:
        JSON string with column-level statistics.
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
        else:
            return json.dumps({"error": f"Unsupported format for profile: {suff}"}, ensure_ascii=False)

        total_rows = len(df)
        profile: dict = {"file": str(p.name), "rows": total_rows, "columns": {}}
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
        return json.dumps(
            {"error": f"Profile failed: {type(e).__name__}: {e}"},
            ensure_ascii=False,
        )


if __name__ == "__main__":
    mcp.run()
