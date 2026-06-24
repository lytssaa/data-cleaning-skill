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

### Per-Sheet / Per-Table Rules (multi-sheet Excel / multi-table DB)

For workbooks with heterogeneous sheets, specify rules per sheet name:

```python
df, audit = cleaner.execute(
    "gym.xlsx",
    schema_rules={
        "会员信息": {"会员类型": "str", "入会日期": "datetime"},
        "健身记录": {"体重kg": "float", "时长分钟": "int"},
        "体测记录": {"BMI": "float", "体脂率": "float"},
    },
)
# Sheet 会员信息 → 会员类型 coerced to str
# Sheet 健身记录 → 体重kg coerced to float
# Sheet 体测记录 → BMI coerced to float
# No cross-sheet noise — unmatched columns silently skipped.
```

A flat `dict[str, str]` (the default) is applied to all sheets. In multi-sheet mode with shared rules, a single summary warning is emitted instead of per-column noise.

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

### Common patterns

```python
business_rules = {
    # NULL 会员类型 → "未指定" (not "Unknown" — has business meaning)
    "会员类型": {"missing_means": "not_specified", "fill": "未指定"},

    # NULL 退货日期 → "未退货" (recorded but never returned)
    "return_date": {"missing_means": "not_returned", "fill": "未还"},

    # NULL 实际完成(万元) → keep as NA (future quarter, not yet realized)
    "实际完成(万元)": {"missing_means": "not_yet", "fill": None},

    # NULL 薪资下限 → 0 (entry-level / unreported)
    "salary_lower": {"missing_means": "unreported", "fill": 0},

    # NULL 备注 → "" (empty notes, not missing data)
    "备注": {"missing_means": "no_notes", "fill": ""},
}
```

### Full example

```python
df, audit = cleaner.execute(
    "members.csv",
    schema_rules={"age": "int", "income": "float"},
    business_rules={
        "会员类型": {"missing_means": "not_specified", "fill": "未指定"},
        "return_date": {"missing_means": "not_returned", "fill": "未还"},
    },
)
# audit["business_na_fixed"] = 161   ← business-rule imputations
# audit["missing_values_fixed"] = 12 ← data-quality imputations (median/Unknown)
```

### Default behavior (no business_rules)

Columns without business rules are imputed automatically:
- **Numeric** → median value
- **Datetime** → forward-fill (last valid value)
- **Text/Category** → `"Unknown"`

> **Tip**: If `"Unknown"` doesn't match your domain semantics (e.g., Chinese datasets
> where `"未指定"` / `"未填写"` is more natural), use `business_rules` for those columns.
> There is no global default text override — this is by design, to avoid ambiguity.

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

Use ``run_on_directory()`` for batch cleaning:

```python
from scripts.clean import DataPipelineCleaner

cleaner = DataPipelineCleaner()
summary = cleaner.run_on_directory(
    input_dir="./raw_data",
    output_dir="./cleaned_data",
    schema_rules={"金额": "float", "积分": "int"},
    outlier_method="iqr",
)
# summary["succeeded"]  → int
# summary["total_original_rows"] → int
# summary["overall_retention_pct"] → float
# summary["files"] → list of per-file dicts with row counts, artifacts
```

Each source file gets its own subdirectory under *output_dir*:

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

## Saving the Audit (JSON-safe)

The audit dict returned by `execute()` is **fully JSON-serializable** — no DataFrames
are embedded. Use `json.dump` directly:

```python
import json

df, audit = cleaner.execute("data.csv", schema_rules=...)

with open("audit.json", "w", encoding="utf-8") as f:
    json.dump(audit, f, ensure_ascii=False, indent=2)
```

Extra sheet/nested DataFrames are accessed via **cleaner properties**, not the audit dict:

```python
# After execute() on a multi-sheet Excel:
for sheet_name, sdf in cleaner.sheet_dfs.items():
    sdf.to_csv(f"{sheet_name}.csv", index=False)

# After execute(expand_nested=True):
events_df = cleaner.nested_dfs["events"]
```

The audit dict contains JSON-safe metadata summaries instead:
- `_sheet_dfs_summary`: `{"Sheet1": {"rows": 100, "cols": 5, "columns": [...]}}`
- `_nested_dfs_summary`: `{"events": {"rows": 2247, "cols": 4, "columns": [...]}}`

## Custom Business Validation (Post-Pipeline)

The pipeline handles data quality (types, nulls, outliers).  **Business logic**
validation happens after `execute()`:

```python
df, audit = cleaner.execute("jobs.csv", schema_rules={
    "salary_lower": "float", "salary_upper": "float",
})

# Business rule: salary_lower must be <= salary_upper
bad = df["salary_lower"] > df["salary_upper"]
if bad.any():
    print(f"⚠ {bad.sum()} rows have salary_lower > salary_upper")
    audit["business_validation"] = {
        "salary_range_invalid": int(bad.sum()),
        "sample_indices": df.index[bad].tolist()[:5],
    }
```

Common business validations to add post-pipeline:
- `salary_lower <= salary_upper` — salary range logic
- `start_date <= end_date` — date range logic
- `quantity * unit_price ≈ total_price` — computed field checks
- Domain-specific value constraints (e.g., `0 <= age <= 150`)

## Profile Script

`scripts/profile.py` supports all 13 formats that `clean.py` supports:

| Format | Extension |
| ------ | --------- |
| CSV / TSV | `.csv` `.tsv` |
| Excel | `.xlsx` `.xls` |
| JSON | `.json` |
| Parquet | `.parquet` |
| Feather | `.feather` |
| HTML | `.html` `.htm` |
| XML | `.xml` |
| YAML | `.yaml` `.yml` |
| SQLite | `.db` `.sqlite` `.sqlite3` |

```bash
# CLI usage
python scripts/profile.py sales.parquet --top 10
python scripts/profile.py delivery.db --json
```
