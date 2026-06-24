# Chinese Text Normalization — Common Gotchas

When cleaning Chinese-language datasets, watch out for these issues:

## 1. Full-width vs. half-width characters

Chinese input methods often produce full-width ASCII characters (e.g., `１`, `Ａ`, `，`). Mixed width is common in user-submitted data.

- The cleaning script auto-converts full-width ASCII to half-width (`U+FF01`–`U+FF5E` -> `U+0021`–`U+007E`) and full-width space (`U+3000`) to half-width space.
- Examples: `１３９００００００００` -> `13900000000`; `Ｈｅｌｌｏ` -> `Hello`.

## 2. Whitespace

- Chinese text may use `　` (ideographic space, U+3000) — the script normalizes this.
- Internal whitespace (e.g., in mobile numbers like `139 0000 0000`) is collapsed to single spaces.
- Trailing/leading whitespace is stripped.

## 3. Phone numbers

Common patterns and their cleaning:

| Raw | After cleaning |
|---|---|
| `+86 139 0000 0000` | `+86 139 0000 0000` (kept as-is; structure preserved) |
| `１３９００００００００` | `13900000000` |
| `139-0000-0000` | `139-0000-0000` (hyphens preserved) |

If the user wants a single canonical format (e.g., E.164), use `type_coercion` with a custom regex — extend `clean.py` rather than relying on the default.

## 4. Names

- Do not `title` Chinese names — they have no concept of capitalization.
- Strip whitespace only. Leave CJK characters untouched.
- Preserve the original in `_original_name` if you do anything beyond stripping.

## 5. Encoding

CSVs from Chinese systems are often `gbk` or `gb18030`, not `utf-8`. The script tries utf-8 → utf-8-sig → gbk → gb18030 in order. If you see `UnicodeDecodeError`, check the source encoding with `file <path>` (macOS/Linux) or open in a hex editor.

## 6. Mixed Chinese / numeric IDs

Order numbers, employee IDs etc. may mix Chinese characters, English, and digits. Treat as string — do NOT coerce to numeric, or the leading characters will be lost.

## 7. Date formats

- `2026年06月24日`, `2026-06-24`, `2026/06/24`, `24/06/2026` all need different parsers.
- `pd.to_datetime` handles ISO-like formats well; for `YYYY年MM月DD日`, the script's default parser may fail. Pre-process with a regex like `(\d{4})年(\d{1,2})月(\d{1,2})日` -> `\1-\2-\3` if you encounter this often.
