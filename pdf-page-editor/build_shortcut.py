#!/usr/bin/env python3
"""
build_shortcut.py - generates the "PDF Page Editor" Apple Shortcut.

Outputs (in ./build):
  PDF_Page_Editor.unsigned.shortcut   binary plist (what `shortcuts sign` / HubSign take)
  PDF_Page_Editor.unsigned.xml        the same plist as XML, for review / diffing

Run:  python3 build_shortcut.py
Then: python3 sign_shortcut.py        (signs via RoutineHub HubSign, see README)

Design
------
1. Input: Share Sheet PDF (Shortcut Input) or, when run directly, a Files picker.
2. Split the PDF into single-page PDFs (keeps vector content and page size).
3. Menu with exactly three operations: Replace / Remove / Insert After.
4. Numbered multi-select list "Page 1 ... Page N" (original numbering).
   The chosen pages become a key such as ",2,5,9," so membership tests are
   order-independent: pages are always processed in ascending page order
   because the main loop walks the ORIGINAL pages 1..N.
5. Main loop over original pages builds an output list:
     remove  -> selected pages are simply not added
     replace -> selected page is swapped for exactly one scanned page
     insert  -> page is added, then every scanned page (scan order kept)
   Scanning uses the Actions app (Sindre Sorhus) "Scan Documents" action,
   which copies the scanned page images to the clipboard and returns to
   Shortcuts. Before every scan the clipboard is overwritten with a marker
   text, and afterwards only *images* are taken from the clipboard, so a
   cancelled / failed scan can never re-insert stale clipboard content.
6. Make PDF (Append) merges the list, the result is named
   "<original> (edited)", previewed with Quick Look and handed to a
   "Save to Files" dialog (Ask Where to Save). The original is never touched.
"""

from __future__ import annotations

import os
import plistlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shortcutgen import (  # noqa: E402
    CONTAINS, DOES_NOT_HAVE_ANY_VALUE, GREATER_THAN, HAS_ANY_VALUE, IS,
    IS_NOT, LESS_THAN, REPEAT_INDEX, REPEAT_ITEM, SHORTCUT_INPUT, Ref,
    Shortcut, text, var,
)

NAME = "PDF Page Editor"
MAX_SCAN_ATTEMPTS = 5
SCAN_MARKER = "PDF Page Editor: waiting for scanned pages"

OP_REPLACE = "Replace Pages"
OP_REMOVE = "Remove Pages"
OP_INSERT = "Insert After Pages"

# Actions app by Sindre Sorhus (App Store id 1586435171), App Intent
# "ScanDocumentsIntent". Identifier, team ID and descriptor verified from a
# real exported shortcut; behaviour (images -> clipboard, then reopens
# Shortcuts; cancel -> empty clipboard) verified from the app's source.
SCAN_DOCUMENTS = "com.sindresorhus.Actions.ScanDocumentsIntent"
SCAN_DESCRIPTOR = {
    "AppIntentIdentifier": "ScanDocumentsIntent",
    "BundleIdentifier": "com.sindresorhus.Actions",
    "Name": "Actions",
    "TeamIdentifier": "YG56YK5RN5",
}

GLYPH_DOCUMENT = 59725
COLOR_RED = 4282601983


