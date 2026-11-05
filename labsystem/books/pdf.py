"""Real PDF files for download and email attachments, drawn from the same context
dicts as the print pages. ReportLab because it needs no system libraries, so it
behaves the same on App Platform, a droplet and a developer's laptop."""
import io

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.renderSVG import drawToString
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

from .documents import format_money

BRAND = colors.HexColor("#2f4cff")
INK = colors.HexColor("#14161c")
BODY = colors.HexColor("#4a4f5b")
MUTED = colors.HexColor("#767b88")
RULE = colors.HexColor("#e2e5ec")
LOGO_INK = colors.HexColor("#1C2230")
STAMP_COLOURS = {
    "PAID": colors.HexColor("#1f9d55"), "OVERDUE": colors.HexColor("#b42318"),
    "VOID": colors.HexColor("#b42318"), "DRAFT": MUTED,
}
PAGE_WIDTH = A4[0] - 30 * mm

# The same outline as static/images/ternah_logo.svg.
LOGO_VIEW = (12, 12, 77, 70)
LOGO_PATHS = (
    [("M", 27, 14), ("L", 45, 14), ("C", 47, 14, 48, 16, 47, 18), ("L", 38, 41), ("C", 37, 43, 34, 43, 33, 41),
     ("L", 24, 19), ("C", 23, 16, 24, 14, 27, 14)],
    [("M", 58, 14), ("L", 84, 14), ("C", 86, 14, 87, 16, 87, 18), ("L", 87, 34), ("C", 87, 37, 84, 38, 82, 36),
     ("L", 74, 30), ("L", 34, 78), ("C", 33, 79, 31, 80, 29, 80), ("L", 18, 80), ("C", 15, 80, 14, 77, 16, 75), ("L", 58, 14)],
    [("M", 73, 78), ("L", 55, 78), ("C", 53, 78, 52, 76, 53, 74), ("L", 62, 51), ("C", 63, 49, 66, 49, 67, 51),
     ("L", 76, 73), ("C", 77, 76, 76, 78, 73, 78)],
)


def _style(name, **kwargs):
    base = {"fontName": "Helvetica", "fontSize": 9.5, "leading": 13.5, "textColor": INK}
    base.update(kwargs)
    return ParagraphStyle(name, **base)


NORMAL = _style("normal")
BODY_TEXT = _style("body", textColor=BODY)
SMALL = _style("small", fontSize=8.5, leading=12, textColor=MUTED)
LABEL = _style("label", textColor=MUTED)
STRONG = _style("strong", fontName="Helvetica-Bold")
WHO = _style("who", fontName="Helvetica-Bold", fontSize=11, leading=14)
RIGHT = _style("right", alignment=TA_RIGHT)
COMPANY = _style("company", fontName="Helvetica-Bold", fontSize=17, leading=20)
HEADING = _style("heading", fontName="Helvetica-Bold", fontSize=14, leading=17, textColor=BRAND, alignment=TA_RIGHT)
DOCNUM = _style("docnum", fontName="Helvetica-Bold", fontSize=11, leading=14, alignment=TA_RIGHT)
BLOCK_TITLE = _style("blocktitle", fontName="Helvetica-Bold", spaceAfter=3)


def _text(value) -> str:
    """Escapes first, so text from the database can never break the layout
    (or print its markup, as the old PDF's <br/> did)."""
    return escape(str(value or "")).replace("\r\n", "\n").replace("\n", "<br/>")


def _date(value, short=False) -> str:
    if not value:
        return ""
    return f"{value.day} {value.strftime('%b %Y' if short else '%B %Y')}"


class Logo(Flowable):
    def __init__(self, height=11 * mm, colour=LOGO_INK):
        super().__init__()
        self.height = height
        self.width = height * LOGO_VIEW[2] / LOGO_VIEW[3]
        self.colour = colour

    def draw(self):
        canvas = self.canv
        scale = self.height / LOGO_VIEW[3]
        canvas.saveState()
        canvas.setFillColor(self.colour)
        # SVG counts y downwards from the top; PDF upwards from the bottom.
        x = lambda v: (v - LOGO_VIEW[0]) * scale
        y = lambda v: self.height - (v - LOGO_VIEW[1]) * scale
        for commands in LOGO_PATHS:
            path = canvas.beginPath()
            for op, *points in commands:
                if op == "M":
                    path.moveTo(x(points[0]), y(points[1]))
                elif op == "L":
                    path.lineTo(x(points[0]), y(points[1]))
                else:
                    path.curveTo(x(points[0]), y(points[1]), x(points[2]), y(points[3]), x(points[4]), y(points[5]))
            path.close()
            canvas.drawPath(path, stroke=0, fill=1)
        canvas.restoreState()


