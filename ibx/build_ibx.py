#!/usr/bin/env python3
"""Claims Analysis and Fraud Detection -- Independence Blue Cross.

Builds one Sigma workbook (workbooks-as-code) on the MEMBER_CLAIMS_PRODUCTION data model:
  1 Overview  2 Geographic Analysis  3 Procedure Analysis  4 AI Fraud Detection

    python3 build_ibx.py            # print summary
    python3 build_ibx.py create     # POST /v2/workbooks/spec
    python3 build_ibx.py update ID  # PUT  /v2/workbooks/ID/spec
"""
import base64
import json
import pathlib
import sys

import api
from lib import *  # noqa: F401,F403

HERE = pathlib.Path(__file__).parent
DM_ID = "0f9ebf36-c15d-4db8-8e1e-21c6f0124a2b"          # MEMBER_CLAIMS_PRODUCTION-ttqOlX3BfVJKfxnMFizXl
DM_EL = "o6JEmcytza"
FOLDER = "81de80ef-c360-4ee3-9aba-2f2dc31888ce"         # Claude-Builds-3X3H4bxQneKmLyaxhEsGL4
CONN = "9e79f38b-a310-405c-aad9-72f762ac6ff1"           # warehouse connection behind the data model
WB_NAME = "Claims Analysis and Fraud Detection"
DMN = "MEMBER_CLAIMS_PRODUCTION"                         # data-model element name used in formulas

LOGO = (HERE / "assets" / "ibx_white.datauri.txt").read_text().strip()

# Pages --------------------------------------------------------------------
PG_OV, PG_GEO, PG_PROC, PG_FR, PG_DATA = "pg-overview", "pg-geo", "pg-proc", "pg-fraud", "pg-data"
PAGES = [
    (PG_OV, "Overview"),
    (PG_GEO, "Geographic Analysis"),
    (PG_PROC, "Procedure Analysis"),
    (PG_FR, "AI Fraud Detection"),
]

E = []            # flat element list (controls first -- see ordering note below)
CTL = []          # controls are emitted BEFORE every table: formula columns that reference a
#                   control only resolve when the control is declared earlier in elements[]
add = E.append

# ------------------------------------------------------------------ base data
BASE = "Claims Base"
# (key, display name, data-model column | None for calculated, format)
BASE_COLS = [
    ("claim", "Claim Id", "Claim Id"),
    ("line", "Claim Line Id", "Claim Transaction Id"),
    ("patient", "Patient Id", "Patient Id"),
    ("prov", "Provider Id", "Provider Id"),
    ("sdate", "Service Date", "Service Date"),
    ("pstatus", "Payment Status", "Payment Status"),
    ("pdate", "Payment Date", "Payment Date"),
    ("billed", "Billed Amount", "Unit Cost"),
    ("paid", "Paid Amount", "Payments"),
    ("costcat", "Claim Cost Category", "Claim Cost Category"),
    ("denied", "Denied Claim", "Denied Claim"),
    ("dreason", "Denial Reason", "Denial Reason"),
    ("paydays", "Payment Days", "Payment Length"),
    ("eclass", "Encounter Class", "Encounter Class"),
    ("edesc", "Encounter Description", "Encounter Description"),
    ("pcode", "Procedure Code", "Procedure Code"),
    ("proc", "Procedure", "Notes"),
    ("bypcp", "Performed by PCP", "Performed by Pcp"),
    ("ereason", "Encounter Reason", "Encounter Reasondescription"),
    ("death", "Patient Deathdate", "Patient Deathdate"),
    ("age", "Patient Age", "Patient Age"),
    ("ageg", "Age Group", "Patient Age Group"),
    ("gender", "Patient Gender", "Patient Gender"),
    ("race", "Patient Race", "Patient Race"),
    ("zip", "Patient Zip", "Patient Zip"),
    ("incq", "Income Quartile", "Patient Income Quartile"),
    ("risk", "Patient Risk Score", "Patient Risk Score"),
    ("haspcp", "Has PCP", "Has Pcp"),
    ("dkm", "Distance to PCP (km)", "Distance to Pcp Km"),
    ("dgrp", "Distance to PCP Group", "Distance to Pcp Group"),
    ("pname", "Provider Name", "Provider Name"),
    ("facility", "Facility", "Place of Service Name"),
    ("fzip", "Facility Zip", "Place of Service Zip"),
    ("plan", "Plan Name", "Plan Name"),
    ("htn", "Hypertension", "Hypertension"),
    ("dia", "Diabetes", "Diabetes"),
    ("chf", "Heart Failure", "Congestive Heart Failure"),
    ("covid", "COVID-19", "Covid 19"),
]

# Ohio ZIP3 -> market (Independence's Ohio book; every row in the model is Ohio).
ZIP3 = {
    "Columbus": ["430", "431", "432", "433"], "Toledo": ["434", "435", "436"],
    "Southeast Ohio": ["437", "438", "457"], "Eastern Ohio": ["439"],
    "Cleveland": ["440", "441"], "Akron": ["442", "443"], "Youngstown": ["444", "445"],
    "Canton": ["446", "447"], "North Central": ["448", "449"],
    "Cincinnati": ["450", "451", "452"], "Dayton": ["453", "454", "455"],
    "Southern Ohio": ["456"], "Northwest Ohio": ["458"],
}


def market_formula(zip_col):
    args = []
    for mk, zs in ZIP3.items():
        for z in zs:
            args += ['"%s"' % z, '"%s"' % mk]
    return ('If(IsNull([%s]) or Len(Text([%s])) < 5, "ZIP not on file", Switch(Left(Text([%s]), 3), %s, "Other Ohio"))'
            % (zip_col, zip_col, zip_col, ", ".join(args)))


CALC_COLS = [
    ("month", "Service Month", 'DateTrunc("month", [Service Date])', MON),
    ("dflag", "Denied Flag", "Number([Denied Claim])", None),
    ("hcflag", "High Cost Flag", 'If([Claim Cost Category] = "High Cost", 1, 0)', None),
    ("pdflag", "Post-Death Flag",
     "If(IsNotNull([Patient Deathdate]) and [Service Date] > [Patient Deathdate], 1, 0)", None),
    ("market", "Member Market", market_formula("Patient Zip"), None),
    ("fmarket", "Facility Market", market_formula("Facility Zip"), None),
    ("ooa", "Out of Market Flag", "If([Member Market] != [Facility Market], 1, 0)", None),
]
FMT = {"billed": MONEY, "paid": MONEY, "sdate": DAY, "pdate": DAY, "dkm": DEC1}

add_base = {
    "id": "t-base", "kind": "table", "name": BASE,
    "source": {"kind": "data-model", "dataModelId": DM_ID, "elementId": DM_EL},
    "columns": (
        [col("b_" + k, n, "[%s/%s]" % (DMN, dm), FMT.get(k)) for k, n, dm in BASE_COLS]
        + [col("b_" + k, n, f, fm) for k, n, f, fm in CALC_COLS]
    ),
}
# every table must be placed in the layout; data plumbing lives on a hidden page
TABLES = [add_base]


def copy_table(eid, name, prefix, extra=None, source=None, src_name=None):
    """Page-scoped copy of the base table: page filter controls only filter their own page."""
    sn = src_name or BASE
    cols = [col(prefix + k, n, "[%s/%s]" % (sn, n), FMT.get(k)) for k, n, _ in BASE_COLS]
    cols += [col(prefix + k, n, "[%s/%s]" % (sn, n), fm) for k, n, _, fm in CALC_COLS]
    cols += (extra or [])
    return {"id": eid, "kind": "table", "name": name,
            "source": {"kind": "table", "elementId": source or "t-base"}, "columns": cols}


# page scoped copies -----------------------------------------------------------
OV, GEO, PRC, FRD = "Overview Claims", "Geo Claims", "Procedure Claims", "Fraud Claims"
TABLES.append(copy_table("t-ov", OV, "o_"))
TABLES.append(copy_table("t-geo", GEO, "g_"))
TABLES.append(copy_table("t-proc", PRC, "p_"))

# procedure benchmark: average billed amount per procedure over the WHOLE model (stable baseline)
TABLES.append({
    "id": "pv-bench", "kind": "pivot-table", "name": "Procedure Benchmark",
    "source": {"kind": "table", "elementId": "t-base"},
    "columns": [
        col("pb_code", "Procedure Code", "[%s/Procedure Code]" % BASE),
        col("pb_avg", "Avg Billed", "Avg(If([%s/Billed Amount] > 0, [%s/Billed Amount], Null))" % (BASE, BASE)),
        col("pb_n", "Lines", "Count()"),
    ],
    "rowsBy": [{"columnId": "pb_code"}], "values": ["pb_avg", "pb_n"],
})

# fraud copy: row-level rule columns ------------------------------------------------
FR_EXTRA = [
    col("f_bavg", "Procedure Avg Billed",
        "Lookup([Procedure Benchmark/Avg Billed], [Procedure Code], [Procedure Benchmark/Procedure Code])", MONEY2),
    col("f_ratio", "Cost Ratio", "If([Procedure Avg Billed] >= 20 and [Billed Amount] > 0, [Billed Amount] / [Procedure Avg Billed], 1)", DEC2),
    col("f_ext", "Extreme Cost Flag", "If([Cost Ratio] >= [ext_mult], 1, 0)"),
    col("f_pts", "Claim Risk Points", "If([Extreme Cost Flag] = 1, 55, 0) + If([Post-Death Flag] = 1, 45, 0)"),
    col("f_flag", "Flagged Line", "If([Claim Risk Points] >= 40, 1, 0)"),
    col("f_rule", "Rule Hit",
        'If([Post-Death Flag] = 1 and [Extreme Cost Flag] = 1, "Post-death + extreme cost", '
        'If([Post-Death Flag] = 1, "Post-death service", If([Extreme Cost Flag] = 1, "Extreme unit cost", "No rule hit")))'),
]
TABLES.append(copy_table("t-fr", FRD, "f_", FR_EXTRA))
FR_ID = {n: "f_" + k for k, n, _ in BASE_COLS}
FR_ID.update({n: "f_" + k for k, n, _, _ in CALC_COLS})

