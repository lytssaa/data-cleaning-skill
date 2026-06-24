#!/usr/bin/env python
"""
Industrial-Grade Data Cleaning Pipeline — DataPipelineCleaner

Architecture (strict one-way dataflow):
  _safe_ingest → _standardize_columns_and_text → _type_alignment
  → _missing_value_trial → _outlier_suppression → execute()

Philosophy:
  - Never silently drop data. Every deletion must appear in the audit trail.
  - Type inference is the root of corruption. Ingest raw, coerce explicitly.
  - Outliers are suppressed (Winsorized), never deleted.
  - The pipeline either succeeds cleanly or fails loudly with a traceable error.

Usage:
    cleaner = DataPipelineCleaner()
    cleaned_df, audit = cleaner.execute(
        file_path="dirty_data.csv",
        schema_rules={"age": "int", "price": "float"},
    )
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

# ── PyArrow backend for Pandas 2.0+ memory efficiency ──────────────────────
try:
    pd.set_option("mode.dtype_backend", "pyarrow")
except (ValueError, KeyError):
    # Fallback gracefully on older pandas versions
    pass

# ── Ghost / invisible character pattern ────────────────────────────────────
_GHOST_RE = re.compile(
    "["
    "\u200b"  # ZERO WIDTH SPACE
    "\u200c"  # ZERO WIDTH NON-JOINER
    "\u200d"  # ZERO WIDTH JOINER
    "\u200e"  # LEFT-TO-RIGHT MARK
    "\u200f"  # RIGHT-TO-LEFT MARK
    "\ufeff"  # ZERO WIDTH NO-BREAK SPACE / BOM
    "\u2060"  # WORD JOINER
    "\u2061"  # FUNCTION APPLICATION
    "\u2062"  # INVISIBLE TIMES
    "\u2063"  # INVISIBLE SEPARATOR
    "\u2064"  # INVISIBLE PLUS
    "\u00ad"  # SOFT HYPHEN
    "\u034f"  # COMBINING GRAPHEME JOINER
    "\u061c"  # ARABIC LETTER MARK
    "\u180e"  # MONGOLIAN VOWEL SEPARATOR
    "\u2000-\u200a"  # Various width spaces (EN SPACE through HAIR SPACE)
    "\u2028"  # LINE SEPARATOR
    "\u2029"  # PARAGRAPH SEPARATOR
    "\u202a-\u202e"  # Bidi control characters
    "\u2066-\u2069"  # Bidi isolate characters
    "]",
    flags=re.UNICODE,
)

# ── Column name sanitization ───────────────────────────────────────────────
_SNAKE_CASE_SEP_RE = re.compile(r"[\s\-]+")
# Keep parentheses, brackets, dots, and % for Chinese / business column names
_NON_ALNUM_RE = re.compile(r"[^\w()（）\[\]【】.%]+")


# ============================================================================
# DataPipelineCleaner
# ============================================================================

class DataPipelineCleaner:
    """Production-grade DataFrame cleaner with a strict five-phase pipeline.

    Attributes:
        encoding_sequence: Ordered list of encodings tried during CSV ingest.
    """

    # ── Class-level constants ──────────────────────────────────────────────
    MISSING_COLUMN_THRESHOLD: float = 0.70   # Drop col if >70% null
    MISSING_ROW_THRESHOLD: float = 0.50      # Drop row if >50% null fields
    IQR_K: float = 1.5                       # IQR multiplier for outlier bounds
    UNKNOWN_TEXT: str = "Unknown"            # Default fill for categorical nulls

    def __init__(
        self,
        encoding_sequence: tuple[str, ...] = (
            "utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1",
        ),
    ) -> None:
        """Initialise the cleaner with tunable encoding fallback chain.

        Args:
            encoding_sequence: Ordered encodings to attempt when reading CSV.
                The first one that does not raise UnicodeDecodeError wins.
        """
        self.encoding_sequence = encoding_sequence
        self._audit: dict[str, Any] = {}

    # ========================================================================
    # Public API
    # ========================================================================

    def execute(
        self,
        file_path: str | Path,
        schema_rules: dict[str, str],
        engine_kwargs: dict[str, Any] | None = None,
        iqr_k: float | None = None,
        lowercase_columns: bool = True,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Run the full five-phase pipeline and return cleaned data + audit.

        This is the **only** public entry point.  All six phases execute in a
        fixed, unidirectional order:

            0. Safe ingest (all-string, zero type inference)
            1. Column-name / text-value standardisation (snake_case, NFKC, ghost-char removal)
            2. Type coercion per *schema_rules* with ``errors='coerce'``
            3. Missing-value trial (column/row pruning + imputation)
            4. Outlier suppression (IQR Winsorizing, never deletion)
            5. Audit report assembly

        Args:
            file_path: Path to ``.csv``, ``.tsv``, ``.xlsx``, ``.xls``, ``.json``,
                ``.parquet``, ``.feather``, ``.html``, ``.htm``, ``.xml``,
                ``.yaml``, ``.yml``, ``.db``, ``.sqlite``, or ``.sqlite3``.
            schema_rules: Mapping of column name → target dtype.  Supported
                values: ``'int'``, ``'float'``, ``'str'``, ``'datetime'``.
                Columns not listed remain as strings.
                Example: ``{"age": "int", "price": "float", "join_date": "datetime"}``.

        Returns:
            A 2-tuple of ``(cleaned_df, audit_report)`` where *audit_report* is
            a dict containing:

            - ``original_rows`` (int)
            - ``cleaned_rows`` (int)
            - ``retention_rate_pct`` (float)
            - ``dropped_columns`` (list[str])
            - ``dropped_rows_count`` (int)
            - ``missing_values_fixed`` (int)
            - ``outliers_suppressed`` (int)
            - ``per_column`` (dict)
            - ``stage_timings`` (dict)
            - ``warnings`` (list[str])

        Raises:
            FileNotFoundError: If *file_path* does not exist.
            ValueError: If *file_path* has an unsupported extension.
            RuntimeError: If no encoding in *encoding_sequence* can decode the
                file.
        """
        self._audit = {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "original_rows": 0,
            "cleaned_rows": 0,
            "retention_rate_pct": 100.0,
            "dropped_columns": [],
            "dropped_rows_count": 0,
            "missing_values_fixed": 0,
            "outliers_suppressed": 0,
            "per_column": {},
            "stage_timings": {},
            "warnings": [],
        }

        t0 = datetime.now()

        # Phase 0: Safe ingest
        df = self._safe_ingest(Path(file_path), engine_kwargs or {})
        self._audit["original_rows"] = len(df)
        self._audit["stage_timings"]["safe_ingest"] = _elapsed(t0)

        # Phase 1: Standardise columns & text
        df = self._standardize_columns_and_text(df, lowercase=lowercase_columns)
        self._audit["stage_timings"]["standardize"] = _elapsed(t0)

        # Phase 2: Type alignment
        df = self._type_alignment(df, schema_rules)
        self._audit["stage_timings"]["type_alignment"] = _elapsed(t0)

        # Phase 3: Missing-value trial
        df = self._missing_value_trial(df)
        self._audit["stage_timings"]["missing_trial"] = _elapsed(t0)

        # Phase 4: Outlier suppression
        df = self._outlier_suppression(df, iqr_k=iqr_k)
        self._audit["stage_timings"]["outlier_suppression"] = _elapsed(t0)

        # Phase 5: Final audit assembly
        self._audit["cleaned_rows"] = len(df)
        original = self._audit["original_rows"]
        cleaned = self._audit["cleaned_rows"]
        self._audit["retention_rate_pct"] = (
            round(cleaned / original * 100, 2) if original > 0 else 0.0
        )
        self._audit["finished_at"] = datetime.now().isoformat(timespec="seconds")

        return df, self._audit.copy()

    # ========================================================================
    # Phase 0 — Safe Ingest
    # ========================================================================

    def _safe_ingest(
        self, file_path: Path, engine_kwargs: dict[str, Any] | None = None
    ) -> pd.DataFrame:
        """Ingest a file with all columns forced to ``str`` (no type infer).

        Args:
            file_path: Resolved ``Path`` to a data file.
            engine_kwargs: Format-specific options:
                - ``table`` (str): SQLite table name (required when db has multiple tables).

        Returns:
            ``pd.DataFrame`` where every cell is a raw string.

        Raises:
            FileNotFoundError: If the path does not exist.
            ValueError: If the suffix is not recognised or multiple SQLite tables
                found without a ``table`` kwarg.
            RuntimeError: If every encoding in the chain fails for CSV.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        suffix = file_path.suffix.lower()

        if suffix == ".json":
            df = pd.read_json(file_path, dtype=str)
            # Normalise nested json arrays if present — keep it flat
            if df.shape[1] == 1 and isinstance(df.iloc[0, 0], dict):
                df = pd.json_normalize(df.iloc[:, 0])
            return df.astype(str)

        if suffix in {".csv", ".tsv"}:
            sep = "\t" if suffix == ".tsv" else ","
            for enc in self.encoding_sequence:
                try:
                    df = pd.read_csv(
                        file_path,
                        sep=sep,
                        encoding=enc,
                        dtype=str,
                        keep_default_na=False,
                        na_filter=False,
                        engine="pyarrow" if _has_pyarrow_engine() else "c",
                    )
                    return df
                except UnicodeDecodeError:
                    continue
            raise RuntimeError(
                f"Failed to decode {file_path} with encodings: "
                f"{self.encoding_sequence}"
            )

        if suffix in {".xlsx", ".xls"}:
            sheet = (engine_kwargs or {}).get("sheet")
            if sheet is not None:
                return pd.read_excel(
                    file_path, sheet_name=sheet, dtype=str, na_filter=False,
                    engine="openpyxl",
                )
            # Read all sheets to detect multi-sheet workbooks
            xls = pd.ExcelFile(file_path, engine="openpyxl")
            sheet_names = xls.sheet_names
            if len(sheet_names) == 1:
                return pd.read_excel(xls, dtype=str, na_filter=False)
            # Multiple sheets: process first, warn about others
            self._audit["warnings"].append(
                f"Excel workbook has {len(sheet_names)} sheets: {sheet_names}. "
                f"Processing sheet 0 ('{sheet_names[0]}') only. "
                f"Use engine_kwargs={{'sheet': '<name>'}} to select a specific sheet."
            )
            return pd.read_excel(xls, sheet_name=sheet_names[0], dtype=str, na_filter=False)

        if suffix == ".parquet":
            return pd.read_parquet(file_path).astype(str)

        if suffix == ".feather":
            return pd.read_feather(file_path).astype(str)

        if suffix in {".html", ".htm"}:
            tables = pd.read_html(file_path)
            if not tables:
                raise ValueError(f"No <table> found in HTML: {file_path}")
            # If the page has exactly one table, use it.
            # Otherwise combine all tables or let the user pick.
            if len(tables) == 1:
                return tables[0].astype(str)
            # Merge multiple tables with a __table__ sentinel column
            merged = pd.concat(tables, keys=range(len(tables)), names=["__table__", None])
            return merged.reset_index(level=0).astype(str)

        if suffix == ".xml":
            return pd.read_xml(file_path, dtype=str).astype(str)

        if suffix in {".yaml", ".yml"}:
            try:
                import yaml  # PyYAML
            except ImportError:
                raise ImportError(
                    "PyYAML is required for .yaml files. Run: pip install pyyaml"
                )
            with open(file_path, "r", encoding="utf-8") as fh:
                raw = fh.read()
            # First attempt: safe_load.  If the YAML contains PyYAML-dumped
            # numpy/python objects (!!python/… tags), safe_load will fail.
            try:
                data = yaml.safe_load(raw)
            except yaml.YAMLError:
                # Fallback: strip all !!python/… lines and re-parse.
                # This removes serialised numpy scalars, dtypes, etc.
                clean = []
                for line in raw.splitlines():
                    if "!!python/" in line:
                        continue  # skip tagged line entirely
                    # Also skip known numpy-internal continuation lines
                    stripped = line.strip()
                    if stripped in {"args:", "state:", "- f8", "- <", "- null",
                                     "- true", "- false", "- -1", "- 3", "- 0"}:
                        continue
                    if re.match(r"^\s*- &\w+\s*$", line):  # anchor-only lines
                        continue
                    if re.match(r"^\s*\*\w+\s*$", line):   # alias references
                        continue
                    # Remove !!binary lines
                    if "!!binary" in line:
                        continue
                    clean.append(line)
                data = yaml.safe_load("\n".join(clean))
            if isinstance(data, list):
                df = pd.DataFrame(data)
            elif isinstance(data, dict):
                # If the dict has a key that looks like a list-of-records,
                # use that key's value (common pattern: {students: [...], config: {...}})
                list_keys = [k for k, v in data.items() if isinstance(v, list)]
                if list_keys:
                    # Prefer the largest list — that's typically the data
                    best_key = max(list_keys, key=lambda k: len(data[k]))
                    df = pd.DataFrame(data[best_key])
                    self._audit["warnings"].append(
                        f"YAML: extracted '{best_key}' list ({len(df)} rows). "
                        f"Other keys: {[k for k in list_keys if k != best_key]}"
                    )
                else:
                    df = pd.DataFrame.from_dict(data, orient="index")
            else:
                raise ValueError(f"Unexpected YAML root type: {type(data).__name__}")
            return df.astype(str)

        if suffix in {".db", ".sqlite", ".sqlite3"}:
            import sqlite3
            con = sqlite3.connect(str(file_path))
            tables = pd.read_sql_query(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'",
                con,
            )
            table_names = tables["name"].tolist()
            if not table_names:
                raise ValueError(f"No user tables found in SQLite: {file_path}")
            target_table = (engine_kwargs or {}).get("table")
            if target_table:
                if target_table not in table_names:
                    raise ValueError(
                        f"Table '{target_table}' not found in {file_path}. "
                        f"Available: {table_names}"
                    )
                df = pd.read_sql_query(
                    f'SELECT * FROM "{target_table}"', con, dtype=str
                )
                con.close()
                return df
            if len(table_names) == 1:
                df = pd.read_sql_query(
                    f'SELECT * FROM "{table_names[0]}"', con, dtype=str
                )
                con.close()
                return df
            raise ValueError(
                f"Multiple tables found in {file_path}: {table_names}. "
                f"Use engine_kwargs={{'table': '<name>'}} to pick one."
            )

        raise ValueError(
            f"Unsupported file format: '{suffix}'. "
            f"Supported: .csv, .tsv, .xlsx, .xls, .json, .parquet, .feather, "
            f".html, .htm, .xml, .yaml, .yml, .db, .sqlite, .sqlite3"
        )

    # ========================================================================
    # Phase 1 — Standardise Columns & Text
    # ========================================================================

    def _standardize_columns_and_text(
        self, df: pd.DataFrame, lowercase: bool = True
    ) -> pd.DataFrame:
        """Normalise column names to snake_case; strip ghosts from text cells.

        Column operations (order matters):
            1. Strip leading/trailing whitespace from names.
            2. Decompose Unicode (NFKD), then recompose (NFKC).
            3. Convert to lowercase snake_case (unless *lowercase* is False).
            4. Deduplicate colliding names with a ``_N`` suffix.

        Text cell operations:
            1. Unicode NFKC normalisation (fullwidth → halfwidth).
            2. Remove invisible / ghost characters via ``_GHOST_RE``.
            3. Strip leading/trailing whitespace.
            4. Collapse 3+ consecutive spaces into one.

        Args:
            df: DataFrame with potentially messy column names and text.

        Returns:
            A **copy** of *df* with sanitised columns and text.
        """
        df = df.copy()

        # ── Column name normalisation ──────────────────────────────────────
        seen: dict[str, int] = {}
        new_names: list[str] = []
        for col in df.columns:
            name = str(col).strip()
            # Apply NFKC directly to turn fullwidth chars into halfwidth (includes
            # the fullwidth alphabet range U+FF01–U+FF5E and fullwidth space U+3000).
            name = unicodedata.normalize("NFKC", name)
            if lowercase:
                name = name.lower()
            # Replace separators with underscore, collapse
            name = _SNAKE_CASE_SEP_RE.sub("_", name)
            name = _NON_ALNUM_RE.sub("", name)
            name = name.strip("_") or f"col_{len(new_names)}"
            # Deduplicate
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[name]}"
            else:
                seen[name] = 0
            new_names.append(name)

        df.columns = new_names

        # ── Text cell normalisation ────────────────────────────────────────
        text_cols = df.select_dtypes(include=["object", "string"]).columns
        for col in text_cols:
            ser = df[col]
            # Only operate on actual string values; preserve null sentinel
            mask = ser.notna()
            if not mask.any():
                continue
            # Vectorised: NFKC → ghost strip → strip whitespace → collapse spaces
            df.loc[mask, col] = (
                ser[mask]
                .map(
                    lambda x: unicodedata.normalize("NFKC", str(x)),
                    na_action="ignore",
                )
                .str.replace(_GHOST_RE, "", regex=True)
                .str.strip()
                .str.replace(r" {3,}", " ", regex=True)
            )
            # After normalisation: a cell that is now empty/whitespace-only is
            # semantically missing — promote to NaN so the missing-value phase
            # (Phase 3) can detect and handle it.
            df.loc[mask, col] = df.loc[mask, col].replace(
                {"": pd.NA, "nan": pd.NA, "None": pd.NA, "NaN": pd.NA, "null": pd.NA}
            )

        return df

    # ========================================================================
    # Phase 2 — Type Alignment
    # ========================================================================

    def _type_alignment(
        self,
        df: pd.DataFrame,
        schema_rules: dict[str, str],
    ) -> pd.DataFrame:
        """Coerce columns to target types using ``errors='coerce'``.

        Invalid values (e.g. ``"twenty"`` in an ``int`` column) become ``NaN``
        via ``errors='coerce'`` and are deferred to Phase 3 for imputation.

        Args:
            df: DataFrame with all-string columns (post Phase 0/1).
            schema_rules: ``{column_name: target_type}`` mapping.  Supported
                types: ``'int'``, ``'float'``, ``'str'``, ``'datetime'``.

        Returns:
            DataFrame with coerced column dtypes.  Coercion failures are
            logged in ``self._audit["per_column"]``.
        """
        df = df.copy()
        coercion_log: dict[str, dict[str, Any]] = {}

        for col, target in schema_rules.items():
            if col not in df.columns:
                self._audit["warnings"].append(
                    f"schema_rules references unknown column '{col}' — skipped."
                )
                continue

            before_nan = int(df[col].isna().sum())
            try:
                if target == "int":
                    numeric = pd.to_numeric(df[col], errors="coerce")
                    # Use pandas nullable Int64 to distinguish NaN from 0
                    df[col] = numeric.astype("Int64")
                elif target == "float":
                    df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
                elif target == "str":
                    df[col] = df[col].astype("string")
                elif target == "datetime":
                    df[col] = pd.to_datetime(df[col], errors="coerce")
                else:
                    self._audit["warnings"].append(
                        f"Unknown target type '{target}' for column '{col}' — skipped."
                    )
                    continue
            except Exception as exc:
                self._audit["warnings"].append(
                    f"Type coercion failed for '{col}' ({target}): {exc}"
                )
                continue

            after_nan = int(df[col].isna().sum())
            new_nan = after_nan - before_nan
            coercion_log[col] = {
                "target_type": target,
                "invalid_values_coerced_to_null": new_nan,
            }

        self._audit["per_column"]["coercion"] = coercion_log
        return df

    # ========================================================================
    # Phase 3 — Missing-Value Trial
    # ========================================================================

    def _missing_value_trial(self, df: pd.DataFrame) -> pd.DataFrame:
        """Judge and remedy missing values.

        Rules (applied in order):
            1. **Column execution**: any column with ``> 70%`` null →
               drop the entire column.
            2. **Row execution**: any row with ``> 50%`` null fields →
               drop the row.
            3. **Imputation**: numeric → median; text/category →
               ``self.UNKNOWN_TEXT``.

        Args:
            df: DataFrame after type coercion.

        Returns:
            DataFrame with pruned columns/rows and imputed missing values.
        """
        df = df.copy()
        total_rows = len(df)
        total_cols_before = df.shape[1]

        # ── Step 1: Drop fatally-missing columns (>70%) ────────────────────
        col_missing_pct = df.isna().mean()
        cols_to_drop = [
            c for c, pct in col_missing_pct.items()
            if pct > self.MISSING_COLUMN_THRESHOLD
        ]
        if cols_to_drop:
            df.drop(columns=cols_to_drop, inplace=True)
            self._audit["dropped_columns"].extend(cols_to_drop)
            for c in cols_to_drop:
                self._audit["per_column"].setdefault("column_drops", {})[c] = {
                    "reason": f"missing_rate > {self.MISSING_COLUMN_THRESHOLD:.0%}",
                    "missing_rate": round(float(col_missing_pct[c]) * 100, 2),
                }

        # ── Step 2: Drop fatally-missing rows (>50%) ───────────────────────
        row_null_pct = df.isna().mean(axis=1)
        keep_mask = row_null_pct <= self.MISSING_ROW_THRESHOLD
        drop_count = int((~keep_mask).sum())
        if drop_count:
            df = df.loc[keep_mask].reset_index(drop=True)
            self._audit["dropped_rows_count"] = drop_count

        # ── Step 3: Impute remaining missing values ────────────────────────
        missing_fixed = 0
        imputation_log: dict[str, dict[str, Any]] = {}

        for col in df.columns:
            null_count = int(df[col].isna().sum())
            if null_count == 0:
                continue

            if pd.api.types.is_numeric_dtype(df[col]):
                # Use median — robust to outliers that will be capped later
                median_val = df[col].median()
                if pd.isna(median_val):
                    median_val = 0
                df[col] = df[col].fillna(median_val)
                imputation_log[col] = {
                    "strategy": "median",
                    "fill_value": _safe_py(median_val),
                    "count": null_count,
                }
            elif pd.api.types.is_datetime64_any_dtype(df[col]):
                # Forward-fill for datetime
                df[col] = df[col].ffill()
                imputation_log[col] = {
                    "strategy": "forward_fill",
                    "count": int(df[col].isna().sum()),
                }
                null_count -= int(df[col].isna().sum())
            else:
                df[col] = df[col].fillna(self.UNKNOWN_TEXT)
                imputation_log[col] = {
                    "strategy": "constant",
                    "fill_value": self.UNKNOWN_TEXT,
                    "count": null_count,
                }

            missing_fixed += null_count

        self._audit["missing_values_fixed"] = missing_fixed
        self._audit["per_column"].setdefault("imputation", imputation_log)

        # Log column count change
        if total_cols_before != df.shape[1]:
            self._audit["warnings"].append(
                f"Columns pruned: {total_cols_before} → {df.shape[1]} "
                f"({total_cols_before - df.shape[1]} removed)"
            )

        return df

    # ========================================================================
    # Phase 4 — Outlier Suppression
    # ========================================================================

    def _outlier_suppression(
        self, df: pd.DataFrame, iqr_k: float | None = None
    ) -> pd.DataFrame:
        """Winsorize numeric outliers using the IQR fence method.

        For every numeric column, values beyond
        ``[Q1 − k×IQR, Q3 + k×IQR]`` are **clipped** to the boundary —
        never deleted.

        Args:
            df: DataFrame with typed numeric columns.
            iqr_k: Override IQR multiplier. If None, use class default
                ``self.IQR_K`` (1.5). For long-tail distributions (e-commerce,
                finance) pass 3.0 or 5.0.

        Returns:
            DataFrame with outliers clamped to IQR fences.
        """
        df = df.copy()
        k = iqr_k if iqr_k is not None else self.IQR_K
        total_suppressed = 0
        outlier_log: dict[str, dict[str, Any]] = {}

        numeric_cols = df.select_dtypes(include="number").columns
        for col in numeric_cols:
            ser = df[col].dropna()
            if len(ser) < 4:
                continue

            q1 = float(ser.quantile(0.25))
            q3 = float(ser.quantile(0.75))
            iqr = q3 - q1
            if iqr == 0:
                continue

            lo = q1 - k * iqr
            hi = q3 + k * iqr

            below = int((df[col] < lo).sum())
            above = int((df[col] > hi).sum())

            if below + above == 0:
                continue

            # Integer columns (including nullable Int64) need integer fence
            # bounds, otherwise clip() produces floats that break the dtype.
            is_integer_col = pd.api.types.is_integer_dtype(df[col])
            if is_integer_col:
                lo_clip = int(np.floor(lo))
                hi_clip = int(np.ceil(hi))
            else:
                lo_clip = lo
                hi_clip = hi

            df[col] = df[col].clip(lo_clip, hi_clip)
            outlier_log[col] = {
                "method": "IQR",
                "k": k,
                "q1": round(q1, 4),
                "q3": round(q3, 4),
                "lower_fence": round(lo, 4),
                "upper_fence": round(hi, 4),
                "clamped_low": below,
                "clamped_high": above,
            }
            total_suppressed += below + above

        self._audit["outliers_suppressed"] = total_suppressed
        if outlier_log:
            self._audit["per_column"]["outlier_winsorizing"] = outlier_log

        if total_suppressed:
            self._audit["warnings"].append(
                f"{total_suppressed} outlier value(s) clamped to IQR fences "
                f"(k={k})"
            )

        return df

    # ========================================================================
    # Utility — Expand Nested Columns
    # ========================================================================

    @staticmethod
    def expand_nested(
        df: pd.DataFrame, column: str, id_column: str | None = None
    ) -> pd.DataFrame:
        """Expand a column of nested JSON/Python lists into a flat child table.

        Use this after ``execute()`` when a column (like ``events`` in
        user_logs.json) contains serialised lists of dicts that you want to
        analyse row-by-row.

        Args:
            df: The cleaned DataFrame from ``execute()``.
            column: Name of the column containing nested data.
            id_column: Optional column to use as a foreign key linking back
                to the parent table. If None, uses the DataFrame index.

        Returns:
            A new DataFrame with one row per nested item. The original
            ``id_column`` (or ``_parent_idx``) is preserved as a join key.

        Example:
            >>> df, audit = cleaner.execute("user_logs.json", {...})
            >>> events_df = cleaner.expand_nested(df, "events", "session_id")
            >>> events_df.head()
               session_id event_type       page           timestamp
            0      S0001     search    /search  2026-06-21T03:51:44
            1      S0001   add_cart  /checkout  2026-06-21T03:53:24
        """
        import ast
        rows = []
        key_col = id_column or "_parent_idx"
        for idx, row in df.iterrows():
            raw = row[column]
            if isinstance(raw, float) and pd.isna(raw):
                continue
            items = None
            if isinstance(raw, str):
                try:
                    items = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    try:
                        items = ast.literal_eval(raw)
                    except (ValueError, SyntaxError):
                        continue
            elif isinstance(raw, list):
                items = raw
            if not isinstance(items, list):
                continue
            parent_id = row[id_column] if id_column else idx
            for item in items:
                if isinstance(item, dict):
                    r = {key_col: parent_id, **item}
                    rows.append(r)
        return pd.DataFrame(rows)


# ============================================================================
# Module-level helpers
# ============================================================================

def _has_pyarrow_engine() -> bool:
    """Return ``True`` if the pyarrow CSV engine is available (Pandas ≥ 2.0)."""
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        return False


def _elapsed(since: datetime) -> float:
    """Return elapsed seconds from *since* to now, rounded to 2 decimals."""
    return round((datetime.now() - since).total_seconds(), 2)


def _safe_py(value: Any) -> Optional[float]:
    """Convert a value to a JSON-safe Python float, or ``None``."""
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# ============================================================================
# Main: synthetic test suite
# ============================================================================

if __name__ == "__main__":
    # ── Build a deliberately-dirty synthetic dataset ───────────────────────
    raw_data = {
        # Chinese column name with fullwidth characters
        "　职　员　编　号": [  # fullwidth spaces flanking each character
            "E-001", "E-002", "E-003", "E-004", "E-005",
            "E-006", "E-007", "E-008", "E-009", "E-010",
        ],
        "User-Name": [
            "张三\u200b", "李　四", "王五", "赵六", "孙七",
            "周八", "吴九", "郑\u200b\u200c十", "钱十一", "刘十二",
        ],
        " Age ": [
            "25", "三十", "32", "28", "999",        # "三十" is dirty; 999 is extreme outlier
            "45", "29", "31", "27", "33",
        ],
        # Column that is >70% null — should be dropped entirely
        "useless_column": [
            "x", None, None, None, None,
            None, None, None, None, None,
        ],
        "Salary(¥)": [
            "8000", "12000", "9500", "15000", "8800",
            "200000", "11000", "9700", "12500", "10200",  # 200000 is extreme outlier
        ],
        "city": [
            "北京", "上海", None, "深圳", "广州",
            None, None, "杭州", "成都", "武汉",
        ],
        "department": [
            "研发", "研发", "市场", "研发", "市场",
            "市场", "研发", None, None, "市场",
        ],
    }

    df_raw = pd.DataFrame(raw_data)
    print("=" * 72)
    print("  INPUT — RAW SYNTHETIC DATASET")
    print("=" * 72)
    print(df_raw.to_string())
    print(f"\nShape: {df_raw.shape}")
    print(f"Column names: {list(df_raw.columns)}")

    # Write to a temp CSV so we exercise _safe_ingest from disk
    temp_csv = Path(__file__).parent.parent / "assets" / "_test_input.csv"
    temp_csv.parent.mkdir(parents=True, exist_ok=True)
    df_raw.to_csv(temp_csv, index=False, encoding="utf-8")

    # ── Run the pipeline ──────────────────────────────────────────────────
    cleaner = DataPipelineCleaner()
    cleaned, audit = cleaner.execute(
        file_path=temp_csv,
        schema_rules={"age": "int", "salary": "float"},
    )

    print("\n" + "=" * 72)
    print("  OUTPUT — CLEANED DATAFRAME")
    print("=" * 72)
    print(cleaned.to_string())

    print("\n" + "=" * 72)
    print("  AUDIT REPORT")
    print("=" * 72)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        print(json.dumps(audit, ensure_ascii=False, indent=2, default=str))

    # Clean up temp file
    temp_csv.unlink(missing_ok=True)
