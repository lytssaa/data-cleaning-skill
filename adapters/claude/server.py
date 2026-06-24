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

# Make scripts/ importable regardless of cwd
_SKILL_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_SKILL_ROOT))

from scripts.clean import DataPipelineCleaner  # noqa: E402

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Data Cleaner — Industrial-Grade Pipeline")

# ── Tool: clean ────────────────────────────────────────────────────────────


@mcp.tool()
def clean_data(file_path: str, schema_rules: str) -> str:
    """Run the five-phase cleaning pipeline on a CSV/Excel file.

    Phases: safe ingest → standardise → type alignment → missing trial → outlier suppression.

    Args:
        file_path: Absolute or relative path to the data file (.csv, .tsv, .xlsx, .xls).
        schema_rules: JSON string mapping column names to target types.
            Example: '{"age": "int", "salary": "float", "join_date": "datetime"}'
            Supported types: int, float, str, datetime.

    Returns:
        JSON string — the full audit report including original/cleaned row counts,
        retention rate, dropped columns, imputed values, and suppressed outliers.
    """
    try:
        parsed_rules = json.loads(schema_rules)
        if not isinstance(parsed_rules, dict):
            return json.dumps(
                {"error": "schema_rules must be a JSON object"},
                ensure_ascii=False,
            )
    except json.JSONDecodeError as e:
        return json.dumps(
            {"error": f"Invalid JSON in schema_rules: {e}"},
            ensure_ascii=False,
        )

    cleaner = DataPipelineCleaner()
    try:
        _, audit = cleaner.execute(file_path=file_path, schema_rules=parsed_rules)
    except FileNotFoundError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps(
            {"error": f"Pipeline failed: {type(e).__name__}: {e}"},
            ensure_ascii=False,
        )

    # Strip internal helper dicts that aren't JSON-serialisable
    return json.dumps(audit, ensure_ascii=False, indent=2, default=str)


# ── Tool: profile ──────────────────────────────────────────────────────────


@mcp.tool()
def profile_data(file_path: str) -> str:
    """Quick-look data profile: shape, dtypes, null counts, sample values.

    Reads the file with strings only (no type inference), then summarises
    each column's unique count, null count, and top sample values.

    Args:
        file_path: Path to a CSV/Excel/JSON file.

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
        else:
            return json.dumps(
                {"error": f"Unsupported format: {suff}"}, ensure_ascii=False
            )

        total_rows = len(df)
        profile: dict = {"file": str(p.name), "rows": total_rows, "columns": {}}
        for col in df.columns:
            ser = df[col]
            null_count = int((ser.isna() | (ser.astype(str).str.strip() == "")).sum())
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


# ── Entry ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
