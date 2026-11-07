from __future__ import annotations

import html
import math
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    LongTable,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


FONT_DIR = Path('/usr/share/fonts/truetype/dejavu')
pdfmetrics.registerFont(TTFont('DejaVu', str(FONT_DIR / 'DejaVuSans.ttf')))
pdfmetrics.registerFont(TTFont('DejaVu-Bold', str(FONT_DIR / 'DejaVuSans-Bold.ttf')))
pdfmetrics.registerFont(TTFont('DejaVu-Oblique', str(FONT_DIR / 'DejaVuSans-Oblique.ttf')))
pdfmetrics.registerFont(TTFont('DejaVuMono', str(FONT_DIR / 'DejaVuSansMono.ttf')))
pdfmetrics.registerFontFamily('DejaVu', normal='DejaVu', bold='DejaVu-Bold', italic='DejaVu-Oblique')
LATO_DIR = Path('/usr/share/fonts/truetype/lato')
pdfmetrics.registerFont(TTFont('Lato', str(LATO_DIR / 'Lato-Regular.ttf')))
pdfmetrics.registerFont(TTFont('Lato-Bold', str(LATO_DIR / 'Lato-Bold.ttf')))
pdfmetrics.registerFont(TTFont('Lato-Italic', str(LATO_DIR / 'Lato-Italic.ttf')))
pdfmetrics.registerFont(TTFont('Lato-Heavy', str(LATO_DIR / 'Lato-Heavy.ttf')))
pdfmetrics.registerFontFamily('Lato', normal='Lato', bold='Lato-Bold', italic='Lato-Italic')

INK = colors.HexColor('#233039')
TEAL = colors.HexColor('#12626b')
PALE = colors.HexColor('#eaf3f2')

NORMAL = ParagraphStyle('normal', fontName='Lato', fontSize=10.1, leading=15.0,
                        textColor=INK, spaceAfter=8, allowWidows=0, allowOrphans=0)
TITLE = ParagraphStyle('title', parent=NORMAL, fontName='Lato-Heavy', fontSize=23,
                       leading=28, textColor=colors.HexColor('#153b47'), spaceAfter=17,
                       keepWithNext=True)
H2 = ParagraphStyle('h2', parent=NORMAL, fontName='Lato-Heavy', fontSize=14,
                    leading=18, textColor=TEAL, spaceBefore=20, spaceAfter=9,
                    keepWithNext=True)
H3 = ParagraphStyle('h3', parent=NORMAL, fontName='Lato-Bold', fontSize=11.5,
                    leading=15.5, textColor=TEAL, spaceBefore=14, spaceAfter=7,
                    keepWithNext=True)
STEP = ParagraphStyle('step', parent=NORMAL, leftIndent=18, firstLineIndent=-18,
                      spaceAfter=7)
BULLET = ParagraphStyle('bullet', parent=NORMAL, leftIndent=18, firstLineIndent=-18,
                        spaceAfter=6)
CHECK = ParagraphStyle('check', parent=NORMAL, backColor=PALE, borderPadding=9,
                       borderColor=colors.HexColor('#a8c8c8'), borderWidth=0.7,
                       spaceBefore=8, spaceAfter=12, keepTogether=True)
CODE = ParagraphStyle('code', parent=NORMAL, fontName='DejaVuMono', fontSize=8.0,
                      leading=12.0, wordWrap='CJK', spaceAfter=0)
CELL = ParagraphStyle('cell', parent=NORMAL, fontSize=8.8, leading=12.3,
                      spaceAfter=0)
CELL_HEAD = ParagraphStyle('cell-head', parent=CELL, fontName='Lato-Bold',
                           textColor=colors.white)
FILEBOX = ParagraphStyle('filebox', parent=NORMAL, fontSize=9.4, leading=13.5,
                         spaceAfter=0)

RED = colors.HexColor('#b83838')
CHARCOAL = colors.HexColor('#26343d')
GOLD = colors.HexColor('#ad7900')
BLUE = colors.HexColor('#3278a6')
GREY = colors.HexColor('#8d9ba0')


def diagram_text(canvas, x: float, y: float, value: str, size: float = 8,
                 colour=INK, bold: bool = False, centred: bool = False) -> None:
    canvas.setFillColor(colour)
    canvas.setFont('Lato-Bold' if bold else 'Lato', size)
    if centred:
        canvas.drawCentredString(x, y, value)
    else:
        canvas.drawString(x, y, value)


