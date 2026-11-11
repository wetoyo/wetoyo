# PDF Page Editor (Apple Shortcut)

Replace, remove, or insert pages in an existing PDF on iPhone, entirely in
Shortcuts, using the document scanner from the free **Actions** app by Sindre
Sorhus.

**Download:** [`PDF_Page_Editor.shortcut`](PDF_Page_Editor.shortcut) is
Apple-signed ("anyone" mode) and ready to import.

---

## Install

1. Install **Actions** by Sindre Sorhus from the App Store, and open it once.
2. On your iPhone, open `PDF_Page_Editor.shortcut`, for example from Files,
   iCloud Drive, AirDrop or Mail, and tap **Add Shortcut**. If the import
   sheet shows the name `PDF_Page_Editor`, you can rename it to
   *PDF Page Editor*.
3. Optional, but it avoids repeated prompts: in Shortcuts, long-press the shortcut,
   choose **Details**, then **Privacy**, and allow clipboard/paste access.
   The scanner returns its pages through the clipboard.
4. It shows up in the Share Sheet for PDFs automatically.

## Use

| Step | What happens |
|---|---|
| Open | Share a PDF from Files and pick **PDF Page Editor**, *or* run the shortcut directly and pick a PDF in the Files picker. |
| Operation | A menu with exactly **Replace Pages**, **Remove Pages**, **Insert After Pages**. |
| Pages | A numbered multi-select list *Page 1 … Page N* (original numbering). Selection order doesn't matter: pages are always processed in ascending order. |
| Replace | For each selected page *N*: alert **"Scan replacement for page N"**, then the scanner opens. Scan one page and tap Save. |
| Remove | Selected pages are deleted. The scanner is not used. |
| Insert After | For each selected page *N*: alert **"Scan pages to insert after page N"**, then the scanner. Scan one or more pages; they are inserted right after original page *N*, in scan order. |
| Output | A summary (original vs. result page count), then a Quick Look **preview**, then a **Save to Files** dialog. The new file is named `<original> (edited).pdf`. The original is never overwritten. |

Examples for `[1, 2, 3, 4, 5]` with pages 2 and 4 selected:

- **Replace:** `[1, New2, 3, New4, 5]`
- **Remove:** `[1, 3, 5]`
- **Insert After:** `[1, 2, New2, 3, 4, New4, 5]`

### Edge cases handled

| Situation | Behaviour |
|---|---|
| No PDF selected / empty input | Alert, then stop |
| File isn't a `.pdf` | Warning with Continue / Cancel (case-insensitive check) |
| PDF has no readable pages | Alert, then stop |
| No pages selected | Alert, then stop; nothing is saved |
| Every page selected for **Remove** | Blocked: an empty PDF is never produced |
| Scan cancelled or failed | Menu: **Try Again** / **Skip This Page** / **Cancel Editing** (up to 5 attempts) |
| Replace scan skipped or failed 5 times | Original page is kept, with an alert; a page is never silently dropped |
| Replace, but several pages scanned | Menu: **Use First Scanned Page** / **Scan Again** |
| Insert after the last page, replace first or last page | Supported (covered by tests) |
| Stale clipboard content | Before every scan the clipboard is set to a marker text, and afterwards only *images* are taken from it. A failed scan can't re-insert old clipboard images, which prevents duplicate pages. |

## How it works

```
Shortcut Input (Share Sheet)  or  Select File
        |
   Split PDF into Pages  --> list of single-page PDFs (vector, original size)
        |
   Menu (Replace / Remove / Insert After)
        |
   Repeat N: "Page i"  -->  Choose from List (multiple)  -->  key ",2,5,9,"
        |
   Repeat with each original page i:
        not selected  -> add page i
        remove        -> (skip)
        replace       -> scan -> add scanned page   (fallback: add page i)
        insert        -> add page i, then scan -> add scanned pages
        |
   Make PDF (Append)  ->  Set Name "<name> (edited)"  ->  Quick Look  ->  Save (Ask Where)
```

Scanning (per selected page, with up to 5 attempts):
`Set Clipboard <marker>` → `Scan Documents (Actions app)` → `Wait to Return` →
`Get Clipboard` → `Get Images from Input` → `Count` → accept / retry / skip.

Untouched pages are the original single-page PDFs that **Split PDF** produces.
They are merged back with **Make PDF → Append**, so they are not rasterized and
keep their size and orientation. Only scanned pages are images.

