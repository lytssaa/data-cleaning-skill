# Data Cleaning Skill v2 — AI-Native MCP Pipeline

> Industrial-grade data cleaning for AI agents. Seven-phase pipeline with semantic output.

## What This Does

Takes dirty data → applies type rules, semantic tagging, missing handling, per-column outlier detection → returns clean data + AI-readable audit.

## Quick Start

```python
from scripts.clean import DataPipelineCleaner

cleaner = DataPipelineCleaner()
cleaned_df, audit = cleaner.execute(
    "data.csv",
    schema_rules={"age": "int", "salary": "float"},
    semantic_rules={"age": {"invalid": [-5, -1], "suspicious": [150]}},
    missing_rules={"salary": {"sentinel": [-999]}},
    outlier_rules={"salary": {"method": "iqr"}},
)

# AI-native output
print(audit["semantic_output"]["summary"])
print(audit["semantic_output"]["data_quality_score"])
```

## v2 Architecture

```
Phase 0: Safe Ingest        CSV→str, Parquet/Feather→native types
Phase 1: Standardize        snake_case, NFKC, ghost chars
Phase 2: Type Alignment     schema_rules: str→int/float/datetime
Phase 2.3: Semantic Tag     semantic_rules: tag invalid/suspicious (no data modification)
Phase 2.4: Decision Engine  invalid→NaN, suspicious→flag+keep, legacy replace_values
Phase 2.5: Missing Rules    missing_rules: sentinel→NaN (separate from semantic)
Phase 3: Missing Trial      drop cols>70%, rows>50%, fill median/Unknown
Phase 4: Outlier Suppress   per-column methods (iqr/percentile/zscore/none)
Phase 5: Semantic Output    summary, score, insights, recommendations
```

## Parameter Reference

| Parameter | Type | Purpose |
|-----------|------|---------|
| `schema_rules` | dict | Column→type mapping (`"int"`, `"float"`, `"str"`, `"datetime"`) |
| `semantic_rules` | dict | Tag `invalid` (→NaN) and `suspicious` (→flag, keep value) |
| `missing_rules` | dict | Tag `sentinel` values (→NaN, separate from semantic) |
| `business_rules` | dict | Legacy: `replace_values` + `fill` keyword |
| `outlier_rules` | dict | Per-column: `{"col": {"method": "iqr"}}` |
| `outlier_method` | str | Global: `iqr`/`percentile`/`zscore`/`none` |
| `iqr_k` | float | IQR sensitivity (default 1.5) |
| `ingestion_config` | dict | `db`, `expand_nested`, `expected_min_rows` |
| `engine_kwargs` | dict | SQLite table selection |

## Semantic Separation (Key Design)

| Concept | Layer | Processing |
|---------|-------|-----------|
| **invalid** | semantic_rules | Value → NaN (clearly wrong) |
| **suspicious** | semantic_rules | Value KEPT, flagged for review |
| **sentinel** | missing_rules | Value → NaN (placeholder for missing) |

Sentinel is NOT semantic — it's a missing data marker. Different processing path.

## AI-Native Output

Every `execute()` returns `audit["semantic_output"]`:

```json
{
  "summary": "Cleaned 10000 rows → 9850 rows. Filled 423 missing...",
  "data_quality_score": 89,
  "insights": ["High missing rate in column X", "3% invalid age values"],
  "actions_taken": ["Row 5, age: -5 → NaN (age.invalid)"],
  "recommendations": ["Review suspicious values", "Check upstream data quality"]
}
```

## Supported Formats

15 extensions / 11 formats: `.csv` `.tsv` `.xlsx` `.xls` `.json` `.parquet` `.feather` `.html` `.htm` `.xml` `.yaml` `.yml` `.db` `.sqlite` `.sqlite3` `.pkl` `.pickle`

## Install

```bash
# MiMo Code
git clone https://github.com/lytssaa/data-cleaning-skill.git ~/.config/mimocode/skills/data-cleaning

# Claude Desktop
pip install mcp pandas pyarrow openpyxl
# Then add to claude_desktop_config.json (see adapters/claude/README.md)
```

## Dependencies

```bash
pip install pandas pyarrow openpyxl xlrd
```

## License

MIT © 2026 lytssaa
