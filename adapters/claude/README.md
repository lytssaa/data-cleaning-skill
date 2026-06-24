# MCP Server — Data Cleaner v2

Claude Desktop / MiMo Code MCP adapter for `DataPipelineCleaner`.

## Setup

```bash
pip install mcp pandas pyarrow openpyxl
```

Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "data-cleaning": {
      "command": "python",
      "args": ["adapters/claude/server.py"],
      "cwd": "/path/to/data-cleaning"
    }
  }
}
```

## Tools

### `clean_data`

Full v2 pipeline with semantic rules, per-column outlier methods, and AI-native output.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | str | required | Data file path |
| `schema_rules` | JSON str | `{}` | Column→type mapping |
| `semantic_rules` | JSON str | `{}` | invalid/suspicious tagging |
| `missing_rules` | JSON str | `{}` | sentinel→NaN |
| `business_rules` | JSON str | `{}` | Legacy replace_values |
| `outlier_rules` | JSON str | `{}` | Per-column outlier method |
| `outlier_method` | str | `"iqr"` | Global: iqr/percentile/zscore/none |
| `iqr_k` | float | 1.5 | IQR sensitivity |
| `expand_nested` | bool | false | Expand nested JSON |
| `db_table` | str | `""` | SQLite table name |

Returns audit with `semantic_output` (summary, score, insights, recommendations).

### `profile_data`

Quick data profile with column name standardization mapping.

## Example Usage

```json
{
  "file_path": "data.csv",
  "schema_rules": {"age": "int", "salary": "float"},
  "semantic_rules": {"age": {"invalid": [-5, -1], "suspicious": [150]}},
  "missing_rules": {"income": {"sentinel": [-999]}},
  "outlier_rules": {"salary": {"method": "iqr"}},
  "outlier_method": "iqr"
}
```