def build() -> dict:
    sc = Shortcut(NAME)

    sc.comment(
        "PDF Page Editor\n"
        "Replace, remove, or insert pages in a PDF.\n"
        "Run it directly to pick a PDF in Files, or share a PDF to it.\n"
        "Scanning uses the free “Actions” app by Sindre Sorhus "
        "(Scan Documents). The original PDF is never overwritten."
    )

    # ------------------------------------------------------------------ 1. input
    with sc.if_(SHORTCUT_INPUT, HAS_ANY_VALUE) as b:
        first = sc.action("getitemfromlist", {"WFInput": SHORTCUT_INPUT,
                                               "WFItemSpecifier": "First Item"},
                          out="Item from List")
        sc.set_var("SourcePDF", first)
        b.otherwise()
        picked = sc.action("file.select", {"SelectMultiple": False}, out="File")
        sc.set_var("SourcePDF", picked)
    source = var("SourcePDF")

    with sc.if_(source, DOES_NOT_HAVE_ANY_VALUE):
        sc.alert("No PDF was selected.", "PDF Page Editor")
        sc.stop()

    ext = sc.action("properties.files", {"WFInput": source,
                                          "WFContentItemPropertyName": "File Extension"},
                    out="File Extension")
    ext_lower = sc.action("text.changecase", {"WFCaseType": "lowercase", "text": ext},
                          out="Updated Text")
    with sc.if_(ext_lower, IS_NOT, "pdf"):
        sc.alert(text("The selected file (.", ext, ") does not look like a PDF. "
                      "Continue anyway?"), "PDF Page Editor", cancel=True)

    base_name = sc.action("properties.files", {"WFInput": source,
                                                "WFContentItemPropertyName": "Name"},
                          out="Name")
    clean_name = sc.action("text.replace", {"WFInput": text(base_name),
                                             "WFReplaceTextFind": r"\.pdf$",
                                             "WFReplaceTextReplace": "",
                                             "WFReplaceTextRegularExpression": True,
                                             "WFReplaceTextCaseSensitive": False},
                           out="Updated Text")
    sc.set_var("BaseName", clean_name)

    # ------------------------------------------------------- 2. split + count
    pages = sc.action("splitpdf", {"WFInput": source}, out="PDF Pages")
    sc.set_var("Pages", pages)
    page_count = sc.action("count", {"Input": var("Pages"), "WFCountType": "Items"}, out="Count")
    sc.set_var("PageCount", page_count)

    with sc.if_(var("PageCount"), LESS_THAN, 1):
        sc.alert("No pages could be read from this PDF. It may be damaged or "
                 "password-protected.", "PDF Page Editor")
        sc.stop()

    # ------------------------------------------------------------ 3. operation
    with sc.menu(text("“", var("BaseName"), "” has ", var("PageCount"),
                      " page(s). What do you want to do?"),
                 [OP_REPLACE, OP_REMOVE, OP_INSERT]) as m:
        m.case(OP_REPLACE)
        sc.set_var("Operation", sc.text("replace", out="Operation"))
        sc.set_var("SelectPrompt", sc.text("Select the page(s) to replace with a scan",
                                           out="Prompt"))
        m.case(OP_REMOVE)
        sc.set_var("Operation", sc.text("remove", out="Operation"))
        sc.set_var("SelectPrompt", sc.text("Select the page(s) to remove", out="Prompt"))
        m.case(OP_INSERT)
        sc.set_var("Operation", sc.text("insert", out="Operation"))
        sc.set_var("SelectPrompt", sc.text("Select the page(s) to insert scans after",
                                           out="Prompt"))
    op = var("Operation")

    # ---------------------------------------------------------- 4. page list
    with sc.repeat_count(var("PageCount")) as labels:
        sc.text("Page ", REPEAT_INDEX, out="Page Label")
    chosen = sc.action("choosefromlist", {"WFInput": labels,
                                           "WFChooseFromListActionPrompt": text(var("SelectPrompt")),
                                           "WFChooseFromListActionSelectMultiple": True,
                                           "WFChooseFromListActionSelectAll": False},
                       out="Chosen Item")
    sc.set_var("Chosen", chosen)
    chosen_count = sc.action("count", {"Input": var("Chosen"), "WFCountType": "Items"},
                             out="Count")
    sc.set_var("SelectedCount", chosen_count)

    with sc.if_(var("SelectedCount"), LESS_THAN, 1):
        sc.alert("No pages were selected, so nothing was changed.", "PDF Page Editor")
        sc.stop()

    joined = sc.action("text.combine", {"text": var("Chosen"),
                                         "WFTextSeparator": "Custom",
                                         "WFTextCustomSeparator": ","},
                       out="Combined Text")
    numbers = sc.action("text.replace", {"WFInput": text(joined),
                                          "WFReplaceTextFind": "Page ",
                                          "WFReplaceTextReplace": "",
                                          "WFReplaceTextRegularExpression": False,
                                          "WFReplaceTextCaseSensitive": True},
                        out="Updated Text")
    sc.set_var("SelectedNumbers", numbers)
    sc.set_var("SelectedKey", sc.text(",", var("SelectedNumbers"), ",", out="Selected Key"))

    with sc.if_(op, IS, "remove"):
        remaining = sc.action("math", {"WFInput": var("PageCount"),
                                        "WFMathOperation": "-",
                                        "WFMathOperand": var("SelectedCount")},
                              out="Calculation Result")
        with sc.if_(remaining, LESS_THAN, 1):
            sc.alert("You selected every page. Removing all pages would produce "
                     "an empty PDF, so nothing was changed.", "PDF Page Editor")
            sc.stop()

    # ------------------------------------------------------------ 5. main loop
    with sc.repeat_each(var("Pages")):
        sc.set_var("PageNumber", REPEAT_INDEX)
        sc.set_var("PageItem", REPEAT_ITEM)
        page_key = text(",", var("PageNumber"), ",")

        with sc.if_(var("SelectedKey"), CONTAINS, page_key) as sel:
            # ---- selected page
            with sc.if_(op, IS, "remove") as is_remove:
                sc.comment("Remove: the page is not added to the output.")
                is_remove.otherwise()
                with sc.if_(op, IS, "insert"):
                    sc.add_to_var("Output", var("PageItem"))
                scan_page(sc, op)
            sel.otherwise()
            # ---- untouched page
            sc.add_to_var("Output", var("PageItem"))

    # ---------------------------------------------------------------- 6. output
    final_pdf = sc.action("makepdf", {"WFInput": var("Output"),
                                       "WFPDFIncludeMargin": False,
                                       "WFPDFDocumentMergeBehavior": "Append",
                                       "WFPDFIncludedPages": "All Pages"},
                          out="PDF")
    renamed = sc.action("setitemname", {"WFInput": final_pdf,
                                         "WFName": text(var("BaseName"), " (edited)"),
                                         "WFDontIncludeFileExtension": False},
                        out="Renamed Item")
    sc.set_var("EditedPDF", renamed)

    result_pages = sc.action("splitpdf", {"WFInput": var("EditedPDF")}, out="PDF Pages")
    result_count = sc.action("count", {"Input": result_pages, "WFCountType": "Items"},
                             out="Count")
    sc.alert(text("Original: ", var("PageCount"), " page(s)\n"
                  "Result: ", result_count, " page(s)\n\n"
                  "Next you will see a preview, then choose where to save the new PDF. "
                  "The original file is not changed."),
             "PDF Page Editor")
    sc.action("previewdocument", {"WFInput": var("EditedPDF")})
    sc.action("documentpicker.save", {"WFInput": var("EditedPDF"),
                                       "WFAskWhereToSave": True,
                                       "WFSaveFileOverwrite": False},
              out="Saved File")

    return sc.to_plist(input_classes=["WFPDFContentItem"],
                       workflow_types=["ActionExtension"],
                       glyph=GLYPH_DOCUMENT, color=COLOR_RED)


