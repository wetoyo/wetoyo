"""
shortcutgen.py - a tiny, dependency-free builder for Apple Shortcuts
(.shortcut / WFWorkflow) property lists.

The serialization formats used here were checked against real Shortcuts
exports (client version 4610) and against the action definitions in the
Cherri compiler (github.com/electrikmilk/cherri):

  * every action is {"WFWorkflowActionIdentifier", "WFWorkflowActionParameters"}
  * UUID / GroupingIdentifier always live INSIDE WFWorkflowActionParameters
    (placing them at the top level makes the importer drop whole blocks)
  * control flow = one action identifier per block, with
    WFControlFlowMode 0 (start) / 1 (middle: Otherwise, menu case) / 2 (end),
    all parts sharing one GroupingIdentifier; the end part carries the UUID
    that "If Result" / "Repeat Results" / "Menu Result" outputs refer to
  * variable references are WFTextTokenAttachment (whole value) or
    WFTextTokenString (text with U+FFFC placeholders + attachmentsByRange,
    ranges counted in UTF-16 code units, like NSRange)
"""

from __future__ import annotations

import contextlib
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

ACTION_PREFIX = "is.workflow.actions."
OBJ = "￼"  # object replacement character used for inline variables

# WFCondition codes (Cherri shortcut.go, matches Shortcuts exports)
LESS_THAN = 0
LESS_OR_EQUAL = 1
GREATER_THAN = 2
GREATER_OR_EQUAL = 3
IS = 4
IS_NOT = 5
CONTAINS = 99
DOES_NOT_CONTAIN = 999
HAS_ANY_VALUE = 100
DOES_NOT_HAVE_ANY_VALUE = 101

START, MIDDLE, END = 0, 1, 2


# --------------------------------------------------------------------------
# References (things that can be inserted into a parameter)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Ref:
    """A reference to a value: action output, named variable, or global."""
    attachment: dict

    def as_attachment(self) -> dict:
        return {"Value": dict(self.attachment), "WFSerializationType": "WFTextTokenAttachment"}


def output(uuid: str, name: str) -> Ref:
    return Ref({"Type": "ActionOutput", "OutputUUID": uuid, "OutputName": name})


def var(name: str) -> Ref:
    return Ref({"Type": "Variable", "VariableName": name})


SHORTCUT_INPUT = Ref({"Type": "ExtensionInput"})
CLIPBOARD = Ref({"Type": "Clipboard"})
REPEAT_ITEM = var("Repeat Item")
REPEAT_INDEX = var("Repeat Index")


def utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def text(*parts: str | Ref) -> dict | str:
    """Build a text parameter. Plain strings stay plain; anything containing a
    Ref becomes a WFTextTokenString with attachmentsByRange."""
    if all(isinstance(p, str) for p in parts):
        return "".join(parts)  # type: ignore[arg-type]
    string = ""
    attachments: dict[str, dict] = {}
    for p in parts:
        if isinstance(p, Ref):
            attachments[f"{{{utf16_len(string)}, 1}}"] = dict(p.attachment)
            string += OBJ
        else:
            string += p
    return {
        "Value": {"string": string, "attachmentsByRange": attachments},
        "WFSerializationType": "WFTextTokenString",
    }


def value(v: Any) -> Any:
    """Normalize a parameter value: Ref -> attachment, else unchanged."""
    if isinstance(v, Ref):
        return v.as_attachment()
    return v


# Deterministic UUIDs: uuid5(namespace(shortcut name), running counter), so the
# same source always produces a byte-identical unsigned .shortcut.
_uuid_state = {"ns": _uuid.NAMESPACE_URL, "n": 0}


def reset_uuids(seed: str) -> None:
    _uuid_state["ns"] = _uuid.uuid5(_uuid.NAMESPACE_URL, f"shortcutgen:{seed}")
    _uuid_state["n"] = 0


def new_uuid() -> str:
    _uuid_state["n"] += 1
    return str(_uuid.uuid5(_uuid_state["ns"], str(_uuid_state["n"]))).upper()


# --------------------------------------------------------------------------
# Builder
# --------------------------------------------------------------------------

