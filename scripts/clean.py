#!/usr/bin/env python
"""
Industrial-Grade Data Cleaning Pipeline — DataPipelineCleaner

Architecture (strict one-way dataflow):
  _safe_ingest → _standardize_columns_and_text → _type_alignment
  → _semantic_tagging → _decision_engine → _missing_value_trial → _outlier_suppression → execute()

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


def _standardize_col_name(name: str, lowercase: bool = True) -> str:
    """Apply the same column-name normalisation as Phase 1, without a DataFrame.

    Used in ``execute()`` to pre-compute the ``original → standardised``
    mapping before Phase 1 runs, so ``schema_rules`` can be translated.
    """
    name = str(name).strip()
    name = unicodedata.normalize("NFKC", name)
    if lowercase:
        name = name.lower()
    name = _SNAKE_CASE_SEP_RE.sub("_", name)
    name = _NON_ALNUM_RE.sub("", name)
    return name.strip("_") or "col"


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
        self._engine_kwargs: dict = {}
        # Programmatic access to extra DataFrames (NOT in audit — audit is JSON-safe)
        self._nested_dfs: dict[str, pd.DataFrame] = {}
        self._sheet_dfs: dict[str, pd.DataFrame] = {}

    # ── Extra DataFrame accessors (JSON-safe, programmatic only) ─────────

    @property
    def nested_dfs(self) -> dict[str, pd.DataFrame]:
        """Expanded nested-column child tables (populated after execute() with expand_nested=True)."""
        return self._nested_dfs

    @property
    def sheet_dfs(self) -> dict[str, pd.DataFrame]:
        """Extra Excel sheet DataFrames (populated after execute() on multi-sheet workbooks)."""
        return self._sheet_dfs

    # ========================================================================
    # Public API
    # ========================================================================

    def execute(
        self,
        file_path: str | Path,
        schema_rules: dict[str, str] | dict[str, dict[str, str]],
        engine_kwargs: dict[str, Any] | None = None,
        iqr_k: float | None = None,
        lowercase_columns: bool = True,
        output_encoding: str = "utf-8-sig",
        outlier_method: str = "iqr",
        outlier_threshold: float = 0.995,
        zscore_threshold: float = 3.0,
        business_rules: dict[str, dict[str, Any]] | None = None,
        expand_nested: bool = False,
        expected_min_rows: int | None = None,
        semantic_rules: dict[str, dict[str, Any]] | None = None,
        outlier_rules: dict[str, dict[str, Any]] | None = None,
        missing_rules: dict[str, dict[str, Any]] | None = None,
        ingestion_config: dict[str, Any] | None = None,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Run the full seven-phase pipeline and return cleaned data + audit.

        This is the **only** public entry point.  All seven phases execute in a
        fixed, unidirectional order:

            0. Safe ingest (all-string, zero type inference)
            1. Column-name / text-value standardisation (snake_case, NFKC, ghost-char removal)
            2. Type coercion per *schema_rules* with ``errors='coerce'``
            2.3. Semantic tagging (tag cells as invalid/suspicious — no data modification)
            2.4. Decision engine (apply invalid → NaN, suspicious → flag, legacy replace_values)
            2.5. Missing rules (sentinel → NaN — separate from semantic layer)
            3. Missing-value trial (column/row pruning + imputation)
            4. Outlier suppression (multi-strategy, never deletion)
            5. Audit report assembly

        Args:
            file_path: Path to ``.csv``, ``.tsv``, ``.xlsx``, ``.xls``, ``.json``,
                ``.parquet``, ``.feather``, ``.html``, ``.htm``, ``.xml``,
                ``.yaml``, ``.yml``, ``.db``, ``.sqlite``, or ``.sqlite3``.
            schema_rules: Mapping of column name → target dtype.  Supported
                values: ``'int'``, ``'float'``, ``'str'``, ``'datetime'``.
                For multi-sheet Excel / multi-table SQLite, pass a dict of
                dicts keyed by sheet/table name for per-table rules:
                ``{"Sheet1": {"age": "int"}, "Sheet2": {"price": "float"}}``.
                A flat dict is applied to all sheets/tables.
            outlier_method: ``"iqr"`` (default), ``"percentile"`` (best for
                long-tail distributions like e-commerce/finance), ``"zscore"``,
                or ``"none"``.
            outlier_threshold: For percentile method, clamp values beyond
                this quantile (default 0.995 = top/bottom 0.5%).
            zscore_threshold: For zscore method, clamp |z| > threshold.
            business_rules: Per-column semantic rules for missing values.
                Example: ``{"return_date": {"missing_means": "not_returned",
                "fill": "未还"}}``.
            expand_nested: If True, auto-detect and expand nested JSON/list
                columns.  Set to a list of column names for explicit control.
            expected_min_rows: If set and actual rows are fewer, emit a
                warning (useful for YAML/JSON that may be truncated).
            semantic_rules: Per-column semantic value rules. Each key is a
                column name, value is a dict with optional keys:
                ``invalid`` (list of values → NaN), ``suspicious`` (list of
                values → kept but flagged), ``sentinel`` (list of values → NaN).
                Example: ``{"age": {"invalid": [-5, -1], "suspicious": [150]}}``.
            outlier_rules: Per-column outlier suppression overrides. Each key
                is a column name, value is a dict with ``method`` (``"iqr"``,
                ``"percentile"``, ``"zscore"``, ``"none"``) and optional
                ``threshold`` / ``zscore_threshold`` / ``iqr_k``.
                Example: ``{"income": {"method": "percentile", "threshold": 0.995}}``.

        Returns:
            A 2-tuple of ``(cleaned_df, audit_report)``.
        """
        self._audit = {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "original_rows": 0,
            "cleaned_rows": 0,
            "retention_rate_pct": 100.0,
            "dropped_columns": [],
            "dropped_rows_count": 0,
            "missing_values_fixed": 0,
            "business_na_fixed": 0,
            "outliers_suppressed": 0,
            "outlier_method": outlier_method,
            "per_column": {},
            "stage_timings": {},
            "warnings": [],
            "semantic_audit": {"total_cells_tagged": 0, "by_type": {"invalid": 0, "suspicious": 0}},
            "cell_actions": [],
            "outlier_rules_applied": {},
        }

        t0 = datetime.now()
        fp = Path(file_path)

        # Phase 0: Safe ingest
        df = self._safe_ingest(fp, engine_kwargs or {})
        self._audit["original_rows"] = len(df)
        self._audit["stage_timings"]["safe_ingest"] = _elapsed(t0)
        # Row count validation for truncated files
        if expected_min_rows and len(df) < expected_min_rows:
            self._audit["warnings"].append(
                f"Row count ({len(df)}) below expected minimum "
                f"({expected_min_rows}) for {fp.name}. Data may be truncated."
            )

        # Unify ingestion_config with legacy params (backward compat)
        if ingestion_config:
            if "db" in ingestion_config and engine_kwargs is None:
                engine_kwargs = ingestion_config["db"]
            if "expand_nested" in ingestion_config and not expand_nested:
                expand_nested = ingestion_config["expand_nested"]
            if "expected_min_rows" in ingestion_config and expected_min_rows is None:
                expected_min_rows = ingestion_config["expected_min_rows"]

        # ── Pre-compute column name mapping for schema_rules ───────────────
        # Phase 1 will rename columns.  We translate schema_rules keys now
        # so Phase 2 receives column names that actually exist.
        # Detect nested (per-sheet) vs flat (shared) schema_rules.
        is_nested = self._is_nested_rules(schema_rules)
        sheets_order: list[str] = self._audit.get("_raw_sheets_order", [])
        is_multi = len(sheets_order) > 1
        main_sheet_name = sheets_order[0] if sheets_order else None
        main_rules = self._resolve_sheet_rules(schema_rules, main_sheet_name)
        # In multi-sheet mode with shared rules, suppress per-column
        # "not found" warnings to avoid noise from heterogeneous sheets.
        suppress_warn = is_multi and not is_nested
        translated_rules, name_map = self._translate_schema_rules(
            df, main_rules, lowercase_columns, suppress_warnings=suppress_warn,
        )
        self._audit["column_name_mapping"] = name_map
        # In multi-sheet mode with shared rules, warn once that different
        # sheets may have different columns and some rules may not apply.
        if is_multi and not is_nested:
            self._audit["warnings"].append(
                f"Shared schema_rules applied to {len(sheets_order)} sheets "
                f"({sheets_order}). Columns not present in a sheet are "
                f"silently skipped. Use per-sheet rules for granular control: "
                f"schema_rules={{'Sheet1': {{...}}, 'Sheet2': {{...}}}}"
            )
        # Record input/output format metadata
        self._audit["input"] = {
            "file": str(fp.name),
            "format": fp.suffix.lower(),
            "path": str(fp),
        }
        self._audit["output"] = {
            "format": "csv",
            "encoding": output_encoding,
        }

        # Phase 1: Standardise columns & text
        df = self._standardize_columns_and_text(df, lowercase=lowercase_columns)
        self._audit["stage_timings"]["standardize"] = _elapsed(t0)

        # Phase 2: Type alignment (uses translated_rules for post-standardization col names)
        df = self._type_alignment(df, translated_rules)
        self._audit["stage_timings"]["type_alignment"] = _elapsed(t0)
        self._audit["per_column"]["schema_rules_applied"] = translated_rules

        # Phase 2.3: Semantic tagging (tag only, no data modification)
        tag_audit = {}
        if semantic_rules:
            tag_audit = self._semantic_tagging(df, semantic_rules)
            self._audit["stage_timings"]["semantic_tagging"] = _elapsed(t0)

        # Phase 2.4: Decision engine (apply semantic decisions + legacy replace_values)
        cell_actions = []
        if semantic_rules or business_rules:
            df, cell_actions = self._decision_engine(df, semantic_rules, business_rules)
            self._audit["stage_timings"]["decision_engine"] = _elapsed(t0)
        self._audit["cell_actions"] = cell_actions

        # Phase 2.5: Missing rules — sentinel → NaN (separate from semantic layer)
        # Sentinel values are "missing placeholders", not semantic errors.
        # They must be converted BEFORE missing_value_trial fills them.
        sentinel_audit: dict[str, Any] = {}
        if missing_rules:
            sentinel_count = 0
            sentinel_log: dict[str, dict[str, Any]] = {}
            for col, rule in missing_rules.items():
                vals = rule.get("sentinel") or rule.get("values") or []
                if not vals or col not in df.columns:
                    continue
                try:
                    numeric_col = pd.to_numeric(df[col], errors="coerce")
                    mask = numeric_col.isin(vals)
                except Exception:
                    continue
                replaced = int(mask.sum())
                if replaced > 0:
                    df.loc[mask, col] = pd.NA
                    sentinel_count += replaced
                    sentinel_log[col] = {"sentinel_values": vals, "count": replaced}
            sentinel_audit = {"total_sentinels_converted": sentinel_count, "by_column": sentinel_log}
            self._audit["missing_rules_audit"] = sentinel_audit
            if sentinel_count:
                self._audit["warnings"].append(
                    f"missing_rules: {sentinel_count} sentinel value(s) converted to NaN."
                )

        # Phase 3: Missing-value trial
        df = self._missing_value_trial(df, business_rules=business_rules)
        self._audit["stage_timings"]["missing_trial"] = _elapsed(t0)

        # Phase 4: Outlier suppression (per-column methods)
        # Priority validation: skip outlier for columns whose type alignment failed
        if outlier_rules:
            failed_cols = []
            for col in outlier_rules:
                if col in df.columns and df[col].dtype == "object":
                    # Type alignment failed or column stayed string — skip outlier
                    failed_cols.append(col)
                    self._audit["warnings"].append(
                        f"outlier_rules['{col}'] skipped: column is still string "
                        f"(type alignment may have failed)."
                    )
            for col in failed_cols:
                outlier_rules.pop(col, None)

        df = self._outlier_suppression(
            df, iqr_k=iqr_k, method=outlier_method,
            threshold=outlier_threshold, zscore_threshold=zscore_threshold,
            outlier_rules=outlier_rules,
        )
        self._audit["stage_timings"]["outlier_suppression"] = _elapsed(t0)

        # Phase 4.5: Expand nested columns if requested
        nested_dfs: dict[str, pd.DataFrame] = {}
        if expand_nested:
            nested_dfs = self._auto_expand_nested(df, expand_nested)
            self._audit["nested_tables"] = {
                col: len(child) for col, child in nested_dfs.items()
            }

        # Phase 4.6: Process extra Excel sheets (if multi-sheet & no explicit sheet)
        sheet_dfs: dict[str, pd.DataFrame] = {}
        raw_sheets: dict[str, pd.DataFrame] | None = self._audit.pop("_raw_sheets", None)
        sheet_order: list[str] = self._audit.pop("_raw_sheets_order", [])
        if raw_sheets:
            for sname, raw_df in raw_sheets.items():
                try:
                    # Resolve per-sheet schema_rules
                    s_rules = self._resolve_sheet_rules(schema_rules, sname)
                    s_translated, _ = self._translate_schema_rules(
                        raw_df, s_rules, lowercase_columns,
                        suppress_warnings=suppress_warn,
                    )
                    s_df = self._standardize_columns_and_text(
                        raw_df, lowercase=lowercase_columns
                    )
                    s_df = self._type_alignment(s_df, s_translated)
                    s_df = self._missing_value_trial(
                        s_df, business_rules=business_rules
                    )
                    s_df = self._outlier_suppression(
                        s_df, iqr_k=iqr_k, method=outlier_method,
                        threshold=outlier_threshold,
                        zscore_threshold=zscore_threshold,
                    )
                    sheet_dfs[sname] = s_df
                except Exception as exc:
                    self._audit["warnings"].append(
                        f"Multi-sheet processing failed for '{sname}': {exc}"
                    )
            self._audit["sheets_processed"] = sheet_order

        # Phase 5: Final audit assembly
        self._audit["cleaned_rows"] = len(df)
        original = self._audit["original_rows"]
        cleaned = self._audit["cleaned_rows"]
        self._audit["retention_rate_pct"] = (
            round(cleaned / original * 100, 2) if original > 0 else 0.0
        )
        self._audit["semantic_audit"] = tag_audit if tag_audit else self._audit["semantic_audit"]
        self._audit["finished_at"] = datetime.now().isoformat(timespec="seconds")

        # ── Store expanded/sheet DataFrames for programmatic access ─────
        # NOT in the audit dict (which must be JSON-serializable).
        # Use cleaner.nested_dfs / cleaner.sheet_dfs after execute().
        self._nested_dfs = nested_dfs
        self._sheet_dfs = sheet_dfs

        # Audit only contains JSON-safe metadata summaries
        if nested_dfs:
            self._audit["_nested_dfs_summary"] = {
                col: {"rows": len(child), "cols": child.shape[1],
                       "columns": child.columns.tolist()}
                for col, child in nested_dfs.items()
            }
        if sheet_dfs:
            self._audit["_sheet_dfs_summary"] = {
                sname: {"rows": len(sdf), "cols": sdf.shape[1],
                         "columns": sdf.columns.tolist()}
                for sname, sdf in sheet_dfs.items()
            }

        # ═══════════════════════════════════════════════════════════════
        # Phase 6: Semantic Output Layer (for AI consumption)
        # ═══════════════════════════════════════════════════════════════
        self._audit["semantic_output"] = self._build_semantic_output(df, self._audit)

        return df, self._audit.copy()

    # ========================================================================
    # Semantic Output Builder (AI-native layer)
    # ========================================================================

    @staticmethod
    def _build_semantic_output(df: pd.DataFrame, audit: dict) -> dict:
        """Build AI-friendly structured output from raw audit data.

        Returns:
            {
                "summary": one-line summary,
                "data_quality_score": 0-100,
                "data_quality_dimensions": {completeness, validity, consistency},
                "insights": [...],
                "actions_taken": [...],
                "recommendations": [...]
            }
        """
        original = audit.get("original_rows", 0)
        cleaned = audit.get("cleaned_rows", 0)
        retention = audit.get("retention_rate_pct", 100)
        missing = audit.get("missing_values_fixed", 0)
        outliers = audit.get("outliers_suppressed", 0)
        sem = audit.get("semantic_audit", {})
        actions = audit.get("cell_actions", [])

        # --- Data quality dimensions (0-1) ---
        # Completeness: ratio of non-null cells after cleaning
        if cleaned > 0 and len(df.columns) > 0:
            total_cells = cleaned * len(df.columns)
            null_cells = int(df.isna().sum().sum())
            completeness = round(1 - null_cells / total_cells, 3) if total_cells > 0 else 1.0
        else:
            completeness = 1.0

        # Validity: ratio of non-invalid cells (no semantic invalid tags)
        total_invalid = sem.get("by_type", {}).get("invalid", 0)
        validity = round(1 - total_invalid / max(original * len(df.columns), 1), 3) if original > 0 else 1.0

        # Consistency: retention rate as proxy (rows kept / original)
        consistency = round(retention / 100, 3)

        # Overall score (weighted)
        score = int(completeness * 40 + validity * 35 + consistency * 25)
        score = max(0, min(100, score))

        # --- Summary ---
        parts = []
        if missing > 0:
            parts.append(f"filled {missing} missing value(s)")
        if outliers > 0:
            parts.append(f"clipped {outliers} outlier(s)")
        total_tagged = sem.get("total_cells_tagged", 0)
        if total_tagged > 0:
            parts.append(f"tagged {total_tagged} semantic issue(s)")
        if not parts:
            parts.append("no issues detected")
        summary = f"Cleaned {original} rows -> {cleaned} rows ({retention}% retained). " + "; ".join(parts) + f". Quality score: {score}/100."

        # --- Insights ---
        insights = []
        if missing > original * 0.05:
            insights.append(f"High missing rate: {missing}/{original} values ({missing/original*100:.1f}%) were filled.")
        if outliers > original * 0.03:
            insights.append(f"Significant outlier activity: {outliers} values clipped.")
        if total_invalid > 0:
            insights.append(f"Found {total_invalid} clearly invalid value(s) converted to NaN.")
        if sem.get("by_type", {}).get("suspicious", 0) > 0:
            n = sem["by_type"]["suspicious"]
            insights.append(f"{n} value(s) flagged as suspicious but preserved.")
        dropped = audit.get("dropped_columns", [])
        if dropped:
            insights.append(f"Dropped column(s): {dropped}")
        if completeness < 0.95:
            insights.append(f"Completeness is {completeness*100:.1f}% — review source data.")
        if not insights:
            insights.append("Data quality is good.")

        # --- Actions ---
        actions_taken = []
        impute_log = audit.get("per_column", {}).get("imputation", {})
        for col, info in impute_log.items():
            actions_taken.append(f"'{col}': filled {info.get('count',0)} missing with {info.get('strategy','?')}")
        ow = audit.get("per_column", {}).get("outlier_winsorizing", {})
        for col, info in ow.items():
            actions_taken.append(f"'{col}': clipped to [{info.get('lower_fence','?')}, {info.get('upper_fence','?')}] via {info.get('method','?')}")
        for a in actions[:5]:
            actions_taken.append(f"row {a['row']}, {a['col']}: {a['original']} -> {a['action']}")
        if len(actions) > 5:
            actions_taken.append(f"... and {len(actions)-5} more cell actions")

        # --- Recommendations ---
        recommendations = []
        if retention < 95:
            recommendations.append("Retention below 95% — review deletion criteria.")
        if missing > original * 0.1:
            recommendations.append("Over 10% missing — investigate upstream data collection.")
        if sem.get("by_type", {}).get("suspicious", 0) > 0:
            recommendations.append("Suspicious values flagged — human review recommended.")
        if completeness < 0.95:
            recommendations.append("Low completeness — consider imputation or data collection.")
        if not recommendations:
            recommendations.append("No further action needed.")

        return {
            "summary": summary,
            "data_quality_score": score,
            "data_quality_dimensions": {
                "completeness": completeness,
                "validity": validity,
                "consistency": consistency,
            },
            "insights": insights,
            "actions_taken": actions_taken[:15],
            "recommendations": recommendations,
        }

    # ========================================================================
    # Batch Processing — run_on_directory
    # ========================================================================

    # Supported file extensions for batch discovery
    _BATCH_EXTENSIONS: tuple[str, ...] = (
        ".csv", ".tsv", ".xlsx", ".xls", ".json",
        ".parquet", ".feather", ".html", ".htm", ".xml",
        ".yaml", ".yml", ".db", ".sqlite", ".sqlite3",
        ".pkl", ".pickle",
    )

    def run_on_directory(
        self,
        input_dir: str | Path,
        output_dir: str | Path = "cleaned_data",
        schema_rules: dict[str, str] | dict[str, dict[str, str]] | None = None,
        engine_kwargs: dict[str, Any] | None = None,
        iqr_k: float | None = None,
        lowercase_columns: bool = True,
        output_encoding: str = "utf-8-sig",
        outlier_method: str = "iqr",
        outlier_threshold: float = 0.995,
        zscore_threshold: float = 3.0,
        business_rules: dict[str, dict[str, Any]] | None = None,
        expand_nested: bool = False,
        expected_min_rows: int | None = None,
        file_pattern: str = "*.*",
        skip_errors: bool = True,
    ) -> dict[str, Any]:
        """Batch-clean all supported files in a directory.

        Each source file gets its own subdirectory under *output_dir*:

        ::

            cleaned_data/
            ├── orders/           # Single CSV → orders.csv + orders_audit.json
            ├── sales_report/     # Multi-sheet Excel → one CSV per sheet + audit
            ├── user_logs/        # Nested JSON → parent + child CSVs + audit
            ├── library/          # SQLite → per-table CSVs + schema.json + audit
            └── _summary.json     # Per-file row counts, retention, errors

        Args:
            input_dir: Directory containing data files to clean.
            output_dir: Root directory for cleaned output.
            file_pattern: Glob pattern to filter files (default ``"*.*"``).
            skip_errors: If True, log errors and continue; if False, raise.
            All other arguments are forwarded to :meth:`execute`.

        Returns:
            Summary dict with per-file statistics and overall totals.
        """
        input_path = Path(input_dir)
        if not input_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {input_dir}")

        all_schema_rules = schema_rules or {}
        output_path = Path(output_dir)
        summary: dict[str, Any] = {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "input_directory": str(input_path.resolve()),
            "output_directory": str(output_path.resolve()),
            "total_files": 0,
            "succeeded": 0,
            "failed": 0,
            "total_original_rows": 0,
            "total_cleaned_rows": 0,
            "files": [],
        }

        # Discover supported files
        seen: set[str] = set()
        discovered: list[Path] = []
        for ext in self._BATCH_EXTENSIONS:
            pat = file_pattern.replace("*.*", f"*{ext}") if file_pattern == "*.*" else file_pattern
            for fp in input_path.glob(pat):
                if fp.suffix.lower() == ext and str(fp) not in seen:
                    seen.add(str(fp))
                    discovered.append(fp)
        # Fallback: if file_pattern is something arbitrary, try it directly
        if not discovered and file_pattern != "*.*":
            for fp in input_path.glob(file_pattern):
                if fp.suffix.lower() in self._BATCH_EXTENSIONS and str(fp) not in seen:
                    seen.add(str(fp))
                    discovered.append(fp)
        discovered.sort(key=lambda p: p.name)
        summary["total_files"] = len(discovered)

        for fp in discovered:
            file_entry: dict[str, Any] = {
                "file": fp.name,
                "path": str(fp),
                "status": "ok",
                "subdir": None,
                "original_rows": 0,
                "cleaned_rows": 0,
                "retention_rate_pct": 100.0,
                "extra_sheets": [],
                "nested_tables": {},
                "artifacts": [],
            }
            try:
                # Each source gets its own subdirectory
                source_subdir = output_path / fp.stem
                source_subdir.mkdir(parents=True, exist_ok=True)
                file_entry["subdir"] = str(source_subdir.resolve())

                # Run the pipeline
                df, audit = self.execute(
                    file_path=fp,
                    schema_rules=all_schema_rules,
                    engine_kwargs=engine_kwargs,
                    iqr_k=iqr_k,
                    lowercase_columns=lowercase_columns,
                    output_encoding=output_encoding,
                    outlier_method=outlier_method,
                    outlier_threshold=outlier_threshold,
                    zscore_threshold=zscore_threshold,
                    business_rules=business_rules,
                    expand_nested=expand_nested,
                    expected_min_rows=expected_min_rows,
                )

                file_entry["original_rows"] = audit.get("original_rows", 0)
                file_entry["cleaned_rows"] = audit.get("cleaned_rows", 0)
                file_entry["retention_rate_pct"] = audit.get("retention_rate_pct", 100.0)

                artifacts = self._save_cleaned_output(
                    source_subdir, fp.stem, df, audit, output_encoding,
                )
                file_entry["artifacts"] = artifacts

                # Extra sheets (from Excel multi-sheet)
                sheet_dfs = self._sheet_dfs
                if sheet_dfs:
                    file_entry["extra_sheets"] = list(sheet_dfs.keys())
                    for sname, sdf in sheet_dfs.items():
                        sname_safe = _standardize_col_name(sname, lowercase=lowercase_columns)
                        sheet_csv = source_subdir / f"{sname_safe}.csv"
                        sdf.to_csv(sheet_csv, index=False, encoding=output_encoding)
                        file_entry["artifacts"].append(sheet_csv.name)

                # Nested/expanded child tables
                nested_dfs = self._nested_dfs
                if nested_dfs:
                    for child_name, child_df in nested_dfs.items():
                        child_csv = source_subdir / f"{fp.stem}_{child_name}.csv"
                        child_df.to_csv(child_csv, index=False, encoding=output_encoding)
                        file_entry["artifacts"].append(child_csv.name)
                        file_entry["nested_tables"][child_name] = len(child_df)

                summary["total_original_rows"] += file_entry["original_rows"]
                summary["total_cleaned_rows"] += file_entry["cleaned_rows"]
                summary["succeeded"] += 1

            except Exception as exc:
                file_entry["status"] = "failed"
                file_entry["error"] = str(exc)
                summary["failed"] += 1
                if not skip_errors:
                    raise

            summary["files"].append(file_entry)

        # Overall retention rate
        if summary["total_original_rows"] > 0:
            summary["overall_retention_pct"] = round(
                summary["total_cleaned_rows"] / summary["total_original_rows"] * 100, 2
            )
        else:
            summary["overall_retention_pct"] = 0.0

        summary["finished_at"] = datetime.now().isoformat(timespec="seconds")

        # Write _summary.json
        summary_json = output_path / "_summary.json"
        with open(summary_json, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

        return summary

    def _save_cleaned_output(
        self,
        subdir: Path,
        source_stem: str,
        df: pd.DataFrame,
        audit: dict[str, Any],
        output_encoding: str = "utf-8-sig",
    ) -> list[str]:
        """Save the main DataFrame and audit JSON into *subdir*.

        Returns a list of saved artifact filenames (relative to *subdir*).
        """
        artifacts: list[str] = []

        # ═══════════════════════════════════════════════════════════════
        # Data Product Package Output
        # ═══════════════════════════════════════════════════════════════

        # 1. data/ — cleaned data (CSV + Parquet)
        data_dir = subdir / "data"
        data_dir.mkdir(exist_ok=True)

        csv_path = data_dir / f"{source_stem}.csv"
        df.to_csv(csv_path, index=False, encoding=output_encoding)
        artifacts.append(f"data/{csv_path.name}")

        try:
            parquet_path = data_dir / f"{source_stem}.parquet"
            df.to_parquet(parquet_path, index=False)
            artifacts.append(f"data/{parquet_path.name}")
        except Exception:
            pass  # parquet optional

        # 2. report/ — audit + data quality
        report_dir = subdir / "report"
        report_dir.mkdir(exist_ok=True)

        audit_path = report_dir / "audit.json"
        with open(audit_path, "w", encoding="utf-8") as f:
            json.dump(audit, f, ensure_ascii=False, indent=2, default=str)
        artifacts.append(f"report/audit.json")

        semantic = audit.get("semantic_output", {})
        if semantic:
            dq_path = report_dir / "data_quality.json"
            with open(dq_path, "w", encoding="utf-8") as f:
                json.dump({
                    "score": semantic.get("data_quality_score", 0),
                    "dimensions": semantic.get("data_quality_dimensions", {}),
                    "summary": semantic.get("summary", ""),
                    "insights": semantic.get("insights", []),
                    "recommendations": semantic.get("recommendations", []),
                }, f, ensure_ascii=False, indent=2, default=str)
            artifacts.append(f"report/data_quality.json")

        # 3. lineage/ — transformation steps
        lineage_dir = subdir / "lineage"
        lineage_dir.mkdir(exist_ok=True)

        lineage = {
            "pipeline_version": "v2",
            "steps": [],
        }
        if audit.get("per_column", {}).get("schema_rules_applied"):
            lineage["steps"].append({"phase": "type_alignment", "rules": audit["per_column"]["schema_rules_applied"]})
        if audit.get("semantic_audit", {}).get("total_cells_tagged", 0) > 0:
            lineage["steps"].append({"phase": "semantic_tagging", "audit": audit["semantic_audit"]})
        if audit.get("cell_actions"):
            lineage["steps"].append({"phase": "decision_engine", "actions_count": len(audit["cell_actions"])})
        if audit.get("missing_rules_audit", {}).get("total_sentinels_converted", 0) > 0:
            lineage["steps"].append({"phase": "missing_rules", "audit": audit["missing_rules_audit"]})
        if audit.get("per_column", {}).get("imputation"):
            lineage["steps"].append({"phase": "imputation", "details": audit["per_column"]["imputation"]})
        if audit.get("per_column", {}).get("outlier_winsorizing"):
            lineage["steps"].append({"phase": "outlier_suppression", "details": audit["per_column"]["outlier_winsorizing"]})

        lineage_path = lineage_dir / "transformations.json"
        with open(lineage_path, "w", encoding="utf-8") as f:
            json.dump(lineage, f, ensure_ascii=False, indent=2, default=str)
        artifacts.append(f"lineage/transformations.json")

        # 4. metadata.json — control layer
        input_info = audit.get("input", {})
        metadata = {
            "source_file": input_info.get("file", source_stem),
            "source_format": input_info.get("format", ""),
            "rows_before": audit.get("original_rows", 0),
            "rows_after": audit.get("cleaned_rows", 0),
            "retention_rate_pct": audit.get("retention_rate_pct", 0),
            "missing_fixed": audit.get("missing_values_fixed", 0),
            "outliers_suppressed": audit.get("outliers_suppressed", 0),
            "data_quality_score": semantic.get("data_quality_score", 0),
            "pipeline_version": "v2",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        meta_path = subdir / "metadata.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2, default=str)
        artifacts.append("metadata.json")

        # 5. samples/ — before vs after (first 5 rows)
        samples_dir = subdir / "samples"
        samples_dir.mkdir(exist_ok=True)

        # Read original for before sample
        try:
            original_path = input_info.get("path", "")
            if original_path and Path(original_path).exists():
                from profile import _read as _profile_read
                df_before = _profile_read(Path(original_path))
                before_sample = samples_dir / "before_sample.csv"
                df_before.head(5).to_csv(before_sample, index=False, encoding=output_encoding)
                artifacts.append(f"samples/before_sample.csv")
        except Exception:
            pass

        after_sample = samples_dir / "after_sample.csv"
        df.head(5).to_csv(after_sample, index=False, encoding=output_encoding)
        artifacts.append(f"samples/after_sample.csv")

        # SQLite schema (if the source was a database)
        input_fmt = input_info.get("format", "")
        if input_fmt in {".db", ".sqlite", ".sqlite3"}:
            original_path = input_info.get("path", "")
            if original_path and Path(original_path).exists():
                try:
                    schema = self.extract_sqlite_schema(original_path)
                    schema_path = lineage_dir / "schema.json"
                    with open(schema_path, "w", encoding="utf-8") as f:
                        json.dump(schema, f, ensure_ascii=False, indent=2, default=str)
                    artifacts.append(f"lineage/schema.json")
                except Exception:
                    pass

        return artifacts

    # ========================================================================
    # Schema Rules Helpers
    # ========================================================================

    @staticmethod
    def _is_nested_rules(rules: dict) -> bool:
        """Return True if *rules* is a dict-of-dicts (per-sheet rules)."""
        if not rules:
            return False
        # At least one value must be a dict → nested
        return any(isinstance(v, dict) for v in rules.values())

    @staticmethod
    def _resolve_sheet_rules(
        rules: dict[str, str] | dict[str, dict[str, str]],
        sheet_name: str | None = None,
    ) -> dict[str, str]:
        """Resolve per-sheet schema_rules for a given sheet/table name.

        - If *rules* is flat (dict[str, str]) → return as-is.
        - If *rules* is nested (dict[str, dict]) and *sheet_name* matches
          a key → return the matching sub-dict.
        - If nested but no match → return empty dict (no rules for this sheet).
        """
        if not rules:
            return {}
        if DataPipelineCleaner._is_nested_rules(rules):
            if sheet_name is not None and sheet_name in rules:
                val = rules[sheet_name]
                if isinstance(val, dict):
                    return val  # type: ignore[return-value]
            return {}
        # Flat dict — shared across all sheets
        return rules  # type: ignore[return-value]

    # ========================================================================
    # Schema Rules Translation (shared by main DF and multi-sheet processing)
    # ========================================================================

    def _translate_schema_rules(
        self, df: pd.DataFrame, schema_rules: dict[str, str],
        lowercase_columns: bool,
        suppress_warnings: bool = False,
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Translate *schema_rules* keys to post-standardisation column names.

        Phase 1 will rename columns.  We predict what each column name will
        become, then map *schema_rules* keys to those predicted names so
        Phase 2 receives column names that actually exist.

        Args:
            suppress_warnings: If True, skip "not found" warnings.  Useful
                in multi-sheet mode where different sheets have different
                columns and mismatches are expected.

        Returns:
            ``(translated_rules, name_map)`` where *name_map* is
            ``{original_col → predicted_col}``.
        """
        original_cols = list(df.columns)
        predicted_names = [
            _standardize_col_name(c, lowercase=lowercase_columns)
            for c in original_cols
        ]
        name_map: dict[str, str] = dict(zip(original_cols, predicted_names))
        # Handle deduplication collisions (same logic as Phase 1)
        seen: dict[str, int] = {}
        deduped: list[str] = []
        for name in predicted_names:
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[name]}"
            else:
                seen[name] = 0
            deduped.append(name)
        name_map = dict(zip(original_cols, deduped))
        # Translate schema_rules to use standardised column names
        translated: dict[str, str] = {}
        for col_key, target in schema_rules.items():
            matched = name_map.get(col_key)
            if matched is not None:
                translated[matched] = target
            elif col_key in deduped:
                translated[col_key] = target
            else:
                # Try case-insensitive match as fallback
                for orig, pred in name_map.items():
                    if str(orig).lower() == str(col_key).lower():
                        translated[pred] = target
                        break
                else:
                    if not suppress_warnings:
                        all_keys = list(name_map.keys())
                        shown = all_keys if len(all_keys) <= 50 else all_keys[:50]
                        suffix = f" (and {len(all_keys) - 50} more)" if len(all_keys) > 50 else ""
                        self._audit["warnings"].append(
                            f"schema_rules key '{col_key}' not found in "
                            f"columns: {shown}{suffix}"
                        )
        return translated, name_map

    # ========================================================================
    # Phase 0 — Safe Ingest
    # ========================================================================

    def _safe_ingest(
        self, file_path: Path, engine_kwargs: dict[str, Any] | None = None,
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
            with open(file_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                df = pd.DataFrame(raw)
            elif isinstance(raw, dict):
                list_keys = [k for k, v in raw.items() if isinstance(v, list)]
                if list_keys:
                    best_key = max(list_keys, key=lambda k: len(raw[k]))
                    df = pd.DataFrame(raw[best_key])
                else:
                    df = pd.json_normalize(raw)
            else:
                df = pd.DataFrame([raw])
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
            engine = "xlrd" if suffix == ".xls" else "openpyxl"
            sheet = (engine_kwargs or {}).get("sheet")
            if sheet is not None:
                return pd.read_excel(
                    file_path, sheet_name=sheet, dtype=str, na_filter=False,
                    engine=engine,
                )
            # Read all sheets to detect multi-sheet workbooks
            xls = pd.ExcelFile(file_path, engine=engine)
            sheet_names = xls.sheet_names
            if len(sheet_names) == 1:
                return pd.read_excel(xls, dtype=str, na_filter=False)
            # Multiple sheets: process ALL by default (like SQLite multi-table).
            # Store extra sheets so execute() can run the full pipeline on each.
            extra_sheets: dict[str, pd.DataFrame] = {}
            for sname in sheet_names[1:]:
                extra_sheets[sname] = pd.read_excel(
                    xls, sheet_name=sname, dtype=str, na_filter=False
                )
            self._audit["_raw_sheets"] = extra_sheets
            self._audit["_raw_sheets_order"] = sheet_names
            self._audit["warnings"].append(
                f"Excel workbook has {len(sheet_names)} sheets: {sheet_names}. "
                f"Processing all sheets. Returning sheet 0 ('{sheet_names[0]}') "
                f"as main DataFrame; remaining {len(sheet_names)-1} sheet(s) in "
                f"audit['_sheets']."
            )
            return pd.read_excel(xls, sheet_name=sheet_names[0], dtype=str, na_filter=False)

        if suffix == ".parquet":
            return pd.read_parquet(file_path)

        if suffix == ".feather":
            return pd.read_feather(file_path)

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
            with sqlite3.connect(str(file_path)) as con:
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
                    return df
                if len(table_names) == 1:
                    df = pd.read_sql_query(
                        f'SELECT * FROM "{table_names[0]}"', con, dtype=str
                    )
                    return df
                raise ValueError(
                    f"Multiple tables found in {file_path}: {table_names}. "
                    f"Use engine_kwargs={{'table': '<name>'}} to pick one."
                )

        if suffix in {".pkl", ".pickle"}:
            data = pd.read_pickle(file_path)
            if isinstance(data, dict):
                list_keys = [k for k, v in data.items() if isinstance(v, list)]
                if list_keys:
                    best_key = max(list_keys, key=lambda k: len(data[k]))
                    df = pd.DataFrame(data[best_key])
                else:
                    df = pd.DataFrame.from_dict(data, orient="index")
            elif isinstance(data, pd.DataFrame):
                df = data
            elif isinstance(data, list):
                df = pd.DataFrame(data)
            else:
                df = pd.DataFrame([data])
            return df.astype(str)

        raise ValueError(
            f"Unsupported file format: '{suffix}'. "
            f"Supported: .csv, .tsv, .xlsx, .xls, .json, .parquet, .feather, "
            f".html, .htm, .xml, .yaml, .yml, .db, .sqlite, .sqlite3, .pkl, .pickle"
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
            name = re.sub(r"_+", "_", name)
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
                .str.replace(r" {2,}", " ", regex=True)
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
                if target in ("int", "float"):
                    # Strip currency symbols (¥ $ € £) and thousand separators
                    # before numeric coercion so "¥3,839.59" → "3839.59".
                    raw_col = df[col].astype(str).str.replace(
                        r'[¥￥$€£,]', '', regex=True
                    )
                    numeric = pd.to_numeric(raw_col, errors="coerce")
                    if target == "int":
                        # Round before casting to Int64 so "45.7" → 46
                        df[col] = numeric.round().astype("Int64")
                    else:
                        df[col] = numeric.astype("float64")
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
    # Phase 2.3 — Semantic Tagging
    # ========================================================================

    def _semantic_tagging(
        self,
        df: pd.DataFrame,
        semantic_rules: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Tag cells as invalid/suspicious without modifying data.

        For each column with semantic_rules, creates a ``{col}_tags`` column
        with per-cell tags.  Does NOT modify the original data values.
        Sentinel handling is now in missing_rules (Phase 2.5).

        Args:
            df: DataFrame after type alignment (Phase 2).
            semantic_rules: ``{col: {"invalid": [...], "suspicious": [...]}}``.

        Returns:
            Tag audit: ``{"col": {"invalid": N, "suspicious": N}}``.
        """
        tag_audit: dict[str, dict[str, int]] = {}
        total_tagged = 0
        by_type = {"invalid": 0, "suspicious": 0}

        for col, rules in semantic_rules.items():
            if col not in df.columns:
                continue

            col_audit = {"invalid": 0, "suspicious": 0}
            tags = pd.Series(None, index=df.index, dtype="string")

            for tag_type in ("invalid", "suspicious"):
                values = rules.get(tag_type, [])
                if not values:
                    continue
                try:
                    numeric_vals = pd.to_numeric(df[col], errors="coerce")
                    mask = numeric_vals.isin(values)
                except Exception:
                    mask = df[col].astype(str).isin([str(v) for v in values])
                count = int(mask.sum())
                if count > 0:
                    tags[mask] = tag_type
                    col_audit[tag_type] = count
                    by_type[tag_type] += count
                    total_tagged += count

            if any(v > 0 for v in col_audit.values()):
                df[f"{col}_tags"] = tags
                tag_audit[col] = col_audit

        return {"total_cells_tagged": total_tagged, "by_type": by_type, "details": tag_audit}

    # ========================================================================
    # Phase 2.4 — Decision Engine
    # ========================================================================

    def _decision_engine(
        self,
        df: pd.DataFrame,
        semantic_rules: dict[str, dict[str, Any]] | None,
        business_rules: dict[str, dict[str, Any]] | None,
    ) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
        """Apply semantic decisions and legacy replace_values.

        For each column with semantic_rules:
          - 'invalid' cells → convert to NaN
          - 'suspicious' cells → KEEP the value, but flag in audit

        Sentinel handling is now in missing_rules (Phase 2.5).
        Then handles legacy ``replace_values`` from business_rules (backward compat).
        Drops tag columns after processing.

        Returns:
            ``(df, cell_actions)`` where cell_actions is a list of dicts
            describing each cell action taken.
        """
        df = df.copy()
        cell_actions: list[dict[str, Any]] = []

        # Process semantic_rules
        if semantic_rules:
            for col, rules in semantic_rules.items():
                if col not in df.columns:
                    continue

                for tag_type in ("invalid",):
                    values = rules.get(tag_type, [])
                    if not values:
                        continue
                    try:
                        numeric_vals = pd.to_numeric(df[col], errors="coerce")
                        mask = numeric_vals.isin(values)
                    except Exception:
                        mask = df[col].astype(str).isin([str(v) for v in values])

                    matched_indices = df.index[mask]
                    for idx in matched_indices:
                        original_val = df.at[idx, col]
                        cell_actions.append({
                            "row": int(idx),
                            "col": col,
                            "original": _safe_py(original_val) if pd.api.types.is_numeric_dtype(df[col]) else str(original_val),
                            "action": f"{tag_type}_to_nan",
                            "rule": f"{col}.{tag_type}",
                        })
                    df.loc[mask, col] = pd.NA

        # Handle legacy replace_values from business_rules (backward compat)
        brules = business_rules or {}
        for col, rule in brules.items():
            vals_to_replace = rule.get("replace_values")
            if vals_to_replace is None or col not in df.columns:
                continue
            try:
                numeric_col = pd.to_numeric(df[col], errors="coerce")
                mask = numeric_col.isin(vals_to_replace)
            except Exception:
                continue
            matched_indices = df.index[mask]
            for idx in matched_indices:
                original_val = df.at[idx, col]
                cell_actions.append({
                    "row": int(idx),
                    "col": col,
                    "original": _safe_py(original_val) if pd.api.types.is_numeric_dtype(df[col]) else str(original_val),
                    "action": "replace_value_to_nan",
                    "rule": f"{col}.replace_values",
                })
            df.loc[mask, col] = pd.NA

        # Drop tag columns after processing
        tag_cols = [c for c in df.columns if c.endswith("_tags")]
        if tag_cols:
            df.drop(columns=tag_cols, inplace=True)

        return df, cell_actions

    # ========================================================================
    # Phase 3 — Missing-Value Trial
    # ========================================================================

    def _missing_value_trial(
        self, df: pd.DataFrame,
        business_rules: dict[str, dict[str, Any]] | None = None,
    ) -> pd.DataFrame:
        """Judge and remedy missing values.

        Rules (applied in order):
            1. **Column execution**: any column with ``> 70%`` null →
               drop the entire column.
            2. **Row execution**: any row with ``> 50%`` null fields →
               drop the row.
            3. **Business rules**: per-column semantic rules from
               *business_rules* override the default strategy.
            4. **Imputation**: numeric → median; text/category →
               ``self.UNKNOWN_TEXT``.

        Args:
            df: DataFrame after type coercion.
            business_rules: ``{col_name: {missing_means: str, fill: Any}}``.
                Columns listed here bypass the default imputation and use the
                specified fill value.  Marked ``type: "business_na"`` in audit.

        Returns:
            DataFrame with pruned columns/rows and imputed missing values.
        """
        df = df.copy()
        total_rows = len(df)
        total_cols_before = df.shape[1]

        # ── Ghost-string sentinel cleanup ─────────────────────────────────
        # dtype=str ingestion turns blanks into literal "nan"/"NaN"/"None"/"null"
        # which defeat .isna() downstream.  Replace ALL of them with real NaN.
        _GHOST_NA_RE = re.compile(
            r"^\s*(nan|none|null|n/a|na|n/a|-|\.+)\s*$", re.IGNORECASE
        )
        text_cols = df.select_dtypes(include=["object", "string"]).columns
        ghost_fixed = 0
        for col in text_cols:
            # Also catch truly empty / whitespace-only strings
            empty_mask = df[col].astype(str).str.strip().eq("") | df[col].isna()
            ghost_mask = df[col].astype(str).apply(
                lambda x: bool(_GHOST_NA_RE.match(x)) if pd.notna(x) else False
            )
            combined = empty_mask | ghost_mask
            if combined.any():
                ghost_fixed += int(combined.sum())
                df.loc[combined, col] = pd.NA
        if ghost_fixed:
            self._audit["per_column"]["ghost_na_cleaned"] = {"count": ghost_fixed}
            self._audit["warnings"].append(
                f"Ghost-string sentinels (nan/None/null/N/A/-) cleaned: "
                f"{ghost_fixed} cell(s) replaced with real NaN."
            )

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

        # ── Step 2.5: Business-rule value replacement ──────────────────────
        # Convert specified sentinel values to NaN BEFORE imputation.
        # This lets business_rules handle logical errors (e.g. height=0)
        # before statistical methods like IQR run.
        brules = business_rules or {}
        replace_count = 0
        replace_log: dict[str, dict[str, Any]] = {}
        for col, rule in brules.items():
            vals_to_replace = rule.get("replace_values")
            if vals_to_replace is None or col not in df.columns:
                continue
            try:
                numeric_col = pd.to_numeric(df[col], errors="coerce")
                mask = numeric_col.isin(vals_to_replace)
            except Exception:
                continue
            replaced = int(mask.sum())
            if replaced > 0:
                df.loc[mask, col] = pd.NA
                replace_count += replaced
                replace_log[col] = {
                    "replaced_values": vals_to_replace,
                    "count": replaced,
                    "reason": rule.get("missing_means", "business_rule"),
                }
        if replace_log:
            self._audit["per_column"]["value_replacements"] = replace_log
            self._audit["warnings"].append(
                f"Business rules replaced {replace_count} sentinel value(s) "
                f"with NaN across {len(replace_log)} column(s)."
            )

        # ── Step 3: Impute remaining missing values ────────────────────────
        missing_fixed = 0
        business_na_fixed = 0
        imputation_log: dict[str, dict[str, Any]] = {}

        for col in df.columns:
            null_count = int(df[col].isna().sum())
            if null_count == 0:
                continue

            # ── Business rules take priority ──────────────────────────────
            if col in brules:
                rule = brules[col]
                fill_val = rule.get("fill", self.UNKNOWN_TEXT)
                meaning = rule.get("missing_means", "unknown")
                # Support special fill values: "median", "mean", "mode"
                # Try numeric conversion for string columns too (auto-detected numerics)
                numeric_for_fill = pd.to_numeric(df[col], errors="coerce")
                has_numeric = numeric_for_fill.notna().sum() > 0
                if fill_val in ("median", "mean") and has_numeric:
                    if fill_val == "median":
                        computed = numeric_for_fill.median()
                    else:
                        computed = numeric_for_fill.mean()
                    fill_val = computed if not pd.isna(computed) else 0
                elif fill_val == "mode":
                    mode_vals = df[col].mode()
                    fill_val = mode_vals.iloc[0] if len(mode_vals) > 0 else self.UNKNOWN_TEXT
                df[col] = df[col].fillna(fill_val)
                imputation_log[col] = {
                    "strategy": "business_rule",
                    "type": "business_na",
                    "semantic": meaning,
                    "fill_value": _safe_py(fill_val),
                    "count": null_count,
                }
                business_na_fixed += null_count
                continue

            if pd.api.types.is_numeric_dtype(df[col]):
                # Use median — robust to outliers that will be capped later
                median_val = df[col].median()
                if pd.isna(median_val):
                    median_val = 0
                # Integer columns (including nullable Int64) need an integer
                # fill value, otherwise fillna(72.5) raises TypeError.
                if pd.api.types.is_integer_dtype(df[col]):
                    median_val = round(float(median_val))
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
        self._audit["business_na_fixed"] = business_na_fixed
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
        self,
        df: pd.DataFrame,
        iqr_k: float | None = None,
        method: str = "iqr",
        threshold: float = 0.995,
        zscore_threshold: float = 3.0,
        outlier_rules: dict[str, dict[str, Any]] | None = None,
    ) -> pd.DataFrame:
        """Suppress outliers using a configurable strategy.

        Supported methods:
            - ``"iqr"``: Clip values beyond ``[Q1 − k×IQR, Q3 + k×IQR]``.
              Use *iqr_k* to control aggressiveness (default 1.5).
            - ``"percentile"``: Clip values below/above *threshold* quantile.
              Best for long-tail distributions (e-commerce, finance).
              Default threshold=0.995 clips top/bottom 0.5%.
            - ``"zscore"``: Clip values with |z| > *zscore_threshold*.
            - ``"none"``: Skip outlier suppression entirely.

        Returns:
            DataFrame with outliers clamped.
        """
        if method == "none" or method not in ("iqr", "percentile", "zscore"):
            if method not in ("iqr", "percentile", "zscore", "none"):
                self._audit["warnings"].append(
                    f"Unknown outlier_method '{method}', skipping suppression."
                )
            return df

        df = df.copy()
        total_suppressed = 0
        outlier_log: dict[str, dict[str, Any]] = {}

        # Auto-detect numeric columns: first check actual numeric dtype,
        # then try to detect string columns that contain numeric data
        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        auto_detected_cols: list[str] = []
        
        if not numeric_cols:
            # No explicit numeric columns - try to detect from string columns
            for col in df.columns:
                if df[col].dtype in ("object", "string"):
                    # Try to convert to numeric to see if it's numeric data
                    test_convert = pd.to_numeric(df[col], errors="coerce")
                    # If >50% of non-null values convert successfully, treat as numeric
                    non_null_count = df[col].notna().sum()
                    if non_null_count > 0:
                        success_rate = test_convert.notna().sum() / non_null_count
                        if success_rate > 0.5:
                            auto_detected_cols.append(col)
            
            if auto_detected_cols:
                self._audit["warnings"].append(
                    f"Auto-detected {len(auto_detected_cols)} numeric columns "
                    f"from string data: {auto_detected_cols}"
                )
                numeric_cols = auto_detected_cols

        for col in numeric_cols:
            # Per-column method resolution: outlier_rules overrides global
            col_method = method
            col_threshold = threshold
            col_zscore_threshold = zscore_threshold
            col_iqr_k = iqr_k
            if outlier_rules and col in outlier_rules:
                col_rule = outlier_rules[col]
                col_method = col_rule.get("method", method)
                col_threshold = col_rule.get("threshold", threshold)
                col_zscore_threshold = col_rule.get("zscore_threshold", zscore_threshold)
                col_iqr_k = col_rule.get("iqr_k", iqr_k)
                self._audit["outlier_rules_applied"][col] = {
                    "method": col_method,
                    "params": {k: v for k, v in col_rule.items()},
                }

            # Handle auto-detected numeric columns (stored as strings)
            if col in auto_detected_cols:
                # Convert to numeric for outlier detection
                numeric_series = pd.to_numeric(df[col], errors="coerce")
                ser = numeric_series.dropna()
            else:
                ser = df[col].dropna()
            
            if len(ser) < 4:
                continue

            col_min = float(ser.min())
            col_max = float(ser.max())
            is_integer_col = pd.api.types.is_integer_dtype(df[col])

            # ── IQR method ────────────────────────────────────────────────
            if col_method == "iqr":
                k = col_iqr_k if col_iqr_k is not None else self.IQR_K
                q1 = float(ser.quantile(0.25))
                q3 = float(ser.quantile(0.75))
                iqr_val = q3 - q1
                if iqr_val == 0:
                    continue
                lo = q1 - k * iqr_val
                hi = q3 + k * iqr_val
                method_label = f"IQR(k={k})"
                meta = {"k": k, "q1": round(q1, 4), "q3": round(q3, 4)}

            # ── Percentile method ─────────────────────────────────────────
            elif col_method == "percentile":
                lo_thresh = 1.0 - col_threshold
                lo = float(ser.quantile(lo_thresh))
                hi = float(ser.quantile(col_threshold))
                if lo >= hi:
                    continue
                method_label = f"percentile(thresh={col_threshold})"
                meta = {"threshold": col_threshold,
                        "lo_quantile": round(lo_thresh, 4),
                        "hi_quantile": round(col_threshold, 4)}

            # ── Z-score method ────────────────────────────────────────────
            else:  # zscore
                mean_val = float(ser.mean())
                std_val = float(ser.std())
                if std_val == 0:
                    continue
                lo = mean_val - col_zscore_threshold * std_val
                hi = mean_val + col_zscore_threshold * std_val
                method_label = f"zscore(threshold={col_zscore_threshold})"
                meta = {"mean": round(mean_val, 4),
                        "std": round(std_val, 4)}

            # Count outliers - handle both numeric and string columns
            if col in auto_detected_cols:
                # For auto-detected numeric columns, convert to numeric for comparison
                numeric_vals = pd.to_numeric(df[col], errors="coerce")
                below = int((numeric_vals < lo).sum())
                above = int((numeric_vals > hi).sum())
            else:
                below = int((df[col] < lo).sum())
                above = int((df[col] > hi).sum())

            if below + above == 0:
                continue

            # Integer columns need integer fence bounds
            lo_clip_val = int(np.floor(lo)) if is_integer_col else lo
            hi_clip_val = int(np.ceil(hi)) if is_integer_col else hi

            # Track original range for impact reporting
            clipped_vals = df.loc[pd.to_numeric(df[col], errors="coerce") > hi, col].dropna()
            impact_note = None
            if above > 0 and len(clipped_vals) > 0:
                top5 = clipped_vals.sort_values(ascending=False).head(3).tolist()
                impact_note = (
                    f"{above} values > {round(hi, 1)} clamped; "
                    f"original max={round(col_max, 1)}, "
                    f"top clamped values={top5}"
                )

            # Apply clipping - handle both numeric and string columns
            if col in auto_detected_cols:
                numeric_vals = pd.to_numeric(df[col], errors="coerce")
                clipped_numeric = numeric_vals.clip(lo_clip_val, hi_clip_val)
                clipped_numeric = clipped_numeric.round(2)
                df[col] = clipped_numeric.astype(str).replace("nan", "")
            else:
                clipped = df[col].clip(lo_clip_val, hi_clip_val)
                if pd.api.types.is_float_dtype(clipped):
                    clipped = clipped.round(2)
                df[col] = clipped
            log_entry: dict[str, Any] = {
                "method": method_label,
                "lower_fence": round(lo, 4),
                "upper_fence": round(hi, 4),
                "clamped_low": below,
                "clamped_high": above,
                "original_range": [round(col_min, 4), round(col_max, 4)],
                **meta,
            }
            if impact_note:
                log_entry["impact"] = impact_note
            outlier_log[col] = log_entry
            total_suppressed += below + above

        self._audit["outliers_suppressed"] = total_suppressed
        self._audit["outlier_method"] = method
        if outlier_log:
            self._audit["per_column"]["outlier_winsorizing"] = outlier_log

        if total_suppressed:
            self._audit["warnings"].append(
                f"{total_suppressed} outlier value(s) clamped "
                f"({method_label if total_suppressed else method})"
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

    def _auto_expand_nested(
        self, df: pd.DataFrame, expand_nested: bool | list[str]
    ) -> dict[str, pd.DataFrame]:
        """Auto-detect or use explicit list of nested columns to expand.

        Returns a dict of ``{column_name: expanded_DataFrame}``.
        """
        result: dict[str, pd.DataFrame] = {}
        targets: list[str] = []

        if isinstance(expand_nested, list):
            targets = [c for c in expand_nested if c in df.columns]
        elif expand_nested is True:
            # Auto-detect: any column where >20% of values look like
            # serialised lists or are already Python list/dict objects
            for col in df.columns:
                ser = df[col].dropna()
                if len(ser) == 0:
                    continue
                # Sample up to 50 non-null values for detection
                sample = ser.head(50)
                list_like = 0
                for val in sample:
                    # Already-parsed Python list/dict objects
                    if isinstance(val, (list, dict)):
                        list_like += 1
                        if list_like >= max(3, len(sample) * 0.2):
                            targets.append(col)
                            break
                    # String-form JSON array or Python list literal
                    s = str(val).strip()
                    if s.startswith("[") and s.endswith("]"):
                        list_like += 1
                        if list_like >= max(3, len(sample) * 0.2):
                            targets.append(col)
                            break
                if col not in targets and list_like > 0:
                    # Edge case: try parsing a few
                    try:
                        import ast
                        parsed = ast.literal_eval(s)
                        if isinstance(parsed, list):
                            targets.append(col)
                    except (ValueError, SyntaxError):
                        pass

        if not targets:
            return result

        for col in targets:
            try:
                expanded = self.expand_nested(df, col)
                if len(expanded) > 0:
                    result[col] = expanded
                    self._audit["warnings"].append(
                        f"Auto-expanded nested column '{col}': "
                        f"{len(df)} parent rows → {len(expanded)} child rows"
                    )
            except Exception as exc:
                self._audit["warnings"].append(
                    f"Failed to expand nested column '{col}': {exc}"
                )

        return result

    # ========================================================================
    # Utility — Extract SQLite Schema
    # ========================================================================

    @staticmethod
    def extract_sqlite_schema(db_path: str | Path) -> dict[str, Any]:
        """Extract table schemas and foreign-key relationships from a SQLite db.

        Returns a dict suitable for serialisation to ``schema.json``, so
        downstream JOIN analysis can reconstruct relationships.

        Args:
            db_path: Path to a ``.db`` / ``.sqlite`` / ``.sqlite3`` file.

        Returns:
            Dict with ``tables`` (list of column definitions) and
            ``foreign_keys`` (list of FK relationships).

        Example:
            >>> schema = cleaner.extract_sqlite_schema("library.db")
            >>> schema["foreign_keys"]
            [{"from": "borrows.book_id", "to": "books.book_id"}, ...]
        """
        import sqlite3
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        tables_q = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables = []
        for (tbl,) in tables_q:
            cols = cur.execute(f"PRAGMA table_info('{tbl}')").fetchall()
            columns = [
                {"name": c[1], "type": c[2], "nullable": not c[3],
                 "default": c[4], "pk": bool(c[5])}
                for c in cols
            ]
            tables.append({"name": tbl, "columns": columns})
        foreign_keys = []
        for (tbl,) in tables_q:
            fks = cur.execute(f"PRAGMA foreign_key_list('{tbl}')").fetchall()
            for fk in fks:
                foreign_keys.append({
                    "from": f"{tbl}.{fk[3]}",
                    "to": f"{fk[2]}.{fk[4]}",
                    "on_delete": fk[5],
                    "on_update": fk[6],
                })
        con.close()
        return {"tables": tables, "foreign_keys": foreign_keys}


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