# ------------------------------------------------------------------ write-back store
FLAG_STATUSES = ["Suspicious", "Under investigation", "Confirmed fraud", "Cleared"]
FLAG_SEV = ["Low", "Medium", "High", "Critical"]
TABLES.append({
    "id": "it-flags", "kind": "input-table", "name": "Flag Log", "inputMode": "edit",
    "source": {"kind": "empty", "connectionId": CONN},
    "columns": [
        {"id": "fl_type", "name": "Entity Type", "type": "text", "values": ["Provider", "Claim"], "pills": "color-by-option"},
        {"id": "fl_eid", "name": "Entity Id", "type": "text"},
        {"id": "fl_ename", "name": "Entity Name", "type": "text"},
        {"id": "fl_status", "name": "Flag Status", "type": "text", "values": FLAG_STATUSES, "pills": "color-by-option"},
        {"id": "fl_sev", "name": "Severity", "type": "text", "values": FLAG_SEV, "pills": "color-by-option"},
        {"id": "fl_score", "name": "Risk Score", "type": "number"},
        {"id": "fl_comment", "name": "Analyst Comment", "type": "text"},
        {"id": "CREATED_AT", "name": "Flagged At", "format": {"kind": "datetime", "formatString": "%b %d, %Y %H:%M"}},
        {"id": "CREATED_BY", "name": "Flagged By"},
    ],
    "tableStyle": {"preset": "presentation", "cellSpacing": "small", "gridLines": "horizontal"},
})
# latest decision per entity (append-only log -> newest wins)
TABLES.append({
    "id": "t-latest", "kind": "table", "name": "Latest Flags",
    "source": {"kind": "table", "elementId": "it-flags"},
    "columns": [
        col("lf_eid", "Entity Id", "[Flag Log/Entity Id]"),
        col("lf_status", "Latest Status",
            "MaxIf([Flag Log/Flag Status], [Flag Log/Flagged At] = Max([Flag Log/Flagged At]))"),
        col("lf_sev", "Latest Severity",
            "MaxIf([Flag Log/Severity], [Flag Log/Flagged At] = Max([Flag Log/Flagged At]))"),
        col("lf_comment", "Latest Comment",
            "MaxIf([Flag Log/Analyst Comment], [Flag Log/Flagged At] = Max([Flag Log/Flagged At]))"),
        col("lf_n", "Times Flagged", "Count()"),
        col("lf_at", "Last Flagged", "Max([Flag Log/Flagged At])"),
    ],
    "groupings": [{"id": "lf_g", "groupBy": ["lf_eid"],
                   "calculations": ["lf_status", "lf_sev", "lf_comment", "lf_n", "lf_at"]}],
})

# ------------------------------------------------------------------ provider risk model
PROV_PIVOT_COLS = [
    ("v_id", "Provider Id", "[%s/Provider Id]" % FRD, None),
    ("v_name", "Provider", "Max([%s/Provider Name])" % FRD, None),
    ("v_fac", "Primary Facility", "Max([%s/Facility])" % FRD, None),
    ("v_mkt", "Facility Market", "Max([%s/Facility Market])" % FRD, None),
    ("v_lines", "Claim Lines", "Count()", INT),
    ("v_claims", "Claims", "CountDistinct([%s/Claim Id])" % FRD, INT),
    ("v_pts", "Members", "CountDistinct([%s/Patient Id])" % FRD, INT),
    ("v_paid", "Paid", "Sum([%s/Paid Amount])" % FRD, MONEY),
    ("v_billed", "Billed", "Sum([%s/Billed Amount])" % FRD, MONEY),
    ("v_den", "Denied Lines", "Sum([%s/Denied Flag])" % FRD, INT),
    ("v_hc", "High-Cost Lines", "Sum([%s/High Cost Flag])" % FRD, INT),
    ("v_ratio", "Avg Cost Ratio", "Avg([%s/Cost Ratio])" % FRD, DEC2),
    ("v_ext", "Extreme-Cost Lines", "Sum([%s/Extreme Cost Flag])" % FRD, INT),
    ("v_pd", "Post-Death Lines", "Sum([%s/Post-Death Flag])" % FRD, INT),
]
TABLES.append({
    "id": "pv-prov", "kind": "pivot-table", "name": "Provider Aggregates",
    "source": {"kind": "table", "elementId": "t-fr"},
    "columns": [col(i, n, f, fm) for i, n, f, fm in PROV_PIVOT_COLS],
    "rowsBy": [{"columnId": "v_id"}],
    "values": [i for i, *_ in PROV_PIVOT_COLS[1:]],
})

NET_DEN = "Avg([%s/Denied Flag])" % FRD
NET_HC = "Avg([%s/High Cost Flag])" % FRD
NET_RATIO = "Avg([%s/Cost Ratio])" % FRD
PR = "Provider Risk"
COMPONENTS = {
    "s_den": ("Denial Score", "30 * Least(1, Greatest(0, ([Denial Rate] - %s) / (%s * 1.5)))" % (NET_DEN, NET_DEN)),
    "s_hc": ("High-Cost Score", "20 * Least(1, Greatest(0, ([High-Cost Share] - %s) / (%s * 2)))" % (NET_HC, NET_HC)),
    "s_cost": ("Cost Score", "25 * Least(1, Greatest(0, ([Avg Cost Ratio] - %s) / 0.5))" % NET_RATIO),
    "s_ext": ("Extreme Score", "15 * Least(1, [Extreme Share] / 0.01)"),
    "s_dead": ("Post-Death Score", "10 * Least(1, [Post-Death Lines] / 5)"),
}
TABLES.append({
    "id": "it-prov", "kind": "input-table", "name": PR, "inputMode": "view",
    "source": {"kind": "linked", "from": "pv-prov"},
    "columns": [
        {"id": "ip_id", "key": "v_id", "name": "Provider Id", "hidden": True},
        {"id": "ip_name", "key": "v_name", "name": "Provider"},
        {"id": "ip_fac", "key": "v_fac", "name": "Primary Facility"},
        {"id": "ip_mkt", "key": "v_mkt", "name": "Facility Market"},
        {"id": "ip_lines", "key": "v_lines", "name": "Claim Lines", "format": INT},
        {"id": "ip_claims", "key": "v_claims", "name": "Claims", "format": INT, "hidden": True},
        {"id": "ip_pts", "key": "v_pts", "name": "Members", "format": INT},
        {"id": "ip_paid", "key": "v_paid", "name": "Paid", "format": MONEY},
        {"id": "ip_billed", "key": "v_billed", "name": "Billed", "format": MONEY, "hidden": True},
        {"id": "ip_den", "key": "v_den", "name": "Denied Lines", "hidden": True},
        {"id": "ip_hc", "key": "v_hc", "name": "High-Cost Lines", "hidden": True},
        {"id": "ip_ratio", "key": "v_ratio", "name": "Avg Cost Ratio", "format": DEC2},
        {"id": "ip_ext", "key": "v_ext", "name": "Extreme-Cost Lines", "format": INT},
        {"id": "ip_pd", "key": "v_pd", "name": "Post-Death Lines", "format": INT},
        {"id": "ip_dr", "name": "Denial Rate", "formula": "[Denied Lines] / [Claim Lines]", "format": PCT1},
        {"id": "ip_hs", "name": "High-Cost Share", "formula": "[High-Cost Lines] / [Claim Lines]", "format": PCT1},
        {"id": "ip_es", "name": "Extreme Share", "formula": "[Extreme-Cost Lines] / [Claim Lines]", "format": PCT1,
         "hidden": True},
    ] + [
        {"id": "ip_" + k, "name": n, "formula": f, "hidden": True} for k, (n, f) in COMPONENTS.items()
    ] + [
        {"id": "ip_score", "name": "Risk Score",
         "formula": "If([Claim Lines] >= [min_lines], Round([Denial Score] + [High-Cost Score] + [Cost Score] "
                    "+ [Extreme Score] + [Post-Death Score], 0), Null)", "format": INT},
        {"id": "ip_tier", "name": "Risk Tier",
         "formula": 'If(IsNull([Risk Score]), "Below volume floor", If([Risk Score] >= [alert_score], "Critical", '
                    'If([Risk Score] >= [alert_score] - 15, "High", If([Risk Score] >= [alert_score] - 30, "Watch", "Normal"))))'},
        {"id": "ip_driver", "name": "Top Risk Driver",
         "formula": 'If(IsNull([Risk Score]), "", If([Risk Score] < 20, "None material", '
                    'Switch(Greatest([Denial Score] / 30, [High-Cost Score] / 20, [Cost Score] / 25, [Extreme Score] / 15, [Post-Death Score] / 10), '
                    '[Post-Death Score] / 10, "Billing after member death", [Extreme Score] / 15, "Extreme unit costs", '
                    '[Cost Score] / 25, "Above-benchmark pricing", [High-Cost Score] / 20, "High-cost claim share", '
                    '"Denial rate")))'},
        {"id": "ip_status", "name": "Review Status",
         "formula": 'Coalesce(Lookup([Latest Flags/Latest Status], [Provider Id], [Latest Flags/Entity Id]), "Not reviewed")'},
        {"id": "ip_comment", "name": "Latest Comment",
         "formula": 'Lookup([Latest Flags/Latest Comment], [Provider Id], [Latest Flags/Entity Id])'},
    ],
    "sort": [{"columnId": "ip_score", "direction": "descending", "nulls": "last"}],
})

TABLES.append({
    "id": "t-top", "kind": "table", "name": "Top Provider", "source": {"kind": "table", "elementId": "it-prov"},
    "columns": [col("tp_n", "Provider", "[Provider Risk/Provider]"), col("tp_s", "Risk Score", "[Provider Risk/Risk Score]", INT),
                col("tp_d", "Top Risk Driver", "[Provider Risk/Top Risk Driver]")],
    "filters": [{"id": "tp_f", "columnId": "tp_s", "kind": "top-n", "rankingFunction": "row-number", "mode": "top-n", "rowCount": 1,
                 "includeNulls": "never"}],
})

# suspicious claim lines -----------------------------------------------------------------
SUSP_COLS = [
    ("sc_line", "Claim Line Id", "Claim Line Id", None),
    ("sc_claim", "Claim Id", "Claim Id", None),
    ("sc_date", "Service Date", "Service Date", DAY),
    ("sc_prov", "Provider", "Provider Name", None),
    ("sc_pid", "Provider Id", "Provider Id", None),
    ("sc_fac", "Facility", "Facility", None),
    ("sc_proc", "Procedure", "Procedure", None),
    ("sc_plan", "Plan", "Plan Name", None),
    ("sc_billed", "Billed", "Billed Amount", MONEY2),
    ("sc_avg", "Procedure Avg Billed", "Procedure Avg Billed", MONEY2),
    ("sc_ratio", "Cost Ratio", "Cost Ratio", DEC1),
    ("sc_paid", "Paid", "Paid Amount", MONEY2),
    ("sc_pts", "Risk Points", "Claim Risk Points", INT),
    ("sc_rule", "Rule Hit", "Rule Hit", None),
    ("sc_status", "Payment Status", "Payment Status", None),
]
TABLES.append({
    "id": "t-susp", "kind": "table", "name": "Suspicious Claim Lines",
    "source": {"kind": "table", "elementId": "t-fr"},
    "columns": [col(i, n, "[%s/%s]" % (FRD, src), fm) for i, n, src, fm in SUSP_COLS] + [
        col("sc_review", "Review Status",
            'Coalesce(Lookup([Latest Flags/Latest Status], [Claim Line Id], [Latest Flags/Entity Id]), "Not reviewed")'),
    ],
    "filters": [{"id": "susp_f1", "columnId": "sc_pts", "kind": "number-range", "min": 40}],
    "sort": [{"columnId": "sc_pts", "direction": "descending", "nulls": "last"},
             {"columnId": "sc_ratio", "direction": "descending", "nulls": "last"}],
    "actions": [{"id": "act-susp-sel", "trigger": "on-select", "effects": [
        {"effect": "set-control-value", "control": "sel_claim_id", "value": {"type": "column", "columnId": "sc_line"}},
        {"effect": "set-control-value", "control": "sel_claim_prov", "value": {"type": "column", "columnId": "sc_prov"}},
        {"effect": "set-control-value", "control": "sel_claim_rule", "value": {"type": "column", "columnId": "sc_rule"}},
        {"effect": "open-overlay", "overlayId": "m-claim"},
    ]}],
    "tableComponents": {"summaryBar": "hidden"},
    "tableStyle": {"preset": "presentation", "cellSpacing": "small", "gridLines": "horizontal"},
})


