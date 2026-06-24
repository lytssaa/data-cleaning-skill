# Data Cleaning — Industrial-Grade Pipeline

A production-level data cleaning skill. Five-phase unidirectional pipeline with full audit trail.

## Pre-decision Checklist (MANDATORY — Before Every execute() Call)

**You MUST answer ALL of these before generating parameters. If you skip this, you WILL produce bad results.**

### 1. Did you profile the data first?
- [ ] Did you run `profile()` or `_safe_ingest()` to see actual column values?
- [ ] Did you look at min/max/mean/median for every numeric column?
- [ ] Did you check for logical impossibilities (height=0, age=-5, salary=99999)?

### 2. What is the outlier strategy per column?
**FORBIDDEN: Do NOT blindly set `outlier_method="none"` for all columns.**

For EACH numeric column, decide based on data domain:

| Column type | Distribution | Required method | Reason |
|-------------|-------------|----------------|--------|
| Height, weight, age, BMI | Normal/symmetric | `"iqr"` | Physical bounds are real |
| Test scores, response time | Normal/symmetric | `"iqr"` | Physical bounds are real |
| Salary, revenue, price | Long-tail / power law | `"none"` or `"percentile"` | High values are legitimate |
| Counts (orders, clicks) | Right-skewed | `"percentile"` or `"none"` | Zero-inflated |
| ID codes, zip codes | N/A | `"none"` | Not continuous data |

**If in doubt, use `"iqr"` — clipping is safer than ignoring.**

### 3. Did you set business_rules for logical errors?
- [ ] height ≤ 0 → replace_values=[0, -1]
- [ ] age < 5 or age > 120 → replace_values with bounds
- [ ] negative prices/quantities → replace_values
- [ ] sentinel values (999, -999, 0 for non-zero fields) → replace_values

### 4. Did you verify after execution?
- [ ] Check `audit["outliers_suppressed"]` — if 0 for numeric data, something is wrong
- [ ] Check `audit["retention_rate_pct"]` — if < 90%, review what was dropped
- [ ] Spot-check 3-5 rows to confirm values look reasonable

---

## AI Agent Workflow (CRITICAL — Follow This Order)

When a user asks to clean data, follow this exact sequence. **Do NOT skip steps.**

### Step 1: Profile the data first

```python
# ALWAYS profile before cleaning — understand the data before touching it
df_raw = cleaner._safe_ingest(Path(file_path), {})
print(f"Rows: {len(df_raw)}, Columns: {len(df_raw.columns)}")
print(f"Column names: {df_raw.columns.tolist()}")
print(f"Dtypes:\n{df_raw.dtypes}")
print(f"\nSample values per column:")
for col in df_raw.columns:
    vals = df_raw[col].dropna().unique()[:5]
    print(f"  {col}: {vals}")
```

### Step 2: Decide rules based on what you see

Based on the profile, **you (the AI) must decide**:

1. **schema_rules**: Which columns need type conversion? (Look for: numeric-looking strings, date strings, ID columns that should stay as string)
2. **business_rules**: Which columns have logical impossible values? (Look for: height=0, age=300, negative prices)
3. **outlier_method**: Is the data normally distributed or long-tail? (Height/weight → iqr; salary/revenue → none or percentile)
4. **iqr_k**: How aggressive? (1.5 = standard, 2.0 = conservative, 3.0 = very conservative)

### Step 3: Execute with your decided parameters

```python
cleaned_df, audit = cleaner.execute(
    file_path=file_path,
    schema_rules=your_decided_schema_rules,      # From Step 2
    business_rules=your_decided_business_rules,   # From Step 2
    outlier_method=your_chosen_method,            # From Step 2
    iqr_k=your_chosen_k,                         # From Step 2
)
```

### Step 4: Verify the result

```python
# Check audit for anomalies
if audit["retention_rate_pct"] < 90:
    print(f"WARNING: {audit['retention_rate_pct']}% retention — review carefully")
if audit["outliers_suppressed"] > len(cleaned_df) * 0.05:
    print(f"WARNING: {audit['outliers_suppressed']} outliers suppressed — check if too aggressive")
```

## Supported Formats (15 extensions / 11 formats)

| Format | Extensions |
|--------|-----------|
| CSV/TSV | `.csv` `.tsv` |
| Excel | `.xlsx` `.xls` |
| JSON | `.json` |
| Parquet | `.parquet` |
| Feather | `.feather` |
| HTML | `.html` `.htm` |
| XML | `.xml` |
| YAML | `.yaml` `.yml` |
| SQLite | `.db` `.sqlite` `.sqlite3` |
| Pickle | `.pkl` `.pickle` |

