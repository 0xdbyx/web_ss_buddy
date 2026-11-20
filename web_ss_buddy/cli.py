"""Command-line interface and output path planning."""

import argparse
import asyncio
from pathlib import Path
import subprocess
import sys
from tempfile import mkdtemp

from playwright.async_api import Error as BrowserError, async_playwright

from . import __version__
from .capture import DEFAULT_HEIGHT, DEFAULT_WIDTH, capture_targets, check_system, chromium_path
from .inputs import parse_input_file
from .reports import build_csv, build_html, build_pdf, build_xlsx


EXPORTERS = {"html": build_html, "pdf": build_pdf, "xlsx": build_xlsx, "csv": build_csv}


def parser():
    cli = argparse.ArgumentParser(
        description="Capture headed Chromium windows from URL lists or ProjectDiscovery httpx output.",
    )
    cli.add_argument("input_file", type=Path, nargs="?", help="text file with one HTTP(S) URL per line")
    cli.add_argument("-o", "--output", type=Path, help="report path or filename stem (default: report)")
    cli.add_argument("--format", choices=EXPORTERS, action="append",
                     help="report format, repeatable (default: html)")
    cli.add_argument("--all-formats", action="store_true", help="write HTML, PDF, XLSX and CSV")
    cli.add_argument("--csv", type=Path, metavar="PATH", help="also write CSV to this path")
    cli.add_argument("--wait", type=float, default=10, help="seconds after navigation (default: 10)")
    cli.add_argument("--timeout", type=float, default=20, help="navigation deadline in seconds (default: 20)")
    cli.add_argument("--width", type=int, default=DEFAULT_WIDTH, help=f"desktop width (default: {DEFAULT_WIDTH})")
    cli.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help=f"desktop height (default: {DEFAULT_HEIGHT})")
    cli.add_argument("--user-agent", help="custom Chromium User-Agent")
    cli.add_argument("--ignore-cert-errors", action="store_true", help="accept invalid HTTPS certificates")
    cli.add_argument("--skip-invalid", action="store_true", help="skip malformed lines in the input file")
    cli.add_argument("--browser-executable", type=Path, help="path to a system Chromium executable")
    cli.add_argument("--install-browser", action="store_true", help="install Playwright-managed Chromium")
    cli.add_argument("--doctor", action="store_true", help="check system tools and find Chromium")
    cli.add_argument("--version", action="version", version=__version__)
    return cli


def plan_outputs(args, input_file: Path):
    output = args.output.expanduser() if args.output else Path("report")
    suffix = output.suffix.lower().lstrip(".")
    if args.all_formats:
        formats = list(EXPORTERS)
    elif args.format:
        formats = list(dict.fromkeys(args.format))
    elif args.output and suffix not in ("html", "xlsx", "csv"):
        # Existing -o invocations wrote a PDF and a CSV even without a .pdf suffix.
        formats = ["pdf", "csv"]
    else:
        formats = [suffix] if suffix in EXPORTERS else ["html"]

    if args.csv is not None and "csv" not in formats:
        formats.append("csv")
    if suffix in EXPORTERS and suffix not in formats:
        raise ValueError(f"The -o extension .{suffix} needs --format {suffix} or a different output name.")

    paths = {name: output if suffix == name else output.with_suffix(f".{name}") for name in formats}
    if args.csv is not None:
        paths["csv"] = args.csv.expanduser()

    for path in paths.values():
        if path.is_symlink():
            raise ValueError(f"Report destination is a symbolic link: {path}")
        if path.exists() and not path.is_file():
            raise ValueError(f"Report destination is not a regular file: {path}")

    paths = {name: path.resolve() for name, path in paths.items()}
    screenshot_root = (paths.get("html") or next(iter(paths.values()))).parent / "screenshots"
    if screenshot_root.is_symlink() or (screenshot_root.exists() and not screenshot_root.is_dir()):
        raise ValueError(f"Expected a real screenshot directory at {screenshot_root}")
    if input_file == screenshot_root:
        raise ValueError(f"Screenshot directory would overwrite the input: {input_file}")

    destinations = list(paths.values())
    for index, path in enumerate(destinations):
        if path == input_file or (path.exists() and path.samefile(input_file)):
            raise ValueError(f"Report would overwrite the input file: {path}")
        if path == screenshot_root or screenshot_root in path.parents or path in screenshot_root.parents:
            raise ValueError(f"Report conflicts with the screenshot directory: {path}")
        for other in destinations[:index]:
            if (
                path == other or path in other.parents or other in path.parents
                or (path.exists() and other.exists() and path.samefile(other))
            ):
                raise ValueError(f"Reports have conflicting destinations: {path} and {other}")
    return paths, screenshot_root


async def doctor(executable):
    check_system()
    async with async_playwright() as playwright:
        browser = chromium_path(playwright.chromium, executable)
    print(f"Xvfb, xauth and scrot found. Chromium: {browser}")
    return 0


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    try:
        if args.install_browser:
            return subprocess.call([sys.executable, "-m", "playwright", "install", "chromium", "--no-shell"])
        if args.doctor:
            return asyncio.run(doctor(args.browser_executable))
        if args.input_file is None:
            cli.error("an input file is required")
        if not (640 <= args.width <= 7680 and 480 <= args.height <= 4320):
            cli.error("resolution must be between 640 × 480 and 7680 × 4320")
        if not (0 <= args.wait <= 3600 and 0 < args.timeout <= 3600):
            cli.error("--wait must be 0–3600 and --timeout must be greater than 0 and at most 3600 seconds")

        input_file = args.input_file.expanduser().resolve()
        if not input_file.is_file():
            raise ValueError(f"Input file not found: {input_file}")
        paths, screenshot_root = plan_outputs(args, input_file)
        targets = parse_input_file(
            input_file, skip_invalid=args.skip_invalid, warn=lambda message: print(message, file=sys.stderr),
        )
        check_system()
        for path in paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
        screenshot_root.mkdir(mode=0o700, exist_ok=True)
        screenshot_dir = Path(mkdtemp(prefix="run-", dir=screenshot_root))
        try:
            results = asyncio.run(capture_targets(
                targets, screenshot_dir, width=args.width, height=args.height, wait=args.wait,
                timeout=args.timeout, user_agent=args.user_agent, ignore_cert_errors=args.ignore_cert_errors,
                browser_executable=args.browser_executable,
            ))
        finally:
            if not any(screenshot_dir.iterdir()):
                screenshot_dir.rmdir()

        failed = sum(result.screenshot is None for result in results)
        if failed:
            print(f"{failed} of {len(results)} targets have no screenshot.", file=sys.stderr)
        if screenshot_dir.exists():
            print(f"Screenshots: {screenshot_dir}")
        for name, path in paths.items():
            try:
                EXPORTERS[name](results, path)
                print(f"{name.upper()}: {path}")
            except Exception as exc:
                failed += 1
                print(f"Could not write {name.upper()} report: {exc}", file=sys.stderr)
        return int(bool(failed))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
    except (BrowserError, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
    return 1