# ====================================================================== helpers
def R(tbl, name):
    return "[%s/%s]" % (tbl, name)


def kpi(eid, label, src, tbl, formula, fmt, direction="higher", compare=False, accent=BLUE, size=26):
    """Comparative KPI card: headline + month-over-month delta + sparkline."""
    cols = [col(eid + "-v", label, formula, fmt)]
    if compare:
        cols.insert(0, col(eid + "-m", "Month", 'DateTrunc("month", %s)' % R(tbl, "Service Date")))
    d = {"id": eid, "kind": "kpi-chart",
         "name": {"text": label, "color": MUTED, "fontSize": 12, "fontWeight": "bold"},
         "source": {"kind": "table", "elementId": src}, "columns": cols,
         "value": {"columnId": eid + "-v", "color": INK, "fontSize": size, "fontWeight": "bold"},
         "style": card_style()}
    if compare:
        d.update({"timeline": {"columnId": eid + "-m"}, "periodComparison": "month",
                  "comparison": {"display": "relative", "direction": direction, "colorGood": GREEN,
                                 "colorBad": RED, "fontSize": 12, "label": "vs prior month"},
                  "trend": {"shape": "area", "areaStyle": "gradient", "valueColor": accent,
                            "comparisonColor": SLATE}})
    return d


def ctitle(t):
    return {"text": t, "color": INK, "fontSize": 14, "fontWeight": "bold"}


def chart(eid, kind, title, src, cols, **kw):
    d = {"id": eid, "kind": kind, "name": ctitle(title), "source": {"kind": "table", "elementId": src},
         "columns": cols, "style": card_style()}
    d.update(kw)
    return d


def sec(eid, label, sub=None):
    body = '<span style="color:%s">**%s**</span>' % (NAVY, label.upper())
    if sub:
        body += '　<span style="color:%s">%s</span>' % (MUTED, sub)
    return text(eid, body, verticalAlign="center")


NAV_OPTS = [{"label": n, "destination": {"type": "page", "pageId": p}} for p, n in PAGES]


def header(n, title, sub):
    """Brand header: real IBX wordmark (recoloured white), title, subtitle, in-page navigation."""
    add(container("hdr-%d" % n, bg=NAVY, border=NAVY))
    add({"id": "logo-%d" % n, "kind": "image", "source": {"kind": "url", "url": LOGO},
         "style": {"fit": "contain", "align": "start", "backgroundColor": "transparent", "padding": "none"}})
    add(text("eyebrow-%d" % n, '<span style="color:%s">**CLAIMS INTELLIGENCE　·　OHIO BOOK OF BUSINESS**</span>' % SKY))
    add(text("title-%d" % n, '<span style="color:#FFFFFF;font-size:30px">**%s**</span>' % title, verticalAlign="center"))
    add(text("sub-%d" % n, '<span style="color:#C9D8E8">%s</span>' % sub))
    add({"id": "nav-%d" % n, "kind": "navigation", "mode": "manual", "showIcons": False,
         "style": {"backgroundColor": "transparent"},
         "optionStyle": {"textColor": "#FFFFFF", "selectedColor": BLUE, "style": "pill", "orientation": "horizontal"},
         "options": NAV_OPTS})
    return box("hdr-%d" % n, 1, 25, 1, 13, [
        el("logo-%d" % n, 1, 8, 2, 5), el("nav-%d" % n, 11, 25, 2, 5),
        el("eyebrow-%d" % n, 1, 14, 6, 7), el("title-%d" % n, 1, 18, 7, 10), el("sub-%d" % n, 1, 22, 10, 12),
    ])


def filter_bar(n, controls, rows=6):
    """controls: list of (control_element, c0, c1, r0, r1) placed in a bordered filter container."""
    add(container("flt-%d" % n, bg=CARD_ALT))
    kids = []
    for ctl, c0, c1, r0, r1 in controls:
        CTL.append(ctl)
        kids.append(el(ctl["id"], c0, c1, r0, r1))
    return box("flt-%d" % n, 1, 25, 0, 0, kids)  # rows patched by caller


def place(node, r0, r1):
    node.r0, node.r1 = r0, r1
    return node


# ====================================================================== PAGE 1 -- Overview
def page_overview():
    nodes = [header(1, "Claims Overview", "Spend, utilization and denials across Independence's Ohio membership — filter once, everything responds.")]
    T, S = OV, "t-ov"
    ctls = [
        (ctl_date("c-ov-date", "ov_date", "Service date", [(S, "o_sdate")]), 1, 7, 1, 5),
        (ctl_list("c-ov-plan", "ov_plan", "Plan", S, "o_plan", [(S, "o_plan")]), 7, 11, 1, 5),
        (ctl_list("c-ov-class", "ov_class", "Encounter class", S, "o_eclass", [(S, "o_eclass")]), 11, 15, 1, 5),
        (ctl_list("c-ov-age", "ov_age", "Age group", S, "o_ageg", [(S, "o_ageg")]), 15, 19, 1, 5),
        (ctl_list("c-ov-gender", "ov_gender", "Gender", S, "o_gender", [(S, "o_gender")]), 19, 22, 1, 5),
        (ctl_list("c-ov-risk", "ov_risk", "Risk score", S, "o_risk", [(S, "o_risk")]), 22, 25, 1, 5),
    ]
    nodes.append(place(filter_bar(1, ctls), 13, 18))

    # KPI band
    k = [
        ("ov-k1", "Total Paid", "Sum(%s)" % R(T, "Paid Amount"), MONEY_S, "higher"),
        ("ov-k2", "Claims", "CountDistinct(%s)" % R(T, "Claim Id"), INT, "higher"),
        ("ov-k3", "Members with Claims", "CountDistinct(%s)" % R(T, "Patient Id"), INT, "higher"),
        ("ov-k4", "Paid per Claim", "Sum(%s) / CountDistinct(%s)" % (R(T, "Paid Amount"), R(T, "Claim Id")), MONEY, "lower"),
        ("ov-k5", "Denial Rate", "Sum(%s) / Count()" % R(T, "Denied Flag"), PCT1, "lower"),
        ("ov-k6", "High-Cost Line Share", "Sum(%s) / Count()" % R(T, "High Cost Flag"), PCT1, "lower"),
    ]
    for i, (eid, lab, f, fm, dr) in enumerate(k):
        add(kpi(eid, lab, S, T, f, fm, dr))
        nodes.append(el(eid, 1 + i * 4, 5 + i * 4, 20, 29))

    # AI insight band (Cortex). The text binds to the sourced KPI sharing its container.
    add(container("ov-ai-c", bg="#EAF6FC", border="#BFE3F3"))
    add(text("ov-ai", '<span style="color:%s">**◆ AI CLAIMS BRIEF**</span>　{{Replace(CallText("SNOWFLAKE.CORTEX.COMPLETE", "CLAUDE-4-SONNET", '
             '"You are a health-plan claims analytics advisor for Independence Blue Cross. In two sentences, summarize the '
             'current claims picture and name one thing to watch. Be quantitative, do not invent figures. Facts: total paid $" & '
             'Text(Round(Sum(%s)/1000000, 1)) & " million across " & Text(CountDistinct(%s)) & " claims for " & '
             'Text(CountDistinct(%s)) & " members; denial rate " & Text(Round(Sum(%s)/Count()*100, 1)) & " percent; '
             'high-cost lines " & Text(Round(Sum(%s)/Count()*100, 2)) & " percent of lines; paid per claim $" & '
             'Text(Round(Sum(%s)/CountDistinct(%s), 0))), \'"\', "")}}'
             % (BLUE, R(T, "Paid Amount"), R(T, "Claim Id"), R(T, "Patient Id"), R(T, "Denied Flag"), R(T, "High Cost Flag"),
                R(T, "Paid Amount"), R(T, "Claim Id")), verticalAlign="center"))
    add(kpi("ov-ai-k", "Lines in view", S, T, "Count()", INT, compare=False, size=20))
    nodes.append(box("ov-ai-c", 1, 25, 32, 38, [el("ov-ai", 1, 20, 1, 6), el("ov-ai-k", 20, 25, 1, 6)]))

    # trend + mix ---------------------------------------------------------------------------------
    nodes.append(el("sec-ov-1", 1, 25, 39, 41)); add(sec("sec-ov-1", "Spend & denial trend", "monthly paid vs denial rate"))
    add(chart("ov-trend", "combo-chart", "Monthly paid vs denial rate", S, [
        col("ot-m", "Month", 'DateTrunc("month", %s)' % R(T, "Service Date"), MON),
        col("ot-paid", "Paid", "Sum(%s)" % R(T, "Paid Amount"), MONEY_S),
        col("ot-den", "Denial rate", "Sum(%s) / Count()" % R(T, "Denied Flag"), PCT1)],
        xAxis={"columnId": "ot-m"}, yAxis={"columnIds": ["ot-paid", {"columnId": "ot-den", "type": "line"}]}, yAxis2={"columnIds": ["ot-den"]},
        color={"by": "single", "value": BLUE}, legend={"position": "top"}))
    nodes.append(el("ov-trend", 1, 13, 41, 59))
    add(chart("ov-plan", "donut-chart", "Paid by plan", S, [
        col("op-plan", "Plan", R(T, "Plan Name")), col("op-paid", "Paid", "Sum(%s)" % R(T, "Paid Amount"), MONEY_S)],
        color={"columnId": "op-plan", "scheme": [BLUE, NAVY, SKY]}, value={"columnId": "op-paid"},
        legend={"position": "bottom"}, dataLabel={"labels": "shown", "labelDisplay": "percent"}))
    nodes.append(el("ov-plan", 13, 19, 41, 59))
    add(chart("ov-class", "bar-chart", "Paid by encounter class", S, [
        col("oc-c", "Encounter class", R(T, "Encounter Class")), col("oc-paid", "Paid", "Sum(%s)" % R(T, "Paid Amount"), MONEY_S)],
        xAxis={"columnId": "oc-c", "sort": {"by": "oc-paid", "aggregation": "sum", "direction": "descending"}},
        yAxis={"columnIds": ["oc-paid"]}, orientation="horizontal", color={"by": "single", "value": BLUE}))
    nodes.append(el("ov-class", 19, 25, 41, 59))

    # demographics -------------------------------------------------------------------------------
    nodes.append(el("sec-ov-2", 1, 25, 60, 62)); add(sec("sec-ov-2", "Who is driving cost", "age, gender, risk and denial drivers"))
    add(chart("ov-age", "bar-chart", "Paid by age group & gender", S, [
        col("oa-a", "Age group", 'Switch(%s, "<10", "00-10", %s)' % (R(T, "Age Group"), R(T, "Age Group"))), col("oa-g", "Gender", R(T, "Patient Gender")),
        col("oa-paid", "Paid", "Sum(%s)" % R(T, "Paid Amount"), MONEY_S)],
        xAxis={"columnId": "oa-a", "sort": {"by": "oa-a", "aggregation": "min", "direction": "ascending"}},
        yAxis={"columnIds": ["oa-paid"]}, stacking="stacked", color={"by": "category", "column": "oa-g", "scheme": [BLUE, NAVY]},
        legend={"position": "top"}))
    nodes.append(el("ov-age", 1, 9, 62, 80))
    add(chart("ov-risk", "bar-chart", "Paid per member by risk score", S, [
        col("or-r", "Risk score", R(T, "Patient Risk Score")),
        col("or-v", "Paid per member", "Sum(%s) / CountDistinct(%s)" % (R(T, "Paid Amount"), R(T, "Patient Id")), MONEY)],
        xAxis={"columnId": "or-r", "sort": {"by": "or-v", "aggregation": "sum", "direction": "descending"}},
        yAxis={"columnIds": ["or-v"]}, color={"by": "single", "value": TEAL},
        dataLabel={"labels": "shown"}))
    nodes.append(el("ov-risk", 9, 17, 62, 80))
    add(chart("ov-denial", "bar-chart", "Denied lines by reason", S, [
        col("od-r", "Denial reason", 'If(IsNull(%s) or %s = "", "Approved", %s)' % (R(T, "Denial Reason"), R(T, "Denial Reason"), R(T, "Denial Reason"))), col("od-n", "Denied lines", "Sum(%s)" % R(T, "Denied Flag"), INT)],
        xAxis={"columnId": "od-r", "sort": {"by": "od-n", "aggregation": "sum", "direction": "descending"}},
        yAxis={"columnIds": ["od-n"]}, orientation="horizontal", color={"by": "single", "value": AMBER},
        filters=[{"id": "od-f", "columnId": "od-r", "kind": "list", "mode": "exclude", "values": ["Approved"]}]))
    nodes.append(el("ov-denial", 17, 25, 62, 80))

    # facilities + payment speed -------------------------------------------------------------------
    nodes.append(el("sec-ov-3", 1, 25, 81, 83)); add(sec("sec-ov-3", "Where the money goes", "top facilities and payment speed"))
    add(chart("ov-fac", "bar-chart", "Top 10 facilities by paid", S, [
        col("of-f", "Facility", R(T, "Facility")), col("of-paid", "Paid", "Sum(%s)" % R(T, "Paid Amount"), MONEY_S)],
        xAxis={"columnId": "of-f", "sort": {"by": "of-paid", "aggregation": "sum", "direction": "descending"}},
        yAxis={"columnIds": ["of-paid"]}, orientation="horizontal", color={"by": "single", "value": NAVY},
        filters=[{"id": "of-top", "columnId": "of-paid", "kind": "top-n", "rankingFunction": "rank", "mode": "top-n", "rowCount": 10}]))
    nodes.append(el("ov-fac", 1, 14, 83, 103))
    add(chart("ov-lag", "bar-chart", "Average days to pay, by plan", S, [
        col("ol-p", "Plan", R(T, "Plan Name")), col("ol-m", "Month", 'DateTrunc("month", %s)' % R(T, "Service Date"), MON),
        col("ol-d", "Avg days to pay", "Avg(%s)" % R(T, "Payment Days"), DEC1)],
        xAxis={"columnId": "ol-m"}, yAxis={"columnIds": ["ol-d"]}, stacking="none",
        color={"by": "category", "column": "ol-p", "scheme": [BLUE, NAVY, SKY]}, legend={"position": "top"}))
    nodes.append(el("ov-lag", 14, 25, 83, 103))
    return nodes


