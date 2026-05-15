#!/usr/bin/env python3

import argparse
import csv
import hashlib
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from pyvirtualdisplay import Display
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Image as PdfImage,
    PageBreak,
)


SCREENSHOTS_DIR = Path("screenshots")
DEFAULT_WIDTH = 1366
DEFAULT_HEIGHT = 768


ART = r"""
             __               __           __   __    
 _    _____ / /    ___ ___   / /  __ _____/ /__/ /_ __
| |/|/ / -_) _ \  (_-<(_-<  / _ \/ // / _  / _  / // /
|__,__/\__/_.__/ /___/___/ /_.__/\_,_/\_,_/\_,_/\_, / 
                                               /___/   
"""


class HelpFormatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog):
        super().__init__(
            prog,
            max_help_position=30,
            width=100,
        )


def extract_url(line: str):
    """
    Extract a URL from either:

    1. Plain URL:
       https://example.com

    2. httpx-style output:
       https://example.com [200] [Example Title] [nginx]
    """
    line = line.strip()
    match = re.match(r"^(https?://\S+)", line)
    return match.group(1) if match else None


def safe_filename(url: str):
    parsed = urlparse(url)
    host = parsed.netloc.replace(":", "_")
    digest = hashlib.sha1(url.encode()).hexdigest()[:8]
    return f"{host}_{digest}.png"


def make_placeholder(path: Path, text: str, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT):
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    y = 40
    for line in text.splitlines():
        draw.text((40, y), line, fill="black")
        y += 24

    img.save(path)


def desktop_screenshot(path: Path):
    subprocess.run(["scrot", str(path)], check=True)


def get_browser_url(page, fallback_url: str):
    if not page:
        return fallback_url

    try:
        href = page.evaluate("() => window.location.href")
        if href and href != "about:blank":
            return href
    except Exception:
        pass

    try:
        if page.url and page.url != "about:blank":
            return page.url
    except Exception:
        pass

    return fallback_url


def get_page_title(page):
    if not page:
        return ""

    try:
        return page.title() or ""
    except Exception:
        return ""


def get_target_and_port(url: str):
    parsed = urlparse(url)
    target = parsed.hostname or parsed.netloc or url

    if parsed.port:
        port = parsed.port
    elif parsed.scheme == "https":
        port = 443
    elif parsed.scheme == "http":
        port = 80
    else:
        port = ""

    return target, port


def screenshot_url(context, raw_line: str, url: str, out_path: Path, wait_seconds: int):
    final_url = url
    final_response_code = ""
    title = ""
    page = None

    try:
        page = context.new_page()

        def track_response(response):
            """
            Track browser-observed main-frame document responses.

            This helps capture the final response code after redirects.
            """
            nonlocal final_response_code

            try:
                request = response.request

                if (
                    request.is_navigation_request()
                    and request.frame == page.main_frame
                    and request.resource_type == "document"
                ):
                    final_response_code = response.status
            except Exception:
                pass

        page.on("response", track_response)

        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=20000,
            )

            if response:
                final_response_code = response.status

        except PlaywrightTimeoutError:
            pass
        except Exception:
            pass

        final_url = get_browser_url(page, url)

        end_time = time.time() + wait_seconds

        while time.time() < end_time:
            time.sleep(0.5)

            current_url = get_browser_url(page, final_url)

            if current_url and current_url != "about:blank":
                final_url = current_url

        title = get_page_title(page)
        screenshot_taken_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        desktop_screenshot(out_path)

        target, port = get_target_and_port(url)

        return {
            "raw_line": raw_line,
            "target": target,
            "port": port,
            "url": url,
            "final_url": final_url,
            "response_code": final_response_code,
            "title": title,
            "screenshot": out_path,
            "screenshot_taken_at": screenshot_taken_at,
        }

    except Exception as e:
        screenshot_taken_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        make_placeholder(
            out_path,
            f"Screenshot failed\n\nURL: {url}\n\nReason:\n{str(e)[:700]}",
        )

        target, port = get_target_and_port(url)

        return {
            "raw_line": raw_line,
            "target": target,
            "port": port,
            "url": url,
            "final_url": final_url,
            "response_code": final_response_code,
            "title": title,
            "screenshot": out_path,
            "screenshot_taken_at": screenshot_taken_at,
        }

    finally:
        try:
            if page:
                page.close()
        except Exception:
            pass