## Pipeline Architecture

```
Raw File
   │
   ▼
Phase 0 — _safe_ingest()          CSV/Excel: dtype=str (zero inference)
                                   Parquet/Feather: native types (preserved)
   │
   ▼
Phase 1 — _standardize()          Column names → snake_case (NFKC)
                                   Ghost chars (\u200b \ufeff) stripped
   │
   ▼
Phase 2 — _type_alignment()       schema_rules applied: str → int/float/datetime
                                   errors='coerce': bad values → NaN
   │
   ▼
Phase 2.5 — _missing_value_trial() Ghost strings ("nan","None","null","N/A","-")
                                    → real NaN (case-insensitive)
                                   business_rules replace_values: sentinel → NaN
                                   Then: drop cols >70% null, drop rows >50% null
                                   Fill: median (numeric), "Unknown" (text),
                                   or business_rules override (fill="median"/"mean"/"mode")
   │
   ▼
Phase 4 — _outlier_suppression()  Auto-detects numeric columns from string data
                                   IQR / percentile / zscore / none
                                   Clips to fence bounds — never deletes rows
                                   rounding to 2 decimal places
   │
   ▼
           (cleaned_df, audit_report)
```

## Schema Rules

```python
schema_rules = {
    "age": "int",           # → Int64 (nullable)
    "salary": "float",      # → float64
    "name": "str",          # → string
    "join_date": "datetime" # → datetime64
}
```

Columns NOT listed stay as-is. For multi-sheet Excel, use per-sheet rules:
```python
schema_rules = {
    "Sheet1": {"age": "int"},
    "Sheet2": {"price": "float"},
}
```

## Business Rules

### Value replacement (sentinel → NaN → fill)

```python
business_rules = {
    "height_cm": {
        "replace_values": [0, -1, 999],   # These values become NaN
        "fill": "median",                   # Then filled with median
        "missing_means": "impossible value"
    },
    "weight_kg": {
        "replace_values": [0],
        "fill": "mean",                     # Fill with mean
        "missing_means": "invalid entry"
    },
    "status": {
        "fill": "Unknown",                  # Simple fill
        "missing_means": "not recorded"
    }
}
```

### Fill keywords

| Keyword | Effect |
|---------|--------|
| `"median"` | Fill with column median (auto-converts string columns to numeric) |
| `"mean"` | Fill with column mean |
| `"mode"` | Fill with column mode (most frequent value) |
| Any value | Fill with that literal value |
| `None` | Keep as NaN |

### Ghost string detection

The pipeline automatically converts these string literals to real NaN (case-insensitive):
`"nan"`, `"None"`, `"null"`, `"N/A"`, `"-"`, `""`, whitespace-only strings

## Outlier Methods

| Method | When to use | Parameter |
|--------|-------------|-----------|
| `"none"` | Long-tail data (salary, revenue), or when you want raw data | — |
| `"iqr"` | Normal/symmetric distributions (height, weight, age) | `iqr_k` (default 1.5) |
| `"percentile"` | Moderate long-tail, want to clip top/bottom 0.5% | `outlier_threshold` (default 0.995) |
| `"zscore"` | Known normal distribution | `zscore_threshold` (default 3.0) |

### Per-column outlier rules (v2)

Override the global method per column:

```python
cleaner.execute(
    "data.csv",
    schema_rules={"age": "int", "income": "float"},
    outlier_rules={
        "income": {"method": "percentile", "threshold": 0.995},
        "age": {"method": "iqr"},
    },
)
```

## Semantic Rules (v2)

Tag values by semantic meaning **before** any data modification:

```python
semantic_rules = {
    "age": {
        "invalid": [-5, -1],       # → NaN (clearly wrong)
        "suspicious": [150]         # → flagged but KEPT
    },
    "rooms": {
        "invalid": [-1],
        "suspicious": [0]           # 0 might be valid (full hotel)
    }
}
```

| Tag | Effect | Example |
|-----|--------|---------|
| `invalid` | Value → NaN → fill | age=-5 is impossible |
| `suspicious` | Value kept, flagged in audit | rooms=0 might be valid |

## Missing Rules (v2) — Sentinel Handling

