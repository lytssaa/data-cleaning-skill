# Data Cleaning — Industrial-Grade Pipeline

A production-level data cleaning skill. Five-phase unidirectional pipeline with full audit trail.

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

## Quick Start

```python
from scripts.clean import DataPipelineCleaner

cleaner = DataPipelineCleaner()

# Single file
cleaned_df, audit = cleaner.execute(
    "data.csv",
    schema_rules={"age": "int", "salary": "float"},
    business_rules={"salary": {"replace_values": [0, -1], "fill": "median"}},
    outlier_method="iqr",
    iqr_k=1.5,
)

# Batch
summary = cleaner.run_on_directory(
    input_dir="./raw",
    output_dir="./cleaned",
    schema_rules={"amount": "float"},
    outlier_method="none",
)
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

## Bundled Scripts

| Script | Purpose |
|--------|---------|
| `scripts/clean.py` | Core `DataPipelineCleaner` class |
| `scripts/profile.py` | Quick data profiling (all 15 formats) |
| `scripts/quality_report.py` | Renders audit JSON to Markdown report |
| `adapters/claude/server.py` | MCP server for Claude Desktop |