def build_pdf(results, pdf_path: Path):
    styles = getSampleStyleSheet()

    styles["Code"].fontName = "Courier"
    styles["Code"].fontSize = 8
    styles["Code"].leading = 10
    styles["Code"].textColor = colors.black

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )

    story = []

    for item in results:
        story.append(Paragraph(f"<b>{item['url']}</b>", styles["Heading2"]))
        story.append(Spacer(1, 0.4 * cm))

        story.append(Paragraph("<b>Input Line</b>", styles["Heading3"]))
        story.append(Paragraph(item["raw_line"], styles["Code"]))
        story.append(Spacer(1, 0.3 * cm))

        story.append(Paragraph(f"<b>Original URL:</b> {item['url']}", styles["Normal"]))
        story.append(Paragraph(f"<b>Final URL:</b> {item['final_url']}", styles["Normal"]))
        story.append(Paragraph(f"<b>Response Code:</b> {item['response_code']}", styles["Normal"]))
        story.append(Paragraph(f"<b>Title:</b> {item['title']}", styles["Normal"]))
        story.append(Spacer(1, 0.5 * cm))

        story.append(Paragraph("<b>Screenshot</b>", styles["Heading3"]))
        story.append(
            Paragraph(
                f"<b>Date and Time Screenshot Was Taken:</b> {item['screenshot_taken_at']}",
                styles["Normal"],
            )
        )
        story.append(Spacer(1, 0.3 * cm))

        try:
            img = PdfImage(str(item["screenshot"]), width=17 * cm, height=9.5 * cm)
            story.append(img)
        except Exception:
            story.append(Paragraph("Could not embed screenshot.", styles["Normal"]))

        story.append(PageBreak())

    doc.build(story)


def clean_csv_text(value):
    """
    Make browser text safer for Excel CSV display.

    Some page titles contain non-breaking spaces. Excel may display those badly
    if the CSV is opened with the wrong encoding.
    """
    if value is None:
        return ""

    value = str(value)
    value = value.replace("\u00a0", " ")
    value = value.replace("\u202f", " ")

    return value


def build_csv(results, csv_path: Path):
    fieldnames = [
        "Target",
        "Port",
        "URL",
        "Final URL",
        "Response Code",
        "Title",
    ]

    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for item in results:
            writer.writerow(
                {
                    "Target": clean_csv_text(item.get("target", "")),
                    "Port": clean_csv_text(item.get("port", "")),
                    "URL": clean_csv_text(item.get("url", "")),
                    "Final URL": clean_csv_text(item.get("final_url", "")),
                    "Response Code": clean_csv_text(item.get("response_code", "")),
                    "Title": clean_csv_text(item.get("title", "")),
                }
            )


def parse_input_file(input_path: Path, skip_invalid: bool):
    lines = [
        line.strip()
        for line in input_path.read_text(errors="ignore").splitlines()
        if line.strip()
    ]

    if not lines:
        print("[-] Input file is empty.")
        sys.exit(1)

    parsed = []
    invalid = []

    for line_number, line in enumerate(lines, start=1):
        url = extract_url(line)

        if not url:
            invalid.append((line_number, line))
            continue

        parsed.append(
            {
                "line_number": line_number,
                "raw_line": line,
                "url": url,
            }
        )

    if invalid and not skip_invalid:
        print("[-] Invalid input found.")
        print()
        print("The script accepts:")
        print("  - Plain URLs with http:// or https://")
        print("  - httpx-style lines that start with http:// or https://")
        print()
        print("Invalid lines:")

        for line_number, line in invalid:
            print(f"  Line {line_number}: {line}")

        print()
        print("Fix the input file or use --skip-invalid to ignore invalid lines.")
        sys.exit(1)

    if invalid and skip_invalid:
        for line_number, line in invalid:
            print(f"[-] Skipping invalid line {line_number}: {line}")

    if not parsed:
        print("[-] No valid URLs found.")
        sys.exit(1)

    return parsed