def diagram_box(canvas, x: float, y: float, width: float, height: float,
                title: str, subtitle: str, fill=colors.white) -> None:
    canvas.setFillColor(fill)
    canvas.setStrokeColor(colors.HexColor('#a7c0c3'))
    canvas.setLineWidth(0.8)
    canvas.roundRect(x, y, width, height, 6, fill=1, stroke=1)
    diagram_text(canvas, x + width / 2, y + height / 2 + 4, title,
                 9, INK, True, True)
    diagram_text(canvas, x + width / 2, y + height / 2 - 10, subtitle,
                 7, INK, centred=True)


def diagram_line(canvas, x1: float, y1: float, x2: float, y2: float,
                 colour, dashed: bool = False, arrow: bool = False) -> None:
    canvas.setStrokeColor(colour)
    canvas.setFillColor(colour)
    canvas.setLineWidth(2)
    if dashed:
        canvas.setDash(4, 3)
    canvas.line(x1, y1, x2, y2)
    canvas.setDash()
    if arrow:
        dx, dy = x2 - x1, y2 - y1
        distance = math.hypot(dx, dy)
        ux, uy = dx / distance, dy / distance
        px, py = -uy, ux
        path = canvas.beginPath()
        path.moveTo(x2, y2)
        path.lineTo(x2 - 7 * ux + 3.5 * px, y2 - 7 * uy + 3.5 * py)
        path.lineTo(x2 - 7 * ux - 3.5 * px, y2 - 7 * uy - 3.5 * py)
        path.close()
        canvas.drawPath(path, fill=1, stroke=0)


class VectorDiagram(Flowable):
    base_width = 509

    def __init__(self, width: float, base_height: float):
        super().__init__()
        self.width = width
        self.height = base_height * width / self.base_width

    def draw(self) -> None:
        scale = self.width / self.base_width
        self.canv.saveState()
        self.canv.scale(scale, scale)
        self.draw_diagram(self.canv)
        self.canv.restoreState()


class ConnectionsDiagram(VectorDiagram):
    def __init__(self, width: float):
        super().__init__(width, 204)

    def draw_diagram(self, c) -> None:
        c.setStrokeColor(colors.HexColor('#c4d6d8'))
        c.setFillColor(colors.HexColor('#fbfcfc'))
        c.roundRect(0, 0, 509, 204, 7, fill=1, stroke=1)
        diagram_text(c, 15, 186, 'What is connected during the demo', 10, TEAL, True)

        diagram_box(c, 12, 122, 136, 49, 'MacBook Pro M1', 'card reader: setup only')
        diagram_box(c, 190, 122, 130, 49, 'iPhone hotspot', 'shared Wi-Fi', PALE)
        diagram_box(c, 362, 122, 135, 49, 'Android phone', 'Bluetooth transmitter')
        diagram_box(c, 12, 38, 136, 49, 'Wall charger', '5 V / 3 A output')
        diagram_box(c, 190, 38, 130, 49, 'Raspberry Pi 4', 'microSD card inside', PALE)
        diagram_box(c, 362, 38, 135, 49, 'Motor module', 'IN · VCC · GND')

        diagram_line(c, 148, 146, 190, 146, BLUE, dashed=True)
        diagram_text(c, 155, 155, 'Wi-Fi', 6.8, BLUE)
        diagram_line(c, 255, 122, 255, 87, BLUE, dashed=True)
        diagram_text(c, 264, 104, 'Wi-Fi', 6.8, BLUE)
        diagram_line(c, 362, 138, 320, 83, BLUE, dashed=True)
        diagram_text(c, 323, 112, 'Bluetooth', 6.8, BLUE)
        diagram_line(c, 148, 62, 190, 62, TEAL, arrow=True)
        diagram_text(c, 153, 71, 'USB-C', 6.8, TEAL)
        diagram_line(c, 320, 62, 362, 62, TEAL, arrow=True)
        diagram_text(c, 323, 71, '3 wires', 6.8, TEAL)
        diagram_text(
            c, 15, 15,
            'Solid = cable. Dashed = wireless. Move the microSD from laptop reader to Pi before power.',
            7, CHARCOAL,
        )


