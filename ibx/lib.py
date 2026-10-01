"""Small helpers for the Claims Analysis & Fraud Detection workbook generator."""
import json

# ---------------------------------------------------------------- palette
BLUE = "#0093D0"      # Independence Blue Cross brand blue (sampled from the logo)
NAVY = "#0A2A4A"
INK = "#10243B"
SKY = "#5FC4E8"
TEAL = "#00A19B"
AMBER = "#F2A93B"
RED = "#D64545"
GREEN = "#2FA66B"
PURPLE = "#7B6CC4"
SLATE = "#9AA9BC"
MUTED = "#5B6B80"
PAPER = "#F3F6FA"
CARD = "#FFFFFF"
CARD_ALT = "#EEF3F9"
RULE = "#DCE4EE"
CATEGORICAL = [BLUE, NAVY, SKY, AMBER, TEAL, PURPLE, RED, SLATE]

INT = {"kind": "number", "formatString": ",d"}
MONEY = {"kind": "number", "formatString": "$,.0f"}
MONEY2 = {"kind": "number", "formatString": "$,.2f"}
MONEY_S = {"kind": "number", "formatString": "$,.3~s"}
PCT1 = {"kind": "number", "formatString": ".1%"}
PCT0 = {"kind": "number", "formatString": ".0%"}
DEC1 = {"kind": "number", "formatString": ",.1f"}
DEC2 = {"kind": "number", "formatString": ",.2f"}
MON = {"kind": "datetime", "formatString": "%b %Y"}
DAY = {"kind": "datetime", "formatString": "%b %d, %Y"}


def col(cid, name, formula, fmt=None, **extra):
    d = {"id": cid, "name": name, "formula": formula}
    if fmt:
        d["format"] = fmt
    d.update(extra)
    return d


# ------------------------------------------------------------- layout XML
class Node:
    """A layout node: leaf element or container with children."""

    def __init__(self, eid, c0, c1, r0, r1, kids=None, tabs=None):
        self.eid, self.c0, self.c1, self.r0, self.r1 = eid, c0, c1, r0, r1
        self.kids = kids or []

    def xml(self, indent="  "):
        pos = 'gridColumn="%d / %d" gridRow="%d / %d"' % (self.c0, self.c1, self.r0, self.r1)
        if not self.kids:
            return '%s<Element elementId="%s" %s/>' % (indent, self.eid, pos)
        span = self.r1 - self.r0
        need = max(k.r1 for k in self.kids) - 1
        assert need <= span, "container %s: children need %d rows, parent grants %d" % (self.eid, need, span)
        for a in self.kids:
            assert a.c1 <= 25 and a.c0 >= 1, a.eid
        inner = "\n".join(k.xml(indent + "  ") for k in self.kids)
        return ('%s<Container elementId="%s" type="grid" %s gridTemplateColumns="repeat(24, 1fr)" '
                'gridTemplateRows="auto">\n%s\n%s</Container>' % (indent, self.eid, pos, inner, indent))

    def ids(self):
        out = [self.eid]
        for k in self.kids:
            out += k.ids()
        return out

    def check_overlap(self):
        ks = self.kids
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                a, b = ks[i], ks[j]
                if a.c0 < b.c1 and b.c0 < a.c1 and a.r0 < b.r1 and b.r0 < a.r1:
                    raise AssertionError("overlap in %s: %s vs %s" % (self.eid, a.eid, b.eid))
        for k in ks:
            k.check_overlap()


def el(eid, c0, c1, r0, r1):
    return Node(eid, c0, c1, r0, r1)


def box(eid, c0, c1, r0, r1, kids):
    return Node(eid, c0, c1, r0, r1, kids)


def page_xml(pid, nodes):
    for n in nodes:
        n.check_overlap()
    top = type("Top", (), {"kids": nodes})
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            a, b = nodes[i], nodes[j]
            if a.c0 < b.c1 and b.c0 < a.c1 and a.r0 < b.r1 and b.r0 < a.r1:
                raise AssertionError("page %s overlap: %s vs %s" % (pid, a.eid, b.eid))
    body = "\n".join(n.xml() for n in nodes)
    return ('<Page type="grid" gridTemplateColumns="repeat(24, 1fr)" gridTemplateRows="auto" id="%s">\n%s\n</Page>'
            % (pid, body))


# ------------------------------------------------------------ element kit
def card_style(bg=CARD):
    return {"backgroundColor": bg, "borderRadius": "round", "borderColor": RULE, "borderWidth": 1}


def text(eid, body, **extra):
    d = {"id": eid, "kind": "text", "body": body}
    d.update(extra)
    return d


def container(eid, bg=CARD, border=RULE, **extra):
    d = {"id": eid, "kind": "container", "spacing": "small",
         "style": {"backgroundColor": bg, "borderRadius": "round", "borderColor": border, "borderWidth": 1}}
    d.update(extra)
    return d


def button(eid, label, effects, bg=BLUE, fg="#FFFFFF", appearance="filled"):
    return {"id": eid, "kind": "button", "text": label, "appearance": appearance,
            "style": {"backgroundColor": bg, "color": fg},
            "actions": [{"id": "act-" + eid, "trigger": "on-click", "effects": effects}]}


def ctl_list(eid, cid, name, src_table, src_col, targets, selection="multiple", value=None):
    """List control that filters `targets` = [(table_id, column_id), ...]."""
    d = {"id": eid, "kind": "control", "controlId": cid, "name": name, "controlType": "list",
         "mode": "include", "selectionMode": selection, "values": [] if value is None else value,
         "source": {"kind": "source", "source": {"kind": "table", "elementId": src_table}, "columnId": src_col},
         "filters": [{"source": {"kind": "table", "elementId": t}, "columnId": c} for t, c in targets]}
    return d


def ctl_date(eid, cid, name, targets):
    return {"id": eid, "kind": "control", "controlId": cid, "name": name, "controlType": "date-range",
            "mode": "between", "includeNulls": "when-no-value-is-selected",
            "filters": [{"source": {"kind": "table", "elementId": t}, "columnId": c} for t, c in targets]}


def ctl_text_filter(eid, cid, name, targets, mode="contains"):
    return {"id": eid, "kind": "control", "controlId": cid, "name": name, "controlType": "text",
            "case": "insensitive", "mode": mode, "value": "", "includeNulls": "when-no-value-is-selected",
            "showOperators": False,
            "filters": [{"source": {"kind": "table", "elementId": t}, "columnId": c} for t, c in targets]}


def ctl_value_text(eid, cid, name, value=""):
    """Plain text control used as a value holder (no filters)."""
    return {"id": eid, "kind": "control", "controlId": cid, "name": name, "controlType": "text",
            "case": "insensitive", "mode": "equals", "value": value,
            "includeNulls": "when-no-value-is-selected", "showOperators": False}


def ctl_segmented(eid, cid, name, values, value):
    return {"id": eid, "kind": "control", "controlId": cid, "name": name, "controlType": "segmented",
            "source": {"kind": "manual", "valueType": "text", "values": values}, "value": value}


def ctl_slider(eid, cid, name, low, high, step, value):
    return {"id": eid, "kind": "control", "controlId": cid, "name": name, "controlType": "slider",
            "mode": "=", "low": low, "high": high, "step": step, "value": value}
