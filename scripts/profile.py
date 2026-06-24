#!/usr/bin/env python
"""
Profile a tabular dataset (CSV/TSV/XLSX/JSON/Parquet/Feather/HTML/XML/YAML/DB)
and print a quality summary.

Usage:
    python profile.py <input_file> [--top 5]

Outputs (stdout, JSON-friendly text):
    - shape (rows, cols)
    - per-column: dtype, null_count, null_pct, unique_count, sample_values
    - duplicate row count
    - numeric summary for numeric columns

Supports 13 formats: .csv .tsv .xlsx .xls .json .parquet .feather .html .htm
.xml .yaml .yml .db .sqlite .sqlite3
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

# Reuse the same column-name normalisation logic from clean.py
_SNAKE_SEP_RE = re.compile(r"[\s\-]+")
_NON_ALNUM_RE = re.compile(r"[^\w()（）\[\]【】.%]+")
import unicodedata

def _standardize_col_name(name: str) -> str:
    name = unicodedata.normalize("NFKC", str(name)).strip()
    name = _SNAKE_SEP_RE.sub("_", name)
    name = _NON_ALNUM_RE.sub("", name)
    name = re.sub(r"_+", "_", name)
    return name.lower().strip("_") or "col"


def _read(path: Path) -> pd.DataFrame:
    suf = path.suffix.lower()
    if suf in {".csv", ".tsv"}:
        sep = "\t" if suf == ".tsv" else ","
        # Try utf-8 first, fall back to gbk (common in Chinese datasets)
        for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
            try:
                return pd.read_csv(path, sep=sep, encoding=enc, low_memory=False)
            except UnicodeDecodeError:
                continue
        raise RuntimeError(f"Cannot decode {path} with any of utf-8/gbk")
    if suf in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suf == ".json":
        df = pd.read_json(path)
        # Normalise nested json if first cell is dict
        if df.shape[1] == 1 and isinstance(df.iloc[0, 0], dict):
            df = pd.json_normalize(df.iloc[:, 0])
        return df
    if suf == ".parquet":
        return pd.read_parquet(path)
    if suf == ".feather":
        return pd.read_feather(path)
    if suf in {".html", ".htm"}:
        tables = pd.read_html(path)
        if not tables:
            raise ValueError(f"No <table> found in HTML: {path}")
        if len(tables) == 1:
            return tables[0]
        # Profile the largest table by default
        return max(tables, key=lambda t: len(t))
    if suf == ".xml":
        return pd.read_xml(path)
    if suf in {".yaml", ".yml"}:
        try:
            import yaml  # PyYAML
        except ImportError:
            raise ImportError(
                "PyYAML is required for .yaml files. Run: pip install pyyaml"
            )
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError:
            # Strip !!python/… lines (numpy serialized objects)
            clean = []
            for line in raw.splitlines():
                if "!!python/" in line or "!!binary" in line:
                    continue
                clean.append(line)
            data = yaml.safe_load("\n".join(clean))
        if isinstance(data, list):
            return pd.DataFrame(data)
        elif isinstance(data, dict):
            list_keys = [k for k, v in data.items() if isinstance(v, list)]
            if list_keys:
                best_key = max(list_keys, key=lambda k: len(data[k]))
                return pd.DataFrame(data[best_key])
            return pd.DataFrame.from_dict(data, orient="index")
        raise ValueError(f"Unexpected YAML root type: {type(data).__name__}")
    if suf in {".pkl", ".pickle"}:
        data = pd.read_pickle(path)
        if isinstance(data, dict) and "columns" in data and "data" in data:
            return pd.DataFrame(data["data"], columns=data["columns"])
        if isinstance(data, dict):
            list_keys = [k for k, v in data.items() if isinstance(v, list)]
            if list_keys:
                best = max(list_keys, key=lambda k: len(data[k]))
                return pd.DataFrame(data[best])
            return pd.DataFrame.from_dict(data, orient="index")
        if isinstance(data, list):
            return pd.DataFrame(data)
        return data if isinstance(data, pd.DataFrame) else pd.DataFrame([data])
    if suf in {".db", ".sqlite", ".sqlite3"}:
        con = sqlite3.connect(str(path))
        tables = pd.read_sql_query(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'",
            con,
        )
        table_names = tables["name"].tolist()
        if not table_names:
            raise ValueError(f"No user tables found in SQLite: {path}")
        # Profile the largest table by default
        best_table = None
        best_rows = -1
        for tname in table_names:
            n = pd.read_sql_query(
                f'SELECT COUNT(*) FROM "{tname}"', con
            ).iloc[0, 0]
            if n > best_rows:
                best_rows = n
                best_table = tname
        df = pd.read_sql_query(
            f'SELECT * FROM "{best_table}"', con
        )
        con.close()
        print(f"[profiling table '{best_table}' ({best_rows} rows) — "
              f"other tables: {[t for t in table_names if t != best_table]}]",
              file=sys.stderr)
        return df
    raise ValueError(
        f"Unsupported format: {suf}. Supported: .csv, .tsv, .xlsx, .xls, "
        f".json, .parquet, .feather, .html, .htm, .xml, .yaml, .yml, "
        f".db, .sqlite, .sqlite3"
    )


def profile(df: pd.DataFrame, top: int = 5) -> dict:
    # Flatten any list/dict columns to strings so pandas hash operations work
    df_work = df.copy()
    for col in df_work.columns:
        if df_work[col].apply(lambda x: isinstance(x, (list, dict))).any():
            df_work[col] = df_work[col].apply(lambda x: str(x) if isinstance(x, (list, dict)) else x)

    # Pre-compute column name mapping so AI sees standardised names
    col_mapping = {}
    for col in df_work.columns:
        std = _standardize_col_name(col)
        if std != str(col):
            col_mapping[str(col)] = std

    out: dict = {
        "shape": {"rows": int(len(df_work)), "cols": int(df_work.shape[1])},
        "duplicate_rows": int(df_work.duplicated().sum()),
        "columns": [],
    }
    if col_mapping:
        out["column_name_mapping"] = col_mapping

    for col in df_work.columns:
        s = df_work[col]
        n_null = int(s.isna().sum())
        n_unique = int(s.nunique(dropna=True))
        sample = (
            s.dropna().astype(str).head(top).tolist()
            if n_unique > 0
            else []
        )
        col_info: dict = {
            "name": str(col),
            "standardized_name": _standardize_col_name(col),
            "dtype": str(s.dtype),
            "null_count": n_null,
            "null_pct": round(n_null / max(len(df), 1) * 100, 2),
            "unique_count": n_unique,
            "sample": sample,
        }
        if pd.api.types.is_numeric_dtype(s):
            col_info["stats"] = {
                "min": _safe(s.min()),
                "max": _safe(s.max()),
                "mean": _safe(s.mean()),
                "median": _safe(s.median()),
                "std": _safe(s.std()),
            }
        elif pd.api.types.is_datetime64_any_dtype(s):
            col_info["min_date"] = _safe(s.min())
            col_info["max_date"] = _safe(s.max())
        out["columns"].append(col_info)
    return out


def _safe(v):
    try:
        if pd.isna(v):
            return None
        if isinstance(v, (int, float, str, bool)):
            return v
        return str(v)
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("--top", type=int, default=5, help="Sample values per column")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = ap.parse_args()

    if not args.input.exists():
        print(f"File not found: {args.input}", file=sys.stderr)
        return 1

    df = _read(args.input)
    p = profile(df, top=args.top)

    # Detect ragged rows (common with custom delimiters or unescaped commas)
    expected = df.shape[1]
    ragged: list[dict] = []
    raw = args.input.read_bytes()
    for enc in ("utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    for i, line in enumerate(text.splitlines(), 1):
        n = line.count(",") + 1
        if n != expected:
            ragged.append({"line": i, "commas": n - 1, "expected_cols": expected})
            if len(ragged) >= 5:
                break
    if ragged:
        p["warnings"] = p.get("warnings", []) + [{
            "type": "ragged_rows",
            "message": (
                f"{len(ragged)}+ rows have a different number of fields than "
                f"the header ({expected}). This usually means the delimiter is "
                "wrong, or commas appear inside unquoted fields. Fix the source "
                "file or re-read with a different separator."
            ),
            "samples": ragged,
        }]

    if args.json:
        print(json.dumps(p, ensure_ascii=False, indent=2))
    else:
        print(f"File: {args.input}")
        print(f"Shape: {p['shape']['rows']} rows x {p['shape']['cols']} cols")
        print(f"Duplicate rows: {p['duplicate_rows']}")
        if p.get("column_name_mapping"):
            print(f"\nColumn name mapping (original -> standardised):")
            for orig, std in p["column_name_mapping"].items():
                print(f"  {orig} -> {std}")
        for w in p.get("warnings", []):
            print(f"WARNING [{w['type']}]: {w['message']}")
            for s in w.get("samples", []):
                print(f"  - line {s['line']}: {s['commas']} commas (expected {s['expected_cols']-1})")
        print("-" * 60)
        for c in p["columns"]:
            line = (
                f"[{c['standardized_name']}] (original: {c['name']}) "
                f"dtype={c['dtype']} "
                f"null={c['null_count']} ({c['null_pct']}%) "
                f"unique={c['unique_count']} "
                f"sample={c['sample'][:3]}"
            )
            print(line)
            if "stats" in c:
                print(f"   stats: {c['stats']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
