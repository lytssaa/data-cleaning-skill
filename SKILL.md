# Data Cleaning — Industrial-Grade Pipeline

A production-level data cleaning skill built around the `DataPipelineCleaner` class.
Five-phase unidirectional pipeline: **Safe Ingest → Standardise → Align Types → Trial Missing → Suppress Outliers**.

## Platform Support

| Platform | Status | Install |
| -------- | ------ | ------- |
| **WorkBuddy** | ✅ native | `workbuddy skill install <repo-url>` |
| **AtomCode** | ✅ | `git clone <repo-url> ~/.atomcode/skills/data-cleaning` |
| **MiMo Code** | ✅ | `git clone <repo-url> ~/.config/mimocode/skills/data-cleaning` |
| **Claude** | ✅ MCP | Config: `"command": "python", "args": ["adapters/claude/server.py"]` |

Single codebase, four platforms — core pipeline class in `scripts/clean.py`, platform adapters in `adapters/`.

## When to use this skill

Use this skill whenever the user provides a tabular file and asks to:
- Clean / 清洗 / 整理 / 处理 data
- Remove duplicates / 去重 / 重复值
- Handle missing values / 缺失值 / 空值 / NaN
- Standardize column names, types, or formats / 标准化
- Detect and treat outliers / 异常值处理
- Normalize text (whitespace, casing, full-width, ghost characters)
- Produce a data quality audit report

Supported input formats: `.csv`, `.tsv`, `.xlsx`, `.xls`, `.json` (array of objects).

## Architecture

```
Raw File
   │
   ▼
┌──────────────────────────────────────────────────┐
│ Phase 0 — _safe_ingest(file_path)               │
│   All columns read as dtype=str.  Zero type      │
│   inference.  Protects IDs, codes, long numbers. │
└──────────────────────┬───────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────┐
│ Phase 1 — _standardize_columns_and_text(df)     │
│   Column names → snake_case (via NFKC).          │
│   Text cells → NFKC (full→halfwidth), ghost      │
│   chars stripped (\u200b \ufeff etc.).           │
└──────────────────────┬───────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────┐
│ Phase 2 — _type_alignment(df, schema_rules)     │
│   Coerce columns per user mapping.  errors=      │
│   'coerce': dirty strings → NaN, not exceptions. │
└──────────────────────┬───────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────┐
│ Phase 3 — _missing_value_trial(df)              │
│   Column >70% null → drop column.                │
│   Row    >50% null → drop row.                   │
│   Numeric → median; categorical → "Unknown".     │
└──────────────────────┬───────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────┐
│ Phase 4 — _outlier_suppression(df)              │
│   IQR (1.5×) Winsorizing.  Clips to fence        │
│   bounds — never deletes a row.                  │
└──────────────────────┬───────────────────────────┘
                       ▼
              (cleaned_df, audit_report)
```

## Quick Start

### From a file on disk

```python
from scripts.clean import DataPipelineCleaner

cleaner = DataPipelineCleaner()
cleaned_df, audit = cleaner.execute(
    file_path="dirty_survey.csv",
    schema_rules={"age": "int", "income": "float", "signup_date": "datetime"},
)
print(audit["retention_rate_pct"], "% rows kept")
```

### Directly from a DataFrame (skip Phase 0)

```python
cleaner = DataPipelineCleaner()
# Manually inject a raw string DataFrame, then chain phases yourself:
raw_df = pd.read_csv("file.csv", dtype=str)
raw_df = cleaner._standardize_columns_and_text(raw_df)
raw_df = cleaner._type_alignment(raw_df, {"age": "int"})
raw_df = cleaner._missing_value_trial(raw_df)
raw_df = cleaner._outlier_suppression(raw_df)
# Access audit state: cleaner._audit
```

## Schema Rules

Passed as `{column_name: target_type}`.  Supported types:

| Type       | Effect                                                    |
| ---------- | --------------------------------------------------------- |
| `"int"`    | `pd.to_numeric(…, errors="coerce").astype("Int64")`       |
| `"float"`  | `pd.to_numeric(…, errors="coerce")`                       |
| `"str"`    | `astype("string")`                                        |
| `"datetime"` | `pd.to_datetime(…, errors="coerce")`                     |

Columns not listed in `schema_rules` stay as strings.

## Audit Report

The `execute()` method returns `(cleaned_df, audit_dict)` where `audit_dict` contains:

```json
{
  "original_rows": 10000,
  "cleaned_rows": 9850,
  "retention_rate_pct": 98.5,
  "dropped_columns": ["useless_survey_field"],
  "dropped_rows_count": 150,
  "missing_values_fixed": 423,
  "outliers_suppressed": 37,
  "per_column": {
    "coercion":  { "age": {"target_type": "int", "invalid_values_coerced_to_null": 12} },
    "column_drops": { "useless_survey_field": {"reason": "missing_rate > 70%", "missing_rate": 85.3} },
    "imputation": { "city": {"strategy": "constant", "fill_value": "Unknown", "count": 35} },
    "outlier_winsorizing": { "income": {"method": "IQR", "lower_fence": 1500, "upper_fence": 85000} }
  },
  "stage_timings": { "safe_ingest": 0.12, "standardize": 0.35, "type_alignment": 0.08,
                     "missing_trial": 0.05, "outlier_suppression": 0.02 },
  "warnings": ["schema_rules references unknown column 'bonus' — skipped."]
}
```

## Design Philosophy

1. **Never silently drop data.**  Every deletion is logged in the audit.
2. **Never trust type inference.**  Ingest as `str`, coerce explicitly with `errors='coerce'`.
3. **Never delete outliers.**  Winsorize (clip) — the row stays, the value is capped.
4. **Full traceability.**  The audit tells you exactly what happened to every column.

## Bundled Scripts

| Script | Purpose |
| ------ | ------- |
| `scripts/clean.py` | Core `DataPipelineCleaner` class — the pipeline engine |
| `scripts/profile.py` | Quick data profiling (shape, dtypes, nulls, samples) |
| `scripts/quality_report.py` | Renders audit JSON into a Markdown quality report |

## Important Rules

1. **Always run `execute()`** — it's the single entry point that guarantees pipeline order.
2. **Never overwrite the input file.**  Output goes to a new path.
3. **Type coercion is best-effort.**  Uncastable values become `NaN` and are handled by the missing-value phase.
4. **Report in the same language the user used.**  Chinese user gets Chinese audit descriptions.
