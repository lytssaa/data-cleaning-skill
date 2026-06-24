# Data Cleaning Skill — 工业级数据清洗管道

**面向 AI 编程助手的生产级数据清洗工具。五阶段单向管道架构，四平台兼容。**

[![WorkBuddy](https://img.shields.io/badge/WorkBuddy-原生支持-6366f1)](https://www.codebuddy.cn)
[![Claude](https://img.shields.io/badge/Claude-MCP-d97706)](https://claude.ai)
[![AtomCode](https://img.shields.io/badge/AtomCode-兼容-10b981)](https://atomcode.atomgit.com)
[![MiMo Code](https://img.shields.io/badge/MiMo_Code-兼容-ef4444)](https://mimo.xiaomi.com)

---

## 设计哲学

**永不静默丢弃数据。永不信任自动类型推断。永不删除异常行。全程可审计。**

这不是一个 `df.dropna()` 脚本。每一步操作都有记录，每一次删除都有日志，每一个异常值都被截断到边界值——而不是被删除。

## 管道架构

```
原始文件 (CSV/Excel/JSON/TSV)
   │
   ▼
阶段 0 ─ 安全吞入        dtype=str · 零类型推断 · 多编码回退
阶段 1 ─ 结构规范化      列名→snake_case · NFKC全角转半角 · 幽灵字符剔除
阶段 2 ─ 类型对齐        errors='coerce' · 脏文本→NaN · 交由下一阶段处理
阶段 3 ─ 缺失审判        列缺失>70%→斩 · 行缺失>50%→斩 · 中位数/"Unknown"填充
阶段 4 ─ 异常压制        IQR(1.5×)截断 · 绝不删行
   │
   ▼
(cleaned_df, audit_report)
```

## 各平台安装与使用

### WorkBuddy

**安装：**
```
workbuddy skill install https://github.com/lytssaa/data-cleaning-skill
```

或者在 WorkBuddy 对话中直接说：
```
帮我安装 data-cleaning skill，地址是 https://github.com/lytssaa/data-cleaning-skill
```

**使用：**
在对话中直接说即可，AI 会自动加载 Skill 并执行：
```
帮我把 sales_2024.csv 洗一下，age 转成 int，salary 转成 float
```

---

### Claude Desktop

**安装：**

1. 安装依赖：
```bash
pip install mcp pandas pyarrow openpyxl
```

2. 克隆仓库：
```bash
git clone https://github.com/lytssaa/data-cleaning-skill.git ~/data-cleaning-skill
```

3. 编辑 Claude Desktop 配置文件（`claude_desktop_config.json`），添加：
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

4. 重启 Claude Desktop

**使用：**
```
帮我把 sales.csv 清洗一下，用 data-cleaning 工具，age 转 int，amount 转 float
```

Claude 会自动调用 MCP tool，返回审计报告。

---

### AtomCode

**安装：**
```bash
git clone https://github.com/lytssaa/data-cleaning-skill.git ~/.atomcode/skills/data-cleaning
```

**使用：**
方式一 —— 斜杠命令：
```
/data-cleaning
```

方式二 —— 自然语言，AI 自动加载：
```
清洗 dirty_data.csv，把 age 转 int，price 转 float
```

---

### MiMo Code

**安装：**
```bash
git clone https://github.com/lytssaa/data-cleaning-skill.git ~/.config/mimocode/skills/data-cleaning
```

或者在 `mimocode.json` 中配置：
```json
{
  "skills": {
    "paths": ["./skills/"],
    "urls": ["https://github.com/lytssaa/data-cleaning-skill/releases/latest/download/bundle.json"]
  }
}
```

**使用：**
```
清洗 survey_results.xlsx，把 respondent_age 转 int，income 转 float
```

---

### 纯 Python API（所有平台通用）

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
```

## Schema Rules（类型映射）

```python
schema_rules = {
    "age": "int",           # pd.to_numeric(errors='coerce') → Int64
    "salary": "float",      # pd.to_numeric(errors='coerce') → float64
    "name": "str",          # astype("string")
    "join_date": "datetime", # pd.to_datetime(errors='coerce')
}
```

未在 schema_rules 中列出的列保持字符串不变。

## 审计报告示例

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
    "coercion": { "age": {"target_type": "int", "invalid_values_coerced_to_null": 12} },
    "column_drops": { "useless_survey_field": {"reason": "缺失率 > 70%", "missing_rate": 85.3} },
    "imputation": { "city": {"strategy": "constant", "fill_value": "Unknown", "count": 35} },
    "outlier_winsorizing": { "income": {"method": "IQR", "lower_fence": 1500, "upper_fence": 85000} }
  },
  "stage_timings": { "safe_ingest": 0.12, "standardize": 0.35, "type_alignment": 0.08,
                     "missing_trial": 0.05, "outlier_suppression": 0.02 },
  "warnings": []
}
```

## 关键行为保证

| 场景 | 行为 |
| ---- | ---- |
| 工号 "001" | **不会变成 1**——所有列先以字符串读入 |
| 年龄填了 "二十岁" | **不报错**——自动转 NaN，用中位数填充 |
| 薪资列有 200000 异常值 | **不删行**——截断到 IQR 上界 |
| 某列 90% 是空的 | **整列砍掉**——审计报告有记录 |
| 长数字如身份证号 | **不变科学计数法**——全程 dtype=str |

## 依赖

```bash
pip install pandas pyarrow openpyxl

# Claude Desktop MCP 额外需要:
pip install mcp
```

## 运行测试

```bash
python scripts/clean.py
```

内置合成脏数据集测试：全角空格列名、零宽字符、中文数字、极端异常值、90% 缺失列——端到端跑通并打印审计报告。

## 文件结构

```
data-cleaning-skill/
├── README.md                           # 本文件
├── SKILL.md                            # Skill 入口（WorkBuddy/AtomCode/MiMo 共用）
├── scripts/
│   ├── clean.py                        # DataPipelineCleaner 核心管道类
│   ├── profile.py                      # 数据画像工具
│   └── quality_report.py               # 审计报告→Markdown 渲染器
├── adapters/
│   └── claude/
│       ├── server.py                   # Claude Desktop MCP 包装
│       └── README.md
├── references/
│   ├── chinese_text_normalization.md   # 中文文本清洗要点
│   └── cleaning_strategies.md          # 清洗策略详解
└── assets/
    └── clean_config.example.json       # 配置文件示例
```

## License

MIT
