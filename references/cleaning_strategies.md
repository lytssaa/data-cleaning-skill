# Cleaning Strategies — Detailed Guide

This document expands on the strategies referenced in `SKILL.md`. Load it when deciding how to handle a specific issue in a dataset.

## 1. Missing Values

### Numeric
- **median** (default): robust to outliers. Use when the distribution is skewed.
- **mean**: use when the distribution is roughly symmetric and you want to preserve variance.
- **zero**: use when zero is a meaningful "absent" value (e.g., count of logins, where null = never logged in).
- **drop**: only when the column is not central to the analysis AND missing rate is low (<5%).

### Categorical
- **mode** (default): most frequent value. Safe for low-cardinality columns.
- **constant** ("UNKNOWN"): explicit marker. Better when the missing rate is high — mode would skew the distribution.

### Datetime
- **ffill** (default): forward fill. Good for time-series where the last known value is a reasonable estimate.
- **bfill**: backward fill. Use when the value is expected to persist forward.
- **drop**: only for sparse datetime columns that aren't used for ordering.

## 2. Duplicates

- **Full-row duplicates** (default `drop`): safe to drop — they represent accidental double-entry.
- **Partial duplicates** (use `duplicate_subset`): e.g., drop rows where `email` is duplicated but keep the first occurrence. Be careful — you may be losing the most-recent record.
- **Keep**: keep all, but flag in the report.

## 3. Text Normalization

| Operation | When to use |
|---|---|
| `strip` + collapse whitespace | Always, unless you have leading-zero IDs. |
| `fullwidth_to_halfwidth` | Always for Chinese data; mixed full/half-width is common from copy-paste. |
| `lower` | Email addresses, free text, tags. |
| `upper` | Country codes, status flags. |
| `title` | Person names (but be careful with Chinese names — title-casing doesn't apply). |
| `keep` | IDs, hash-like strings, anything case-sensitive. |

## 4. Type Coercion

- `int` and `float` use `pd.to_numeric(..., errors='coerce')`. Failed values become `NaN`, which is then handled by the missing-value step.
- `datetime` parses common formats; ambiguous formats (e.g., `01/02/2026`) will be inferred as MM/DD/YYYY (pandas default). For DD/MM/YYYY, use `dayfirst=True` by overriding the script or pre-formatting the column.
- `str` is a fallback when parsing fails repeatedly — the column is kept as string and flagged.

## 5. Outliers

### IQR method (default)
- Cap values outside `[Q1 - k*IQR, Q3 + k*IQR]`. `k=1.5` is standard; `k=3.0` is "extreme" outliers only.
- Pros: non-parametric, robust to distribution shape.
- Cons: clips legitimate extreme values in heavy-tailed distributions.

### Z-score method
- Cap values where `|z| > threshold` (default 3.0).
- Pros: symmetric and well-understood.
- Cons: assumes a roughly normal distribution. Sensitive to the very outliers you're trying to cap.

### `none`
- Skip outlier treatment. Use when downstream analysis needs raw values.

## 6. Order of Operations

The script applies operations in this fixed order:
1. Normalize column names
2. Preserve originals (optional)
3. Clean text
4. Coerce types
5. Drop duplicates
6. Impute missing values
7. Cap outliers
8. Drop all-null rows

This order matters. For example, type coercion happens before imputation so that a string "123" can become a number and then be checked for missing values consistently.
