#render_resume.py

import json, sys, os, re
import html as _html
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor, black
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

# Carlito (Calibri-metric, OFL) is installed on the VM. Allow override via RESUME_FONTDIR.
_CARLITO_DIR = os.environ.get("RESUME_FONTDIR", "/usr/share/fonts/truetype/crosextra")
pdfmetrics.registerFont(TTFont("Carlito", f"{_CARLITO_DIR}/Carlito-Regular.ttf"))
pdfmetrics.registerFont(TTFont("Carlito-Bold", f"{_CARLITO_DIR}/Carlito-Bold.ttf"))
pdfmetrics.registerFont(TTFont("Carlito-Italic", f"{_CARLITO_DIR}/Carlito-Italic.ttf"))
pdfmetrics.registerFont(TTFont("Carlito-BoldItalic", f"{_CARLITO_DIR}/Carlito-BoldItalic.ttf"))
pdfmetrics.registerFontFamily("Carlito", normal="Carlito", bold="Carlito-Bold", italic="Carlito-Italic", boldItalic="Carlito-BoldItalic")

LINK = HexColor("#1155CC")       # contact hyperlinks
SECLBL = HexColor("#1F3A8A")     # section labels (dark blue)
GREY = HexColor("#EDEDED")       # section bar fill
DARKGREY = HexColor("#888888")
RULE = HexColor("#000000")

run = sys.argv[1]
path = os.path.join(run, "evaluations.jsonl")
rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
rows = [r for r in rows if r.get("tailored") and r["status"] == "Apply"]

# --- static, canonical content loaded from the single source of truth ---
_HERE = os.path.dirname(os.path.abspath(__file__))
CANON = json.load(open(os.path.join(_HERE, "canonical_resume.json"), encoding="utf-8"))
NAME = CANON["name"]
CONTACT_PARTS = [(t, bool(l)) for t, l in CANON["contact"]]
SUMMARY = CANON["summary"]
SKILLS = [(k, v) for k, v in CANON["skills"]]
EDU = [(a, b) for a, b in CANON["education"]]
# role -> date map (dates are canonical, not tailored)
EXP_META = {e["role"]: e.get("date", "") for e in CANON["experience"]}

MARGIN = 0.5 * inch
CONTENT_W = letter[0] - 2 * MARGIN

ST_NAME = ParagraphStyle("n", fontName="Carlito-Bold", fontSize=16.5, alignment=TA_CENTER, textColor=black, spaceAfter=3, leading=19)
ST_CONTACT = ParagraphStyle("c", fontName="Carlito", fontSize=10.2, alignment=TA_CENTER, textColor=black, leading=13)
ST_SEC = ParagraphStyle("sec", fontName="Carlito-Bold", fontSize=10.7, textColor=SECLBL, leading=12.5, alignment=TA_CENTER)
ST_ROLE = ParagraphStyle("r", fontName="Carlito-Bold", fontSize=10.5, leading=12.5)
ST_DATE = ParagraphStyle("d", fontName="Carlito-Italic", fontSize=10.2, leading=12.5, alignment=TA_RIGHT, textColor=black)

BASE_LEAD = 12.8
BASE_AFTER = 2.0

_METRIC = re.compile(r"(\$?\d[\d,\.]*\s?(?:%|[KMBkmb]\b|x\b|SKUs?\b)?(?:\+|-year| markets| records| customers| SKUs)?)")
def boldify(text):
    # Run the metric match on RAW text, escaping each segment afterward, so HTML
    # entities introduced by escaping (e.g. apostrophe -> &#x27;) can never be
    # matched and partially bolded (which produced &#x<b>27</b>; -> reportlab crash).
    out = []
    last = 0
    for m in _METRIC.finditer(text):
        tok = m.group(0)
        out.append(_html.escape(text[last:m.start()]))
        if re.search(r"\d", tok) and re.search(r"%|\$|x\b|[KMB]\b|,\d|\d{2,}", tok):
            out.append("<b>%s</b>" % _html.escape(tok))
        else:
            out.append(_html.escape(tok))
        last = m.end()
    out.append(_html.escape(text[last:]))
    return "".join(out)

