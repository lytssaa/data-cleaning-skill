# Data Cleaning Skill — 工业级数据清洗管道

> 🏭 面向 AI 编程助手的生产级数据清洗工具。五阶段单向管道架构，四平台兼容。
>
> **数据清洗 · 数据预处理 · 缺失值处理 · 异常值检测 · ETL 管道 · pandas 工具**

[![WorkBuddy](https://img.shields.io/badge/WorkBuddy-原生支持-6366f1)](https://www.codebuddy.cn)
[![Claude](https://img.shields.io/badge/Claude-MCP-d97706)](https://claude.ai)
[![AtomCode](https://img.shields.io/badge/AtomCode-兼容-10b981)](https://atomcode.atomgit.com)
[![MiMo Code](https://img.shields.io/badge/MiMo_Code-兼容-ef4444)](https://mimo.xiaomi.com)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## 📋 目录

- [适合谁用](#适合谁用)
- [使用场景](#使用场景)
- [设计哲学](#设计哲学)
- [管道架构](#管道架构)
- [各平台安装与使用](#各平台安装与使用)
  - [WorkBuddy](#workbuddy)
  - [Claude Desktop](#claude-desktop)
  - [AtomCode](#atomcode)
  - [MiMo Code](#mimo-code)
  - [纯 Python API](#纯-python-api所有平台通用)
- [Schema Rules（类型映射）](#schema-rules类型映射)
- [审计报告](#审计报告)
- [关键行为保证](#关键行为保证)
- [运行测试](#运行测试)
- [依赖](#依赖)
- [文件结构](#文件结构)
- [常见问题](#常见问题)
- [贡献指南](#贡献指南)
- [License](#license)

---

## 适合谁用

| 角色 | 怎么用 | 收益 |
| ---- | ------ | ---- |
| **数据分析师** | CSV/Excel 拖进去，一句话说清要转什么类型 | 省去手写 `pd.to_numeric() + fillna() + clip()` 的重复劳动 |
| **数据工程师** | 集成到 ETL 管道，直接用 Python API | 有审计日志，出问题能回溯每一步 |
| **AI Coding 用户** | 对话中说"帮我把这个表洗一下" | 不用写代码，AI 自动加载 Skill 执行 |
| **团队管理者** | 把 Skill 装到团队 WorkBuddy/Claude 里 | 清洗标准统一，不再每人一套脚本 |
| **Python 初学者** | 直接 `import`，一行调用 | 不需要懂 pandas 细节就能做专业数据清洗 |

## 使用场景

### 场景一：用户调研数据清洗

> 从问卷平台导出 2 万行 CSV，列名是中文、有全角空格、年龄填了"二十岁"、"35+"、"中年"……

**用本工具：** `schema_rules={"年龄": "int", "收入": "float"}`，一行代码拿到干净数据 + 审计报告。全角空格自动去、中文数字自动转 NaN 再中位数填充、异常值截断不丢行。

### 场景二：多源数据合并前的标准化

> 三个部门交上来的 Excel 格式各不相同：有的用 `User Name`，有的用 `user_name`，有的用 `用户名`……

**用本工具：** 列名自动统一为 snake_case，NFKC 全角转半角，幽灵字符剔除，合并前每个人都跑一遍。

### 场景三：生产 ETL 管道中的质量守门员

> 每天凌晨自动入库的 CSV 数据，上游可能丢字段、可能混入脏值……

**用本工具：** `execute()` 返回的 `audit` dict 直接 JSON 序列化送到监控系统。留存率低于阈值自动告警。

### 场景四：AI 辅助数据分析

> 跟 WorkBuddy 对话："帮我把本周的销售数据洗一下，金额转 float，日期转 datetime"

**用本工具：** AI 自动加载此 Skill，识别文件类型、执行五阶段管道、输出干净数据。不必离开对话界面。

### 场景五：教学与面试

> 教学生/新人理解数据清洗的标准流程

**用本工具：** 五阶段管道可拆分调用，每一步独立可观测。审计报告本身就是教学输出。

---

## 设计哲学

**永不静默丢弃数据。永不信任自动类型推断。永不删除异常行。全程可审计。**

❌ 这不是一个 `df.dropna()` 脚本。
✅ 每一步操作都有记录，每一次删除都有日志，每一个异常值都被截断到边界值——而不是被删除。

区别：

| 做法 | 玩具脚本（什么不该做） | 本 Skill（怎么做） |
| ---- | -------------------- | ----------------- |
| 读取 CSV | `pd.read_csv()` 自动推断类型 | `dtype=str` 全文本吞入，零推断 |
| 工号 "001" | 变成 1，前导零丢失 | 保持 "001"，除非你指定它为 int |
| 年龄 "二十岁" | 直接报错崩溃 | 静默转 NaN，中位数填充 |
| 长数字 ID | 变科学计数法 1.23e+15 | 字符串不变 |
| 某列 90% 空 | 没处理 | 整列砍掉 + 审计记录 |
| 异常值 200000 | 静默删除整行 | 截断到 IQR 上界，数据量一条不少 |

---

## 管道架构

```
                    ┌───────────────────┐
                    │  原始文件 (.csv /  │
                    │  .xlsx / .json /  │
                    │  .tsv)            │
                    └─────────┬─────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│  阶段 0 — _safe_ingest()             安全吞入            │
│  · dtype=str，零类型推断                                  │
│  · 多编码自动回退链 (utf-8 → gbk → gb18030 → latin-1)   │
│  · 支持 CSV / TSV / Excel (.xlsx/.xls) / JSON           │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  阶段 1 — _standardize_columns_and_text()  结构规范化    │
│  · 列名：去空格 → NFKC 全角转半角 → snake_case           │
│  · 列名碰撞自动去重 (加 _2、_3 后缀)                     │
│  · 文本值：NFKC 半角化 → 幽灵字符正则剔除                │
│  · 剔除 \u200b \ufeff \u200c \u200d \u200e 等不可见字符 │
│  · 空串 → NaN（语义提升）                                 │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  阶段 2 — _type_alignment(df, schema_rules)  类型对齐   │
│  · 按用户指定的 schema_rules 强转                        │
│  · errors='coerce'：脏值静默转 NaN                      │
│  · 支持 int / float / str / datetime 四种目标类型        │
│  · 未在 schema_rules 中的列保持 string                   │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  阶段 3 — _missing_value_trial(df)  缺失审判             │
│  · 列缺失率 > 70%  →  整列删除                           │
│  · 行缺失率 > 50%  →  整行删除                           │
│  · 数值型列  →  中位数填充                               │
│  · 文本/类别型列  →  "Unknown" 填充                      │
│  · 日期型列  →  前向填充                                 │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  阶段 4 — _outlier_suppression(df)  异常压制             │
│  · IQR (四分位距) 方法，k=1.5                            │
│  · 异常值截断到 [Q1−1.5×IQR, Q3+1.5×IQR] 边界           │
│  · 铁律：绝不删除行，只截断值                             │
│  · 整数列自动取整边界                                     │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
                ┌─────────────────┐
                │  (cleaned_df,   │
                │   audit_report) │
                └─────────────────┘
```

---

## 各平台安装与使用

### WorkBuddy

**安装：**

```bash
workbuddy skill install https://github.com/lytssaa/data-cleaning-skill
```

或在 WorkBuddy 对话中：

```
帮我安装 data-cleaning skill，仓库地址 https://github.com/lytssaa/data-cleaning-skill
```

**使用：**

```
帮我把 sales_2024.csv 洗一下：
- age 转 int
- salary 转 float
- join_date 转 datetime
```

---

### Claude Desktop

**安装：**

```bash
# 1. 安装依赖
pip install mcp pandas pyarrow openpyxl

# 2. 克隆仓库
git clone https://github.com/lytssaa/data-cleaning-skill.git ~/data-cleaning-skill
```

**3. 配置 Claude Desktop：**

打开 `claude_desktop_config.json`（位置：macOS `~/Library/Application Support/Claude/`，Windows `%APPDATA%\Claude\`），添加：

```json
{
  "mcpServers": {
    "data-cleaning": {
      "command": "python",
      "args": ["adapters/claude/server.py"],
      "cwd": "/Users/你的用户名/data-cleaning-skill"
    }
  }
}
```

**4. 重启 Claude Desktop**

**使用：**

```
用 data-cleaning 工具帮我把 sales.csv 清洗，age 转 int，amount 转 float
```

---

### AtomCode

**安装：**

```bash
git clone https://github.com/lytssaa/data-cleaning-skill.git ~/.atomcode/skills/data-cleaning
```

**使用：**

斜杠命令：

```
/data-cleaning
```

或自然语言：

```
清洗 dirty_data.csv，把 age 转 int，price 转 float
```

---

### MiMo Code

**安装：**

```bash
git clone https://github.com/lytssaa/data-cleaning-skill.git ~/.config/mimocode/skills/data-cleaning
```

或在 `mimocode.json` 中配置 url 自动加载：

```json
{
  "skills": {
    "urls": ["https://github.com/lytssaa/data-cleaning-skill/releases/latest/download/bundle.json"]
  }
}
```

**使用：**

```
清洗 survey_results.xlsx，把 respondent_age 转 int，income 转 float
```

---

### 纯 Python API（通用）

```python
from scripts.clean import DataPipelineCleaner

cleaner = DataPipelineCleaner()
cleaned_df, audit = cleaner.execute(
    file_path="dirty_survey.csv",
    schema_rules={"age": "int", "income": "float", "signup_date": "datetime"},
)

print(f"留存率: {audit['retention_rate_pct']}%")
print(f"修复缺失值: {audit['missing_values_fixed']} 处")
print(f"压制异常值: {audit['outliers_suppressed']} 处")

# 保存清洗结果
cleaned_df.to_csv("cleaned_survey.csv", index=False)

# 保存审计报告
import json
with open("audit.json", "w") as f:
    json.dump(audit, f, ensure_ascii=False, indent=2, default=str)
```

**分步调用（高级用法）：**

```python
cleaner = DataPipelineCleaner()

# 可以跳过 _safe_ingest，手动传入 DataFrame
raw_df = pd.read_csv("data.csv", dtype=str)
raw_df = cleaner._standardize_columns_and_text(raw_df)
raw_df = cleaner._type_alignment(raw_df, {"age": "int"})
raw_df = cleaner._missing_value_trial(raw_df)
raw_df = cleaner._outlier_suppression(raw_df)

# 审计状态在 cleaner._audit 中
print(cleaner._audit)
```

---

## Schema Rules（类型映射）

```python
schema_rules = {
    "age":         "int",       # pd.to_numeric(errors='coerce') → Int64
    "salary":      "float",     # pd.to_numeric(errors='coerce') → float64
    "name":        "str",       # astype("string")
    "join_date":   "datetime",  # pd.to_datetime(errors='coerce')
}
```

未列出的列保持字符串，安全第一。

---

## 审计报告

`execute()` 返回的第二个元素是一个完整字典：

```json
{
  "started_at":          "2026-06-24T14:22:37",
  "finished_at":         "2026-06-24T14:22:37",
  "original_rows":        10000,
  "cleaned_rows":         9850,
  "retention_rate_pct":   98.5,
  "dropped_columns":      ["useless_survey_field"],
  "dropped_rows_count":   150,
  "missing_values_fixed": 423,
  "outliers_suppressed":  37,
  "per_column": {
    "coercion": {
      "age": {
        "target_type": "int",
        "invalid_values_coerced_to_null": 12
      }
    },
    "column_drops": {
      "useless_survey_field": {
        "reason": "缺失率 > 70%",
        "missing_rate": 85.3
      }
    },
    "imputation": {
      "city": {
        "strategy": "constant",
        "fill_value": "Unknown",
        "count": 35
      }
    },
    "outlier_winsorizing": {
      "income": {
        "method": "IQR",
        "k": 1.5,
        "lower_fence": 1500.0,
        "upper_fence": 85000.0,
        "clamped_low": 0,
        "clamped_high": 8
      }
    }
  },
  "stage_timings": {
    "safe_ingest":          0.12,
    "standardize":          0.35,
    "type_alignment":       0.08,
    "missing_trial":        0.05,
    "outlier_suppression":  0.02
  },
  "warnings": [
    "schema_rules 引用了不存在的列 'bonus' —— 已跳过",
    "12 处异常值已截断到 IQR 边界 (k=1.5)"
  ]
}
```

---

## 关键行为保证

| 场景 | 普通 pandas 脚本 | 本 Skill |
| ---- | :-------------: | :-----: |
| 工号 "001" | 变 1 | 保持 "001" |
| 年龄填 "二十岁" | 直接报错或变形 | 转 NaN → 中位数填充 |
| 薪资 200000（异常） | 静默删行或不管 | 截断到 IQR 上界 |
| 长数字身份证 | 变科学计数法 | 字符串原样保留 |
| 某列 90% 空 | 留着污染后续分析 | 整列砍掉 + 审计记录 |
| 全角空格列名 | 程序出错 | 自动转 snake_case |
| 零宽字符混入 | 肉眼看不到、程序出错 | 正则剔除 |
| 编码检测 | 乱码 | utf-8 → gbk → gb18030 → latin-1 自动回退 |

---

## 运行测试

```bash
python scripts/clean.py
```

内置合成脏数据集测试覆盖：全角空格列名 · 零宽字符 · 全角空格文本 · 中文数字 "三十" · 极端异常值 999 / 200000 · 90% 缺失列。

---

## 依赖

```bash
pip install pandas pyarrow openpyxl

# Claude Desktop MCP 额外需要:
pip install mcp
```

| 包 | 用途 |
| -- | ---- |
| `pandas >= 2.0` | 核心数据操作，PyArrow 后端降低内存 |
| `pyarrow` | Pandas 2.0+ ArrowDtype 后端 |
| `openpyxl` | Excel (.xlsx) 读写 |
| `mcp` | Claude Desktop MCP 协议（仅 Claude 需要） |

---

## 文件结构

```
data-cleaning-skill/
│
├── README.md                          ← 你正在看的文件
├── SKILL.md                           ← Skill 入口定义 (WorkBuddy/AtomCode/MiMo 共用)
│
├── scripts/
│   ├── clean.py                       ← ★ DataPipelineCleaner 核心类（686 行）
│   │                                     五阶段管道 + 端到端测试
│   ├── profile.py                     ← 数据画像：列统计、空值率、样本值
│   └── quality_report.py             ← 审计 JSON → Markdown 报告渲染（中/英双语）
│
├── adapters/
│   └── claude/
│       ├── server.py                  ← Claude Desktop MCP Server
│       │                                 暴露 clean_data + profile_data 两个 tool
│       └── README.md                  ← Claude 专用安装说明
│
├── references/
│   ├── chinese_text_normalization.md  ← 中文文本清洗：全角/半角、编码陷阱
│   └── cleaning_strategies.md         ← 各清洗策略详解与调参指南
│
└── assets/
    └── clean_config.example.json      ← 配置文件示例
```

---

## 常见问题

<details>
<summary><b>为什么读 CSV 不用 pandas 默认的类型推断？</b></summary>

pandas 的自动推断会导致：
- 工号 `"001"` → `1`（前导零丢失）
- 长数字 `123456789012345` → `1.234567e+14`（科学计数法）
- `"N/A"` → 被误判为 NaN

本 Skill 一律以字符串读入，只有你在 `schema_rules` 里明确指定的列才做类型转换。而且用 `errors='coerce'`，转不动的脏值变成 NaN，交给缺失值阶段填充，不中断管道。
</details>

<details>
<summary><b>为什么异常值不删除而是截断？</b></summary>

删除异常行会丢失信息——那条行的其他字段可能是正常的、有价值的。Winsorizing（截断）只压制极端值，数据量一条不少，后续建模更鲁棒。如果你确实需要删除，在拿到干净 DataFrame 后自己 `df[df['col'] > threshold]` 即可。
</details>

<details>
<summary><b>可以单独调用某个阶段吗？</b></summary>

可以。五个阶段都是公有方法（虽然是 `_` 开头，但可以调用）：

```python
cleaner = DataPipelineCleaner()
df = cleaner._safe_ingest(Path("data.csv"))
df = cleaner._standardize_columns_and_text(df)
# ... 需要时才继续
```

不过推荐直接用 `execute()`，它保证阶段顺序和数据一致性。
</details>

<details>
<summary><b>能处理多大体积的数据？</b></summary>

Pandas 2.0+ PyArrow 后端比旧版 NumPy 后端节省 30-50% 内存。10 万行以内轻松处理，百万行也跑得动。如果是 GB 级数据集，建议先用 `profile.py` 看看数据画像，再决定是否需要切分处理。
</details>

<details>
<summary><b>和 OpenRefine / Trifacta 比有什么优势？</b></summary>

OpenRefine 和 Trifacta 是 GUI 工具，适合手动探索。本 Skill 的优势在于：
- **编程接口**：可嵌入 ETL 管道，自动化运行
- **AI 原生**：对话就能操作，不需要打开另一个软件
- **四平台通用**：同一份代码在四个 AI 助手里跑
</details>

---

## 贡献指南

欢迎 PR。请确保：
1. `scripts/clean.py` 的 `if __name__ == '__main__'` 测试块全部通过
2. 新功能有对应的 Google 风格 docstring
3. 管道阶段的单向顺序不被破坏

---

## License

MIT © 2026 lytssaa