# ====================================================================== PAGE 2 -- Geography
GEO_METRIC = ("Switch([geo_metric], "
              '"Claims", CountDistinct({t}[Claim Id]), '
              '"Paid per member", Sum({t}[Paid Amount]) / CountDistinct({t}[Patient Id]), '
              '"Denial rate", Sum({t}[Denied Flag]) / Count(), '
              'Sum({t}[Paid Amount]))')


def geo_metric(tbl):
    return GEO_METRIC.replace("{t}[", "[%s/" % tbl)


def page_geo():
    nodes = [header(2, "Geographic Analysis", "Where Ohio members live, where they get care, and where cost and denials concentrate.")]
    T, S = GEO, "t-geo"
    ctls = [
        (ctl_date("c-g-date", "g_date", "Service date", [(S, "g_sdate")]), 1, 7, 1, 5),
        (ctl_list("c-g-mkt", "g_mkt", "Member market", S, "g_market", [(S, "g_market")]), 7, 11, 1, 5),
        (ctl_list("c-g-plan", "g_plan", "Plan", S, "g_plan", [(S, "g_plan")]), 11, 15, 1, 5),
        (ctl_list("c-g-inc", "g_inc", "Income quartile", S, "g_incq", [(S, "g_incq")]), 15, 19, 1, 5),
        (ctl_list("c-g-age", "g_age", "Age group", S, "g_ageg", [(S, "g_ageg")]), 19, 22, 1, 5),
        (ctl_list("c-g-class", "g_class", "Encounter class", S, "g_eclass", [(S, "g_eclass")]), 22, 25, 1, 5),
    ]
    nodes.append(place(filter_bar(2, ctls), 13, 18))

    k = [
        ("g-k1", "Paid", "Sum(%s)" % R(T, "Paid Amount"), MONEY_S, "higher"),
        ("g-k2", "ZIP Codes with Claims", "CountDistinct(%s)" % R(T, "Patient Zip"), INT, "higher"),
        ("g-k3", "Paid per Member", "Sum(%s) / CountDistinct(%s)" % (R(T, "Paid Amount"), R(T, "Patient Id")), MONEY, "lower"),
        ("g-k4", "Care Outside Home Market", "Sum(%s) / Count()" % R(T, "Out of Market Flag"), PCT1, "lower"),
        ("g-k5", "Avg Distance to PCP (km)", "Avg(%s)" % R(T, "Distance to PCP (km)"), DEC1, "lower"),
        ("g-k6", "Denial Rate", "Sum(%s) / Count()" % R(T, "Denied Flag"), PCT1, "lower"),
    ]
    for i, (eid, lab, f, fm, dr) in enumerate(k):
        add(kpi(eid, lab, S, T, f, fm, dr))
        nodes.append(el(eid, 1 + i * 4, 5 + i * 4, 20, 29))

    # map + market ranking ----------------------------------------------------------------------
    CTL.append(ctl_segmented("c-g-metric", "geo_metric", "Map metric",
                             ["Total paid", "Claims", "Paid per member", "Denial rate"], "Total paid"))
    add(container("g-maphd-c", bg=CARD_ALT))
    add(sec("g-maphd", "Ohio claims map", "ZIP-level view of the member's home ZIP — pick the metric, click a ZIP to spotlight it below"))
    nodes.append(box("g-maphd-c", 1, 25, 32, 38, [el("g-maphd", 1, 15, 1, 5), el("c-g-metric", 15, 25, 1, 5)]))
    add(chart("g-map", "region-map", "Claims by member ZIP code", S, [
        col("gm-z", "Member ZIP", 'If(Len(Text(%s)) = 5, Text(%s), "00000")' % (R(T, "Patient Zip"), R(T, "Patient Zip"))),
        col("gm-v", "Metric", geo_metric(T))],
        region={"columnId": "gm-z", "regionType": "us-zipcode"},
        color={"by": "scale", "column": "gm-v", "scheme": ["#DDF1FA", BLUE, NAVY]},
        legend={"position": "bottom-left"},
        filters=[{"id": "gm-f", "columnId": "gm-z", "kind": "list", "mode": "exclude", "values": ["00000"]}],
        actions=[{"id": "act-g-map", "trigger": {"on": "on-select", "columnId": "gm-z", "condition": {"type": "column", "columnId": "gm-z", "condition": "IsNotNull"}},
                  "effects": [{"effect": "set-control-value", "control": "zip_focus", "value": {"type": "column", "columnId": "gm-z"}}]}]))
    nodes.append(el("g-map", 1, 15, 38, 66))
    add(chart("g-mkt", "bar-chart", "Markets ranked by selected metric", S, [
        col("gk-m", "Member market", R(T, "Member Market")), col("gk-v", "Metric", geo_metric(T), {"kind": "number", "formatString": ",.3~s"})],
        xAxis={"columnId": "gk-m", "sort": {"by": "gk-v", "aggregation": "sum", "direction": "descending"}},
        yAxis={"columnIds": ["gk-v"]}, orientation="horizontal", color={"by": "single", "value": BLUE},
        dataLabel={"labels": "shown"}, filters=[{"id": "gk-f", "columnId": "gk-m", "kind": "list", "mode": "exclude", "values": ["ZIP not on file"]}]))
    nodes.append(el("g-mkt", 15, 25, 38, 66))

    # demographics x geography -------------------------------------------------------------------
    nodes.append(el("sec-g-2", 1, 25, 67, 69)); add(sec("sec-g-2", "Geography × demographics", "who is using care where"))
    add(chart("g-mktplan", "bar-chart", "Paid by market & plan", S, [
        col("gp-m", "Member market", R(T, "Member Market")), col("gp-p", "Plan", R(T, "Plan Name")),
        col("gp-v", "Paid", "Sum(%s)" % R(T, "Paid Amount"), MONEY_S)],
        xAxis={"columnId": "gp-m", "sort": {"by": "gp-v", "aggregation": "sum", "direction": "descending"}},
        yAxis={"columnIds": ["gp-v"]}, stacking="stacked", color={"by": "category", "column": "gp-p", "scheme": [BLUE, NAVY, SKY]},
        legend={"position": "top"}, filters=[{"id": "gp-f", "columnId": "gp-m", "kind": "list", "mode": "exclude", "values": ["ZIP not on file"]}]))
    nodes.append(el("g-mktplan", 1, 10, 69, 87))
    add(chart("g-inc", "bar-chart", "Paid per member by income quartile", S, [
        col("gi-q", "Income quartile", 'Switch(Left(%s, 3), "Fir", "1 · Low", "Sec", "2 · Below avg", "Thi", "3 · Above avg", "Fou", "4 · High", %s)' % (R(T, "Income Quartile"), R(T, "Income Quartile"))),
        col("gi-v", "Paid per member", "Sum(%s) / CountDistinct(%s)" % (R(T, "Paid Amount"), R(T, "Patient Id")), MONEY)],
        xAxis={"columnId": "gi-q", "sort": {"by": "gi-q", "aggregation": "min", "direction": "ascending"}},
        yAxis={"columnIds": ["gi-v"]}, color={"by": "single", "value": TEAL}, dataLabel={"labels": "shown"}))
    nodes.append(el("g-inc", 10, 18, 69, 87))
    add(chart("g-dist", "bar-chart", "Claim lines by distance to PCP", S, [
        col("gd-g", "Distance to PCP", 'Switch(%s, "<10", "1 · <10 km", "11-25", "2 · 11-25 km", "26-50", "3 · 26-50 km", "51-100", "4 · 51-100 km", "100+", "5 · 100+ km", "6 · No PCP")' % R(T, "Distance to PCP Group")), col("gd-n", "Claim lines", "Count()", INT)],
        xAxis={"columnId": "gd-g", "sort": {"by": "gd-g", "aggregation": "min", "direction": "ascending"}},
        yAxis={"columnIds": ["gd-n"]}, color={"by": "single", "value": AMBER}))
    nodes.append(el("g-dist", 18, 25, 69, 87))

    # leakage matrix + ZIP leaderboard -----------------------------------------------------------
    nodes.append(el("sec-g-3", 1, 25, 88, 90)); add(sec("sec-g-3", "Network leakage & ZIP leaderboard", "how often members leave their home market for care"))
    add(chart("g-flow", "bar-chart", "Care received outside the member's home market", S, [
        col("gf-r", "Member market", R(T, "Member Market")),
        col("gf-v", "Out-of-market share", "Sum(%s) / Count()" % R(T, "Out of Market Flag"), PCT1)],
        xAxis={"columnId": "gf-r", "sort": {"by": "gf-v", "aggregation": "sum", "direction": "descending"}},
        yAxis={"columnIds": ["gf-v"]}, orientation="horizontal", color={"by": "single", "value": PURPLE}, dataLabel={"labels": "shown"}, filters=[{"id": "gf-f", "columnId": "gf-r", "kind": "list", "mode": "exclude", "values": ["ZIP not on file"]}]))
    nodes.append(el("g-flow", 1, 15, 90, 112))
    add({"id": "g-zip", "kind": "pivot-table", "name": ctitle("Top ZIP codes by paid"),
         "source": {"kind": "table", "elementId": S}, "style": card_style(),
         "columns": [col("gz-z", "ZIP", 'If(Len(Text(%s)) = 5, Text(%s), "ZIP not on file")' % (R(T, "Patient Zip"), R(T, "Patient Zip"))),
                     col("gz-m", "Market", "Max(%s)" % R(T, "Member Market")),
                     col("gz-mem", "Members", "CountDistinct(%s)" % R(T, "Patient Id"), INT),
                     col("gz-paid", "Paid", "Sum(%s)" % R(T, "Paid Amount"), MONEY_S),
                     col("gz-pm", "Paid / member", "Sum(%s) / CountDistinct(%s)" % (R(T, "Paid Amount"), R(T, "Patient Id")), MONEY),
                     col("gz-dr", "Denial rate", "Sum(%s) / Count()" % R(T, "Denied Flag"), PCT1)],
         "rowsBy": [{"columnId": "gz-z", "sort": {"direction": "descending", "by": "gz-paid"}}],
         "values": ["gz-m", "gz-mem", "gz-paid", "gz-pm", "gz-dr"],
         "conditionalFormats": [{"type": "dataBars", "columnIds": ["gz-paid"], "scheme": [BLUE, CARD_ALT]}],
         "tableStyle": {"preset": "presentation", "cellSpacing": "small", "gridLines": "horizontal"}})
    nodes.append(el("g-zip", 15, 25, 90, 112))

    # ZIP spotlight (driven by map click) ------------------------------------------------------------
    nodes.append(el("sec-g-4", 1, 25, 113, 115)); add(sec("sec-g-4", "ZIP spotlight", "click a ZIP on the map, or pick one here — blank shows all"))
    TABLES.append(copy_table("t-gdrill", "ZIP Spotlight", "z_", source="t-geo", src_name=GEO))
    CTL.append(ctl_list("c-g-zip", "zip_focus", "ZIP spotlight", "t-geo", "g_zip", [("t-gdrill", "z_zip")]))
    nodes.append(el("c-g-zip", 1, 7, 115, 120))
    ZT = "ZIP Spotlight"
    for i, (eid, lab, f, fm) in enumerate([
            ("g-z1", "Paid", "Sum(%s)" % R(ZT, "Paid Amount"), MONEY_S),
            ("g-z2", "Claims", "CountDistinct(%s)" % R(ZT, "Claim Id"), INT),
            ("g-z3", "Denial Rate", "Sum(%s) / Count()" % R(ZT, "Denied Flag"), PCT1)]):
        add(kpi(eid, lab, "t-gdrill", ZT, f, fm, "lower" if lab == "Denial Rate" else "higher"))
        nodes.append(el(eid, 7 + i * 6, 13 + i * 6, 115, 126))
    add(chart("g-ztrend", "line-chart", "Paid by month — selected ZIP", "t-gdrill", [
        col("gt-m", "Month", 'DateTrunc("month", %s)' % R(ZT, "Service Date"), MON),
        col("gt-v", "Paid", "Sum(%s)" % R(ZT, "Paid Amount"), MONEY_S)],
        xAxis={"columnId": "gt-m"}, yAxis={"columnIds": ["gt-v"]}, color={"by": "single", "value": BLUE}))
    nodes.append(el("g-ztrend", 1, 13, 126, 142))
    add(chart("g-zclass", "bar-chart", "Paid by encounter class — selected ZIP", "t-gdrill", [
        col("gc-c", "Encounter class", R(ZT, "Encounter Class")), col("gc-v", "Paid", "Sum(%s)" % R(ZT, "Paid Amount"), MONEY_S)],
        xAxis={"columnId": "gc-c", "sort": {"by": "gc-v", "aggregation": "sum", "direction": "descending"}},
        yAxis={"columnIds": ["gc-v"]}, orientation="horizontal", color={"by": "single", "value": NAVY}))
    nodes.append(el("g-zclass", 13, 25, 126, 142))
    return nodes