def main():
    parser = argparse.ArgumentParser(
        prog="web_ss_buddy",
        formatter_class=HelpFormatter,
        description=(
            "Take browser screenshots from plain URLs or httpx-style output "
            "and generate a PDF report plus a CSV summary."
        ),
        epilog="""
Input formats supported:

  Plain URL:
    https://example.com

  httpx-style output:
    https://example.com [200] [Example Domain] [nginx]

Examples:

  python3 web_ss_buddy.py urls.txt -o report.pdf
  python3 web_ss_buddy.py urls.txt -o report.pdf --csv results.csv
  python3 web_ss_buddy.py live_web.txt -o report.pdf --wait 15
  python3 web_ss_buddy.py urls.txt -o report.pdf --skip-invalid
  python3 web_ss_buddy.py urls.txt -o report.pdf --ignore-cert-errors
  python3 web_ss_buddy.py urls.txt -o report.pdf --user-agent "Mozilla/5.0"

Important:

  URLs must include http:// or https://.
  The script does not assume a scheme.
""",
    )

    parser.add_argument(
        "input_file",
        help="Input file with plain URLs or httpx-style output.",
    )

    parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="OUTPUT",
        help="Output PDF filename, for example report.pdf.",
    )

    parser.add_argument(
        "--csv",
        metavar="CSV",
        default=None,
        help="Output CSV filename. Default: output name with .csv.",
    )

    parser.add_argument(
        "--wait",
        metavar="WAIT",
        type=int,
        default=10,
        help="Seconds to wait before each screenshot. Default: 10.",
    )

    parser.add_argument(
        "--width",
        metavar="WIDTH",
        type=int,
        default=DEFAULT_WIDTH,
        help=f"Browser/X display width. Default: {DEFAULT_WIDTH}.",
    )

    parser.add_argument(
        "--height",
        metavar="HEIGHT",
        type=int,
        default=DEFAULT_HEIGHT,
        help=f"Browser/X display height. Default: {DEFAULT_HEIGHT}.",
    )

    parser.add_argument(
        "--user-agent",
        metavar="USER_AGENT",
        default=None,
        help="Custom User-Agent string.",
    )

    parser.add_argument(
        "--ignore-cert-errors",
        action="store_true",
        help="Ignore certificate errors.",
    )

    parser.add_argument(
        "--skip-invalid",
        action="store_true",
        help="Skip invalid input lines.",
    )

    args = parser.parse_args()

    print(ART)

    input_path = Path(args.input_file)

    if not input_path.exists():
        print(f"[-] Input file not found: {input_path}")
        sys.exit(1)

    output_path = Path(args.output)

    if output_path.suffix.lower() != ".pdf":
        output_path = output_path.with_suffix(".pdf")

    csv_path = Path(args.csv) if args.csv else output_path.with_suffix(".csv")

    SCREENSHOTS_DIR.mkdir(exist_ok=True)

    targets = parse_input_file(input_path, args.skip_invalid)
    results = []

    chromium_args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        f"--window-size={args.width},{args.height}",
        "--new-window",
    ]

    if args.ignore_cert_errors:
        chromium_args.append("--ignore-certificate-errors")

    def run_browser():
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=chromium_args,
            )

            context_kwargs = {
                "viewport": {"width": args.width, "height": args.height},
            }

            if args.user_agent:
                context_kwargs["user_agent"] = args.user_agent

            if args.ignore_cert_errors:
                context_kwargs["ignore_https_errors"] = True

            context = browser.new_context(**context_kwargs)

            for target in targets:
                url = target["url"]
                raw_line = target["raw_line"]
                screenshot_path = SCREENSHOTS_DIR / safe_filename(url)

                print(f"[+] Opening: {url}")

                result = screenshot_url(
                    context=context,
                    raw_line=raw_line,
                    url=url,
                    out_path=screenshot_path,
                    wait_seconds=args.wait,
                )

                print(f"    Original URL: {result['url']}")
                print(f"    Final URL: {result['final_url']}")
                print(f"    Response Code: {result['response_code']}")
                print(f"    Title: {result['title']}")
                print(f"    Screenshot Time: {result['screenshot_taken_at']}")
                print(f"    Screenshot: {result['screenshot']}")

                results.append(result)

            browser.close()

    try:
        with Display(
            visible=False,
            size=(args.width, args.height),
            color_depth=24,
        ):
            run_browser()

        build_pdf(results, output_path)
        build_csv(results, csv_path)

    except KeyboardInterrupt:
        print()
        print("[-] Interrupted by user.")
        sys.exit(1)

    except FileNotFoundError as e:
        print()
        print(f"[-] Missing dependency or executable: {e}")
        print()
        print("Make sure these are installed:")
        print("  sudo apt install xvfb scrot")
        print("  pip install pillow playwright pyvirtualdisplay reportlab")
        print("  playwright install chromium")
        sys.exit(1)

    except Exception as e:
        print()
        print(f"[-] Unexpected error: {e}")
        sys.exit(1)

    print()
    print(f"[+] Screenshots saved in: {SCREENSHOTS_DIR}/")
    print(f"[+] PDF report saved as: {output_path}")
    print(f"[+] CSV report saved as: {csv_path}")


if __name__ == "__main__":
    main()
