#!/usr/bin/env python3
"""
validate_shortcut.py - static checks on a .shortcut plist (unsigned, or the
plist extracted from a signed file).

Checks
  * top-level keys and types required by the Shortcuts importer
  * every action: identifier string + parameter dict, UUID/GroupingIdentifier
    only inside WFWorkflowActionParameters (top-level placement makes the
    importer silently drop blocks)
  * every action identifier is one of the verified identifiers below
  * control flow: start/middle/end modes, proper nesting, one Otherwise per
    If, menu cases == menu items in order, end action carries a UUID
  * UUIDs are unique; every ActionOutput reference points at an action that
    appears EARLIER in the list
  * every named variable is written (Set/Add to Variable) before it is read
  * Repeat Item / Repeat Index are only used inside a repeat block
  * WFTextTokenString ranges are consistent (UTF-16 offsets of U+FFFC)

Usage: validate_shortcut.py <file.shortcut|file.plist>   (exit 0 = valid)
"""

from __future__ import annotations

import plistlib
import sys

# Identifiers used by PDF Page Editor, each verified against a real exported
# shortcut or the Cherri action catalogue (see README "Verification").
KNOWN_ACTIONS = {
    "is.workflow.actions.comment",
    "is.workflow.actions.conditional",
    "is.workflow.actions.choosefrommenu",
    "is.workflow.actions.repeat.count",
    "is.workflow.actions.repeat.each",
    "is.workflow.actions.getitemfromlist",
    "is.workflow.actions.file.select",
    "is.workflow.actions.setvariable",
    "is.workflow.actions.appendvariable",
    "is.workflow.actions.alert",
    "is.workflow.actions.exit",
    "is.workflow.actions.properties.files",
    "is.workflow.actions.text.changecase",
    "is.workflow.actions.text.replace",
    "is.workflow.actions.text.combine",
    "is.workflow.actions.gettext",
    "is.workflow.actions.splitpdf",
    "is.workflow.actions.count",
    "is.workflow.actions.choosefromlist",
    "is.workflow.actions.math",
    "is.workflow.actions.setclipboard",
    "is.workflow.actions.getclipboard",
    "is.workflow.actions.waittoreturn",
    "is.workflow.actions.detect.images",
    "is.workflow.actions.makepdf",
    "is.workflow.actions.setitemname",
    "is.workflow.actions.previewdocument",
    "is.workflow.actions.documentpicker.save",
    "com.sindresorhus.Actions.ScanDocumentsIntent",
}
CONTROL_FLOW = {
    "is.workflow.actions.conditional": "if",
    "is.workflow.actions.choosefrommenu": "menu",
    "is.workflow.actions.repeat.count": "repeat",
    "is.workflow.actions.repeat.each": "repeat",
}
VARIABLE_WRITERS = {"is.workflow.actions.setvariable", "is.workflow.actions.appendvariable"}
REPEAT_VARS = {"Repeat Item", "Repeat Index"}
REQUIRED_TOP = {
    "WFWorkflowActions": list,
    "WFWorkflowClientVersion": str,
    "WFWorkflowIcon": dict,
    "WFWorkflowImportQuestions": list,
    "WFWorkflowInputContentItemClasses": list,
    "WFWorkflowMinimumClientVersion": int,
    "WFWorkflowTypes": list,
}


def walk(obj, path=""):
    """Yield (path, dict) for every dict nested in obj."""
    if isinstance(obj, dict):
        yield path, obj
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")


def utf16_offsets_of_obj(s: str) -> list[int]:
    out, pos = [], 0
    for ch in s:
        if ch == "￼":
            out.append(pos)
        pos += len(ch.encode("utf-16-le")) // 2
    return out


