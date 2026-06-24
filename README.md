<div align="center">

# 🧹 Data Cleaning Skill

**面向 AI 智能体的工业级数据清洗管道**

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Platforms](https://img.shields.io/badge/Works%20on-WorkBuddy%20%7C%20MiMo%20%7C%20Claude%20%7C%20AtomCode-purple)](https://github.com/lytssaa/data-cleaning-skill)
[![Formats](https://img.shields.io/badge/Formats-15%20extensions-orange)](https://github.com/lytssaa/data-cleaning-skill)

一句话让 AI 把脏数据洗干净。自动识别缺失值、异常值，全角转半角，支持 15 种文件格式。

</div>

---

## 目录

- [特性亮点](#特性亮点)
- [快速开始](#快速开始)
- [安装](#安装)
- [管道架构](#管道架构)
- [参数说明](#参数说明)
- [三层 API](#三层-api)
- [输出结构](#输出结构数据产品包)
- [AI 语义输出](#ai-语义输出)
- [使用示例](#使用示例)
- [设计原则](#设计原则)
- [许可证](#许可证)

---

## 特性亮点

| 能力 | 说明 |
|------|------|
| 📂 **15 种格式** | CSV / TSV / Excel / JSON / Parquet / Feather / XML / YAML / HTML / SQLite / Pickle |
| 🔍 **语义标记层** | `invalid`（→NaN）/ `suspicious`（保留+标记）/ `sentinel`（占位符→NaN）三类独立处理 |
| 📊 **AI 可读输出** | 每次执行返回 `semantic_output`：一句话摘要 + 质量分数 + 洞察 + 建议 |
| 🔧 **七阶段管道** | 读取 → 标准化 → 类型对齐 → 语义标记 → 缺失处理 → 异常压制 → 语义输出 |
| 📦 **数据产品包** | 清洗结果自动打包为五层结构（数据 / 报告 / 血缘 / 样本 / 元数据） |
| 🤖 **多平台兼容** | WorkBuddy / MiMo Code / Claude Code / AtomCode / Claude Desktop (MCP) |

---

## 快速开始

```python
from scripts.clean import DataPipelineCleaner

cleaner = DataPipelineCleaner()

# 基础用法
cleaned_df, audit = cleaner.execute(
    "data.csv",
    schema_rules={"age": "int", "salary": "float"},
)

# v2 完整参数（推荐）
cleaned_df, audit = cleaner.execute(
    "data.csv",
    schema_rules={"age": "int", "salary": "float"},
    semantic_rules={"age": {"invalid": [-5, -1], "suspicious": [150]}},
    missing_rules={"salary": {"sentinel": [-999]}},
    outlier_rules={"salary": {"method": "iqr"}},
)

# 读取 AI 语义输出
so = audit["semantic_output"]
print(so["summary"])              # "清洗 10000 行 → 9850 行（98.5% 保留）..."
print(so["data_quality_score"])   # 89
```

---

## 安装

### WorkBuddy

```bash
workbuddy skill install https://github.com/lytssaa/data-cleaning-skill
```

### MiMo Code

```bash
git clone https://github.com/lytssaa/data-cleaning-skill.git ~/.config/mimocode/skills/data-cleaning
```

### Claude Code

```bash
git clone https://github.com/lytssaa/data-cleaning-skill.git ~/.claude/skills/data-cleaning
```

### AtomCode

```bash
git clone https://github.com/lytssaa/data-cleaning-skill.git ~/.atomcode/skills/data-cleaning
```

### Claude Desktop（MCP 模式）

**第一步：安装依赖**

```bash
pip install mcp pandas pyarrow openpyxl xlrd
```

**第二步：编辑 `claude_desktop_config.json`**

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

### 通用依赖（非 MCP 平台）

```bash
pip install pandas pyarrow openpyxl xlrd
```

---

## 管道架构

```
Raw File
   │
   ▼
Phase 0 — 数据读取        CSV/Excel: dtype=str（零推断）
                           Parquet/Feather: 保留原生类型
   │
   ▼
Phase 1 — 标准化          列名 → snake_case（NFKC 全角转半角）
                           幽灵字符（\u200b \ufeff）剔除
   │
   ▼
Phase 2 — 类型对齐        schema_rules: str → int/float/datetime
                           errors='coerce': 不合法值 → NaN
   │
   ▼
Phase 2.3 — 语义标记       semantic_rules: 标记 invalid/suspicious（不改数据）
   │
   ▼
Phase 2.4 — 决策引擎       invalid → NaN，suspicious → 保留+标记
Phase 2.5 — 缺失规则       missing_rules: sentinel → NaN（与语义层分离）
   │
   ▼
Phase 3 — 缺失值处理       列 >70% 空 → 删列，行 >50% 空 → 删行
                           数值列填 median，文本列填 Unknown
   │
   ▼
Phase 4 — 异常值压制       per-column 策略：iqr / percentile / zscore / none
                           只截断到围栏边界，不删行
   │
   ▼
Phase 5 — 语义输出         summary + score + insights + recommendations
   │
   ▼
(cleaned_df, audit_report)
```

---

## 参数说明

### execute() 参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `schema_rules` | dict | 列 → 类型映射（`"int"` / `"float"` / `"str"` / `"datetime"`） |
| `semantic_rules` | dict | 语义标记：`invalid`（→NaN）/ `suspicious`（保留+标记） |
| `missing_rules` | dict | 哨兵值处理：`sentinel`（→NaN，与语义层分离） |
| `business_rules` | dict | 旧版：`replace_values` + `fill` 关键字 |
| `outlier_rules` | dict | per-column 策略：`{"列名": {"method": "iqr"}}` |
| `outlier_method` | str | 全局策略：`iqr` / `percentile` / `zscore` / `none` |
| `iqr_k` | float | IQR 灵敏度（默认 1.5） |
| `ingestion_config` | dict | `db`（表名）/ `expand_nested`（展开嵌套 JSON）/ `expected_min_rows` |
| `engine_kwargs` | dict | SQLite 表名选择 |

### 语义分层说明

| 概念 | 所在层 | 处理方式 | 适用场景 |
|------|--------|---------|---------|
| `invalid` | semantic_rules | 值 → NaN（明显错误） | `age = -5`，不可能存在的值 |
| `suspicious` | semantic_rules | 值**保留**，标记待审核 | `age = 150`，异常但不排除 |
| `sentinel` | missing_rules | 值 → NaN（缺失占位符） | `-999`、`9999` 这类填充代码 |

> **为什么 sentinel 必须独立？** sentinel 是"没有数据"，不是"数据错了"，处理路径本质不同：语义 invalid → 修复数据；missing sentinel → 补全空白。

### 异常值方法对照

| 方法 | 适用场景 | 参数 |
|------|---------|------|
| `"iqr"` | 正态/对称分布（身高、体重、年龄） | `iqr_k`（默认 1.5） |
| `"percentile"` | 中度长尾，截断头尾 0.5% | `outlier_threshold`（默认 0.995） |
| `"zscore"` | 已知正态分布 | `zscore_threshold`（默认 3.0） |
| `"none"` | 长尾数据（薪资、营收）或 ID 列 | — |

---

## 三层 API

```python
# 第一层：管道执行（只做清洗，不写文件）
cleaned_df, audit = cleaner.execute("data.csv", schema_rules={...})

# 第二层：数据产品包构建（纯内存，零 I/O）
bundle = cleaner.build_artifacts(cleaned_df, audit, source_stem="data")

# 第三层：落盘存储
artifacts = cleaner.save_artifacts(bundle, output_dir="cleaned_data/")

# 旧版一体化（内部调用 build + save）
cleaner._save_cleaned_output(subdir, stem, cleaned_df, audit)
```

---

## 输出结构（数据产品包）

每个数据集清洗后生成一个完整的**五层数据产品包**：

```
cleaned_data/
├── fitness/
│   ├── data/
│   │   ├── fitness.csv              ← 清洗后的数据（CSV）
│   │   └── fitness.parquet          ← 清洗后的数据（Parquet，工业标准）
│   ├── report/
│   │   ├── audit.json               ← 完整审计日志
│   │   └── data_quality.json        ← 质量评分卡（分数 + 洞察 + 建议）
│   ├── lineage/
│   │   └── transformations.json     ← 数据血缘（每一步变换记录）
│   ├── samples/
│   │   ├── before_sample.csv        ← 清洗前前 5 行
│   │   └── after_sample.csv         ← 清洗后前 5 行
│   └── metadata.json                ← 控制层（行数、留存率、时间戳）
├── hotels/
│   └── ...（同样五层结构）
└── _summary.json                    ← 全局汇总
```

| 层级 | 文件 | 用途 | 使用方 |
|------|------|------|--------|
| `data/` | `*.csv` + `*.parquet` | 干净数据 | 下游系统 |
| `report/audit.json` | 审计日志 | 每阶段操作明细 | 开发调试 |
| `report/data_quality.json` | 质量评分卡 | 总分 + 三维分析 + AI 洞察 | AI 读取汇报 |
| `lineage/` | 数据血缘 | 变换路径记录 | 数据治理 / 合规 |
| `samples/` | 前后对比 | 清洗前后各 5 行 | 人工快速验证 |
| `metadata.json` | 控制层 | 源文件 + 行数变化 + 时间戳 | 自动化流水线 |

---

## AI 语义输出

每次 `execute()` 都会在 `audit["semantic_output"]` 中返回 AI 可直读的结构化摘要：

```json
{
  "summary": "清洗 10000 行 → 9850 行（98.5% 保留）。修复 423 处缺失值，压制 37 处异常值。",
  "data_quality_score": 89,
  "insights": [
    "salary 列缺失率偏高（12%）",
    "3% 的 age 值被标记为 invalid"
  ],
  "actions_taken": [
    "第5行, age: -5 → NaN (age.invalid)",
    "salary: 12 处缺失值 → median 填充"
  ],
  "recommendations": [
    "建议人工审核 suspicious 标记的 150 处记录",
    "检查 salary 数据采集流程，缺失率异常偏高"
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `summary` | str | 一句话总结（AI 直接转述给用户） |
| `data_quality_score` | int 0–100 | 数据健康分数 |
| `insights` | list[str] | 关键发现 |
| `actions_taken` | list[str] | 具体操作记录 |
| `recommendations` | list[str] | 后续建议 / 人工审核提示 |

---

## 使用示例

### 基础清洗

```python
from scripts.clean import DataPipelineCleaner

cleaner = DataPipelineCleaner()
cleaned_df, audit = cleaner.execute(
    "employees.csv",
    schema_rules={"age": "int", "salary": "float", "join_date": "datetime"},
)
print(audit["semantic_output"]["summary"])
```

### 完整参数（带语义规则）

```python
cleaned_df, audit = cleaner.execute(
    "survey.xlsx",
    schema_rules={"age": "int", "income": "float"},
    semantic_rules={
        "age": {"invalid": [-5, -1, 0], "suspicious": [150]},
        "rooms": {"invalid": [-1], "suspicious": [0]},
    },
    missing_rules={
        "income": {"sentinel": [-999]},
        "membership_years": {"sentinel": [9999]},
    },
    outlier_rules={
        "age": {"method": "iqr"},
        "income": {"method": "percentile", "threshold": 0.995},
    },
)
```

### SQLite 数据库

```python
cleaned_df, audit = cleaner.execute(
    "records.db",
    schema_rules={"price": "float"},
    ingestion_config={"db": {"table": "orders"}},
)
```

### 保存数据产品包

```python
cleaned_df, audit = cleaner.execute("data.csv", schema_rules={"age": "int"})
bundle = cleaner.build_artifacts(cleaned_df, audit, source_stem="data")
artifacts = cleaner.save_artifacts(bundle, output_dir="cleaned_data/")
print(artifacts)  # 所有输出文件路径
```

---

## 内置脚本

| 脚本 | 用途 |
|------|------|
| `scripts/clean.py` | 核心 `DataPipelineCleaner` 类 |
| `scripts/profile.py` | 快速数据概览（支持全部 15 种格式） |
| `scripts/quality_report.py` | 将 audit JSON 渲染为 Markdown 质量报告 |
| `adapters/claude/server.py` | Claude Desktop MCP 适配器 |

---

## 设计原则

1. **AI 决策，Python 执行** — AI 负责分析数据并生成规则，Python 管道确定性执行
2. **不静默丢数据** — 每一次删除都有日志记录
3. **不信任类型推断** — CSV/Excel 全部以 `str` 读入，再显式转换
4. **不删除异常行** — 只将异常值截断到围栏边界，保留行完整性
5. **业务规则优先于统计规则** — `height=0` 先由 `replace_values` 处理，再走 IQR
6. **语义分离** — `invalid`/`suspicious`（语义层）与 `sentinel`（缺失层）处理路径完全独立
7. **AI 原生输出** — 每次执行都返回可被 AI 直接读取的结构化摘要
8. **类型感知填充** — 分类列用 mode 填充，数值列用 rounded median，不乱填 `"Unknown"`

---

## 许可证

MIT © 2026 [lytssaa](https://github.com/lytssaa)