class QrCode(Flowable):
    def __init__(self, value, size=22 * mm):
        super().__init__()
        self.value, self.width, self.height = value, size, size

    def draw(self):
        _qr_drawing(self.value, self.width).drawOn(self.canv, 0, 0)


def _qr_drawing(value, size):
    widget = QrCodeWidget(value)
    x0, y0, x1, y1 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    drawing.add(widget)
    return drawing


def qr_svg(value, size=78) -> str:
    """The same code as an inline SVG for the print page."""
    svg = drawToString(_qr_drawing(value, size))
    return svg[svg.index("<svg"):]


def _header(ctx):
    company = ctx["company"]
    name = company.get("legal_name") or company.get("trading_name") or ""
    identity = Table(
        [[Logo(), Paragraph(_text(name), COMPANY)]],
        colWidths=[15 * mm, 95 * mm], style=[("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0)],
    )
    address = company.get("address") or ""
    if company.get("city"):
        address = f"{address}, {company['city']}" if address else company["city"]
    details = [line for line in (company.get("tagline"), address, company.get("phone"), company.get("email")) if line]
    left = [identity, Spacer(1, 2 * mm)] + [Paragraph(_text(line), SMALL) for line in details]
    right = [Paragraph(_text(ctx["heading"]), HEADING), Paragraph(_text(ctx.get("number") or "Draft"), DOCNUM)]
    table = Table([[left, right]], colWidths=[PAGE_WIDTH - 55 * mm, 55 * mm])
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return table


def _party_block(label, client, extra=()):
    parts = [Paragraph(_text(label), LABEL)]
    name = client.get("name", "")
    if client.get("trading_name"):
        name = f"{name} ({client['trading_name']})"
    parts.append(Paragraph(_text(name), WHO))
    for line in (client.get("address"), client.get("email"), client.get("phone")):
        if line:
            parts.append(Paragraph(_text(line), NORMAL))
    if client.get("tin"):
        parts.append(Paragraph(f"TIN {_text(client['tin'])}", NORMAL))
    for line in extra:
        parts.append(Paragraph(_text(line), NORMAL))
    return parts


def _meta(left_cells, rows):
    data = [[Paragraph(_text(k), SMALL), Paragraph(_text(v), RIGHT)] for k, v in rows if v]
    dates = Table(data, colWidths=[24 * mm, 48 * mm])
    dates.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    table = Table([[left_cells, dates]], colWidths=[PAGE_WIDTH - 72 * mm, 72 * mm])
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return table


def _lines(headers, rows, widths, numeric_from=1):
    """`headers` then `rows` of cells; columns from `numeric_from` are right-aligned."""
    data = [[Paragraph(_text(h), _style("th", fontSize=8.5, textColor=MUTED, alignment=TA_RIGHT if i >= numeric_from else 0)) for i, h in enumerate(headers)]] + rows
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, INK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE),
        ("ALIGN", (numeric_from, 1), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 1), (-1, -1), 6), ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 1), (-1, -1), 9.5), ("TEXTCOLOR", (0, 1), (-1, -1), INK),
    ]))
    return table


def _description(title, detail=""):
    cells = [Paragraph(_text(title), STRONG)]
    if detail:
        cells.append(Paragraph(_text(detail), LABEL))
    return cells