def _body_styles(k):
    lead = BASE_LEAD * k
    after = BASE_AFTER * k
    summary = ParagraphStyle("s", fontName="Carlito", fontSize=10.2, alignment=TA_JUSTIFY, textColor=black, leading=lead, spaceAfter=after * 0.6)
    skill = ParagraphStyle("sk", fontName="Carlito", fontSize=10.2, leading=lead, spaceAfter=after * 0.6, alignment=TA_JUSTIFY)
    bullet = ParagraphStyle("b", fontName="Carlito", fontSize=10.2, leading=lead, leftIndent=13, bulletIndent=3, spaceAfter=after * 0.7, alignment=TA_JUSTIFY)
    group = ParagraphStyle("g", fontName="Carlito-BoldItalic", fontSize=10.2, leading=lead, spaceBefore=after * 0.5, spaceAfter=after * 0.3, textColor=black)
    proj = ParagraphStyle("p", fontName="Carlito", fontSize=10.2, leading=lead, spaceAfter=after, alignment=TA_JUSTIFY)
    edu = ParagraphStyle("e", fontName="Carlito", fontSize=10.2, leading=lead, spaceAfter=after * 0.6)
    edu_r = ParagraphStyle("er", fontName="Carlito-Italic", fontSize=10.2, leading=lead, spaceAfter=after * 0.6, alignment=TA_RIGHT, textColor=black)
    return summary, skill, bullet, group, proj, edu, edu_r

def contact_para():
    seg = []
    for txt, is_link in CONTACT_PARTS:
        if is_link:
            seg.append('<font color="#1155CC">%s</font>' % _html.escape(txt))
        else:
            seg.append(_html.escape(txt))
    joined = ('  <font color="#888888">|</font>  ').join(seg)
    return Paragraph(joined, ST_CONTACT)

def bar(t):
    tb = Table([[Paragraph(t, ST_SEC)]], colWidths=[CONTENT_W])
    tb.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GREY),
        ("LINEABOVE", (0, 0), (-1, 0), 0.8, RULE),
        ("LINEBELOW", (0, -1), (-1, -1), 0.8, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2.0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.0)]))
    return tb

def role_row(role, date):
    t = Table([[Paragraph(role, ST_ROLE), Paragraph(date, ST_DATE)]], colWidths=[CONTENT_W * 0.74, CONTENT_W * 0.26])
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("VALIGN", (0, 0), (-1, -1), "BOTTOM")]))
    return t

def edu_row(degree, meta, edu, edu_r):
    t = Table([[Paragraph(degree, edu), Paragraph(meta, edu_r)]], colWidths=[CONTENT_W * 0.70, CONTENT_W * 0.30])
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5), ("VALIGN", (0, 0), (-1, -1), "BOTTOM")]))
    return t

def _story(t, k):
    summary, skill, bullet, group, proj, edu, edu_r = _body_styles(k)
    sp = lambda v: Spacer(1, v * k)
    st = [Paragraph(NAME, ST_NAME), contact_para(), sp(3),
          HRFlowable(width="100%", thickness=0.8, color=RULE, spaceBefore=1, spaceAfter=4),
          Paragraph(SUMMARY, summary)]
    st += [sp(2), bar("SKILLS"), sp(2.5)]
    for kk, v in SKILLS:
        st.append(Paragraph('<b>%s:</b> %s' % (kk, v), skill))
    st += [sp(3), bar("EXPERIENCE")]
    for e in t["experience"]:
        # skip a role with no bullets in any group
        if not any(g["bullets"] for g in e.get("groups", [])):
            continue
        st.append(role_row(e["role"], EXP_META.get(e["role"], e.get("date", ""))))
        for g in e.get("groups", []):
            if not g["bullets"]:
                continue
            if g.get("label"):
                st.append(Paragraph(_html.escape(g["label"]), group))
            for b in g["bullets"]:
                st.append(Paragraph(boldify(b["replacement"]), bullet, bulletText="•"))
    st += [sp(3), bar("EDUCATION"), sp(2.5)]
    for a, b in EDU:
        st.append(edu_row(a, b, edu, edu_r))
    st += [sp(3), bar("RELEVANT PROJECTS"), sp(2.5)]
    for p in t["projects"]:
        st.append(Paragraph('<b>%s:</b> %s' % (_html.escape(p["name"]), boldify(p["replacement"])), proj))
    return st

def _render(story, out):
    doc = BaseDocTemplate(out, pagesize=letter, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=0.5 * inch, bottomMargin=0.45 * inch)
    fr = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="m", frames=[fr])])
    doc.build(story)
    return doc.page

def build(rec, out):
    t = rec["tailored"]
    for k in [round(1.0 - 0.025 * i, 3) for i in range(0, 16)]:
        if _render(_story(t, k), out) == 1:
            return k
    _render(_story(t, 0.625), out)
    return 0.625

def san(n):
    return re.sub(r"[^A-Za-z0-9]+", "_", n).strip("_")

seen = {}
made = []
for r in rows:
    base = san(r["companyName"])
    seen[base] = seen.get(base, 0) + 1
    fn = "Ranjith_%s.pdf" % base if seen[base] == 1 else "Ranjith_%s_%d.pdf" % (base, seen[base])
    k = build(r, os.path.join(run, fn))
    made.append((r["jobId"], fn, k))

print("Rendered", len(made), "PDFs (Letter / Carlito / grey bars / grouped experience / bold metrics)")
for jid, fn, k in made:
    print("  %s %s fit_scale=%s" % (jid, fn, k))