Sentinel values are "missing placeholders" (like -999, 9999), NOT semantic errors. Separate from semantic_rules:

```python
missing_rules = {
    "income": {"sentinel": [-999]},         # → NaN → median fill
    "membership_years": {"sentinel": [9999]}
}
```

**Why separate?** Sentinel = "this field has no data", not "this value is wrong". Different processing path:
- semantic invalid → wrong data → fix
- missing sentinel → no data → fill

## Ingestion Config (v2)

Unified data source configuration:

```python
ingestion_config = {
    "db": {"table": "orders"},      # SQLite table selection
    "expand_nested": True,           # Auto-expand JSON columns
    "expected_min_rows": 100         # Warn if fewer rows
}
```

## Semantic Output (v2) — AI-native layer

Every `execute()` call now includes `audit["semantic_output"]`:

```python
cleaned_df, audit = cleaner.execute("data.csv", schema_rules={"age": "int"})
so = audit["semantic_output"]

print(so["summary"])
# "Cleaned 10000 rows → 9850 rows (98.5% retained). filled 423 missing..."

print(so["data_quality_score"])
# 89

for insight in so["insights"]:
    print(f"- {insight}")

for rec in so["recommendations"]:
    print(f"- {rec}")
```

| Field | Type | Description |
|-------|------|-------------|
| `summary` | str | One-line summary for AI to relay |
| `data_quality_score` | int 0-100 | Auto-calculated health score |
| `insights` | list[str] | Key findings from cleaning |
| `actions_taken` | list[str] | Specific operations performed |
| `recommendations` | list[str] | Next steps / human review flags |

## Quick Start

```python
from scripts.clean import DataPipelineCleaner

cleaner = DataPipelineCleaner()

# Basic
cleaned_df, audit = cleaner.execute(
    "data.csv",
    schema_rules={"age": "int", "salary": "float"},
)

# v2 with semantic rules
cleaned_df, audit = cleaner.execute(
    "data.csv",
    schema_rules={"age": "int", "salary": "float"},
    semantic_rules={"age": {"invalid": [-5, -1], "suspicious": [150]}},
    missing_rules={"salary": {"sentinel": [-999]}},
    outlier_rules={"salary": {"method": "iqr"}},
)

# AI reads the semantic output
so = audit["semantic_output"]
print(so["summary"])
print(f"Quality: {so['data_quality_score']}/100")
```

## Audit Report

```json
{
  "original_rows": 10000,
  "cleaned_rows": 9850,
  "retention_rate_pct": 98.5,
  "missing_values_fixed": 423,
  "outliers_suppressed": 37,
  "per_column": {
    "value_replacements": {"height_cm": {"count": 10}},
    "ghost_na_cleaned": {"count": 15},
    "imputation": {"salary": {"strategy": "median", "count": 12}},
    "outlier_winsorizing": {"age": {"method": "IQR(k=1.5)", "lower_fence": 10, "upper_fence": 90}}
  },
  "warnings": ["Ghost-string sentinels cleaned: 15 cells"]
}
```

## Design Principles

1. **AI decides, Python executes.** AI profiles data and generates rules; Python runs the pipeline deterministically.
2. **Never silently drop data.** Every deletion is logged.
3. **Never trust type inference.** CSV/Excel ingested as str, coerced explicitly.
4. **Never delete outlier rows.** Only clip values to fence bounds.
5. **Business rules before statistics.** Logical errors (height=0) handled by replace_values; statistical outliers handled by IQR.
6. **NEVER use outlier_method="none" as a blanket default.** Every numeric column must be evaluated individually for its distribution type.
7. **Semantic separation.** invalid/suspicious (semantic layer) vs sentinel (missing layer) — different concepts, different processing paths.
8. **AI-native output.** Every execution returns a `semantic_output` with summary, score, insights, and recommendations — not just raw audit JSON.
6. **NEVER use outlier_method="none" as a blanket default.** Every numeric column must be evaluated individually for its distribution type.
6. **NEVER use outlier_method="none" as a blanket default.** Every numeric column must be evaluated individually for its distribution type.

## Bundled Scripts

| Script | Purpose |
|--------|---------|
| `scripts/clean.py` | Core `DataPipelineCleaner` class |
| `scripts/profile.py` | Quick data profiling (all 15 formats) |
| `scripts/quality_report.py` | Renders audit JSON to Markdown report |
| `adapters/claude/server.py` | MCP server for Claude Desktop |
