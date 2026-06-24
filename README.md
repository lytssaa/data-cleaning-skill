# Data Cleaning Skill v2 — AI 原生数据清洗管道

> 面向 AI 智能体的工业级数据清洗工具。七阶段管道架构，语义输出层，四平台兼容。
>
> **数据清洗 · 缺失值处理 · 异常值检测 · 语义标记 · ETL 管道**

## 快速开始

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

# AI 可直接读取的语义输出
print(audit["semantic_output"]["summary"])
print(audit["semantic_output"]["data_quality_score"])
```

## v2 管道架构

```
Phase 0: 数据读取          CSV→str, Parquet/Feather→保留原生类型
Phase 1: 标准化            snake_case列名, NFKC全角转半角, 幽灵字符剔除
Phase 2: 类型对齐          schema_rules: str→int/float/datetime
Phase 2.3: 语义标记        semantic_rules: 标记 invalid/suspicious（不改数据）
Phase 2.4: 决策引擎        invalid→NaN, suspicious→保留+标记, 旧版 replace_values
Phase 2.5: 缺失规则        missing_rules: sentinel→NaN（与语义层分离）
Phase 3: 缺失值处理        列>70%删除, 行>50%删除, 填充 median/Unknown
Phase 4: 异常值压制        per-column 策略（iqr/percentile/zscore/none）
Phase 5: 语义输出          summary, score, insights, recommendations
```

## 参数参考

| 参数 | 类型 | 作用 |
|------|------|------|
| `schema_rules` | dict | 列→类型映射（`"int"`, `"float"`, `"str"`, `"datetime"`） |
| `semantic_rules` | dict | 标记 `invalid`（→NaN）和 `suspicious`（→保留+标记） |
| `missing_rules` | dict | 标记 `sentinel`（→NaN，与语义层分离） |
| `business_rules` | dict | 旧版：`replace_values` + `fill` 关键字 |
| `outlier_rules` | dict | per-column：`{"列名": {"method": "iqr"}}` |
| `outlier_method` | str | 全局策略：`iqr`/`percentile`/`zscore`/`none` |
| `iqr_k` | float | IQR 灵敏度（默认 1.5） |
| `ingestion_config` | dict | `db`、`expand_nested`、`expected_min_rows` |
| `engine_kwargs` | dict | SQLite 表名选择 |

## 语义分层（核心设计）

| 概念 | 所在层 | 处理方式 |
|------|--------|---------|
| **invalid** | semantic_rules | 值→NaN（明显错误） |
| **suspicious** | semantic_rules | 值**保留**，标记待人工审核 |
| **sentinel** | missing_rules | 值→NaN（缺失占位符） |

为什么 sentinel 必须独立？因为 sentinel 是"没有数据"，不是"数据错了"。处理路径不同。

## AI 语义输出

每次 `execute()` 都返回 `audit["semantic_output"]`：

```json
{
  "summary": "清洗 10000 行 → 9850 行（98.5% 保留）。修复 423 处缺失值...",
  "data_quality_score": 89,
  "insights": ["X列缺失率偏高", "3% 的年龄值无效"],
  "actions_taken": ["第5行, age: -5 → NaN (age.invalid)"],
  "recommendations": ["审核可疑值", "检查上游数据采集流程"]
}
```

| 字段 | 类型 | 作用 |
|------|------|------|
| `summary` | str | 一句话总结（AI 直接读） |
| `data_quality_score` | int 0-100 | 数据健康分数 |
| `insights` | list[str] | 关键发现 |
| `actions_taken` | list[str] | 具体操作记录 |
| `recommendations` | list[str] | 后续建议 |

## 支持格式

15 种扩展名 / 11 种格式：`.csv` `.tsv` `.xlsx` `.xls` `.json` `.parquet` `.feather` `.html` `.htm` `.xml` `.yaml` `.yml` `.db` `.sqlite` `.sqlite3` `.pkl` `.pickle`

## 安装

### 各平台安装命令

| 平台 | 命令 | 说明 |
|------|------|------|
| **MiMo Code** | `git clone https://github.com/lytssaa/data-cleaning-skill.git ~/.config/mimocode/skills/data-cleaning` | 读 SKILL.md 直接用 |
| **Claude Code** | `git clone https://github.com/lytssaa/data-cleaning-skill.git ~/.claude/skills/data-cleaning` | 同上 |
| **AtomCode** | `git clone https://github.com/lytssaa/data-cleaning-skill.git ~/.atomcode/skills/data-cleaning` | 同上 |
| **WorkBuddy** | `workbuddy skill install https://github.com/lytssaa/data-cleaning-skill` | 或手动 clone |
| **Claude Desktop** | 见下方 MCP 配置 | 需要额外配置 adapter |

### Claude Desktop MCP 配置

```bash
pip install mcp pandas pyarrow openpyxl
```

编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "data-cleaning": {
      "command": "python",
      "args": ["adapters/claude/server.py"],
      "cwd": "/你的安装路径/data-cleaning"
    }
  }
}
```

### 通用依赖

```bash
pip install pandas pyarrow openpyxl xlrd
```

编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "data-cleaning": {
      "command": "python",
      "args": ["adapters/claude/server.py"],
      "cwd": "/你的安装路径/data-cleaning"
    }
  }
}
```

### 通用依赖

```bash
pip install pandas pyarrow openpyxl xlrd
```

编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "data-cleaning": {
      "command": "python",
      "args": ["adapters/claude/server.py"],
      "cwd": "/你的安装路径/data-cleaning"
    }
  }
}
```

### 通用依赖

```bash
pip install pandas pyarrow openpyxl xlrd
```

编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "data-cleaning": {
      "command": "python",
      "args": ["adapters/claude/server.py"],
      "cwd": "/你的安装路径/data-cleaning"
    }
  }
}
```

### 通用依赖

```bash
pip install pandas pyarrow openpyxl xlrd
```

编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "data-cleaning": {
      "command": "python",
      "args": ["adapters/claude/server.py"],
      "cwd": "/你的安装路径/data-cleaning"
    }
  }
}
```

### 通用依赖

```bash
pip install pandas pyarrow openpyxl xlrd
```

## 依赖

```bash
pip install pandas pyarrow openpyxl xlrd
```

## 许可证

MIT © 2026 lytssaa
