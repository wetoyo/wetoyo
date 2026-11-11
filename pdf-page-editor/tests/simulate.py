#!/usr/bin/env python3
"""
simulate.py - executes the GENERATED shortcut plist (not the Python source)
with a small Shortcuts interpreter, real PDF files (pypdf) and scripted user
interaction / scanner results, then checks the saved PDF.

What this proves: the serialized control flow, variable plumbing, page
selection logic, scan retry/duplicate protection and page ordering are
correct for every scenario below.

What it does NOT prove: how iOS itself implements the built-in actions
(PDFKit page handling, Make PDF image sizing, clipboard timing). Those
semantics are modelled here as documented by Apple / observed in Shortcuts.

Usage: python3 tests/simulate.py [path/to/PDF_Page_Editor.(unsigned.shortcut|plist)]
"""

from __future__ import annotations

import io
import os
import plistlib
import random
import re
import sys
import traceback
from dataclasses import dataclass, field

from pypdf import PdfReader, PdfWriter
from PIL import Image as PILImage, ImageDraw
from reportlab.lib.pagesizes import A4, landscape, letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PLIST = os.path.join(HERE, "..", "build", "PDF_Page_Editor.unsigned.shortcut")
SCAN_ID = "com.sindresorhus.Actions.ScanDocumentsIntent"

# --------------------------------------------------------------------------
# Content model
# --------------------------------------------------------------------------


@dataclass
class PdfDoc:
    pages: list            # pypdf PageObjects
    name: str = "PDF.pdf"

    def __str__(self):
        return self.name


@dataclass
class Img:
    label: str
    size: tuple = (1240, 1754)
    name: str = "Scan.png"

    def __str__(self):
        return self.name


class Stop(Exception):
    pass


def fmt(item) -> str:
    if isinstance(item, bool):
        return "Yes" if item else "No"
    if isinstance(item, float) and item.is_integer():
        return str(int(item))
    return str(item)


def as_text(coll: list) -> str:
    return "\n".join(fmt(x) for x in coll)


# --------------------------------------------------------------------------
# Test fixtures
# --------------------------------------------------------------------------

def page_size(n: int):
    if n == 7:
        return (300, 500)                 # odd custom size
    return A4 if n % 2 else landscape(letter)  # mixed orientation


def make_source_pdf(n_pages: int = 12, name: str = "Contract.pdf") -> PdfDoc:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for n in range(1, n_pages + 1):
        w, h = page_size(n)
        c.setPageSize((w, h))
        c.rect(20, 20, w - 40, h - 40)            # vector content
        c.setFont("Helvetica", 18)
        c.drawString(40, h - 60, f"ORIG-{n:02d}")
        c.showPage()
    c.save()
    reader = PdfReader(io.BytesIO(buf.getvalue()))
    return PdfDoc(list(reader.pages), name)


def scan(label: str) -> Img:
    return Img(label)


