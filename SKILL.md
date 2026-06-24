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

Supported input formats: `.csv`, `.tsv`, `.xlsx`, `.xls`, `.json`, `.parquet`, `.feather`, `.html`, `.htm`, `.xml`, `.yaml`, `.yml`, `.db`, `.sqlite`, `.sqlite3`.

## Architecture

```
Raw File
   │
   ▼
┌──────────────────────────────────────────────────┐
│ Phase 0 — _safe_ingest(file_path)               │
│   All columns read as dtype=str.  Zero type      │
│   inference.  Protects IDs, codes, long numbers. │
│   Supports 13 formats inc. Parquet, SQLite, YAML.│
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
│   Int columns use nullable Int64 for NaN safety. │
└──────────────────────┬───────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────┐
│ Phase 3 — _missing_value_trial(df)              │
│   Column >70% null → drop column.                │
│   Row    >50% null → drop row.                   │
│   Numeric → median; categorical → "Unknown".     │
│   business_rules override default strategy per   │
│   column (e.g. "return_date NA = not_returned"). │
└──────────────────────┬───────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────┐
│ Phase 4 — _outlier_suppression(df)              │
│   Multi-strategy: IQR / percentile / zscore.     │
│   Clips to fence bounds — never deletes a row.   │
│   Percentile best for long-tail (e-commerce).     │
└──────────────────────┬───────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────┐
│ Phase 4.5 — _auto_expand_nested(df) (optional)   │
│   Auto-detects & flattens nested JSON/list cols. │
│   Returns child DataFrames for 1:N analysis.     │
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

## Outlier Strategies

Control via `outlier_method` parameter:

| Method         | When to use                                | Param              |
| -------------- | ------------------------------------------ | ------------------ |
| `"percentile"` | Long-tail data (e-commerce, finance)       | `outlier_threshold` (default 0.995) |
| `"iqr"`        | Normal/symmetric distributions             | `iqr_k` (default 1.5) |
| `"zscore"`     | Known normal distribution                  | `zscore_threshold` (default 3.0) |
| `"none"`       | Skip outlier handling entirely             | —                  |

```python
# Long-tail e-commerce data: only clip top/bottom 0.5%
df, audit = cleaner.execute(
    "orders.csv",
    schema_rules={"price": "float"},
    outlier_method="percentile",
    outlier_threshold=0.995,
)

# Strict IQR for survey data
df, audit = cleaner.execute(
    "survey.csv",
    schema_rules={"age": "int"},
    outlier_method="iqr",
    iqr_k=1.5,
)
```

## Business Rules for Missing Values

Use `business_rules` to distinguish semantic NAs from data-quality NAs.
Semantic NAs are tagged `"type": "business_na"` in the audit.

```python
# return_date NA = "not yet returned" (business meaning)
# 实际完成 NA = "not yet occurred" (future quarter)
df, audit = cleaner.execute(
    "library.db",
    schema_rules={"return_date": "datetime", "实际完成(万元)": "float"},
    engine_kwargs={"table": "borrows"},
    business_rules={
        "return_date": {"missing_means": "not_returned", "fill": "未还"},
        "实际完成(万元)": {"missing_means": "not_yet", "fill": None},
    },
)
# audit["business_na_fixed"] = 161  (separate from data-quality missing)
```

## Nested JSON / List Expansion

When a column contains serialised JSON arrays or Python lists, use
`expand_nested=True` to auto-detect and flatten them into child DataFrames.
The expanded tables are accessible via `audit["_nested_dfs"]`.

```python
df, audit = cleaner.execute(
    "user_logs.json",
    schema_rules={"session_start": "datetime"},
    expand_nested=True,  # auto-detect & explode
)
# audit["nested_tables"] = {"events": 2247}  ← 500 sessions → 2247 events
# audit["_nested_dfs"]["events"] → child DataFrame
```

Or expand a specific column manually after cleaning:

```python
events_df = cleaner.expand_nested(df, "events", id_column="session_id")
```

## Row Count Validation

For truncated file formats (YAML/JSON exports), set `expected_min_rows`:

```python
df, audit = cleaner.execute(
    "grades.yaml",
    schema_rules={"score": "int"},
    expected_min_rows=100,  # warn if fewer than 100 rows
)
# Warning: "Row count (50) below expected minimum (100). Data may be truncated."
```

## SQLite Database Support

Pass `.db` / `.sqlite` / `.sqlite3` files.  For multi-table databases,
specify the table via `engine_kwargs`:

```python
df, audit = cleaner.execute(
    "library.db",
    schema_rules={"price": "float", "stock": "int"},
    engine_kwargs={"table": "books"},
)

# Extract full schema with foreign keys for downstream JOIN analysis
schema = cleaner.extract_sqlite_schema("library.db")
# schema["foreign_keys"] = [{"from": "borrows.book_id", "to": "books.book_id"}]
```

## Batch Output Structure

For multi-file batch cleaning, organize output by source:

```
cleaned_data/
├── orders/                    # Single CSV
│   ├── orders.csv
│   └── orders_audit.json
├── sales_report/              # Multi-sheet Excel
│   ├── 销售明细.csv
│   ├── 产品利润表.csv
│   ├── 季度KPI.csv
│   └── sales_audit.json
├── user_logs/                 # Nested JSON expanded
│   ├── user_logs.csv
│   ├── user_logs_events.csv
│   └── user_logs_audit.json
├── library/                   # Multi-table SQLite
│   ├── books.csv
│   ├── authors.csv
│   ├── borrows.csv
│   ├── members.csv
│   ├── schema.json
│   └── library_audit.json
└── _summary.json              # Overall summary
```

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