class PinDiagram(VectorDiagram):
    def __init__(self, width: float):
        super().__init__(width, 330)

    def draw_diagram(self, c) -> None:
        c.setStrokeColor(colors.HexColor('#c4d6d8'))
        c.setFillColor(colors.HexColor('#fbfcfc'))
        c.roundRect(0, 0, 509, 330, 7, fill=1, stroke=1)
        diagram_text(c, 15, 312, 'Raspberry Pi 4 pin locations · view from above', 10, TEAL, True)

        c.setFillColor(colors.HexColor('#e8f2eb'))
        c.setStrokeColor(colors.HexColor('#8ab19a'))
        c.roundRect(23, 185, 360, 115, 7, fill=1, stroke=1)
        diagram_text(c, 40, 211, 'Pi 4: chips facing up', 8, CHARCOAL, True)
        diagram_text(c, 40, 197, '40 pins run across its top edge', 7, CHARCOAL)
        c.setFillColor(colors.HexColor('#2f3d43'))
        c.roundRect(38, 261, 263, 35, 3, fill=1, stroke=0)
        for index in range(20):
            x = 48 + 12.7 * index
            for pin, y in ((2 * index + 2, 286), (2 * index + 1, 271)):
                selected = {2: RED, 6: CHARCOAL, 12: GOLD}.get(pin)
                if pin == 6:
                    c.setFillColor(colors.white)
                    c.circle(x, y, 5.3, fill=1, stroke=0)
                c.setFillColor(selected or colors.HexColor('#d4dcde'))
                c.circle(x, y, 3.6 if pin == 6 else 4.5 if selected else 3.3,
                         fill=1, stroke=0)
        diagram_text(c, 38, 248, 'left end →', 7, CHARCOAL, True)
        c.setFillColor(colors.HexColor('#a9b7bd'))
        c.setStrokeColor(colors.HexColor('#83949b'))
        c.roundRect(383, 245, 64, 46, 3, fill=1, stroke=1)
        c.roundRect(383, 198, 64, 41, 3, fill=1, stroke=1)
        diagram_text(c, 392, 263, 'USB', 9, CHARCOAL, True)
        diagram_text(c, 390, 213, 'Ethernet', 7, CHARCOAL, True)
        diagram_text(c, 384, 189, 'ports on right', 7, CHARCOAL)

        c.setFillColor(colors.white)
        c.setStrokeColor(colors.HexColor('#bccdd0'))
        c.roundRect(15, 92, 479, 83, 5, fill=1, stroke=1)
        diagram_text(c, 28, 161, 'LEFT END OF HEADER · enlarged', 8, TEAL, True)
        diagram_text(c, 28, 137, 'TOP EDGE', 7, CHARCOAL, True)
        diagram_text(c, 28, 109, 'BOARD CENTRE', 7, CHARCOAL, True)
        for index in range(6):
            x = 153 + 52 * index
            for pin, y in ((2 * index + 2, 139), (2 * index + 1, 111)):
                selected = {2: RED, 6: CHARCOAL, 12: GOLD}.get(pin)
                c.setFillColor(selected or colors.white)
                c.setStrokeColor(selected or GREY)
                c.setLineWidth(1.2)
                c.circle(x, y, 11.5, fill=1, stroke=1)
                diagram_text(c, x, y - 3.2, str(pin), 8.5,
                             colors.white if pin in (2, 6) else CHARCOAL,
                             True, True)
        diagram_text(c, 28, 95, 'Use only the three highlighted pins: 2, 6, and 12.', 6.7, CHARCOAL)

        diagram_text(c, 24, 79, 'FINAL THREE WIRES · match the printed motor-board labels', 8, TEAL, True)
        c.setFillColor(PALE)
        c.setStrokeColor(colors.HexColor('#b4cfcf'))
        c.roundRect(349, 16, 143, 60, 5, fill=1, stroke=1)
        for label, y, colour, source in (
            ('IN', 62, GOLD, 'Pi pin 12   GPIO18'),
            ('VCC', 44, RED, 'Pi pin 2    5 V'),
            ('GND', 26, CHARCOAL, 'Pi pin 6    GND'),
        ):
            diagram_text(c, 25, y - 3, source, 8, CHARCOAL, True)
            diagram_line(c, 157, y, 355, y, colour, arrow=True)
            diagram_text(c, 367, y - 3, label, 8, CHARCOAL, True)
        diagram_text(c, 23, 5, 'Connect the wires only while the Pi is unplugged.', 7, CHARCOAL)