def image_to_pdf_page(img: Img):
    """Model of Make PDF for an image: one page per image, image drawn at its size."""
    pil = PILImage.new("RGB", (img.size[0] // 4, img.size[1] // 4), "white")
    ImageDraw.Draw(pil).text((10, 10), img.label, fill="black")
    w, h = img.size[0] * 72 / 150, img.size[1] * 72 / 150
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.drawImage(ImageReader(pil), 0, 0, w, h)
    c.setFont("Helvetica", 1)
    c.drawString(1, 1, f"SCAN-{img.label}")   # tiny marker for identification
    c.showPage()
    c.save()
    return PdfReader(io.BytesIO(buf.getvalue())).pages[0]


# --------------------------------------------------------------------------
# Scripted environment
# --------------------------------------------------------------------------

@dataclass
class Script:
    share_input: list = field(default_factory=list)   # Shortcut Input
    picked_file: object = None                        # Select File result (None = empty)
    menu_answers: list = field(default_factory=list)  # titles, consumed in order
    list_selection: list = field(default_factory=list)  # e.g. ["Page 5", "Page 2"]
    scans: list = field(default_factory=list)         # [Img,...] | "cancel" | "error"
    confirm_answers: list = field(default_factory=list)  # True = OK, False = Cancel
    clipboard: list = field(default_factory=list)     # initial clipboard

    # recorded
    alerts: list = field(default_factory=list)
    menus: list = field(default_factory=list)
    previewed: list = field(default_factory=list)
    saved: list = field(default_factory=list)
    scans_launched: int = 0


# --------------------------------------------------------------------------
# Interpreter
# --------------------------------------------------------------------------

class Interpreter:
    def __init__(self, plist: dict, script: Script):
        self.actions = plist["WFWorkflowActions"]
        self.s = script
        self.vars: dict[str, list] = {}
        self.outputs: dict[str, list] = {}
        self.clipboard = list(script.clipboard)
        self.loops: list[dict] = []
        self.last_output: list = []
        self._index_blocks()

    # ---- structure
    def _index_blocks(self):
        self.parts: dict[str, list[int]] = {}
        for i, a in enumerate(self.actions):
            p = a["WFWorkflowActionParameters"]
            g = p.get("GroupingIdentifier")
            if g:
                self.parts.setdefault(g, []).append(i)

    def _end_of(self, group):
        return self.parts[group][-1]

    # ---- values
    def resolve(self, att) -> list:
        t = att.get("Type")
        if t == "ActionOutput":
            return list(self.outputs.get(att["OutputUUID"], []))
        if t == "ExtensionInput":
            return list(self.s.share_input)
        if t == "Clipboard":
            return list(self.clipboard)
        if t == "Variable":
            name = att["VariableName"]
            if name in ("Repeat Item", "Repeat Index"):
                frame = self.loops[-1]
                return [frame["items"][frame["i"]]] if name == "Repeat Item" else [frame["i"] + 1]
            return list(self.vars.get(name, []))
        raise NotImplementedError(f"attachment type {t}")

    def val(self, v) -> list:
        """Parameter value -> collection."""
        if isinstance(v, dict) and v.get("WFSerializationType") == "WFTextTokenAttachment":
            return self.resolve(v["Value"])
        if isinstance(v, dict) and v.get("WFSerializationType") == "WFTextTokenString":
            return [self.string(v)]
        if v is None:
            return []
        return [v]

    def string(self, v) -> str:
        if isinstance(v, str):
            return v
        if isinstance(v, dict) and v.get("WFSerializationType") == "WFTextTokenString":
            s = v["Value"]["string"]
            atts = v["Value"].get("attachmentsByRange", {})
            # replace U+FFFC from the end so earlier offsets stay valid
            for key in sorted(atts, key=lambda k: -int(k.strip("{}").split(",")[0])):
                pos = int(key.strip("{}").split(",")[0])
                s = s[:pos] + as_text(self.resolve(atts[key])) + s[pos + 1:]
            return s
        return as_text(self.val(v))

    # ---- conditions
    def condition(self, p) -> bool:
        subject = self.resolve(p["WFInput"]["Variable"]["Value"])
        c = p["WFCondition"]
        if c == 100:
            return any(fmt(x) != "" for x in subject)
        if c == 101:
            return not any(fmt(x) != "" for x in subject)
        if "WFNumberValue" in p:
            a = float(as_text(subject) or 0)
            b = float(p["WFNumberValue"])
            return {0: a < b, 1: a <= b, 2: a > b, 3: a >= b, 4: a == b, 5: a != b}[c]
        a, b = as_text(subject), self.string(p.get("WFConditionalActionString", ""))
        return {4: a == b, 5: a != b, 99: b in a, 999: b not in a,
                8: a.startswith(b), 9: a.endswith(b)}[c]

    # ---- run
    def run(self):
        pc = 0
        try:
            while pc < len(self.actions):
                pc = self.step(pc)
        except Stop:
            return "stopped"
        return "finished"

    def step(self, pc: int) -> int:
        a = self.actions[pc]
        ident = a["WFWorkflowActionIdentifier"]
        p = a["WFWorkflowActionParameters"]
        mode = p.get("WFControlFlowMode")
        short = ident.replace("is.workflow.actions.", "")

        if short == "conditional":
            g = p["GroupingIdentifier"]
            if mode == 0:
                if self.condition(p):
                    return pc + 1
                parts = self.parts[g]
                return (parts[1] + 1) if len(parts) == 3 else parts[-1]
            if mode == 1:                       # reached Otherwise from the true branch
                return self._end_of(g)
            return pc + 1
        if short == "choosefrommenu":
            g = p["GroupingIdentifier"]
            if mode == 0:
                prompt = self.string(p.get("WFMenuPrompt", ""))
                answer = self.s.menu_answers.pop(0)
                self.s.menus.append((prompt, answer))
                assert answer in p["WFMenuItems"], f"menu answer {answer!r} not in {p['WFMenuItems']}"
                for idx in self.parts[g][1:-1]:
                    if self.actions[idx]["WFWorkflowActionParameters"].get("WFMenuItemTitle") == answer:
                        return idx + 1
                raise AssertionError("menu case not found")
            if mode == 1:
                return self._end_of(g)
            return pc + 1
        if short in ("repeat.count", "repeat.each"):
            g = p["GroupingIdentifier"]
            if mode == 0:
                if short == "repeat.count":
                    n = int(float(as_text(self.val(p["WFRepeatCount"])) or 0))
                    items = list(range(1, n + 1))
                else:
                    items = self.val(p["WFInput"])
                if not items:
                    self.outputs[p.get("UUID") or self.actions[self._end_of(g)]["WFWorkflowActionParameters"]["UUID"]] = []
                    return self._end_of(g) + 1
                self.loops.append({"group": g, "items": items, "i": 0, "results": [], "start": pc})
                return pc + 1
            frame = self.loops[-1]
            assert frame["group"] == g
            frame["results"].extend(self.last_output)
            frame["i"] += 1
            if frame["i"] < len(frame["items"]):
                return frame["start"] + 1
            self.loops.pop()
            self.outputs[p["UUID"]] = frame["results"]
            self.last_output = frame["results"]
            return pc + 1

        out = self.execute(short, ident, p)
        if out is not None:
            self.last_output = out
            if "UUID" in p:
                self.outputs[p["UUID"]] = out
        return pc + 1

    def execute(self, short, ident, p):
        s = self.s
        if short == "comment" or short == "waittoreturn":
            return None
        if short == "exit":
            raise Stop()
        if short == "setvariable":
            self.vars[p["WFVariableName"]] = self.val(p["WFInput"])
            return None
        if short == "appendvariable":
            self.vars.setdefault(p["WFVariableName"], []).extend(self.val(p["WFInput"]))
            return None
        if short == "getitemfromlist":
            coll = self.val(p["WFInput"])
            assert p["WFItemSpecifier"] == "First Item"
            return coll[:1]
        if short == "file.select":
            if s.picked_file is None:
                return []
            return [s.picked_file]
        if short == "alert":
            msg = self.string(p.get("WFAlertActionMessage", ""))
            s.alerts.append(msg)
            if p.get("WFAlertActionCancelButtonShown"):
                ok = s.confirm_answers.pop(0) if s.confirm_answers else True
                if not ok:
                    raise Stop()
            return None
        if short == "properties.files":
            item = self.val(p["WFInput"])[0]
            name = item.name
            stem, _, ext = name.rpartition(".")
            prop = p["WFContentItemPropertyName"]
            return [ext if prop == "File Extension" else (stem or name)]
        if short == "text.changecase":
            assert p["WFCaseType"] == "lowercase"
            return [self.string(p["text"]).lower()]
        if short == "text.replace":
            subject = self.string(p["WFInput"])
            find, repl = p["WFReplaceTextFind"], p.get("WFReplaceTextReplace", "")
            flags = 0 if p.get("WFReplaceTextCaseSensitive", True) else re.IGNORECASE
            if not p.get("WFReplaceTextRegularExpression"):
                find = re.escape(find)
            return [re.sub(find, repl.replace("\\", "\\\\"), subject, flags=flags)]
        if short == "text.combine":
            assert p["WFTextSeparator"] == "Custom"
            return [p["WFTextCustomSeparator"].join(fmt(x) for x in self.val(p["text"]))]
        if short == "gettext":
            return [self.string(p["WFTextActionText"])]
        if short == "splitpdf":
            out = []
            for doc in self.val(p["WFInput"]):
                assert isinstance(doc, PdfDoc), f"Split PDF got {type(doc)}"
                base = doc.name.rsplit(".", 1)[0]
                out += [PdfDoc([pg], f"{base} page {i}.pdf") for i, pg in enumerate(doc.pages, 1)]
            return out
        if short == "count":
            assert p["WFCountType"] == "Items"
            return [len(self.val(p["Input"]))]
        if short == "choosefromlist":
            items = self.val(p["WFInput"])
            labels = [fmt(x) for x in items]
            assert p.get("WFChooseFromListActionSelectMultiple") is True
            for sel in s.list_selection:
                assert sel in labels, f"{sel} not offered in {labels}"
            # Shortcuts returns the chosen items; order deliberately left as the
            # user tapped them to prove the shortcut does not depend on it.
            return [items[labels.index(sel)] for sel in s.list_selection]
        if short == "math":
            a = float(as_text(self.val(p["WFInput"])))
            b = float(as_text(self.val(p["WFMathOperand"])))
            op = p.get("WFMathOperation", "+")
            return [{"+": a + b, "-": a - b, "×": a * b, "÷": a / b}[op]]
        if short == "setclipboard":
            self.clipboard = self.val(p["WFInput"])
            return None
        if short == "getclipboard":
            return list(self.clipboard)
        if ident == SCAN_ID:
            d = p["AppIntentDescriptor"]
            assert d["BundleIdentifier"] == "com.sindresorhus.Actions"
            s.scans_launched += 1
            result = s.scans.pop(0)
            if result == "cancel":
                self.clipboard = []          # app sets pasteboard.images = []
            elif result == "error":
                pass                         # app shows error, clipboard untouched
            else:
                self.clipboard = list(result)
            return []
        if short == "detect.images":
            return [x for x in self.val(p["WFInput"]) if isinstance(x, Img)]
        if short == "makepdf":
            assert p["WFPDFDocumentMergeBehavior"] == "Append"
            pages = []
            for item in self.val(p["WFInput"]):
                if isinstance(item, PdfDoc):
                    pages += item.pages
                elif isinstance(item, Img):
                    pages.append(image_to_pdf_page(item))
                else:
                    raise AssertionError(f"Make PDF got {type(item)}")
            return [PdfDoc(pages, "PDF.pdf")] if pages else []
        if short == "setitemname":
            doc = self.val(p["WFInput"])[0]
            name = self.string(p["WFName"])
            ext = doc.name.rsplit(".", 1)[-1]
            return [PdfDoc(doc.pages, f"{name}.{ext}")]
        if short == "previewdocument":
            s.previewed += self.val(p["WFInput"])
            return None
        if short == "documentpicker.save":
            assert p["WFAskWhereToSave"] is True and p.get("WFSaveFileOverwrite") is False
            files = self.val(p["WFInput"])
            s.saved += files
            return files
        raise NotImplementedError(f"simulator has no model for {ident}")


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def page_labels(doc: PdfDoc) -> list[str]:
    """Round-trip through a real PDF file and identify each page."""
    w = PdfWriter()
    for pg in doc.pages:
        w.add_page(pg)
    buf = io.BytesIO()
    w.write(buf)
    reader = PdfReader(io.BytesIO(buf.getvalue()))
    labels = []
    for pg in reader.pages:
        t = pg.extract_text() or ""
        m = re.search(r"ORIG-(\d+)", t) or re.search(r"SCAN-(\S+)", t)
        assert m, f"unidentifiable page: {t!r}"
        labels.append(m.group(1) if m.group(0).startswith("ORIG") else "S:" + m.group(1))
    return [str(int(x)) if x.isdigit() else x for x in labels]


def assert_originals_untouched(result: PdfDoc, source: PdfDoc):
    for pg in result.pages:
        text = pg.extract_text() or ""
        m = re.search(r"ORIG-(\d+)", text)
        if not m:
            continue
        src = source.pages[int(m.group(1)) - 1]
        assert pg.mediabox == src.mediabox, "page size changed"
        assert pg.get_contents().get_data() == src.get_contents().get_data(), "content stream changed"
        res = pg.get("/Resources") or {}
        assert "/XObject" not in res, "original page was rasterized"


def run(plist, script) -> tuple[str, Script]:
    status = Interpreter(plist, script).run()
    return status, script


SCENARIOS = []


def scenario(fn):
    SCENARIOS.append(fn)
    return fn


def picker(**kw) -> Script:
    return Script(picked_file=make_source_pdf(), **kw)


@scenario
def acceptance_replace_2_5_9(plist):
    """Replace pages 2, 5, 9 of a 12-page PDF -> 12 pages."""
    s = picker(menu_answers=["Replace Pages"], list_selection=["Page 2", "Page 5", "Page 9"],
               scans=[[scan("A")], [scan("B")], [scan("C")]])
    st, s = run(plist, s)
    assert st == "finished" and len(s.saved) == 1
    out = s.saved[0]
    assert page_labels(out) == ["1", "S:A", "3", "4", "S:B", "6", "7", "8", "S:C", "10", "11", "12"]
    assert_originals_untouched(out, s.picked_file)
    assert out.name == "Contract (edited).pdf"
    assert [a for a in s.alerts if a.startswith("Scan replacement")] == [
        f"Scan replacement for page {n}\n\nScan exactly one page, then tap Save." for n in (2, 5, 9)]
    assert "Result: 12 page(s)" in s.alerts[-1]
    assert s.previewed == s.saved
    return f"{len(out.pages)} pages: {page_labels(out)}"


@scenario
def acceptance_remove_2_5_9_share_sheet(plist):
    """Remove pages 2, 5, 9 (PDF shared via Share Sheet) -> 9 pages."""
    src = make_source_pdf()
    s = Script(share_input=[src], menu_answers=["Remove Pages"],
               list_selection=["Page 9", "Page 2", "Page 5"])
    st, s = run(plist, s)
    assert st == "finished" and s.scans_launched == 0
    out = s.saved[0]
    assert page_labels(out) == ["1", "3", "4", "6", "7", "8", "10", "11", "12"]
    assert_originals_untouched(out, src)
    return f"{len(out.pages)} pages: {page_labels(out)}"


@scenario
def acceptance_insert_one_after_2_5_9(plist):
    """Insert one scanned page after pages 2, 5, 9 -> 15 pages."""
    s = picker(menu_answers=["Insert After Pages"], list_selection=["Page 5", "Page 9", "Page 2"],
               scans=[[scan("A")], [scan("B")], [scan("C")]])
    st, s = run(plist, s)
    out = s.saved[0]
    assert page_labels(out) == ["1", "2", "S:A", "3", "4", "5", "S:B", "6", "7", "8", "9", "S:C",
                                "10", "11", "12"]
    assert_originals_untouched(out, s.picked_file)
    assert [a.split("\n")[0] for a in s.alerts if a.startswith("Scan pages")] == [
        f"Scan pages to insert after page {n}" for n in (2, 5, 9)]
    return f"{len(out.pages)} pages: {page_labels(out)}"


@scenario
def acceptance_insert_multiple_after_one(plist):
    """Insert three scanned pages after page 4 -> 15 pages, scan order kept."""
    s = picker(menu_answers=["Insert After Pages"], list_selection=["Page 4"],
               scans=[[scan("X1"), scan("X2"), scan("X3")]])
    st, s = run(plist, s)
    out = s.saved[0]
    assert page_labels(out) == ["1", "2", "3", "4", "S:X1", "S:X2", "S:X3", "5", "6", "7", "8",
                                "9", "10", "11", "12"]
    return f"{len(out.pages)} pages: {page_labels(out)}"


@scenario
def insert_after_final_page(plist):
    s = picker(menu_answers=["Insert After Pages"], list_selection=["Page 12"],
               scans=[[scan("END1"), scan("END2")]])
    run(plist, s)
    labels = page_labels(s.saved[0])
    assert labels[-3:] == ["12", "S:END1", "S:END2"] and len(labels) == 14
    return f"last pages: {labels[-3:]}"


@scenario
def replace_first_and_last(plist):
    s = picker(menu_answers=["Replace Pages"], list_selection=["Page 12", "Page 1"],
               scans=[[scan("F")], [scan("L")]])
    run(plist, s)
    labels = page_labels(s.saved[0])
    assert labels[0] == "S:F" and labels[-1] == "S:L" and len(labels) == 12
    return f"{labels}"


@scenario
def remove_all_pages_blocked(plist):
    s = picker(menu_answers=["Remove Pages"], list_selection=[f"Page {i}" for i in range(1, 13)])
    st, s = run(plist, s)
    assert st == "stopped" and not s.saved and "every page" in s.alerts[-1]
    return "stopped with alert, nothing saved"


@scenario
def remove_all_but_one(plist):
    s = picker(menu_answers=["Remove Pages"], list_selection=[f"Page {i}" for i in range(2, 13)])
    run(plist, s)
    assert page_labels(s.saved[0]) == ["1"]
    return "1 page left"


@scenario
def no_pdf_selected(plist):
    s = Script(picked_file=None)
    st, s = run(plist, s)
    assert st == "stopped" and not s.saved and s.alerts == ["No PDF was selected."]
    return "stopped with alert"


@scenario
def no_pages_selected(plist):
    s = picker(menu_answers=["Replace Pages"], list_selection=[])
    st, s = run(plist, s)
    assert st == "stopped" and not s.saved and s.scans_launched == 0
    return "stopped with alert, scanner never launched"


@scenario
def cancelled_scan_then_retry(plist):
    """Cancel -> Try Again -> success. No duplicate or stale pages."""
    s = picker(menu_answers=["Replace Pages", "Try Again"], list_selection=["Page 3"],
               scans=["cancel", [scan("R")]], clipboard=[scan("STALE")])
    run(plist, s)
    labels = page_labels(s.saved[0])
    assert labels.count("S:R") == 1 and "S:STALE" not in labels and len(labels) == 12
    assert labels[2] == "S:R" and s.scans_launched == 2
    return f"2 scan attempts, page 3 -> {labels[2]}"


@scenario
def failed_scan_with_stale_clipboard_images(plist):
    """Scanner error leaves the clipboard untouched: the marker text guarantees
    old clipboard images are not used; Skip keeps the original page."""
    s = picker(menu_answers=["Replace Pages", "Skip This Page"], list_selection=["Page 6"],
               scans=["error"], clipboard=[scan("STALE1"), scan("STALE2")])
    run(plist, s)
    labels = page_labels(s.saved[0])
    assert labels == [str(i) for i in range(1, 13)]
    assert any("not replaced" in a for a in s.alerts)
    return "original page 6 kept, no stale images"


@scenario
def replace_with_multi_page_scan(plist):
    s = picker(menu_answers=["Replace Pages", "Scan Again", "Use First Scanned Page"],
               list_selection=["Page 5"],
               scans=[[scan("M1"), scan("M2")], [scan("N1"), scan("N2"), scan("N3")]])
    run(plist, s)
    labels = page_labels(s.saved[0])
    assert labels[4] == "S:N1" and len(labels) == 12
    assert any("can only be replaced by one page" in m[0] for m in s.menus)
    return f"page 5 -> {labels[4]} after 'Scan Again' + 'Use First Scanned Page'"


@scenario
def cancel_editing(plist):
    s = picker(menu_answers=["Insert After Pages", "Cancel Editing"], list_selection=["Page 2"],
               scans=["cancel"])
    st, s = run(plist, s)
    assert st == "stopped" and not s.saved
    return "stopped, nothing saved"


@scenario
def five_failed_attempts(plist):
    s = picker(menu_answers=["Replace Pages"] + ["Try Again"] * 5, list_selection=["Page 8"],
               scans=["cancel"] * 5)
    run(plist, s)
    labels = page_labels(s.saved[0])
    assert labels == [str(i) for i in range(1, 13)] and s.scans_launched == 5
    return "gave up after 5 attempts, original kept"


@scenario
def insert_skipped(plist):
    s = picker(menu_answers=["Insert After Pages", "Skip This Page"],
               list_selection=["Page 2", "Page 3"],
               scans=["cancel", [scan("Z")]])
    run(plist, s)
    labels = page_labels(s.saved[0])
    assert labels == ["1", "2", "3", "S:Z"] + [str(i) for i in range(4, 13)]
    return f"{labels}"


@scenario
def uppercase_extension_no_warning(plist):
    src = make_source_pdf(name="SCAN.PDF")
    s = Script(share_input=[src], menu_answers=["Remove Pages"], list_selection=["Page 1"])
    run(plist, s)
    assert not any("does not look like a PDF" in a for a in s.alerts)
    assert s.saved[0].name == "SCAN (edited).pdf"   # Make PDF output is a new .pdf
    return "no warning; saved as 'SCAN (edited).pdf'"


@scenario
def non_pdf_warning_cancel(plist):
    src = make_source_pdf(name="notes.txt")
    s = Script(share_input=[src], confirm_answers=[False])
    st, s = run(plist, s)
    assert st == "stopped" and "does not look like a PDF" in s.alerts[0]
    return "warned and stopped"


@scenario
def random_selections_fuzz(plist):
    """Random op/selection/scan sizes vs. an independent reference model."""
    rng = random.Random(1234)
    for _ in range(40):
        n = rng.randint(1, 12)
        sel = sorted(rng.sample(range(1, n + 1), rng.randint(1, n)))
        op = rng.choice(["Replace Pages", "Remove Pages", "Insert After Pages"])
        if op == "Remove Pages" and len(sel) == n:
            continue
        scans, expected = [], []
        for p in range(1, n + 1):
            if p not in sel:
                expected.append(str(p))
            elif op == "Replace Pages":
                scans.append([scan(f"r{p}")])
                expected.append(f"S:r{p}")
            elif op == "Insert After Pages":
                k = rng.randint(1, 3)
                imgs = [scan(f"i{p}x{j}") for j in range(k)]
                scans.append(imgs)
                expected += [str(p)] + [f"S:i{p}x{j}" for j in range(k)]
        chosen = [f"Page {p}" for p in sel]
        rng.shuffle(chosen)
        s = Script(picked_file=make_source_pdf(n), menu_answers=[op],
                   list_selection=chosen, scans=scans)
        st, s = run(plist, s)
        assert st == "finished", (op, sel)
        got = page_labels(s.saved[0])
        assert got == expected, (op, sel, got, expected)
    return "40 random cases match the reference model"


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PLIST
    with open(path, "rb") as f:
        plist = plistlib.load(f)
    failed = 0
    for fn in SCENARIOS:
        try:
            detail = fn(plist)
            print(f"PASS  {fn.__name__}: {detail}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(SCENARIOS) - failed}/{len(SCENARIOS)} scenarios passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