# ====================================================================== PAGE 3 -- Procedures
def page_proc():
    nodes = [header(3, "Procedure Analysis", "Which procedures drive spend, where unit costs diverge, and which carry denial risk.")]
    T, S = PRC, "t-proc"
    ctls = [
        (ctl_date("c-p-date", "p_date", "Service date", [(S, "p_sdate")]), 1, 7, 1, 5),
        (ctl_list("c-p-class", "p_class", "Encounter class", S, "p_eclass", [(S, "p_eclass")]), 7, 11, 1, 5),
        (ctl_list("c-p-plan", "p_plan", "Plan", S, "p_plan", [(S, "p_plan")]), 11, 15, 1, 5),
        (ctl_list("c-p-cost", "p_cost", "Cost category", S, "p_costcat", [(S, "p_costcat")]), 15, 19, 1, 5),
        (ctl_list("c-p-age", "p_age", "Age group", S, "p_ageg", [(S, "p_ageg")]), 19, 22, 1, 5),
        (ctl_text_filter("c-p-search", "p_search", "Procedure search", [(S, "p_proc")]), 22, 25, 1, 5),
    ]
    nodes.append(place(filter_bar(3, ctls), 13, 18))
    k = [
        ("p-k1", "Procedures Billed", "CountDistinct(%s)" % R(T, "Procedure Code"), INT, "higher"),
        ("p-k2", "Paid", "Sum(%s)" % R(T, "Paid Amount"), MONEY_S, "higher"),
        ("p-k3", "Avg Billed per Line", "Avg(%s)" % R(T, "Billed Amount"), MONEY2, "lower"),
        ("p-k4", "Avg Paid per Line", "Avg(%s)" % R(T, "Paid Amount"), MONEY2, "lower"),
        ("p-k5", "Denial Rate", "Sum(%s) / Count()" % R(T, "Denied Flag"), PCT1, "lower"),
        ("p-k6", "Avg Days to Pay", "Avg(%s)" % R(T, "Payment Days"), DEC1, "lower"),
    ]
    for i, (eid, lab, f, fm, dr) in enumerate(k):
        add(kpi(eid, lab, S, T, f, fm, dr))
        nodes.append(el(eid, 1 + i * 4, 5 + i * 4, 20, 29))

    nodes.append(el("sec-p-1", 1, 25, 32, 34)); add(sec("sec-p-1", "Where spend concentrates", "top procedures and encounter mix"))
    add(chart("p-top", "bar-chart", "Top 15 procedures by paid", S, [
        col("pt-p", "Procedure", R(T, "Procedure")), col("pt-v", "Paid", "Sum(%s)" % R(T, "Paid Amount"), MONEY_S)],
        xAxis={"columnId": "pt-p", "sort": {"by": "pt-v", "aggregation": "sum", "direction": "descending"}},
        yAxis={"columnIds": ["pt-v"]}, orientation="horizontal", color={"by": "single", "value": BLUE},
        filters=[{"id": "pt-top", "columnId": "pt-v", "kind": "top-n", "rankingFunction": "rank", "mode": "top-n", "rowCount": 15}]))
    nodes.append(el("p-top", 1, 14, 34, 58))
    add(chart("p-class", "bar-chart", "Paid by encounter class & cost category", S, [
        col("pc-c", "Encounter class", R(T, "Encounter Class")), col("pc-k", "Cost category", R(T, "Claim Cost Category")),
        col("pc-v", "Paid", "Sum(%s)" % R(T, "Paid Amount"), MONEY_S)],
        xAxis={"columnId": "pc-c", "sort": {"by": "pc-v", "aggregation": "sum", "direction": "descending"}},
        yAxis={"columnIds": ["pc-v"]}, stacking="stacked", orientation="horizontal",
        color={"by": "category", "column": "pc-k", "scheme": [BLUE, RED], "customColors": [{"value": "Standard Cost", "color": BLUE}, {"value": "High Cost", "color": RED}]}, legend={"position": "top"}))
    nodes.append(el("p-class", 14, 25, 34, 58))

    nodes.append(el("sec-p-2", 1, 25, 59, 61)); add(sec("sec-p-2", "Volume & trend", "highest-volume procedures and monthly spend by encounter class"))
    add(chart("p-bubble", "bar-chart", "Top 15 procedures by claim-line volume", S, [
        col("pb-p", "Procedure", R(T, "Procedure")), col("pb-n", "Claim lines", "Count()", INT)],
        xAxis={"columnId": "pb-p", "sort": {"by": "pb-n", "aggregation": "sum", "direction": "descending"}},
        yAxis={"columnIds": ["pb-n"]}, orientation="horizontal", color={"by": "single", "value": TEAL},
        filters=[{"id": "pb-top", "columnId": "pb-n", "kind": "top-n", "rankingFunction": "rank", "mode": "top-n", "rowCount": 15}]))
    nodes.append(el("p-bubble", 1, 15, 61, 85))
    add(chart("p-trend", "line-chart", "Monthly paid by encounter class", S, [
        col("ptr-m", "Month", 'DateTrunc("month", %s)' % R(T, "Service Date"), MON),
        col("ptr-p", "Encounter class", R(T, "Encounter Class")), col("ptr-v", "Paid", "Sum(%s)" % R(T, "Paid Amount"), MONEY_S)],
        xAxis={"columnId": "ptr-m"}, yAxis={"columnIds": ["ptr-v"]},
        color={"by": "category", "column": "ptr-p", "scheme": CATEGORICAL}, legend={"position": "bottom"}))
    nodes.append(el("p-trend", 15, 25, 61, 85))

    nodes.append(el("sec-p-3", 1, 25, 86, 88)); add(sec("sec-p-3", "Denial & cost risk", "procedures with at least 5,000 lines"))
    add(chart("p-denial", "bar-chart", "Highest denial rates", S, [
        col("pd-p", "Procedure", R(T, "Procedure")), col("pd-n", "Claim lines", "Count()", INT),
        col("pd-v", "Denial rate", "If(Count() >= 5000, Sum(%s) / Count(), Null)" % R(T, "Denied Flag"), PCT1)],
        xAxis={"columnId": "pd-p", "sort": {"by": "pd-v", "aggregation": "sum", "direction": "descending"}},
        yAxis={"columnIds": ["pd-v"]}, orientation="horizontal", color={"by": "single", "value": AMBER},
        filters=[{"id": "pd-top", "columnId": "pd-v", "kind": "top-n", "rankingFunction": "rank", "mode": "top-n", "rowCount": 12, "includeNulls": "never"}]))
    nodes.append(el("p-denial", 1, 13, 88, 110))
    add(chart("p-cost", "bar-chart", "Highest average billed amount", S, [
        col("pk-p", "Procedure", R(T, "Procedure")), col("pk-n", "Claim lines", "Count()", INT),
        col("pk-v", "Avg billed", "If(Count() >= 5000, Avg(%s), Null)" % R(T, "Billed Amount"), MONEY)],
        xAxis={"columnId": "pk-p", "sort": {"by": "pk-v", "aggregation": "sum", "direction": "descending"}},
        yAxis={"columnIds": ["pk-v"]}, orientation="horizontal", color={"by": "single", "value": RED},
        filters=[{"id": "pk-top", "columnId": "pk-v", "kind": "top-n", "rankingFunction": "rank", "mode": "top-n", "rowCount": 12, "includeNulls": "never"}]))
    nodes.append(el("p-cost", 13, 25, 88, 110))

    nodes.append(el("sec-p-4", 1, 25, 111, 113)); add(sec("sec-p-4", "Procedure scorecard", "every procedure, ranked by paid"))
    add({"id": "p-card", "kind": "pivot-table", "name": ctitle("Procedure scorecard"), "style": card_style(),
         "source": {"kind": "table", "elementId": S},
         "columns": [col("ps-p", "Procedure", R(T, "Procedure")),
                     col("ps-c", "Encounter class", "Max(%s)" % R(T, "Encounter Class")),
                     col("ps-n", "Claim lines", "Count()", INT),
                     col("ps-paid", "Paid", "Sum(%s)" % R(T, "Paid Amount"), MONEY_S),
                     col("ps-avg", "Avg billed", "Avg(%s)" % R(T, "Billed Amount"), MONEY2),
                     col("ps-dr", "Denial rate", "Sum(%s) / Count()" % R(T, "Denied Flag"), PCT1),
                     col("ps-hc", "High-cost share", "Sum(%s) / Count()" % R(T, "High Cost Flag"), PCT1),
                     col("ps-d", "Days to pay", "Avg(%s)" % R(T, "Payment Days"), DEC1)],
         "rowsBy": [{"columnId": "ps-p", "sort": {"direction": "descending", "by": "ps-paid"}}],
         "values": ["ps-c", "ps-n", "ps-paid", "ps-avg", "ps-dr", "ps-hc", "ps-d"],
         "conditionalFormats": [{"type": "dataBars", "columnIds": ["ps-paid"], "scheme": [BLUE, CARD_ALT]},
                                {"type": "dataBars", "columnIds": ["ps-dr"], "scheme": [AMBER, CARD_ALT]}],
         "tableStyle": {"preset": "presentation", "cellSpacing": "small", "gridLines": "horizontal"}})
    nodes.append(el("p-card", 1, 25, 113, 140))
    return nodes