def markup(value: str) -> str:
    value = html.escape(value)
    saved: list[str] = []

    def stash(value_: str) -> str:
        saved.append(value_)
        return f'@@T{len(saved)-1}@@'

    def link(match: re.Match[str]) -> str:
        label, url = match.groups()
        if url == 'RESULT_LOG_TEMPLATE.md':
            url = 'Canary_result_log.pdf'
        return stash(f'<link href="{url}" color="#086a86"><u>{label}</u></link>')

    value = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link, value)
    value = re.sub(r'`([^`]+)`', lambda m: stash(f'<font name="DejaVuMono" color="#0d5763">{m.group(1)}</font>'), value)
    value = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', value)
    value = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<i>\1</i>', value)
    for index, item in enumerate(saved):
        value = value.replace(f'@@T{index}@@', item)
    return value


def cells(line: str) -> list[str]:
    return [part.strip() for part in line.strip().strip('|').split('|')]


def make_table(headings: list[str], rows: list[list[str]], width: float) -> LongTable:
    count = len(headings)
    if count == 2:
        fractions = [0.38, 0.62] if headings[0] in ('Field', 'Report line', 'Final check') else [0.22, 0.78]
    elif count == 3:
        if headings[0] == 'Check':
            fractions = [0.45, 0.42, 0.13]
        elif headings[0] == 'Command on Pi':
            fractions = [0.41, 0.09, 0.50]
        else:
            fractions = [0.36, 0.16, 0.48]
    elif count == 4:
        fractions = [0.19, 0.25, 0.25, 0.31]
    else:
        fractions = [1 / count] * count
    col_widths = [width * x for x in fractions]
    all_rows = [[Paragraph(markup(cell), CELL_HEAD) for cell in headings]]
    for row in rows:
        all_rows.append([Paragraph(markup(row[index] if index < len(row) else ''), CELL)
                         for index in range(count)])
    table = LongTable(all_rows, colWidths=col_widths, repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), TEAL),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1),
         [colors.white, colors.HexColor('#f6f9f9')]),
        ('LINEBELOW', (0, 0), (-1, -1), 0.35, colors.HexColor('#d4e0e0')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 7),
        ('RIGHTPADDING', (0, 0), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    return table


def flowables(source: str, width: float) -> list:
    lines = source.splitlines()
    out: list = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped == '<!-- diagram:connections -->':
            out.append(Spacer(1, 8))
            out.append(ConnectionsDiagram(width))
            out.append(Spacer(1, 10))
            i += 1
            continue
        if stripped == '<!-- diagram:pi-wiring -->':
            out.append(Spacer(1, 8))
            out.append(PinDiagram(width))
            out.append(Spacer(1, 10))
            i += 1
            continue
        if stripped == ':::filebox':
            groups: list[list[str]] = []
            group: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip() != ':::':
                if not lines[i].strip():
                    if group:
                        groups.append(group)
                        group = []
                else:
                    group.append(lines[i].strip())
                i += 1
            if group:
                groups.append(group)
            rows = [[Paragraph(markup(' '.join(part)), FILEBOX)] for part in groups]
            box = Table(rows, colWidths=[width], hAlign='LEFT')
            box.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#eef6f5')),
                ('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor('#b4cece')),
                ('LINEBEFORE', (0, 0), (0, -1), 3, TEAL),
                ('LEFTPADDING', (0, 0), (-1, -1), 10),
                ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                ('TOPPADDING', (0, 0), (-1, 0), 8),
                ('BOTTOMPADDING', (0, -1), (-1, -1), 8),
                ('TOPPADDING', (0, 1), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -2), 3),
            ]))
            out.extend([Spacer(1, 3), KeepTogether([box]), Spacer(1, 9)])
            i += 1
            continue
        if stripped.startswith('```'):
            language = stripped[3:].strip()
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                code.append(lines[i][3:] if lines[i].startswith('   ') else lines[i])
                i += 1
            code_rows = []
            for text in code:
                text = html.escape(text)
                if language == 'text':
                    text = text.replace(' ', '&#160;')
                code_rows.append(Paragraph(text if text else '&#160;', CODE))
            block = Table([[row] for row in code_rows], colWidths=[width - 20], hAlign='LEFT')
            block.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f2f5f6')),
                ('LINEBEFORE', (0, 0), (0, -1), 2, colors.HexColor('#5e9ea1')),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 7),
                ('TOPPADDING', (0, 0), (-1, 0), 5),
                ('BOTTOMPADDING', (0, -1), (-1, -1), 5),
                ('TOPPADDING', (0, 1), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -2), 0),
            ]))
            code_group = [Spacer(1, 4), block]
            if out and isinstance(out[-1], Paragraph):
                code_group.insert(0, out.pop())
            out.append(KeepTogether(code_group))
            out.append(Spacer(1, 9))
            i += 1
            continue
        if stripped.startswith('|') and i + 1 < len(lines) and re.match(r'^\s*\|?\s*:?-+:?', lines[i + 1]):
            heading = cells(line)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                rows.append(cells(lines[i]))
                i += 1
            table = make_table(heading, rows, width)
            if out and isinstance(out[-1], Paragraph) and out[-1].getPlainText() == "The motor board's three labelled pins":
                out.append(KeepTogether([out.pop(), table]))
            else:
                out.append(KeepTogether([table]) if len(rows) <= 4 else table)
            out.append(Spacer(1, 8))
            continue
        heading = re.match(r'^(#{1,3})\s+(.*)$', stripped)
        if heading:
            level = len(heading.group(1))
            out.append(Paragraph(markup(heading.group(2)), {1: TITLE, 2: H2, 3: H3}[level]))
            i += 1
            continue
        numbered = re.match(r'^(\d+)\.\s+(.*)$', stripped)
        if numbered:
            out.append(Paragraph(f'<b>{numbered.group(1)}.</b> {markup(numbered.group(2))}', STEP))
            i += 1
            continue
        checkbox = re.match(r'^-\s+\[ \]\s+(.*)$', stripped)
        if checkbox:
            out.append(Paragraph(f'□  {markup(checkbox.group(1))}', BULLET))
            i += 1
            continue
        bullet = re.match(r'^-\s+(.*)$', stripped)
        if bullet:
            out.append(Paragraph(f'•  {markup(bullet.group(1))}', BULLET))
            i += 1
            continue
        paragraph = [stripped]
        i += 1
        while i < len(lines):
            next_line = lines[i].strip()
            if (not next_line or next_line.startswith(('#', '```', '|', '- '))
                or re.match(r'^\d+\.\s+', next_line)):
                break
            paragraph.append(next_line)
            i += 1
        content = markup(' '.join(paragraph))
        out.append(Paragraph(content, CHECK if stripped.startswith('**CHECK:**') else NORMAL))
    return out


