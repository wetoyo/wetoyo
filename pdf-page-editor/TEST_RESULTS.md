# Test Results: PDF Page Editor

Run date: 2026-11-11, on Linux (no Mac, no iOS device available), using `make_all.sh`.

| Artifact | SHA-256 |
|---|---|
| `build/PDF_Page_Editor.unsigned.shortcut` (deterministic) | `b394a5deaaf0db3237e9c309d09616a195a8c0d29cfbf8e214a55abbe6282815` |
| `PDF_Page_Editor.shortcut` (signed; Apple's signature embeds a timestamp, so re-signing changes the bytes) | `cb18b6c5b5200066ad109dda5bb0a3dadd6dd8583f7c1e1da4acd8ced2b87a55` |

## 1. Structure: `tests/validate_shortcut.py`

```
VALID: 152 actions, 23 control-flow blocks, all references resolve
```

Passed on both the unsigned file and the plist extracted from the signed
file. It checks the following:

- known action identifiers
- `UUID` and `GroupingIdentifier` only inside the parameters
- If/Otherwise/End, Menu/cases/End and Repeat/End properly nested
- menu cases match menu items in order
- unique UUIDs
- every action-output reference points backwards
- every variable is set before it's read
- Repeat Item and Repeat Index are used only inside loops
- inline-variable offsets are UTF-16-correct

An earlier build failed this check: built-in identifiers that contain a
dot, such as `file.select`, were missing the `is.workflow.actions.` prefix.
The bug was fixed in `shortcutgen.py` before signing.

An independent check: the Cherri decompiler (github.com/electrikmilk/cherri)
reads the file back into the intended program. See
`docs/PDF_Page_Editor.decompiled.cherri`.

## 2. Signature: libshortcutsign, `tools/verify_shortcut`

```
format:    iCloud-signed (anyone)
Subject: /CN=3e7324ea…/O=Apple Inc./C=US
Issuer:  /CN=Apple System Integration CA 4/OU=Apple Certification Authority/O=Apple Inc./C=US
Subject: /CN=Apple System Integration CA 4/…   Issuer: /CN=Apple Root CA - G3/…
Subject: /CN=Apple Root CA - G3/…              Issuer: /CN=Apple Root CA - G3/…
signature: VALID
```

## 3. Payload integrity: `tools/compare_payload.py`

```
  tolerated .WFWorkflowClientVersion: '4610' -> '1505.3'
  tolerated .WFWorkflowName: removed 'PDF Page Editor'
152 actions sent, 152 in signed file: IDENTICAL
```

## 4. Behaviour: `tests/simulate.py` on the signed payload

The simulator runs the actual serialized actions from the signed file. It
uses a real 12-page PDF with mixed page sizes (A4 portrait, Letter landscape,
and a 300×500 pt page 7) that contains vector text and shapes. Scanner
results, menu choices and list selections are scripted. Each output is written
as a real PDF and read back. Every original page in it is checked: it must
have the same MediaBox and byte-identical content stream as the source, and
no image XObjects (meaning it wasn't rasterized).

```
PASS  acceptance_replace_2_5_9: 12 pages: ['1', 'S:A', '3', '4', 'S:B', '6', '7', '8', 'S:C', '10', '11', '12']
PASS  acceptance_remove_2_5_9_share_sheet: 9 pages: ['1', '3', '4', '6', '7', '8', '10', '11', '12']
PASS  acceptance_insert_one_after_2_5_9: 15 pages: ['1', '2', 'S:A', '3', '4', '5', 'S:B', '6', '7', '8', '9', 'S:C', '10', '11', '12']
PASS  acceptance_insert_multiple_after_one: 15 pages: ['1', '2', '3', '4', 'S:X1', 'S:X2', 'S:X3', '5', '6', '7', '8', '9', '10', '11', '12']
PASS  insert_after_final_page: last pages: ['12', 'S:END1', 'S:END2']
PASS  replace_first_and_last: ['S:F', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', 'S:L']
PASS  remove_all_pages_blocked: stopped with alert, nothing saved
PASS  remove_all_but_one: 1 page left
PASS  no_pdf_selected: stopped with alert
PASS  no_pages_selected: stopped with alert, scanner never launched
PASS  cancelled_scan_then_retry: 2 scan attempts, page 3 -> S:R
PASS  failed_scan_with_stale_clipboard_images: original page 6 kept, no stale images
PASS  replace_with_multi_page_scan: page 5 -> S:N1 after 'Scan Again' + 'Use First Scanned Page'
PASS  cancel_editing: stopped, nothing saved
PASS  five_failed_attempts: gave up after 5 attempts, original kept
PASS  insert_skipped: ['1', '2', '3', 'S:Z', '4', '5', '6', '7', '8', '9', '10', '11', '12']
PASS  uppercase_extension_no_warning: no warning; saved as 'SCAN (edited).pdf'
PASS  non_pdf_warning_cancel: warned and stopped
PASS  random_selections_fuzz: 40 random cases match the reference model

19/19 scenarios passed
```

The page selections are fed to the shortcut in shuffled order. This shows the
result doesn't depend on the order you tap pages in.

## 5. NOT verified (no iOS device or Mac was available)

These items are stated explicitly. Please treat your first real run as the
final acceptance test.

1. **Import on iOS 26.** The file has a valid Apple "anyone" signature and the
   same structure as working exports, but it hasn't actually been imported on
   a device.
2. **Apple's implementation of the built-in actions.** The simulator models
   them; it doesn't run Apple's code. These assumptions come from documented
   and observed behaviour:
   - *Split PDF into Pages* keeps vector content and page size.
   - *Make PDF → Append* combines PDFs without rasterizing.
   - *Get Clipboard* returns **all** images the scanner put on the clipboard.
     A multi-page insert depends on this.
3. **Size of scanned pages.** *Make PDF* sizes each page from the scanned
   image. A scanned page may have different dimensions from the page it
   replaces. Original pages are unaffected.
4. **Current App Store version of Actions.** Its scanner behaviour (clipboard
   handoff, automatic return to Shortcuts, empty clipboard on cancel) was read
   from a public copy of the app's source, not from the App Store build. The
   identifier and team ID come from a real exported shortcut. If a future
   version changes the handoff, the scan step will report "No scanned pages
   were received" instead of inserting wrong pages.
5. **Running from the Share Sheet.** Scan Documents opens the Actions app. iOS
   may move a Share Sheet run into the Shortcuts app (a "continue in Shortcuts"
   prompt) before scanning. Remove Pages never leaves the Share Sheet. Very
   large PDFs may hit Share Sheet memory limits; running the shortcut from the
   Shortcuts app avoids that.
6. **Thumbnails** are not shown. Page selection is a numbered list, which the
   spec allows. Thumbnail rendering would have added several actions that
   couldn't be tested here.
7. **Clipboard.** The shortcut overwrites the clipboard (marker text, then the
   scanned images) and doesn't restore it afterwards.
