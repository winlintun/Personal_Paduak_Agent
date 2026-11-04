# Report: Correct Myanmar Font Rendering in VocApp GUI

**Project:** English-Chinese-MM-MeaningGUI (VocApp)
**Main file:** `vocapp.py` (PySide6 / Qt)
**Date:** 2026-11-04

---

## 1. Summary

VocApp renders Myanmar text correctly by combining **three layers of defense**:

1. **Font resolution** — picks a real Unicode Myanmar-capable font (never a fallback like Arial).
2. **Font registration** — loads the bundled `.ttf` files from the `fonts/` folder into Qt if no installed Myanmar font exists.
3. **Text normalization** — cleans and NFC-normalizes every Myanmar string before it is shown or saved, and repairs old rows in the database.

All Myanmar fonts in this project (Noto Sans Myanmar, Pyidaungsu) are **Unicode fonts**. The app never uses Zawgyi, so no glyph-to-Unicode remapping is needed.

---

## 2. How the font is chosen (font resolution)

`vocapp.py` lines 57–103:

```python
MYANMAR_FONT_PRIORITY = (
    "Noto Sans Myanmar",
    "Noto Sans Myanmar UI",
    "Myanmar Text",     # built into Windows 8+
    "Padauk",
    "Myanmar3",
    "Pyidaungsu",
)

def resolve_myanmar_font_family():
    installed = set(QFontDatabase.families())
    for family in MYANMAR_FONT_PRIORITY:      # 1) use an installed font first
        if family in installed:
            return family
    font_file = _find_local_noto_font()       # 2) register a bundled copy
    if font_file:
        font_id = QFontDatabase.addApplicationFont(font_file)
        if font_id != -1:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                return families[0]
    return QFont().defaultFamily()            # 3) last resort: system default
```

It is applied once at startup (`vocapp.py` line 1339):

```python
font_family = resolve_myanmar_font_family()
app.setFont(QFont(font_family, 11))
```

Because `QApplication.setFont()` sets the **application-wide** font, every widget (inputs, table tree, quiz window, dialogs) inherits the Myanmar-capable family automatically. Stylesheets in the app only set `font-size`/`font-weight`, so the family survives everywhere.

### Bundled fonts available

```
fonts/
├── Pyidaungsu-2.5.3_Bold.ttf
├── Pyidaungsu-2.5.3_Numbers.ttf
├── Pyidaungsu-2.5.3_Regular.ttf
└── Noto_Sans_Myanmar/
    ├── NotoSansMyanmar-Regular.ttf   (and Bold, Light, Medium…)
    ├── NotoSansMyanmar-VariableFont_wdth,wght.ttf
    └── static/ …
```

---

## 3. How the bundled font is loaded

`_find_local_noto_font()` (lines 67–87) walks the `fonts/` directory recursively and returns the first `.ttf` matching, in order:

1. Name contains **"noto"** + ("sans" AND "regular"), or "normal".
2. Name contains "regular" or "normal" on its own.
3. Any `.ttf` at all.

The returned file is registered with:

```python
QFontDatabase.addApplicationFont(font_file)
```

After registration Qt can shape Myanmar correctly (vowel signs, medials, kinzi, stacked consonants) through its text engine — no extra shaping code is required.

---

## 4. How text itself is corrected (normalization)

`normalize_myanmar_text()` (lines 106–113):

```python
def normalize_myanmar_text(text):
    text = text.strip()
    text = text.replace("\u200b", "")   # zero-width space
    text = text.replace("\u200c", "")   # zero-width non-joiner
    text = text.replace("\u200d", "")   # zero-width joiner
    return unicodedata.normalize("NFC", text)
```

- Invisible zero-width characters are stripped — these are the #1 cause of Myanmar text that "looks broken" (a Gantitive mark floating in the wrong place, etc.).
- NFC normalization converts decomposed Myanmar sequences to their canonical form, which Noto Sans Myanmar / Pyidaungsu expect.
- This function is applied at **every entry point**: search input, save-edit flows, lookup results, table rows.

### One-time database repair

`_migrate_myanmar_text()` (lines 367–382) runs once (guarded by SQLite `user_version`) and rewrites every `myanmar` column value:

```python
for word_id, mm in rows:
    fixed = normalize_myanmar_text(mm) or None
    if mm != fixed:
        conn.execute("UPDATE words SET myanmar=? WHERE id=?", (fixed, word_id))
```

So legacy rows created before the cleanup also render correctly. The console prints `Normalized Myanmar text in N row(s)` when it repairs something.