@dataclass
class _Block:
    kind: str           # "if" | "menu" | "repeat"
    identifier: str     # full action identifier
    group: str          # GroupingIdentifier
    end_uuid: str       # UUID placed on the END action
    items: list[str] = field(default_factory=list)   # menu items
    cases_seen: list[str] = field(default_factory=list)
    has_else: bool = False


class Shortcut:
    def __init__(self, name: str):
        self.name = name
        self.actions: list[dict] = []
        self._stack: list[_Block] = []
        reset_uuids(name)

    # ---- raw ------------------------------------------------------------
    def action(self, identifier: str, params: dict | None = None, *,
               out: str | None = None, uuid: str | None = None) -> Ref | None:
        """Append an action. If `out` is given, the action gets a UUID and an
        output Ref named `out` is returned."""
        # Built-in actions are given without the prefix ("text.replace");
        # third-party App Intents use their full reverse-DNS identifier.
        if not identifier.startswith(("is.workflow.actions.", "com.")):
            identifier = ACTION_PREFIX + identifier
        p = {k: value(v) for k, v in (params or {}).items() if v is not None}
        ref = None
        if out is not None or uuid is not None:
            uuid = uuid or new_uuid()
            p["UUID"] = uuid
            if out is not None:
                ref = output(uuid, out)
        self.actions.append({"WFWorkflowActionIdentifier": identifier,
                             "WFWorkflowActionParameters": p})
        return ref

    # ---- common actions -------------------------------------------------
    def comment(self, s: str) -> None:
        self.action("comment", {"WFCommentActionText": s})

    def set_var(self, name: str, ref: Ref) -> Ref:
        self.action("setvariable", {"WFVariableName": name, "WFInput": ref})
        return var(name)

    def add_to_var(self, name: str, ref: Ref) -> Ref:
        self.action("appendvariable", {"WFVariableName": name, "WFInput": ref})
        return var(name)

    def text(self, *parts: str | Ref, out: str = "Text") -> Ref:
        return self.action("gettext", {"WFTextActionText": text(*parts)}, out=out)  # type: ignore[return-value]

    def number(self, n: int | float, out: str = "Number") -> Ref:
        return self.action("number", {"WFNumberActionNumber": n}, out=out)  # type: ignore[return-value]

    def alert(self, message: str | dict, title: str | dict | None = None, *, cancel: bool = False) -> None:
        self.action("alert", {"WFAlertActionMessage": message,
                              "WFAlertActionTitle": title,
                              "WFAlertActionCancelButtonShown": cancel})

    def stop(self) -> None:
        self.action("exit")

    # ---- control flow ---------------------------------------------------
    def _cond_params(self, subject: Ref, condition: int, operand: Any) -> dict:
        p: dict[str, Any] = {
            "WFCondition": condition,
            "WFInput": {"Type": "Variable", "Variable": subject.as_attachment()},
        }
        if condition in (HAS_ANY_VALUE, DOES_NOT_HAVE_ANY_VALUE):
            return p
        if isinstance(operand, (int, float)) and not isinstance(operand, bool):
            p["WFNumberValue"] = operand
        elif isinstance(operand, Ref):
            p["WFConditionalActionString"] = text(operand)
        else:
            p["WFConditionalActionString"] = operand
        return p

    @contextlib.contextmanager
    def if_(self, subject: Ref, condition: int, operand: Any = None):
        blk = _Block("if", ACTION_PREFIX + "conditional", new_uuid(), new_uuid())
        params = {"GroupingIdentifier": blk.group, "WFControlFlowMode": START}
        params.update(self._cond_params(subject, condition, operand))
        self.actions.append({"WFWorkflowActionIdentifier": blk.identifier,
                             "WFWorkflowActionParameters": params})
        self._stack.append(blk)
        yield _IfHandle(self, blk)
        self._close(blk)

    @contextlib.contextmanager
    def menu(self, prompt: str | dict, items: list[str]):
        blk = _Block("menu", ACTION_PREFIX + "choosefrommenu", new_uuid(), new_uuid(), items=list(items))
        self.actions.append({"WFWorkflowActionIdentifier": blk.identifier,
                             "WFWorkflowActionParameters": {
                                 "GroupingIdentifier": blk.group,
                                 "WFControlFlowMode": START,
                                 "WFMenuPrompt": prompt,
                                 "WFMenuItems": list(items)}})
        self._stack.append(blk)
        yield _MenuHandle(self, blk)
        if blk.cases_seen != blk.items:
            raise ValueError(f"menu cases {blk.cases_seen} != items {blk.items}")
        self._close(blk)

    @contextlib.contextmanager
    def repeat_count(self, count: int | Ref):
        blk = _Block("repeat", ACTION_PREFIX + "repeat.count", new_uuid(), new_uuid())
        self.actions.append({"WFWorkflowActionIdentifier": blk.identifier,
                             "WFWorkflowActionParameters": {
                                 "GroupingIdentifier": blk.group,
                                 "WFControlFlowMode": START,
                                 "WFRepeatCount": value(count)}})
        self._stack.append(blk)
        yield output(blk.end_uuid, "Repeat Results")
        self._close(blk)

    @contextlib.contextmanager
    def repeat_each(self, items: Ref):
        blk = _Block("repeat", ACTION_PREFIX + "repeat.each", new_uuid(), new_uuid())
        self.actions.append({"WFWorkflowActionIdentifier": blk.identifier,
                             "WFWorkflowActionParameters": {
                                 "GroupingIdentifier": blk.group,
                                 "WFControlFlowMode": START,
                                 "WFInput": items.as_attachment()}})
        self._stack.append(blk)
        yield output(blk.end_uuid, "Repeat Results")
        self._close(blk)

    def _close(self, blk: _Block) -> None:
        top = self._stack.pop()
        assert top is blk, "control flow blocks closed out of order"
        self.actions.append({"WFWorkflowActionIdentifier": blk.identifier,
                             "WFWorkflowActionParameters": {
                                 "GroupingIdentifier": blk.group,
                                 "WFControlFlowMode": END,
                                 "UUID": blk.end_uuid}})

    # ---- output ---------------------------------------------------------
    def to_plist(self, *, input_classes: Iterable[str], workflow_types: Iterable[str],
                 glyph: int, color: int) -> dict:
        if self._stack:
            raise ValueError("unclosed control flow block(s)")
        return {
            "WFWorkflowName": self.name,
            "WFWorkflowClientVersion": "4610",
            "WFWorkflowMinimumClientVersion": 900,
            "WFWorkflowMinimumClientVersionString": "900",
            "WFWorkflowHasOutputFallback": False,
            "WFWorkflowHasShortcutInputVariables": True,
            "WFWorkflowIcon": {"WFWorkflowIconStartColor": color,
                               "WFWorkflowIconGlyphNumber": glyph},
            "WFWorkflowImportQuestions": [],
            "WFWorkflowInputContentItemClasses": list(input_classes),
            "WFWorkflowOutputContentItemClasses": [],
            "WFWorkflowTypes": list(workflow_types),
            "WFQuickActionSurfaces": [],
            "WFWorkflowActions": self.actions,
        }