# ====================================================================== PAGE 4 -- AI fraud detection
def page_fraud():
    nodes = [header(4, "AI Fraud Detection", "A transparent risk model scores every provider and claim line; Cortex explains it; analysts flag and comment — all written back to the warehouse.")]
    T, S = FRD, "t-fr"
    # --- detection controls --------------------------------------------------------------------
    ctls = [
        (ctl_date("c-f-date", "f_date", "Service date", [(S, "f_sdate")]), 1, 7, 1, 5),
        (ctl_list("c-f-plan", "f_plan", "Plan", S, "f_plan", [(S, "f_plan")]), 7, 11, 1, 5),
        (ctl_list("c-f-class", "f_class", "Encounter class", S, "f_eclass", [(S, "f_eclass")]), 11, 15, 1, 5),
        (ctl_list("c-f-mkt", "f_mkt", "Facility market", S, "f_fmarket", [(S, "f_fmarket")]), 15, 19, 1, 5),
        (ctl_text_filter("c-f-search", "f_search", "Provider search", [(S, "f_pname")]), 19, 25, 1, 5),
        (ctl_slider("c-f-minlines", "min_lines", "Min claim lines to score a provider", 50, 2000, 50, 200), 1, 9, 6, 10),
        (ctl_slider("c-f-ext", "ext_mult", "Extreme cost = × procedure average", 1.5, 10, 0.5, 2.5), 9, 17, 6, 10),
        (ctl_slider("c-f-alert", "alert_score", "Critical alert score", 20, 80, 5, 40), 17, 25, 6, 10),
    ]
    nodes.append(place(filter_bar(4, ctls), 13, 24))

    PRt = PR
    k = [
        ("f-k1", "Providers Scored", "it-prov", PRt, "Count(%s)" % R(PRt, "Risk Score"), INT),
        ("f-k2", "Critical Providers", "it-prov", PRt, 'CountIf(%s = "Critical")' % R(PRt, "Risk Tier"), INT),
        ("f-k3", "Flagged Claim Lines", "t-fr", T, "Sum(%s)" % R(T, "Flagged Line"), INT),
        ("f-k4", "Billed on Flagged Lines", "t-fr", T, "SumIf(%s, %s = 1)" % (R(T, "Billed Amount"), R(T, "Flagged Line")), MONEY_S),
        ("f-k5", "Analyst Flags Logged", "it-flags", "Flag Log", "Count()", INT),
        ("f-k6", "Open Reviews", "t-latest", "Latest Flags",
         'CountDistinct(If(%s = "Suspicious" or %s = "Under investigation", %s, Null))'
         % (R("Latest Flags", "Latest Status"), R("Latest Flags", "Latest Status"), R("Latest Flags", "Entity Id")), INT),
    ]
    for i, (eid, lab, src, tb, f, fm) in enumerate(k):
        d = kpi(eid, lab, src, tb, f, fm, compare=False, accent=RED if i in (1, 2, 3) else BLUE)
        d["style"] = card_style("#FFF5F5" if i in (1, 2, 3) else CARD)
        add(d)
        nodes.append(el(eid, 1 + i * 4, 5 + i * 4, 25, 35))

    # --- AI brief ------------------------------------------------------------------------------------
    add(container("f-ai-c", bg="#EAF6FC", border="#BFE3F3"))
    add(text("f-ai", '<span style="color:%s">**◆ AI FRAUD BRIEF**</span>　{{Replace(CallText("SNOWFLAKE.CORTEX.COMPLETE", "CLAUDE-4-SONNET", '
             '"You are a special-investigations-unit analyst assistant at a health plan. In three sentences brief the SIU on the '
             'current fraud-risk picture and recommend the first two actions. Use cautious language: these are risk indicators for review, not findings of fraud. Be specific and quantitative, do not invent facts. '
             'Facts: " & Text(Count(%s)) & " providers scored; " & Text(CountIf(%s = "Critical")) & " are Critical (score at or above " & '
             'Text([alert_score]) & "). Highest-risk provider: " & Max(%s) & " with score " & Text(Max(%s)) & '
             '" driven by " & Max(%s) & ". Rule engine flagged " & Text(Sum(%s)) & " claim lines (extreme unit cost at or above " & '
             'Text([ext_mult]) & " times the procedure average, or service billed after the member\'s date of death) worth $" & '
             'Text(Round(SumIf(%s, %s = 1)/1000000, 2)) & " million billed, of which " & Text(CountIf(%s = "Post-death service")) & " lines are post-death."), \'"\', "")}}'
             % (BLUE, R(PRt, "Risk Score"), R(PRt, "Risk Tier"), R("Top Provider", "Provider"),
                R("Top Provider", "Risk Score"), R("Top Provider", "Top Risk Driver"), R(T, "Flagged Line"),
                R(T, "Billed Amount"), R(T, "Flagged Line"), R(T, "Rule Hit")),
         verticalAlign="center"))
    add(kpi("f-ai-k", "Providers in view", "it-prov", PRt, "Count()", INT, compare=False, size=20))
    nodes.append(box("f-ai-c", 1, 25, 36, 43, [el("f-ai", 1, 20, 1, 7), el("f-ai-k", 20, 25, 1, 7)]))

    # --- leaderboard + copilot ---------------------------------------------------------------------------
    nodes.append(el("sec-f-1", 1, 25, 44, 46)); add(sec("sec-f-1", "Provider risk leaderboard", "click a provider to review, flag and comment"))
    prov = next(t for t in TABLES if t["id"] == "it-prov")
    prov["name"] = PR
    prov["actions"] = [{"id": "act-prov-sel", "trigger": "on-select", "effects": [
        {"effect": "set-control-value", "control": "sel_prov_id", "value": {"type": "column", "columnId": "ip_id"}},
        {"effect": "set-control-value", "control": "sel_prov_name", "value": {"type": "column", "columnId": "ip_name"}},
        {"effect": "open-overlay", "overlayId": "m-prov"}]}]
    prov["conditionalFormats"] = [
        {"type": "dataBars", "columnIds": ["ip_score"], "scheme": [RED, CARD_ALT]},
        {"type": "single", "columnIds": ["ip_tier"], "condition": "formula", "formula": '[Risk Tier] = "Critical"',
         "style": {"backgroundColor": "#FCE8E8", "color": RED}},
        {"type": "single", "columnIds": ["ip_tier"], "condition": "formula", "formula": '[Risk Tier] = "High"',
         "style": {"backgroundColor": "#FDF1DD", "color": "#B26A00"}},
        {"type": "single", "columnIds": ["ip_status"], "condition": "formula",
         "formula": '[Review Status] != "Not reviewed"', "style": {"backgroundColor": "#E6F4EC", "color": GREEN}},
    ]
    prov["tableComponents"] = {"summaryBar": "hidden"}
    prov["tableStyle"] = {"preset": "presentation", "cellSpacing": "small", "gridLines": "horizontal"}
    prov["style"] = card_style()
    for c_ in prov["columns"]:
        if c_["id"] in ("ip_pts", "ip_hs", "ip_ext", "ip_pd", "ip_comment", "ip_fac"):
            c_["hidden"] = True
    prov["order"] = ["ip_name", "ip_mkt", "ip_score", "ip_tier", "ip_driver", "ip_lines", "ip_paid", "ip_dr", "ip_ratio", "ip_status"]
    nodes.append(el("it-prov", 1, 19, 46, 78))
    add({"id": "f-chat", "kind": "chat", "agentId": "ag-siu"})
    nodes.append(el("f-chat", 19, 25, 46, 78))

    # --- risk map & rule engine ----------------------------------------------------------------------------
    nodes.append(el("sec-f-2", 1, 25, 79, 81)); add(sec("sec-f-2", "Risk map & rule engine", "outliers, tiers and which rules are firing"))
    add(chart("f-scatter", "scatter-chart", "Provider risk map: volume vs denial rate", "it-prov", [
        col("fs-n", "Claim lines", R(PRt, "Claim Lines"), INT), col("fs-d", "Denial rate", R(PRt, "Denial Rate"), PCT1),
        col("fs-p", "Paid", R(PRt, "Paid"), MONEY_S), col("fs-t", "Risk tier", R(PRt, "Risk Tier")),
        col("fs-name", "Provider", R(PRt, "Provider"))],
        xAxis={"columnId": "fs-n"}, yAxis={"columnIds": ["fs-d"]}, size={"columnId": "fs-p"},
        color={"by": "category", "column": "fs-t", "scheme": [RED, AMBER, SKY, SLATE, "#C8D3E0"],
               "customColors": [{"value": "Critical", "color": RED}, {"value": "High", "color": AMBER}, {"value": "Watch", "color": SKY},
                                {"value": "Normal", "color": SLATE}, {"value": "Below volume floor", "color": "#D5DEE8"}]},
        legend={"position": "bottom"},
        filters=[{"id": "fs-f", "columnId": "fs-t", "kind": "list", "mode": "exclude", "values": ["Below volume floor"]}]))
    nodes.append(el("f-scatter", 1, 11, 81, 103))
    add(chart("f-tier", "donut-chart", "Providers by risk tier", "it-prov", [
        col("ft-t", "Risk tier", R(PRt, "Risk Tier")), col("ft-n", "Providers", "Count()", INT)],
        color={"columnId": "ft-t", "customColors": [{"value": "Critical", "color": RED}, {"value": "High", "color": AMBER},
                                                    {"value": "Watch", "color": SKY}, {"value": "Normal", "color": SLATE},
                                                    {"value": "Below volume floor", "color": "#D5DEE8"}]},
        value={"columnId": "ft-n"}, legend={"position": "bottom"}, dataLabel={"labels": "shown", "labelDisplay": "value"},
        filters=[{"id": "ft-f", "columnId": "ft-t", "kind": "list", "mode": "exclude", "values": ["Below volume floor"]}]))
    nodes.append(el("f-tier", 11, 17, 81, 103))
    add(chart("f-rules", "bar-chart", "Claim lines by rule hit", S, [
        col("fr-r", "Rule hit", R(T, "Rule Hit")), col("fr-n", "Claim lines", "Count()", INT)],
        xAxis={"columnId": "fr-r", "sort": {"by": "fr-n", "aggregation": "sum", "direction": "descending"}},
        yAxis={"columnIds": ["fr-n"]}, orientation="horizontal", color={"by": "single", "value": RED},
        filters=[{"id": "fr-rf", "columnId": "fr-r", "kind": "list", "mode": "exclude", "values": ["No rule hit"]}],
        dataLabel={"labels": "shown"}))
    nodes.append(el("f-rules", 17, 25, 81, 92))
    add(chart("f-mkt", "bar-chart", "Billed on flagged lines by facility market", S, [
        col("fm-m", "Facility market", R(T, "Facility Market")),
        col("fm-v", "Billed on flagged lines", "SumIf(%s, %s = 1)" % (R(T, "Billed Amount"), R(T, "Flagged Line")), MONEY_S)],
        xAxis={"columnId": "fm-m", "sort": {"by": "fm-v", "aggregation": "sum", "direction": "descending"}},
        yAxis={"columnIds": ["fm-v"]}, orientation="horizontal", color={"by": "single", "value": AMBER}))
    nodes.append(el("f-mkt", 17, 25, 92, 103))

    # --- methodology ----------------------------------------------------------------------------------------
    add(container("f-how-c", bg=CARD_ALT))
    add(text("f-how", '**How the risk score works**\n\n'
             'Each provider earns up to **100 points** from five transparent signals, measured against the live network average '
             'under your filters:\n\n'
             '- **30** denial rate vs network\n- **20** high-cost claim share\n- **25** average billed ÷ procedure benchmark\n'
             '- **15** share of *extreme-cost* lines (≥ the multiple set above)\n- **10** services billed after the member\'s date of death\n\n'
             'Claim lines score **55** for extreme cost and **45** for post-death billing; lines at 40+ are surfaced below. '
             'Cortex narrates the result — it never changes the score.', verticalAlign="top"))
    nodes.append(box("f-how-c", 1, 25, 104, 117, [el("f-how", 1, 25, 1, 12)]))

    # --- suspicious claim lines ------------------------------------------------------------------------------
    nodes.append(el("sec-f-3", 1, 25, 119, 121)); add(sec("sec-f-3", "Suspicious claim lines", "rule-engine hits — click a line to review, flag and comment"))
    susp = next(t for t in TABLES if t["id"] == "t-susp")
    susp["style"] = card_style()
    susp["conditionalFormats"] = [
        {"type": "dataBars", "columnIds": ["sc_pts"], "scheme": [RED, CARD_ALT]},
        {"type": "single", "columnIds": ["sc_review"], "condition": "formula", "formula": '[Review Status] != "Not reviewed"',
         "style": {"backgroundColor": "#E6F4EC", "color": GREEN}}]
    nodes.append(el("t-susp", 1, 25, 121, 148))

    # --- write-back log ----------------------------------------------------------------------------------------
    nodes.append(el("sec-f-4", 1, 25, 149, 151)); add(sec("sec-f-4", "Analyst flag log", "every flag and comment is written back to the warehouse"))
    flog = next(t for t in TABLES if t["id"] == "it-flags")
    flog["style"] = card_style()
    nodes.append(el("it-flags", 1, 25, 151, 172))
    return nodes