class CanaryPDF(BaseDocTemplate):
    def __init__(self, filename: str, title: str):
        super().__init__(filename, pagesize=A4, leftMargin=51, rightMargin=51,
                         topMargin=58, bottomMargin=53, title=title, author='Project Canary team')
        self.is_log = 'result log' in title.lower()
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height,
                      leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0)
        self.addPageTemplates(PageTemplate(id='normal', frames=[frame], onPage=self.page_footer))

    def page_footer(self, canvas, doc):
        canvas.saveState()
        canvas.setFont('Lato-Bold', 7.5)
        canvas.setFillColor(TEAL)
        canvas.drawString(51, A4[1] - 34,
                          'CANARY  /  RESULT LOG' if self.is_log else 'CANARY  /  EVENT BUILD GUIDE')
        canvas.setStrokeColor(colors.HexColor('#c9dada'))
        canvas.setLineWidth(0.55)
        canvas.line(51, A4[1] - 42, A4[0] - 51, A4[1] - 42)
        canvas.line(51, 38, A4[0] - 51, 38)
        canvas.setFont('Lato', 8)
        canvas.setFillColor(colors.HexColor('#60717a'))
        canvas.drawString(51, 23, 'Result log' if self.is_log else '24-hour build guide')
        canvas.drawRightString(A4[0] - 51, 23, f'{doc.page}')
        canvas.restoreState()


def main() -> None:
    source, target = map(Path, sys.argv[1:3])
    pdf = CanaryPDF(str(target), source.stem.replace('_', ' '))
    pdf.build(flowables(source.read_text(), pdf.width))


if __name__ == '__main__':
    main()
