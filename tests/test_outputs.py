from contextlib import redirect_stderr, redirect_stdout
import csv
from io import StringIO
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

from PIL import Image

from web_ss_buddy.capture import Capture
from web_ss_buddy.cli import ART, main, parser, plan_outputs
from web_ss_buddy.inputs import Target, parse_input_file
from web_ss_buddy.reports import COLUMNS, build_html, build_pdf, spreadsheet_cell


class OutputTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.source = self.root / "urls.txt"
        self.raw = '  https://example.test [201] [Title <&> "text"]  '
        self.source.write_text(self.raw + "\n", encoding="utf-8-sig")

    def test_full_input_line_and_invalid_line_numbers(self):
        self.source.write_text(
            self.raw + "\n\nnon-url\nhttps://[::1]:8443/path\nhttp://example.test:oops\n",
            encoding="utf-8-sig",
        )
        warnings = []
        with self.assertRaisesRegex(ValueError, r"line\(s\) 3, 5"):
            parse_input_file(self.source, skip_invalid=False, warn=warnings.append)
        targets = parse_input_file(self.source, skip_invalid=True, warn=warnings.append)
        self.assertEqual(targets[0], Target(self.raw, "https://example.test", "example.test", 443))
        self.assertEqual((targets[1].host, targets[1].port), ("::1", 8443))
        self.assertIn("3, 5", warnings[0])

    def test_default_and_legacy_output_paths(self):
        def plan(*args):
            return plan_outputs(parser().parse_args([str(self.source), *args]), self.source)[0]

        self.assertEqual(list(plan()), ["html"])
        report = self.root / "review.pdf"
        self.assertEqual(plan("-o", str(report)), {"pdf": report, "csv": report.with_suffix(".csv")})
        self.assertEqual(plan("--format", "pdf", "-o", str(report)), {"pdf": report})
        self.assertEqual(
            plan("--format", "csv", "--format", "csv", "-o", str(report.with_suffix(".csv"))),
            {"csv": report.with_suffix(".csv")},
        )

    def test_plan_rejects_input_and_output_collisions(self):
        report = self.root / "report.html"
        path = self.root / "alias.csv"
        path.hardlink_to(self.source)
        nested_csv = report / "nested.csv"
        for destination in (self.source, path, report, nested_csv, self.root / "screenshots" / "report.csv"):
            with self.subTest(path=destination), self.assertRaises(ValueError):
                args = parser().parse_args([
                    str(self.source), "-o", str(report), "--all-formats", "--csv", str(destination),
                ])
                plan_outputs(args, self.source)

    def test_all_formats_reuse_captures_and_keep_six_column_parity(self):
        async def capture(targets, folder, **options):
            image = folder / "00001.png"
            width, height = options["width"], options["height"]
            Image.new("RGB", (width, height), "lavender").save(image)
            return [Capture(
                targets[0], final_url="https://example.test/final", response_code=200,
                title='\u00a0=1+2 </script> < & "quotes"', screenshot=image,
                screenshot_taken_at="2026-01-01T12:00:00+00:00",
            )]

        stem = self.root / "example report"
        with (
            patch("web_ss_buddy.cli.check_system"),
            patch("web_ss_buddy.cli.capture_targets", side_effect=capture) as run,
            redirect_stdout(StringIO()) as stdout,
        ):
            self.assertEqual(main([str(self.source), "--all-formats", "-o", str(stem)]), 0)
            run.assert_awaited_once()
        self.assertTrue(stdout.getvalue().startswith(ART))

        with stem.with_suffix(".csv").open(encoding="utf-8-sig", newline="") as file:
            csv_rows = list(csv.reader(file))
        with ZipFile(stem.with_suffix(".xlsx")) as workbook:
            self.assertEqual(
                [name for name in workbook.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", name)],
                ["xl/worksheets/sheet1.xml"],
            )
            ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            sheet = ElementTree.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
            xlsx_rows = [
                [cell.text or "" for cell in row.findall("x:c/x:is/x:t", ns)]
                for row in sheet.findall("x:sheetData/x:row", ns)
            ]
            self.assertIsNone(sheet.find(".//x:f", ns))
        self.assertEqual(csv_rows, xlsx_rows)
        self.assertEqual(tuple(csv_rows[0]), COLUMNS)
        self.assertEqual(csv_rows[1][-1], '\' =1+2 </script> < & "quotes"')
        self.assertTrue(stem.with_suffix(".pdf").read_bytes().startswith(b"%PDF"))

        html = stem.with_suffix(".html").read_text()
        payload = html.split('id="report-data">', 1)[1].split("</script>", 1)[0]
        record = json.loads(payload)["records"][0]
        self.assertEqual(record["metadata"][0], ["Input line", self.raw])
        self.assertEqual(record["columns"][-1], '\u00a0=1+2 </script> < & "quotes"')
        self.assertTrue((self.root / record["image"]).is_file())
        self.assertNotIn("Copy visible URLs", html)
        for suffix in ("html", "pdf", "xlsx", "csv"):
            self.assertEqual(stem.with_suffix(f".{suffix}").stat().st_mode & 0o777, 0o600)

    def test_missing_capture_keeps_reports_and_failure_status(self):
        async def capture(targets, folder, **options):
            return [Capture(targets[0], error="scrot failed")]

        output = self.root / "failed.html"
        with (
            patch("web_ss_buddy.cli.check_system"),
            patch("web_ss_buddy.cli.capture_targets", side_effect=capture),
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()) as stderr,
        ):
            self.assertEqual(main([str(self.source), "-o", str(output)]), 1)
        self.assertIn("1 of 1 targets have no screenshot", stderr.getvalue())
        self.assertIn('"image":"","error":"scrot failed"', output.read_text())

    def test_large_pdf_metadata_and_tall_image(self):
        url = "https://example.test/?" + ("a" * 4000) + "&b=<quoted>"
        image = self.root / "tall.png"
        Image.new("RGB", (640, 1800), "lavender").save(image)
        capture = Capture(
            Target(url + ' [200] [Full input]', url, "example.test", 443),
            final_url=url, title='Title <&> "text" ' * 300, screenshot=image,
        )
        pdf = self.root / "tall.pdf"
        build_pdf([capture], pdf)
        self.assertGreater(len(pdf.read_bytes()), 2000)
        html = self.root / "tall.html"
        image.unlink()
        build_html([capture], html)
        self.assertIn('"image":""', html.read_text())

    def test_spreadsheet_formula_prefix(self):
        self.assertEqual(spreadsheet_cell("normal + title"), "normal + title")
        self.assertEqual(spreadsheet_cell(None), "")
        for value in ("=1", "+SUM(A1)", "-42", "@SUM(A1)", "\ufeff \t=1"):
            self.assertEqual(spreadsheet_cell(value), "'" + value)


if __name__ == "__main__":
    unittest.main()