# ====================================================================== modals (flag + comment)
def flag_modal(kind):
    """kind: 'prov' | 'claim'. Returns (overlay_def, layout_page_xml)."""
    P = "mp" if kind == "prov" else "mc"
    if kind == "prov":
        title, sel_tbl, src = "Review provider", "Selected Provider", "it-prov"
    else:
        title, sel_tbl, src = "Review claim line", "Selected Claim", "t-susp"
    tid = "t-selprov" if kind == "prov" else "t-selclaim"
    idctl = "sel_prov_id" if kind == "prov" else "sel_claim_id"
    # selected-entity detail table (normal table so the control can filter it)
    if kind == "prov":
        cols = [col("sp_id", "Provider Id", R(PR, "Provider Id")), col("sp_nm", "Provider", R(PR, "Provider")),
                col("sp_fac", "Primary Facility", R(PR, "Primary Facility")), col("sp_score", "Risk Score", R(PR, "Risk Score"), INT),
                col("sp_tier", "Risk Tier", R(PR, "Risk Tier")), col("sp_drv", "Top Risk Driver", R(PR, "Top Risk Driver")),
                col("sp_lines", "Claim Lines", R(PR, "Claim Lines"), INT), col("sp_paid", "Paid", R(PR, "Paid"), MONEY),
                col("sp_dr", "Denial Rate", R(PR, "Denial Rate"), PCT1), col("sp_hs", "High-Cost Share", R(PR, "High-Cost Share"), PCT1),
                col("sp_ratio", "Avg Cost Ratio", R(PR, "Avg Cost Ratio"), DEC2), col("sp_ext", "Extreme-Cost Lines", R(PR, "Extreme-Cost Lines"), INT),
                col("sp_pd", "Post-Death Lines", R(PR, "Post-Death Lines"), INT), col("sp_st", "Review Status", R(PR, "Review Status"))]
        filt = ("sp_id", "sel_prov_id")
    else:
        cols = [col("sk_id", "Claim Line Id", R("Suspicious Claim Lines", "Claim Line Id")),
                col("sk_prov", "Provider", R("Suspicious Claim Lines", "Provider")),
                col("sk_fac", "Facility", R("Suspicious Claim Lines", "Facility")),
                col("sk_proc", "Procedure", R("Suspicious Claim Lines", "Procedure")),
                col("sk_billed", "Billed", R("Suspicious Claim Lines", "Billed"), MONEY2),
                col("sk_avg", "Procedure Avg Billed", R("Suspicious Claim Lines", "Procedure Avg Billed"), MONEY2),
                col("sk_ratio", "Cost Ratio", R("Suspicious Claim Lines", "Cost Ratio"), DEC1),
                col("sk_paid", "Paid", R("Suspicious Claim Lines", "Paid"), MONEY2),
                col("sk_rule", "Rule Hit", R("Suspicious Claim Lines", "Rule Hit")),
                col("sk_pts", "Risk Points", R("Suspicious Claim Lines", "Risk Points"), INT),
                col("sk_st", "Review Status", R("Suspicious Claim Lines", "Review Status"))]
        filt = ("sk_id", "sel_claim_id")
    TABLES.append({"id": tid, "kind": "table", "name": sel_tbl, "source": {"kind": "table", "elementId": src},
                   "columns": cols,
                   "style": card_style(), "tableComponents": {"summaryBar": "hidden"},
                   "tableStyle": {"preset": "presentation", "cellSpacing": "small", "gridLines": "horizontal"}})
    # AI note (Cortex) bound to the selected entity
    if kind == "prov":
        prompt = ('"You are an SIU analyst assistant. In two sentences explain why this provider is risk-ranked as it is and what '
                  'to verify first. Be factual and do not accuse. Provider " & Max(%s) & " (" & Max(%s) & ") score " & Text(Max(%s)) & '
                  '", tier " & Max(%s) & ", main driver " & Max(%s) & ". " & Text(Max(%s)) & " claim lines, paid $" & Text(Round(Max(%s), 0)) & '
                  '", denial rate " & Text(Round(Max(%s) * 100, 1)) & " percent, high-cost share " & Text(Round(Max(%s) * 100, 2)) & '
                  '" percent, billed " & Text(Round(Max(%s), 2)) & "x the procedure average, " & Text(Max(%s)) & " extreme-cost lines, " & '
                  'Text(Max(%s)) & " post-death lines."'
                  % tuple(R(sel_tbl, n) for n in ["Provider", "Primary Facility", "Risk Score", "Risk Tier", "Top Risk Driver", "Claim Lines",
                                                  "Paid", "Denial Rate", "High-Cost Share", "Avg Cost Ratio", "Extreme-Cost Lines", "Post-Death Lines"]))
    else:
        prompt = ('"You are an SIU analyst assistant. In two sentences say what is suspicious about this claim line and what document '
                  'to request first. Be factual and do not accuse. Provider " & Max(%s) & ", procedure " & Max(%s) & ", billed $" & '
                  'Text(Round(Max(%s), 2)) & " versus a procedure average of $" & Text(Round(Max(%s), 2)) & " (" & Text(Round(Max(%s), 1)) & '
                  '"x), rule hit: " & Max(%s) & "."'
                  % tuple(R(sel_tbl, n) for n in ["Provider", "Procedure", "Billed", "Procedure Avg Billed", "Cost Ratio", "Rule Hit"]))
    add(text(P + "-ai", '<span style="color:%s">**◆ AI NOTE**</span>　{{Replace(CallText("SNOWFLAKE.CORTEX.COMPLETE", "CLAUDE-4-SONNET", %s), \'"\', "")}}'
             % (BLUE, prompt), verticalAlign="top"))
    add(container(P + "-brief", bg="#EAF6FC", border="#BFE3F3"))
    add(container(P + "-form", bg=CARD_ALT))
    add(text(P + "-hd", '**Flag & comment**　<span style="color:%s">Saved to the Flag Log with your name and timestamp.</span>' % MUTED))
    CTL.append(ctl_segmented("c-%s-status" % P, "%s_status" % P, "Decision", FLAG_STATUSES, "Suspicious"))
    CTL.append(ctl_segmented("c-%s-sev" % P, "%s_sev" % P, "Severity", FLAG_SEV, "Medium"))
    CTL.append({"id": "c-%s-comment" % P, "kind": "control", "controlId": "%s_comment" % P, "name": "Analyst comment",
                "controlType": "text-area", "value": ""})
    ent_type = "Provider" if kind == "prov" else "Claim"
    eid_ctl = "sel_prov_id" if kind == "prov" else "sel_claim_id"
    name_expr = ({"type": "control", "control": "sel_prov_name"} if kind == "prov"
                 else {"type": "formula", "formula": "Max(%s)" % R(sel_tbl, "Provider")})
    score_expr = ({"type": "formula", "formula": "Max(%s)" % R(sel_tbl, "Risk Score")} if kind == "prov"
                  else {"type": "formula", "formula": "Max(%s)" % R(sel_tbl, "Risk Points")})
    save = [
        {"effect": "insert-rows", "tableElementId": "it-flags", "values": {
            "fl_type": {"type": "constant", "value": {"type": "text", "value": ent_type}},
            "fl_eid": {"type": "control", "control": eid_ctl},
            "fl_ename": name_expr,
            "fl_status": {"type": "control", "control": "%s_status" % P},
            "fl_sev": {"type": "control", "control": "%s_sev" % P},
            "fl_score": score_expr,
            "fl_comment": {"type": "control", "control": "%s_comment" % P}}},
        {"effect": "clear-control", "scope": {"type": "control", "controlId": "%s_comment" % P}},
        {"effect": "close-overlay"},
    ]
    add(button("b-%s-save" % P, "Save flag", save, bg=RED))
    add(button("b-%s-cancel" % P, "Cancel", [{"effect": "clear-control", "scope": {"type": "control", "controlId": "%s_comment" % P}},
                                              {"effect": "close-overlay"}], bg="#FFFFFF", fg=NAVY, appearance="outline"))
    # the id holders are filter controls for the detail table
    kids = [
        el(tid, 1, 25, 1, 11),
        box(P + "-brief", 1, 25, 11, 19, [el(P + "-ai", 1, 25, 1, 7)]),
        box(P + "-form", 1, 25, 19, 40, [
            el(P + "-hd", 1, 25, 1, 3),
            el("c-%s-status" % P, 1, 25, 3, 8), el("c-%s-sev" % P, 1, 25, 8, 13),
            el("c-%s-comment" % P, 1, 25, 13, 18),
            el("b-%s-cancel" % P, 1, 12, 18, 21), el("b-%s-save" % P, 12, 25, 18, 21)]),
    ]
    page = '<Page type="grid" gridTemplateColumns="repeat(24, 1fr)" gridTemplateRows="auto" id="m-%s">\n%s\n</Page>' % (
        "prov" if kind == "prov" else "claim", "\n".join(k.xml() for k in kids))
    overlay = {"id": "m-%s" % ("prov" if kind == "prov" else "claim"), "type": "modal", "name": title,
               "modal": {"width": "large", "header": {"title": title, "showCloseIcon": "shown"},
                         "footer": {"primaryCta": {"visible": "hidden"}, "secondaryCta": {"visible": "hidden"}}}}
    return overlay, page, filt


