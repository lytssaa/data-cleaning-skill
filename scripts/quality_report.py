#!/usr/bin/env python
"""
Render a DataPipelineCleaner audit JSON into a human-readable Markdown report.

Usage:
    python quality_report.py <audit.json> [<output.md>] [--lang zh|en]
    # If <output.md> omitted, writes next to the JSON with .md extension.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def render(report: dict, lang: str = "en") -> str:
    """Convert an audit dict into Markdown.

    Args:
        report: The audit dict returned by ``DataPipelineCleaner.execute()``.
        lang: ``"en"`` or ``"zh"``.

    Returns:
        Markdown string.
    """
    if lang == "zh":
        return _render_zh(report)
    return _render_en(report)


def _render_en(r: dict) -> str:
    lines: list[str] = []
    lines.append("# Data Cleaning Audit Report")
    lines.append("")
    lines.append(f"- **Started:**  {r.get('started_at', '-')}")
    lines.append(f"- **Finished:** {r.get('finished_at', '-')}")
    lines.append("")

    # Summary
    original = r.get("original_rows", 0)
    cleaned = r.get("cleaned_rows", 0)
    retention = r.get("retention_rate_pct", 100.0)
    lines.append(f"- **Rows:**  {original} → {cleaned}  (retention {retention}%)")
    lines.append(f"- **Columns dropped:**  {len(r.get('dropped_columns', []))}")
    lines.append(f"- **Rows dropped:**  {r.get('dropped_rows_count', 0)}")
    lines.append(f"- **Missing values fixed:**  {r.get('missing_values_fixed', 0)}")
    lines.append(f"- **Outliers suppressed:**  {r.get('outliers_suppressed', 0)}")
    lines.append("")

    # Phase timings
    timings = r.get("stage_timings", {})
    if timings:
        lines.append("## Pipeline Timings")
        lines.append("")
        lines.append("| Phase | Duration (s) |")
        lines.append("| ----- | ------------ |")
        for phase, sec in timings.items():
            lines.append(f"| `{phase}` | {sec} |")
        lines.append("")

    # Per-column details
    per_col = r.get("per_column", {})

    # Coercion
    coercion = per_col.get("coercion", {})
    if coercion:
        lines.append("## Type Coercion")
        lines.append("")
        lines.append("| Column | Target | Invalid → NaN |")
        lines.append("| ------ | ------ | ------------- |")
        for col, info in coercion.items():
            lines.append(
                f"| `{col}` | `{info.get('target_type', '-')}` | "
                f"{info.get('invalid_values_coerced_to_null', 0)} |"
            )
        lines.append("")

    # Column drops
    drops = per_col.get("column_drops", {})
    if drops:
        lines.append("## Columns Dropped")
        lines.append("")
        lines.append("| Column | Reason | Missing Rate |")
        lines.append("| ------ | ------ | ------------ |")
        for col, info in drops.items():
            lines.append(
                f"| `{col}` | {info.get('reason', '-')} | "
                f"{info.get('missing_rate', '-')}% |"
            )
        lines.append("")

    # Imputation
    imputation = per_col.get("imputation", {})
    if imputation:
        lines.append("## Imputation")
        lines.append("")
        lines.append("| Column | Strategy | Fill Value | Count |")
        lines.append("| ------ | -------- | ---------- | ----- |")
        for col, info in imputation.items():
            lines.append(
                f"| `{col}` | {info.get('strategy', '-')} | "
                f"`{info.get('fill_value', '-')}` | "
                f"{info.get('count', 0)} |"
            )
        lines.append("")

    # Outlier winsorizing
    outliers = per_col.get("outlier_winsorizing", {})
    if outliers:
        lines.append("## Outlier Winsorizing (IQR)")
        lines.append("")
        lines.append(
            "| Column | Lower Fence | Upper Fence | Capped Low | Capped High |"
        )
        lines.append(
            "| ------ | ----------- | ----------- | ---------- | ----------- |"
        )
        for col, info in outliers.items():
            lines.append(
                f"| `{col}` | {info.get('lower_fence', '-')} | "
                f"{info.get('upper_fence', '-')} | "
                f"{info.get('clamped_low', 0)} | "
                f"{info.get('clamped_high', 0)} |"
            )
        lines.append("")

    # Warnings
    warnings = r.get("warnings", [])
    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- ⚠ {w}")
        lines.append("")

    return "\n".join(lines)


def _render_zh(r: dict) -> str:
    lines: list[str] = []
    lines.append("# 数据清洗审计报告")
    lines.append("")
    lines.append(f"- **开始时间:**  {r.get('started_at', '-')}")
    lines.append(f"- **结束时间:**  {r.get('finished_at', '-')}")
    lines.append("")

    original = r.get("original_rows", 0)
    cleaned = r.get("cleaned_rows", 0)
    retention = r.get("retention_rate_pct", 100.0)
    lines.append(f"- **数据行数:**  {original} → {cleaned}  (留存率 {retention}%)")
    lines.append(f"- **剔除列数:**  {len(r.get('dropped_columns', []))}")
    lines.append(f"- **剔除行数:**  {r.get('dropped_rows_count', 0)}")
    lines.append(f"- **修复缺失值:**  {r.get('missing_values_fixed', 0)} 处")
    lines.append(f"- **压制异常值:**  {r.get('outliers_suppressed', 0)} 处")
    lines.append("")

    # Phase timings
    timings = r.get("stage_timings", {})
    phase_zh = {
        "safe_ingest": "安全吞入",
        "standardize": "结构规范化",
        "type_alignment": "类型对齐",
        "missing_trial": "缺失审判",
        "outlier_suppression": "异常压制",
    }
    if timings:
        lines.append("## 管道耗时")
        lines.append("")
        lines.append("| 阶段 | 耗时 (秒) |")
        lines.append("| ---- | --------- |")
        for phase, sec in timings.items():
            label = phase_zh.get(phase, phase)
            lines.append(f"| {label} | {sec} |")
        lines.append("")

    # Per-column details
    per_col = r.get("per_column", {})

    coercion = per_col.get("coercion", {})
    if coercion:
        lines.append("## 类型转换")
        lines.append("")
        lines.append("| 列名 | 目标类型 | 脏数据→空 |")
        lines.append("| ---- | -------- | --------- |")
        for col, info in coercion.items():
            lines.append(
                f"| `{col}` | `{info.get('target_type', '-')}` | "
                f"{info.get('invalid_values_coerced_to_null', 0)} |"
            )
        lines.append("")

    drops = per_col.get("column_drops", {})
    if drops:
        lines.append("## 剔除列")
        lines.append("")
        lines.append("| 列名 | 原因 | 缺失率 |")
        lines.append("| ---- | ---- | ------ |")
        for col, info in drops.items():
            reason = info.get("reason", "-")
            if "missing_rate" in info:
                reason = f"缺失率 {info['missing_rate']}% > 70%"
            lines.append(
                f"| `{col}` | {reason} | "
                f"{info.get('missing_rate', '-')}% |"
            )
        lines.append("")

    imputation = per_col.get("imputation", {})
    if imputation:
        strategy_zh = {
            "median": "中位数填充",
            "constant": "常量填充",
            "forward_fill": "前向填充",
        }
        lines.append("## 缺失值修复")
        lines.append("")
        lines.append("| 列名 | 策略 | 填充值 | 修复数 |")
        lines.append("| ---- | ---- | ------ | ------ |")
        for col, info in imputation.items():
            strat = strategy_zh.get(info.get("strategy", ""), info.get("strategy", "-"))
            lines.append(
                f"| `{col}` | {strat} | "
                f"`{info.get('fill_value', '-')}` | "
                f"{info.get('count', 0)} |"
            )
        lines.append("")

    outliers = per_col.get("outlier_winsorizing", {})
    if outliers:
        lines.append("## 异常值截断 (IQR)")
        lines.append("")
        lines.append("| 列名 | 下界 | 上界 | 压缩低端 | 压缩高端 |")
        lines.append("| ---- | ---- | ---- | -------- | -------- |")
        for col, info in outliers.items():
            lines.append(
                f"| `{col}` | {info.get('lower_fence', '-')} | "
                f"{info.get('upper_fence', '-')} | "
                f"{info.get('clamped_low', 0)} | "
                f"{info.get('clamped_high', 0)} |"
            )
        lines.append("")

    warnings = r.get("warnings", [])
    if warnings:
        lines.append("## 警告")
        lines.append("")
        for w in warnings:
            lines.append(f"- ⚠ {w}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = __import__("argparse").ArgumentParser()
    ap.add_argument("report", type=Path, help="Audit JSON file")
    ap.add_argument("output", type=Path, nargs="?", help="Output Markdown file (optional)")
    ap.add_argument("--lang", choices=["en", "zh"], default="zh")
    args = ap.parse_args()

    if not args.report.exists():
        print(f"Report not found: {args.report}", file=sys.stderr)
        return 1

    report = json.loads(args.report.read_text(encoding="utf-8"))
    md = render(report, lang=args.lang)
    out = args.output or args.report.with_suffix(".md")
    out.write_text(md, encoding="utf-8")
    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