---

## 5. Other places Myanmar text can break — and current handling

| Place | Risk | Handling |
|---|---|---|
| GUI widgets | Wrong family selected | Fixed — app-wide `QFont` with Myanmar family |
| Old DB rows | Broken sequences stored | Fixed — one-time `_migrate_myanmar_text()` |
| CSV export | Excel shows garbage | Fixed — UTF-8 file with **BOM** (`export_csv`, line 542) |
| Excel export | — | openpyxl always writes UTF-8; column widths widened for Myanmar columns |
| **PDF export** | Myanmar shows as boxes | ⚠️ **Not fully fixed — see issue 6.1** |
| Zawgyi input | Legacy encoding pasted by user | ⚠️ Not converted — see issue 6.2 |

---

## 6. Known issues and recommended fixes

### 6.1 PDF export does not use the Myanmar font (priority: high)

`export_pdf()` (lines 609–628) builds an HTML table for `QTextDocument`, but its CSS only sets `font-size`. `QTextDocument` uses its own default font ("MS Shell Dlg 2" on Windows), which has **no Myanmar glyphs**, so PDF output can show empty boxes even when the GUI is perfect.

**Fix** — inject the resolved family into the stylesheet and document default font:

```python
from PySide6.QtGui import QFont

style = (
    "body{font-family:\'%(mmf)s\';}"
    "table{width:100%%;border-collapse:collapse;font-size:9pt;}"
    ...
) % {"mmf": resolve_myanmar_font_family()}

document = QTextDocument()
document.setDefaultFont(QFont(resolve_myanmar_font_family(), 10))
```

### 6.2 Zawgyi-encoded input is shown garbled (priority: medium)

Users often have old Zawgyi text on their clipboard. Qt cannot render Zawgyi with Unicode fonts and the app currently stores it as-is. Optional fix: detect and convert with `rabbit-convert`:

```python
from rabbit import zaw2uni_unicode  # pip install rabbit-convert
```

Convert on paste/save, then run through `normalize_myanmar_text()`.

### 6.3 Variable font weighting (priority: low / cosmetic)

`NotoSansMyanmar-VariableFont_wdth,wght.ttf` is a variable font. `addApplicationFont` sometimes exposes it as "Noto Sans Myanmar Medium" or with abnormal weights depending on the Qt version. The static `NotoSansMyanmar-Regular.ttf` already in `fonts/Noto_Sans_Myanmar/` is the safer registration target. Either bundle **only the static Regular** (smaller `.exe` package too), or make `_find_local_noto_font()` skip files containing `variablefont` in the name.

### 6.4 Packaged `.exe` can lose the fonts folder (priority: medium, only for PyInstaller builds)

`FONT_DIR` is built from `__file__`, which works for source runs and onefile-extracted builds only if the folder is copied next to the executable. For a robust build, add fonts to `VocabApp.spec` as data files:

```python
datas = [("fonts", "fonts")],
```

and read the path from `sys._MEIPASS` when `_MEIPASS` exists, or keep `--add-data "fonts;fonts"` in the command line.

---

## 7. Quick checklist for a maintainer

1. **Keep the `fonts/` folder** beside `vocapp.py` (or beside the `.exe`). The app needs at least one Unicode Myanmar `.ttf` in it.
2. **Only use Unicode fonts** — Noto Sans Myanmar, Pyidaungsu, Padauk, Myanmar Text / Myanmar3. Never Zawgyi.
3. If Myanmar ever renders wrong:
   - Check the file is NFC-normalized and has no U+200B/200C/200D characters (the app does this itself — the bug is usually in data edited externally, e.g. in Excel).
   - Confirm the resolved family at startup:

     ```python
     print(resolve_myanmar_font_family())
     ```

   - Delete `vocabulary.db` (or bump nothing) isn't needed — the one-time migration already repairs old text; just re-run the app once.
4. Database font rendering rules live in **one place**: `vocapp.py` (constants `FONT_DIR`, `MYANMAR_FONT_PRIORITY`, helpers at lines 57–113). Change fonts there only.

---

## 8. Verdict

Myanmar rendering in the **GUI** is already correct and robust: installed-font priority → bundled-font registration → NFC text normalization → one-time DB migration. The only genuinely broken path is the **PDF export** (missing font family in `QTextDocument`), which is a small, one-line-ish fix described in §6.1. The optional Zawgyi conversion (§6.2) and the variable-font cleanup (§6.3) are quality-of-life improvements.