def validate(plist: dict) -> list[str]:
    errors: list[str] = []
    err = errors.append

    for key, typ in REQUIRED_TOP.items():
        if not isinstance(plist.get(key), typ):
            err(f"top-level {key} missing or not {typ.__name__}")
    actions = plist.get("WFWorkflowActions", [])

    seen_uuids: dict[str, int] = {}
    stack: list[dict] = []
    assigned: set[str] = set()

    for i, a in enumerate(actions):
        where = f"action #{i}"
        if set(a) != {"WFWorkflowActionIdentifier", "WFWorkflowActionParameters"}:
            err(f"{where}: unexpected top-level keys {sorted(a)}")
        ident = a.get("WFWorkflowActionIdentifier")
        params = a.get("WFWorkflowActionParameters")
        if not isinstance(ident, str) or not isinstance(params, dict):
            err(f"{where}: malformed action")
            continue
        where = f"action #{i} ({ident.rsplit('.', 1)[-1]})"
        if ident not in KNOWN_ACTIONS:
            err(f"{where}: unverified action identifier {ident}")

        # ---- references used by this action (checked before it defines anything)
        for path, d in walk(params):
            t = d.get("Type")
            if t == "ActionOutput":
                u = d.get("OutputUUID")
                if u not in seen_uuids:
                    err(f"{where}{path}: ActionOutput {d.get('OutputName')!r} -> unknown/later UUID {u}")
            elif t == "Variable" and "Variable" in d:
                # conditional wrapper {"Type": "Variable", "Variable": <attachment>}
                if set(d) != {"Type", "Variable"}:
                    err(f"{where}{path}: malformed conditional input wrapper")
            elif t == "Variable":
                name = d.get("VariableName")
                if name in REPEAT_VARS:
                    if not any(b["kind"] == "repeat" for b in stack):
                        err(f"{where}{path}: {name} used outside a repeat")
                elif name not in assigned:
                    err(f"{where}{path}: variable {name!r} read before it is set")
            if d.get("WFSerializationType") == "WFTextTokenString":
                v = d.get("Value", {})
                s, att = v.get("string", ""), v.get("attachmentsByRange", {})
                offs = utf16_offsets_of_obj(s)
                keys = sorted(int(k.strip("{}").split(",")[0]) for k in att)
                if offs != keys:
                    err(f"{where}{path}: token ranges {keys} != U+FFFC offsets {offs}")
                for k in att:
                    if not k.endswith(", 1}"):
                        err(f"{where}{path}: bad range key {k}")

        # ---- control flow
        if ident in CONTROL_FLOW:
            mode = params.get("WFControlFlowMode")
            group = params.get("GroupingIdentifier")
            if not group:
                err(f"{where}: control flow action without GroupingIdentifier")
            if mode == 0:
                blk = {"kind": CONTROL_FLOW[ident], "ident": ident, "group": group,
                       "else": False, "cases": [], "items": params.get("WFMenuItems")}
                if blk["kind"] == "menu" and not (isinstance(blk["items"], list) and blk["items"]):
                    err(f"{where}: menu without WFMenuItems")
                if blk["kind"] == "if" and "WFCondition" not in params and "WFConditions" not in params:
                    err(f"{where}: If without condition")
                stack.append(blk)
            elif mode in (1, 2):
                if not stack or stack[-1]["group"] != group or stack[-1]["ident"] != ident:
                    err(f"{where}: mode {mode} does not match innermost open block")
                    continue
                blk = stack[-1]
                if mode == 1:
                    if blk["kind"] == "if":
                        if blk["else"]:
                            err(f"{where}: second Otherwise")
                        blk["else"] = True
                    elif blk["kind"] == "menu":
                        blk["cases"].append(params.get("WFMenuItemTitle"))
                    else:
                        err(f"{where}: middle part in a repeat")
                else:
                    if blk["kind"] == "menu" and blk["cases"] != blk["items"]:
                        err(f"{where}: menu cases {blk['cases']} != items {blk['items']}")
                    if "UUID" not in params:
                        err(f"{where}: end of block has no UUID")
                    stack.pop()
            else:
                err(f"{where}: invalid WFControlFlowMode {mode!r}")

        # ---- definitions made by this action
        u = params.get("UUID")
        if u is not None:
            if u in seen_uuids:
                err(f"{where}: duplicate UUID {u} (also #{seen_uuids[u]})")
            seen_uuids[u] = i
        if ident in VARIABLE_WRITERS:
            name = params.get("WFVariableName")
            if not isinstance(name, str) or not name:
                err(f"{where}: variable writer without WFVariableName")
            else:
                assigned.add(name)
        if ident.startswith("com.") and "AppIntentDescriptor" in params:
            d = params["AppIntentDescriptor"]
            for k in ("AppIntentIdentifier", "BundleIdentifier", "Name", "TeamIdentifier"):
                if not isinstance(d.get(k), str):
                    err(f"{where}: AppIntentDescriptor.{k} missing")

    for blk in stack:
        err(f"unclosed {blk['kind']} block {blk['group']}")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    with open(sys.argv[1], "rb") as f:
        plist = plistlib.load(f)
    errors = validate(plist)
    n = len(plist.get("WFWorkflowActions", []))
    if errors:
        print(f"INVALID ({len(errors)} problem(s), {n} actions):")
        for e in errors:
            print("  -", e)
        return 1
    blocks = sum(1 for a in plist["WFWorkflowActions"]
                 if a["WFWorkflowActionIdentifier"] in CONTROL_FLOW
                 and a["WFWorkflowActionParameters"].get("WFControlFlowMode") == 0)
    print(f"VALID: {n} actions, {blocks} control-flow blocks, all references resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
