"""Render the same captures as offline HTML, portrait PDF, CSV and XLSX."""

from contextlib import contextmanager
import csv
from html import escape
from importlib.resources import files
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.parse import quote

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate
from xlsxwriter import Workbook


COLUMNS = ("Target", "Port", "URL", "Final URL", "Response Code", "Title")
CJK = re.compile(r"([\u3000-\u30ff\u3400-\u9fff\uf900-\ufaff]+)")


def metadata(result):
    return (
        ("Input line", result.target.raw_line),
        ("Original URL", result.target.url),
        ("Final URL", result.final_url),
        ("Response Code", result.response_code),
        ("Title", result.title),
        ("Date and time screenshot was taken", result.screenshot_taken_at),
    )


def table_row(result):
    return (
        result.target.host, result.target.port, result.target.url,
        result.final_url, result.response_code, result.title,
    )


def spreadsheet_cell(value):
    text = "" if value is None else str(value)
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    if re.match(r"^[\s\ufeff\u200b]*[=+@-]", text):
        return "'" + text
    return text


@contextmanager
def report_file(destination: Path):
    # A completed report replaces its previous version atomically, with private permissions.
    with NamedTemporaryFile(prefix=".web-ss-buddy-", dir=destination.parent, delete=False) as file:
        temporary = Path(file.name)
    try:
        yield temporary
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def build_csv(results, path: Path):
    with report_file(path) as temporary, temporary.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(COLUMNS)
        for result in results:
            writer.writerow(map(spreadsheet_cell, table_row(result)))


def build_xlsx(results, path: Path):
    with report_file(path) as temporary, Workbook(
        temporary, {"constant_memory": True, "tmpdir": str(path.parent)},
    ) as workbook:
        sheet = workbook.add_worksheet("Results")
        sheet.write_row(0, 0, COLUMNS)
        sheet.freeze_panes(1, 0)
        for column, width in enumerate((26, 10, 48, 48, 17, 36)):
            sheet.set_column(column, column, width)
        for index, result in enumerate(results, 1):
            for column, value in enumerate(table_row(result)):
                status = sheet.write_string(index, column, spreadsheet_cell(value))
                if status:
                    raise ValueError(f"Row {index + 1} exceeds Excel's sheet or cell size limit. Use CSV for the full text.")
        sheet.autofilter(0, 0, len(results), len(COLUMNS) - 1)


def build_html(results, path: Path):
    template = files("web_ss_buddy").joinpath("assets", "report.html").read_text(encoding="utf-8")
    records = []
    for result in results:
        screenshot = ""
        if result.screenshot and result.screenshot.is_file():
            screenshot = quote(os.path.relpath(result.screenshot, path.parent), safe="/")
        records.append({
            "metadata": metadata(result),
            "columns": table_row(result),
            "cells": [spreadsheet_cell(value) for value in table_row(result)],
            "image": screenshot,
            "error": result.error,
        })
    payload = json.dumps({"headers": COLUMNS, "records": records}, separators=(",", ":"))
    # Escape HTML delimiters before placing JSON in an inert script element.
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    with report_file(path) as temporary:
        temporary.write_text(template.replace("__REPORT_DATA__", payload, 1), encoding="utf-8")


def pdf_text(value, cjk_font):
    text = str(value) if value not in (None, "") else "—"
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "\ufffd", text)
    text = "".join(
        f'<font name="{cjk_font}">{escape(part, quote=False)}</font>' if index % 2 else escape(part, quote=False)
        for index, part in enumerate(CJK.split(text))
    )
    text = text.replace("\n", "<br/>").replace("\t", "    ")
    return re.sub(r" {2,}", lambda spaces: "&#160;" * len(spaces[0]), text)


def build_pdf(results, path: Path):
    styles = getSampleStyleSheet()
    fallback = Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf")
    if fallback.is_file():
        cjk_font = "ReportCJK"
        pdfmetrics.registerFont(TTFont(cjk_font, str(fallback)))
    else:
        cjk_font = "STSong-Light"
        pdfmetrics.registerFont(UnicodeCIDFont(cjk_font))
    font = "Helvetica"
    for location in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ):
        if Path(location).is_file():
            font = "ReportSans"
            pdfmetrics.registerFont(TTFont(font, location))
            pdfmetrics.registerFontFamily(font, normal=font, bold="Helvetica-Bold")
            break
    body = ParagraphStyle("Metadata", parent=styles["Normal"], fontName=font, spaceAfter=3)
    code = ParagraphStyle("Input", parent=body, fontSize=8, leading=11, spaceAfter=8)
    heading = ParagraphStyle("URL", parent=styles["Heading2"], fontName=font, spaceAfter=9)
    label = styles["Heading3"]

    with report_file(path) as temporary:
        doc = SimpleDocTemplate(
            str(temporary), pagesize=A4, title="web_ss_buddy screenshot report",
            leftMargin=1.5 * cm, rightMargin=1.5 * cm,
            topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        )
        # SimpleDocTemplate's content frame adds six points of padding on each edge.
        width, height = doc.width - 12, doc.height - 12
        story = []
        for index, result in enumerate(results):
            if index:
                story.append(PageBreak())
            fields = metadata(result)
            url = result.target.url
            display_url = url if len(url) <= 120 else url[:117] + "…"
            details = [
                Paragraph(pdf_text(display_url, cjk_font), heading),
                Paragraph(fields[0][0], label),
                Paragraph(pdf_text(fields[0][1], cjk_font), code),
            ]
            for name, value in fields[1:-1]:
                details.append(Paragraph(f"<b>{name}:</b> {pdf_text(value, cjk_font)}", body))

            timestamp_label, timestamp = fields[-1]
            picture = [
                Paragraph("Screenshot", label),
                Paragraph(f"<b>{timestamp_label}:</b> {pdf_text(timestamp, cjk_font)}", body),
            ]
            if result.screenshot and result.screenshot.is_file():
                image = Image(str(result.screenshot), lazy=0)
                preceding = sum(
                    element.wrap(width, height)[1] + element.getSpaceBefore() + element.getSpaceAfter()
                    for element in details + picture
                )
                remaining = height - preceding - 8
                max_height = remaining if remaining >= 180 else height - 65
                scale = min(width / image.imageWidth, max_height / image.imageHeight)
                image.drawWidth = image.imageWidth * scale
                image.drawHeight = image.imageHeight * scale
                image.spaceBefore = 7
                picture.append(image)
            else:
                picture.append(Paragraph("Screenshot unavailable.", body))
            story.extend(details)
            story.append(KeepTogether(picture))
            if result.error:
                story.append(Paragraph(pdf_text(result.error, cjk_font), code))
        doc.build(story)