def _totals(rows, rule_on=()):
    data = [[Paragraph(_text(k), LABEL), Paragraph(_text(v), RIGHT)] for k, v in rows]
    table = Table(data, colWidths=[52 * mm, 36 * mm], hAlign="RIGHT")
    style = [("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    style += [("LINEABOVE", (0, i), (-1, i), 0.4, RULE) for i in rule_on]
    table.setStyle(TableStyle(style))
    return table


def _amount_box(label, currency, amount, colour=None):
    colour = colour or INK
    value = Paragraph(
        f'<font size="9.5" color="#767b88"><b>{_text(currency)}</b></font>&nbsp; <font size="19"><b>{_text(format_money(amount, currency))}</b></font>',
        _style("amount", alignment=TA_RIGHT, leading=22, textColor=colour),
    )
    box = Table([[Paragraph(f"<b>{_text(label)}</b>", _style("amountlabel", fontSize=11, leading=14)), value]], colWidths=[46 * mm, 54 * mm], hAlign="RIGHT")
    box.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, colour if colour != INK else BRAND),
        ("LEFTPADDING", (0, 0), (0, -1), 6 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return box


def _info_block(title, rows, note=""):
    parts = [Paragraph(_text(title), BLOCK_TITLE)]
    if rows:
        table = Table([[Paragraph(_text(k), SMALL), Paragraph(_text(v), _style("v", fontSize=8.5, leading=12))] for k, v in rows], colWidths=[28 * mm, 54 * mm], hAlign="LEFT")
        table.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        parts.append(table)
    if note:
        parts.append(Paragraph(_text(note), SMALL))
    return parts


def _pay_rows(company):
    rows = []
    if company.get("bank_name"):
        rows.append(("Bank", company["bank_name"]))
        if company.get("bank_account_name"):
            rows.append(("Account name", company["bank_account_name"]))
        if company.get("bank_account_number"):
            rows.append(("Account no.", company["bank_account_number"]))
    for key, label in (("mtn", "MTN MoMo"), ("airtel", "Airtel Money")):
        number = company.get(f"momo_{key}_number")
        if number:
            name = company.get(f"momo_{key}_name")
            rows.append((label, f"{number} ({name})" if name else number))
    return rows


def _build(ctx, story):
    buffer = io.BytesIO()
    stamp = ctx.get("stamp")
    company = ctx["company"]

    def page(canvas, doc):
        canvas.saveState()
        width, height = A4
        canvas.setFillColor(BRAND)
        canvas.roundRect(15 * mm, height - 15 * mm, width - 30 * mm, 1.4 * mm, 0.7 * mm, stroke=0, fill=1)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        footer = company.get("legal_name") or ""
        if company.get("tin"):
            footer += f"  |  TIN {company['tin']}"
        canvas.drawString(15 * mm, 9 * mm, footer)
        canvas.drawRightString(width - 15 * mm, 9 * mm, f"Page {doc.page}")
        if stamp and doc.page == 1:
            colour = STAMP_COLOURS.get(stamp, MUTED)
            canvas.translate(width - 52 * mm, height - 55 * mm)
            canvas.rotate(-12)
            canvas.setStrokeColor(colour)
            canvas.setFillColor(colour)
            canvas.setLineWidth(1.6)
            canvas.setFont("Helvetica-Bold", 15)
            text_width = canvas.stringWidth(stamp, "Helvetica-Bold", 15)
            canvas.roundRect(-text_width / 2 - 8, -7, text_width + 16, 24, 3, stroke=1, fill=0)
            canvas.drawCentredString(0, 0, stamp)
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=15 * mm - 6, rightMargin=15 * mm - 6, topMargin=20 * mm, bottomMargin=16 * mm,  # 6pt: the frame pads each side by that
        title=f"{ctx['heading']} {ctx.get('number') or ''}".strip(), author=company.get("legal_name") or "Ternah",
    )
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buffer.getvalue()


def _footer_notes(ctx):
    if not ctx.get("footer_notes"):
        return []
    return [Spacer(1, 8 * mm), Paragraph(_text(ctx["footer_notes"]), SMALL)]


def invoice_pdf(ctx) -> bytes:
    currency = ctx["currency"]
    money = lambda value: format_money(value, currency)
    rows = [("Issued", _date(ctx["issue_date"])), ("Due", _date(ctx["due_date"])), ("Reference", ctx.get("reference"))]
    rows += [("Our TIN", ctx["company"].get("tin")), ("VAT no.", ctx["company"].get("vrn"))]
    line_rows = [
        [_description(line["description"], line.get("detail")), f"{float(line['quantity']):g}", money(line["unit_price"]), money(line["amount"])]
        for line in ctx["lines"]
    ]
    total_rows = [("Subtotal", money(ctx["subtotal"]))]
    if ctx.get("tax_amount"):
        total_rows.append((ctx["tax_label"], money(ctx["tax_amount"])))
    rule_at = len(total_rows)
    total_rows.append(("Total", money(ctx["total"])))
    for key, label in (("amount_paid", "Received to date"), ("wht_credited", "Withholding tax credited"), ("credited", "Credit notes")):
        if ctx.get(key):
            total_rows.append((label, f"-{money(ctx[key])}"))
    if ctx.get("is_void"):
        box = _amount_box("Void: nothing due", currency, ctx["balance"])
    elif ctx.get("is_settled"):
        box = _amount_box("Settled in full", currency, ctx["balance"], STAMP_COLOURS["PAID"])
    else:
        box = _amount_box("Amount due", currency, ctx["balance"])
    story = [
        _header(ctx), Spacer(1, 8 * mm), _meta(_party_block("Billed to", ctx["client"]), rows), Spacer(1, 7 * mm),
        _lines(["Description", "Qty", "Rate", "Amount"], line_rows, [PAGE_WIDTH - 74 * mm, 14 * mm, 30 * mm, 30 * mm]),
        Spacer(1, 4 * mm), _totals(total_rows, rule_on=[rule_at]), Spacer(1, 6 * mm), box,
    ]
    blocks = []
    if ctx.get("installments"):
        blocks.append(_info_block("Payment schedule", [(_date(i["due_date"], short=True), money(i["amount"])) for i in ctx["installments"]]))
    pay_rows = _pay_rows(ctx["company"])
    if pay_rows:
        note = f"Quote {ctx['number']} on your payment so it is matched to this invoice." if ctx.get("number") else ""
        blocks.append(_info_block("How to pay", pay_rows, note))
    if blocks:
        while len(blocks) < 2:
            blocks.append("")
        story += [Spacer(1, 8 * mm), KeepTogether(Table([blocks], colWidths=[PAGE_WIDTH / 2, PAGE_WIDTH / 2], style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))]
    if ctx.get("notes"):
        story += [Spacer(1, 6 * mm)] + _info_block("Notes", [], ctx["notes"])
    return _build(ctx, story + _footer_notes(ctx))


def receipt_pdf(ctx) -> bytes:
    currency = ctx["currency"]
    money = lambda value: format_money(value, currency)
    rows = [("Date", _date(ctx["date"])), ("Method", ctx["method"]), ("Reference", ctx.get("reference")), ("Our TIN", ctx["company"].get("tin"))]
    allocation_rows = [
        [_description(f"Invoice {a['invoice_number']}", a["kind"]), money(a["invoice_total"]), money(a["amount"]), money(a["invoice_balance_after"])]
        for a in ctx["allocations"]
    ] or [[_description("Held as credit on account", "Not yet applied to an invoice."), "-", "-", "-"]]
    total_rows = []
    if ctx.get("unallocated"):
        total_rows.append(("Held as credit on account", money(ctx["unallocated"])))
    total_rows.append(("Account balance" if ctx.get("is_settled") else "Total still outstanding", money(ctx["client_balance"])))
    story = [
        _header(ctx), Spacer(1, 8 * mm), _meta(_party_block("Received from", ctx["client"]), rows), Spacer(1, 6 * mm),
        _amount_box("Amount received", currency, ctx["amount"]), Spacer(1, 7 * mm),
        _lines(["Applied to", "Invoice total", "Applied", "Now outstanding"], allocation_rows, [PAGE_WIDTH - 90 * mm, 30 * mm, 28 * mm, 32 * mm]),
        Spacer(1, 4 * mm), _totals(total_rows, rule_on=[len(total_rows) - 1]),
    ]
    if ctx.get("verify_url"):
        story += [Spacer(1, 7 * mm), Table(
            [[QrCode(ctx["verify_url"]), Paragraph("Scan to confirm this receipt is genuine.", SMALL)]],
            colWidths=[26 * mm, 70 * mm], hAlign="LEFT", style=[("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)],
        )]
    return _build(ctx, story + _footer_notes(ctx))


def statement_pdf(ctx) -> bytes:
    currency = ctx["currency"]
    money = lambda value: format_money(value, currency)
    rows = [("From", _date(ctx.get("start"))), ("To", _date(ctx.get("end")) or _date(ctx.get("today"))), ("Our TIN", ctx["company"].get("tin"))]
    event_rows = [
        [_date(e["date"], short=True), Paragraph(_text(e["label"]), STRONG), money(e["debit"]) if e["debit"] else "", money(e["credit"]) if e["credit"] else "", money(e["running_balance"])]
        for e in ctx["events"]
    ] or [["", Paragraph("Nothing recorded.", NORMAL), "", "", ""]]
    box = _amount_box("Nothing outstanding", currency, ctx["closing_balance"], STAMP_COLOURS["PAID"]) if ctx.get("is_settled") else _amount_box("Balance due", currency, ctx["closing_balance"])
    story = [
        _header(ctx), Spacer(1, 8 * mm), _meta(_party_block("Statement for", ctx["client"]), rows), Spacer(1, 7 * mm),
        _lines(["Date", "Details", "Charges", "Payments", "Balance"], event_rows, [24 * mm, PAGE_WIDTH - 102 * mm, 26 * mm, 26 * mm, 26 * mm], numeric_from=2),
        Spacer(1, 6 * mm), box,
    ]
    return _build(ctx, story + _footer_notes(ctx))


def credit_note_pdf(ctx) -> bytes:
    currency = ctx["currency"]
    money = lambda value: format_money(value, currency)
    rows = [("Date", _date(ctx["date"])), ("Against invoice", ctx["invoice_number"]), ("Our TIN", ctx["company"].get("tin"))]
    total_rows = [("Net", money(ctx["net_amount"]))]
    if ctx.get("tax_amount"):
        total_rows.append((ctx["tax_label"], money(ctx["tax_amount"])))
    total_rows.append(("Invoice balance now", money(ctx["invoice_balance"])))
    story = [
        _header(ctx), Spacer(1, 8 * mm), _meta(_party_block("Credited to", ctx["client"]), rows), Spacer(1, 6 * mm),
        _amount_box("Amount credited", currency, ctx["amount"]), Spacer(1, 7 * mm),
        _lines(["Reason"], [[_description(ctx["reason"])]], [PAGE_WIDTH]), Spacer(1, 4 * mm), _totals(total_rows, rule_on=[len(total_rows) - 1]),
    ]
    return _build(ctx, story + _footer_notes(ctx))


def quotation_pdf(ctx) -> bytes:
    currency = ctx["currency"]
    money = lambda value: format_money(value, currency)
    extra = []
    if ctx.get("attention"):
        extra.append(f"Attn: {ctx['attention']}")
    if ctx.get("location"):
        extra.append(ctx["location"])
    rows = [("Date", _date(ctx["issue_date"])), ("Valid until", _date(ctx["valid_until"])), ("Currency", ctx["currency_label"]), ("Our TIN", ctx["company"].get("tin")), ("Reg. no.", ctx.get("reg_no"))]
    rows += [(field["label"], field["value"]) for field in ctx.get("extra_fields", [])]
    line_rows = [[_description(line["title"], line.get("description")), Paragraph(_text(line.get("basis")), LABEL), money(line["amount"])] for line in ctx["lines"]]
    vat = money(ctx["tax_amount"]) if ctx.get("apply_vat") and ctx.get("tax_amount") else "Not applicable"
    party = [Paragraph(_text(ctx["subtitle"]), STRONG), Spacer(1, 2 * mm)] if ctx.get("subtitle") else []
    story = [_header(ctx), Spacer(1, 8 * mm), _meta(party + _party_block("Prepared for", ctx["client"], extra), rows), Spacer(1, 5 * mm)]
    if ctx.get("intro"):
        story += [Paragraph(_text(ctx["intro"]), BODY_TEXT)]
    story += [
        Spacer(1, 4 * mm),
        _lines(["Description", "Basis", f"Amount ({currency})"], line_rows, [PAGE_WIDTH - 72 * mm, 40 * mm, 32 * mm], numeric_from=2),
        Spacer(1, 3 * mm), _totals([("Subtotal", money(ctx["subtotal"])), ("VAT", vat)]), Spacer(1, 4 * mm),
        _amount_box("Total", currency, ctx["total"]), Spacer(1, 3 * mm),
        Paragraph(f"<b>Amount in words:</b> {_text(ctx['amount_in_words'])}", BODY_TEXT),
    ]
    if ctx.get("highlight_title") or ctx.get("highlight_body"):
        story += [Spacer(1, 4 * mm)] + _info_block(ctx.get("highlight_title") or "", [], ctx.get("highlight_body") or "")
    notes = [
        _info_block(ctx.get(f"note_{side}_title") or "", [], ctx.get(f"note_{side}_body") or "") for side in ("left", "right")
        if ctx.get(f"note_{side}_title") or ctx.get(f"note_{side}_body")
    ]
    if notes:
        while len(notes) < 2:
            notes.append("")
        story += [Spacer(1, 4 * mm), Table([notes], colWidths=[PAGE_WIDTH / 2, PAGE_WIDTH / 2], style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)])]
    signature = Table(
        [[Paragraph(_text(f"Prepared by: {ctx['prepared_by_name']}") if ctx.get("prepared_by_name") else "", SMALL), Paragraph("Authorised signature &amp; date", _style("sign", fontSize=8.5, textColor=MUTED, alignment=1))]],
        colWidths=[PAGE_WIDTH - 62 * mm, 62 * mm], style=[("LINEABOVE", (1, 0), (1, 0), 0.6, INK), ("LEFTPADDING", (0, 0), (0, 0), 0)],
    )
    story += [Spacer(1, 16 * mm), KeepTogether(signature)]
    if ctx.get("footer_tagline"):
        story += [Spacer(1, 4 * mm), Paragraph(_text(ctx["footer_tagline"]), SMALL)]
    return _build(ctx, story)