## Files

| Path | Purpose |
|---|---|
| `PDF_Page_Editor.shortcut` | **Signed shortcut to import** (AEA1, Apple "anyone" signature) |
| `build_shortcut.py` | Generates the shortcut: the whole workflow, as code |
| `shortcutgen.py` | Small builder library: actions, variables, If/Menu/Repeat blocks, token strings |
| `sign_shortcut.py` | Signs through RoutineHub HubSign (sends only the shortcut definition) |
| `make_all.sh` | Full pipeline: generate → validate → simulate → sign → verify → compare → re-test |
| `tests/validate_shortcut.py` | Static validator for structure, control flow and references |
| `tests/simulate.py` | Runs the *generated plist* on real PDFs with scripted scans and choices |
| `tools/verify_shortcut.c`, `tools/build_verifier.sh` | Independent signature check and payload extraction (libshortcutsign) |
| `tools/compare_payload.py` | Proves the signed payload is identical to what was generated |
| `build/PDF_Page_Editor.unsigned.{shortcut,xml}` | Exact unsigned input that was signed (binary + readable XML) |
| `docs/PDF_Page_Editor.decompiled.cherri` | Readable listing of the signed shortcut, produced by the independent Cherri decompiler (see note below) |
| `TEST_RESULTS.md` | Test log and an explicit list of what could not be verified |

## Reproduce

```bash
python3 build_shortcut.py                     # deterministic: same bytes every run
python3 tests/validate_shortcut.py build/PDF_Page_Editor.unsigned.shortcut
pip install pypdf reportlab pillow            # for the simulator only
python3 tests/simulate.py
python3 sign_shortcut.py                      # -> PDF_Page_Editor.shortcut
tools/build_verifier.sh ~/sv                  # builds ~/sv/verify_shortcut
~/sv/verify_shortcut PDF_Page_Editor.shortcut build/extracted_from_signed.plist
python3 tools/compare_payload.py build/PDF_Page_Editor.unsigned.shortcut build/extracted_from_signed.plist
# or all at once:
VERIFY=~/sv/verify_shortcut ./make_all.sh
```

**Signing on a Mac instead** (no third-party service):

```bash
shortcuts sign --mode anyone \
  --input build/PDF_Page_Editor.unsigned.shortcut --output PDF_Page_Editor.shortcut
```

**About HubSign:** RoutineHub's community service runs Apple's signer on a Mac.
The request contains only the shortcut's XML definition and its name: no
credentials, no PDFs, no personal data. Its output was checked independently
with libshortcutsign. The signature verifies against Apple's certificate
chain: leaf → Apple System Integration CA 4 → Apple Root CA – G3. The
embedded workflow is identical to what was generated, apart from the
signer restamping `WFWorkflowClientVersion` and dropping `WFWorkflowName`.

## Verification sources for action formats

- **Scan Documents:** identifier `com.sindresorhus.Actions.ScanDocumentsIntent`,
  with `AppIntentDescriptor` {`ScanDocumentsIntent`, `com.sindresorhus.Actions`,
  `Actions`, team `YG56YK5RN5`}. Taken from a real exported shortcut that uses it.
  The behaviour comes from the app's source: it has no parameters, opens the app,
  puts scanned images on the clipboard (`UIPasteboard.general.images`) and then
  reopens Shortcuts. On cancel it puts an empty image list on the clipboard; on
  error it leaves the clipboard untouched. The intent's own description tells you
  to add *Wait to Return* and *Get Clipboard*.
- **Built-in actions:** parameter keys were checked against real exports from
  Shortcuts client version 4610: menus, conditionals, repeat, Make PDF,
  Choose from List, Count (`Input`), Combine Text (`text`), Change Case,
  Get Details of Files (`WFInput`), Calculate, Set Name, Save File, Clipboard
  and Select File. They were cross-checked with the Cherri compiler's action
  catalogue.
- **Cherri decompiler note:** in `docs/…decompiled.cherri`, Cherri shows
  `getFileDetail(nil, …)` because its own definition expects `WFFolder`.
  Current Shortcuts exports use `WFInput`, which is what this shortcut uses.
  Cherri also hides the body of the label loop (`Text "Page <Repeat Index>"`)
  and shows *Add to Variable* as `@Output = …`. The XML in `build/` is
  authoritative.