def scan_page(sc: Shortcut, op: Ref) -> None:
    """Scan with retries. Sets ScanStatus to yes / skip / no and, on success,
    appends the scanned page(s) as one PDF to Output. For 'replace' with a
    failed or skipped scan the original page is kept (never dropped)."""
    sc.set_var("ScanStatus", sc.text("no", out="Scan Status"))

    with sc.repeat_count(MAX_SCAN_ATTEMPTS):
        with sc.if_(var("ScanStatus"), IS, "no"):
            # Marker text: if the scanner fails or is cancelled the clipboard
            # holds no images, so stale clipboard images can never be used.
            sc.action("setclipboard", {"WFInput": sc.text(SCAN_MARKER, out="Marker")})
            with sc.if_(op, IS, "replace") as is_replace:
                sc.alert(text("Scan replacement for page ", var("PageNumber"),
                              "\n\nScan exactly one page, then tap Save."),
                         "PDF Page Editor")
                is_replace.otherwise()
                sc.alert(text("Scan pages to insert after page ", var("PageNumber"),
                              "\n\nYou can scan one or more pages; they are inserted "
                              "in scan order. Tap Save when done."),
                         "PDF Page Editor")
            sc.action(SCAN_DOCUMENTS, {"AppIntentDescriptor": dict(SCAN_DESCRIPTOR)},
                      out="Scan Documents")
            sc.action("waittoreturn")
            clip = sc.action("getclipboard", out="Clipboard")
            images = sc.action("detect.images", {"WFInput": clip}, out="Images")
            sc.set_var("ScanImages", images)
            img_count = sc.action("count", {"Input": var("ScanImages"), "WFCountType": "Items"},
                                  out="Count")
            sc.set_var("ScanCount", img_count)

            with sc.if_(var("ScanCount"), GREATER_THAN, 0) as got:
                with sc.if_(op, IS, "replace") as rep:
                    with sc.if_(var("ScanCount"), GREATER_THAN, 1) as many:
                        with sc.menu(text("You scanned ", var("ScanCount"),
                                          " pages, but page ", var("PageNumber"),
                                          " can only be replaced by one page."),
                                     ["Use First Scanned Page", "Scan Again"]) as mm:
                            mm.case("Use First Scanned Page")
                            first_img = sc.action("getitemfromlist",
                                                  {"WFInput": var("ScanImages"),
                                                   "WFItemSpecifier": "First Item"},
                                                  out="Item from List")
                            sc.set_var("ScanImages", first_img)
                            sc.set_var("ScanStatus", sc.text("yes", out="Scan Status"))
                            mm.case("Scan Again")
                            sc.comment("Loop again: ScanStatus is still “no”.")
                        many.otherwise()
                        sc.set_var("ScanStatus", sc.text("yes", out="Scan Status"))
                    rep.otherwise()
                    sc.set_var("ScanStatus", sc.text("yes", out="Scan Status"))
                got.otherwise()
                with sc.menu(text("No scanned pages were received for page ",
                                  var("PageNumber"), ". The scan may have been "
                                  "cancelled or failed."),
                             ["Try Again", "Skip This Page", "Cancel Editing"]) as fm:
                    fm.case("Try Again")
                    sc.comment("Loop again: ScanStatus is still “no”.")
                    fm.case("Skip This Page")
                    sc.set_var("ScanStatus", sc.text("skip", out="Scan Status"))
                    fm.case("Cancel Editing")
                    sc.alert("Editing cancelled. No file was saved.", "PDF Page Editor")
                    sc.stop()

    with sc.if_(var("ScanStatus"), IS, "yes") as ok:
        scan_pdf = sc.action("makepdf", {"WFInput": var("ScanImages"),
                                          "WFPDFIncludeMargin": False,
                                          "WFPDFDocumentMergeBehavior": "Append",
                                          "WFPDFIncludedPages": "All Pages"},
                             out="PDF")
        sc.add_to_var("Output", scan_pdf)
        ok.otherwise()
        with sc.if_(op, IS, "replace"):
            sc.alert(text("Page ", var("PageNumber"), " was not replaced; the original "
                          "page is kept."), "PDF Page Editor")
            sc.add_to_var("Output", var("PageItem"))


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, "build")
    os.makedirs(out_dir, exist_ok=True)
    plist = build()
    with open(os.path.join(out_dir, "PDF_Page_Editor.unsigned.shortcut"), "wb") as f:
        plistlib.dump(plist, f, fmt=plistlib.FMT_BINARY, sort_keys=True)
    with open(os.path.join(out_dir, "PDF_Page_Editor.unsigned.xml"), "wb") as f:
        plistlib.dump(plist, f, fmt=plistlib.FMT_XML, sort_keys=True)
    print(f"{len(plist['WFWorkflowActions'])} actions -> {out_dir}")


if __name__ == "__main__":
    main()