# ====================================================================== assembly
def build():
    global E
    pages_nodes = {PG_OV: page_overview(), PG_GEO: page_geo(), PG_PROC: page_proc(), PG_FR: page_fraud()}
    ov_prov, pg_prov, (prov_tid_col, prov_ctl) = flag_modal("prov")
    ov_claim, pg_claim, (claim_tid_col, claim_ctl) = flag_modal("claim")
    # id holders (also filter the selected-entity detail table)
    CTL.append({"id": "c-sel-prov", "kind": "control", "controlId": "sel_prov_id", "name": "Selected provider id",
                "controlType": "text", "case": "insensitive", "mode": "equals", "value": "", "showOperators": False,
                "includeNulls": "when-no-value-is-selected",
                "filters": [{"source": {"kind": "table", "elementId": "t-selprov"}, "columnId": prov_tid_col}]})
    CTL.append(ctl_value_text("c-sel-prov-name", "sel_prov_name", "Selected provider name"))
    CTL.append({"id": "c-sel-claim", "kind": "control", "controlId": "sel_claim_id", "name": "Selected claim line id",
                "controlType": "text", "case": "insensitive", "mode": "equals", "value": "", "showOperators": False,
                "includeNulls": "when-no-value-is-selected",
                "filters": [{"source": {"kind": "table", "elementId": "t-selclaim"}, "columnId": claim_tid_col}]})
    CTL.append(ctl_value_text("c-sel-claim-prov", "sel_claim_prov", "Selected claim provider"))
    CTL.append(ctl_value_text("c-sel-claim-rule", "sel_claim_rule", "Selected claim rule"))

    agents = [{
        "id": "ag-siu", "name": "SIU Copilot",
        "description": "Special-investigations assistant for Independence claims: explains provider risk, surfaces suspicious claim lines and logs flags.",
        "instructions": (
            "You are the SIU copilot for Independence Blue Cross's Ohio claims book. You can see the provider risk leaderboard "
            "(Provider Risk), the rule-engine suspicious claim lines and the analyst Flag Log. The risk score (0-100) combines denial rate, "
            "high-cost claim share, average billed amount versus procedure benchmark, extreme-cost lines and services billed after a member's "
            "date of death. Be factual and quantitative, never accuse a provider of fraud - describe risk indicators and recommend what to "
            "verify. When the user asks to focus on a provider, use the focus tool; to change sensitivity use the alert-score tool; when "
            "the user asks you to flag or comment on a provider, use the flag tool with the exact Provider Id from the data."),
        "greeting": {"mode": "static", "message": "Ask me which providers look riskiest, why a score is high, or tell me to flag a provider with a comment."},
        "dataSources": [{"kind": "table", "elementId": "it-prov"}, {"kind": "table", "elementId": "t-susp"},
                        {"kind": "table", "elementId": "it-flags"}],
        "tools": [
            {"toolId": "t-focus", "kind": "action", "name": "Focus provider search",
             "description": "Filter the Fraud page to providers whose name contains the given text.",
             "steps": [{"kind": "effect", "effect": "set-control-value", "control": "f_search",
                        "value": {"type": "agent-input", "inputName": "Provider name"}}]},
            {"toolId": "t-alert", "kind": "action", "name": "Set critical alert score",
             "description": "Change the risk score at or above which a provider is Critical (20-80).",
             "steps": [{"kind": "effect", "effect": "set-control-value", "control": "alert_score",
                        "value": {"type": "agent-input", "inputName": "Alert score"}}]},
            {"toolId": "t-flag", "kind": "action", "name": "Flag a provider",
             "description": "Write a flag and comment for a provider to the analyst Flag Log.",
             "steps": [{"kind": "effect", "effect": "insert-rows", "tableElementId": "it-flags", "values": {
                 "fl_type": {"type": "constant", "value": {"type": "text", "value": "Provider"}},
                 "fl_eid": {"type": "agent-input", "inputName": "Provider Id"},
                 "fl_ename": {"type": "agent-input", "inputName": "Provider name"},
                 "fl_status": {"type": "agent-input", "inputName": "Decision: Suspicious, Under investigation, Confirmed fraud or Cleared"},
                 "fl_sev": {"type": "agent-input", "inputName": "Severity: Low, Medium, High or Critical"},
                 "fl_comment": {"type": "agent-input", "inputName": "Comment"}}}]},
        ],
    }]

    # ---- page layout xml ------------------------------------------------------------------------------
    placed = set()
    xml = []
    for pid, _ in PAGES:
        nodes = pages_nodes[pid]
        for n in nodes:
            placed.update(n.ids())
        xml.append(page_xml(pid, nodes))
    # overlay pages
    xml.append(pg_prov)
    xml.append(pg_claim)
    import re
    for pg in (pg_prov, pg_claim):
        placed.update(re.findall(r'elementId="([^"]+)"', pg))

    all_els = CTL + TABLES + E
    seen = set()
    for e in all_els:
        assert e["id"] not in seen, "duplicate element id %s" % e["id"]
        seen.add(e["id"])
    leftover = [e["id"] for e in all_els if e["id"] not in placed]
    # hidden data page: stack every un-placed element
    rows, r = [], 1
    for eid in leftover:
        rows.append('  <Element elementId="%s" gridColumn="1 / 25" gridRow="%d / %d"/>' % (eid, r, r + 3))
        r += 3
    xml.append('<Page type="grid" gridTemplateColumns="repeat(24, 1fr)" gridTemplateRows="auto" id="%s">\n%s\n</Page>' % (PG_DATA, "\n".join(rows)))
    missing = [i for i in placed if i not in seen and not i.startswith("hdr-")]
    assert not missing, "layout references unknown elements: %s" % missing

    layout = '<?xml version="1.0" encoding="utf-8"?>\n' + "\n".join(xml)
    pages = [{"id": p, "name": n, "backgroundColor": PAPER} for p, n in PAGES]
    pages.append({"id": PG_DATA, "name": "Data", "visibility": "hidden", "backgroundColor": PAPER})
    document = {
        "schemaVersion": 1, "kind": "workbook", "elements": all_els, "pages": pages,
        "overlays": [ov_prov, ov_claim], "layout": layout, "agents": agents,
        "settings": {
            "theme": {"name": "Light", "overrides": {
                "colors": {"text": INK, "surface": CARD, "highlight": BLUE, "success": GREEN, "warning": AMBER, "danger": RED},
                "categoricalScheme": CATEGORICAL,
                "borderRadius": "round", "hasCards": "shown",
                "elementBorder": {"color": RULE, "width": 1},
                "space": {"unit": "medium", "showElementPadding": "shown"},
                "tableStyles": {"preset": "presentation", "cellSpacing": "small", "gridLines": "horizontal"},
                "pageWidth": "large"}},
            "navigation": {"pageHeader": "disabled", "pageTabsInViewMode": "hidden"},
        },
    }
    return {"name": WB_NAME, "folderId": FOLDER,
            "description": "Claims analytics and AI-assisted fraud detection for Independence Blue Cross on the MEMBER_CLAIMS_PRODUCTION data model.",
            "document": document}


if __name__ == "__main__":
    body = build()
    json.dump(body, open(HERE / "spec.json", "w"))
    print("elements:", len(body["document"]["elements"]), "layout chars:", len(body["document"]["layout"]))
    if len(sys.argv) > 1 and sys.argv[1] in ("create", "update", "verify"):
        if sys.argv[1] == "verify":
            s, r = api.call("POST", "/v2/workbooks/spec/verify", body)
        elif sys.argv[1] == "create":
            s, r = api.call("POST", "/v2/workbooks/spec", body)
        else:
            s, r = api.call("PUT", "/v2/workbooks/%s/spec" % sys.argv[2], body)
        print(sys.argv[1], s, json.dumps(r)[:1500])
        if sys.argv[1] == "create" and s == 200:
            (HERE / "workbook_id.txt").write_text(r["workbookId"])
