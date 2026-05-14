#!/usr/bin/env python3

import argparse
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
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Image as PdfImage,
    PageBreak,
)
from reportlab.lib.styles import getSampleStyleSheet


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


def screenshot_url(context, raw_line: str, url: str, out_path: Path, wait_seconds: int):
    final_url = url
    page = None

    try:
        page = context.new_page()

        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=20000,
            )
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

        screenshot_taken_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        desktop_screenshot(out_path)

        return {
            "raw_line": raw_line,
            "url": url,
            "final_url": final_url,
            "screenshot": out_path,
            "screenshot_taken_at": screenshot_taken_at,
        }

    except Exception as e:
        screenshot_taken_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        make_placeholder(
            out_path,
            f"Screenshot failed\n\nURL: {url}\n\nReason:\n{str(e)[:700]}",
        )

        return {
            "raw_line": raw_line,
            "url": url,
            "final_url": final_url,
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

        story.append(Paragraph("<b>Input line</b>", styles["Heading3"]))
        story.append(Paragraph(item["raw_line"], styles["Code"]))
        story.append(Spacer(1, 0.3 * cm))

        story.append(Paragraph(f"<b>Original URL:</b> {item['url']}", styles["Normal"]))
        story.append(Paragraph(f"<b>Final URL:</b> {item['final_url']}", styles["Normal"]))
        story.append(Spacer(1, 0.5 * cm))

        story.append(Paragraph("<b>Screenshot</b>", styles["Heading3"]))
        story.append(
            Paragraph(
                f"<b>Date and time screenshot was taken:</b> {item['screenshot_taken_at']}",
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


def print_examples():
    examples = r"""
Examples:

  Plain URL input file:
    https://example.com
    http://example.com:8080

  httpx-style input file:
    https://example.com [200] [Example Domain] [nginx]
    http://192.168.1.10:8080 [401] [Login] [Apache]

Usage:

  python3 web_ss_buddy.py urls.txt -o report.pdf

  python3 web_ss_buddy.py live_web.txt -o screenshots.pdf --wait 15

  python3 web_ss_buddy.py urls.txt -o report.pdf --ignore-cert-errors

  python3 web_ss_buddy.py urls.txt -o report.pdf --skip-invalid

  python3 web_ss_buddy.py urls.txt -o report.pdf --user-agent "Mozilla/5.0"

Help:

  python3 web_ss_buddy.py -h
"""
    print(examples)


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
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Take browser screenshots from plain URLs or httpx-style output "
            "and generate a PDF report."
        ),
        epilog="""
Input formats supported:

  Plain URL:
    https://example.com

  httpx-style output:
    https://example.com [200] [Example Domain] [nginx]

Examples:

  python3 web_ss_buddy.py urls.txt -o report.pdf
  python3 web_ss_buddy.py live_web.txt -o report.pdf --wait 15
  python3 web_ss_buddy.py urls.txt -o report.pdf --skip-invalid
  python3 web_ss_buddy.py urls.txt -o report.pdf --ignore-cert-errors
  python3 web_ss_buddy.py urls.txt -o report.pdf --user-agent "Mozilla/5.0"

Help:

  python3 web_ss_buddy.py -h

Important:

  URLs must include http:// or https://.
  The script does not assume a scheme.
"""
    )

    parser.add_argument(
        "input_file",
        help="Path to input file containing plain URLs or httpx-style output.",
    )

    parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="Output PDF filename, for example report.pdf",
    )

    parser.add_argument(
        "--wait",
        type=int,
        default=10,
        help="Seconds to wait before taking each screenshot. Default: 10",
    )

    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_WIDTH,
        help=f"Browser/X display width. Default: {DEFAULT_WIDTH}",
    )

    parser.add_argument(
        "--height",
        type=int,
        default=DEFAULT_HEIGHT,
        help=f"Browser/X display height. Default: {DEFAULT_HEIGHT}",
    )

    parser.add_argument(
        "--user-agent",
        default=None,
        help="Custom User-Agent string.",
    )

    parser.add_argument(
        "--ignore-cert-errors",
        action="store_true",
        help="Ignore certificate errors. Do NOT use this if you want to capture SSL warning pages.",
    )

    parser.add_argument(
        "--skip-invalid",
        action="store_true",
        help="Skip invalid input lines instead of exiting with an error.",
    )

    parser.add_argument(
        "--examples",
        action="store_true",
        help="Show usage examples and exit.",
    )

    args = parser.parse_args()

    print(ART)

    if args.examples:
        print_examples()
        sys.exit(0)

    input_path = Path(args.input_file)

    if not input_path.exists():
        print(f"[-] Input file not found: {input_path}")
        sys.exit(1)

    output_path = Path(args.output)

    if output_path.suffix.lower() != ".pdf":
        output_path = output_path.with_suffix(".pdf")

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
                print(f"    Screenshot time: {result['screenshot_taken_at']}")
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


if __name__ == "__main__":
    main()
