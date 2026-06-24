# Claude Desktop MCP Adapter

MCP server wrapping `DataPipelineCleaner` for Claude Desktop.

## Install

```bash
pip install mcp pandas pyarrow openpyxl
```

## Configure Claude Desktop

Add to `claude_desktop_config.json`:

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

## Available Tools

### `clean_data`

Full five-phase pipeline.

```
Clean the file sales.csv, convert age to int and revenue to float
```

Claude will invoke `clean_data` with `schema_rules={"age": "int", "revenue": "float"}`.

### `profile_data`

Quick structural overview before cleaning.

```
Profile survey_results.xlsx first
```

Returns column-level stats (null counts, unique values, samples).