class _IfHandle:
    def __init__(self, sc: Shortcut, blk: _Block):
        self.sc, self.blk = sc, blk
        self.result = output(blk.end_uuid, "If Result")

    def otherwise(self) -> None:
        if self.blk.has_else:
            raise ValueError("duplicate Otherwise")
        if self.sc._stack[-1] is not self.blk:
            raise ValueError("Otherwise emitted inside a nested block")
        self.blk.has_else = True
        self.sc.actions.append({"WFWorkflowActionIdentifier": self.blk.identifier,
                                "WFWorkflowActionParameters": {
                                    "GroupingIdentifier": self.blk.group,
                                    "WFControlFlowMode": MIDDLE}})


class _MenuHandle:
    def __init__(self, sc: Shortcut, blk: _Block):
        self.sc, self.blk = sc, blk
        self.result = output(blk.end_uuid, "Menu Result")

    def case(self, title: str) -> None:
        if self.sc._stack[-1] is not self.blk:
            raise ValueError("menu case emitted inside a nested block")
        expected = self.blk.items[len(self.blk.cases_seen)]
        if title != expected:
            raise ValueError(f"menu case {title!r} out of order (expected {expected!r})")
        self.blk.cases_seen.append(title)
        self.sc.actions.append({"WFWorkflowActionIdentifier": self.blk.identifier,
                                "WFWorkflowActionParameters": {
                                    "GroupingIdentifier": self.blk.group,
                                    "WFControlFlowMode": MIDDLE,
                                    "WFMenuItemTitle": title}})
