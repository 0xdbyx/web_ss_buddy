"""Capture the X11 desktop while headed Chromium is still displaying it."""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from PIL import Image
from playwright.async_api import Error as BrowserError, async_playwright
from pyvirtualdisplay import Display

from .inputs import Target


DEFAULT_WIDTH = 1366
DEFAULT_HEIGHT = 768
SYSTEM_TOOLS = ("Xvfb", "xauth", "scrot")
SYSTEM_SETUP = "Install system tools with: sudo apt install xvfb xauth scrot"


@dataclass
class Capture:
    target: Target
    final_url: str = ""
    response_code: int | None = None
    title: str = ""
    screenshot_taken_at: str = ""
    screenshot: Path | None = None
    error: str = ""


def check_system() -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Desktop capture requires Linux with Xvfb.")
    missing = [name for name in SYSTEM_TOOLS if not shutil.which(name)]
    if missing:
        raise RuntimeError(f"Missing {', '.join(missing)}. {SYSTEM_SETUP}")


def chromium_path(chromium, executable: Path | None) -> str:
    if executable is not None:
        path = executable.expanduser().absolute()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(f"Chromium executable is missing or not executable: {path}")
        return str(path)
    managed = Path(chromium.executable_path)
    if managed.is_file():
        return str(managed)
    for name in ("chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError("Chromium is missing. Run web-ss-buddy --install-browser or install system Chromium.")


@contextmanager
def isolated_display(width: int, height: int):
    with TemporaryDirectory(prefix="web-ss-buddy-xauth-") as private_dir:
        authority = Path(private_dir) / "Xauthority"
        authority.touch(mode=0o600)
        cookie = secrets.token_hex(16)

        def add_cookie(display_name):
            subprocess.run(
                ["xauth", "-f", str(authority), "add", display_name, ".", cookie],
                check=True, capture_output=True, timeout=5,
            )

        # Xvfb reads the cookie regardless of the display number in its auth file.
        # Clients need a matching entry once Xvfb has chosen the display number.
        add_cookie(":0")
        with Display(
            size=(width, height), color_depth=24, manage_global_env=False,
            extra_args=["-auth", str(authority)],
        ) as display:
            env = display.env()
            add_cookie(env["DISPLAY"])
            env["XAUTHORITY"] = str(authority)
            yield env


def desktop_screenshot(path: Path, env: dict[str, str], size: tuple[int, int]):
    completed = subprocess.run(
        ["scrot", str(path)], env=env, capture_output=True, text=True, timeout=15,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or f"scrot exited with status {completed.returncode}")
    path.chmod(0o600)
    with Image.open(path) as image:
        if image.size != size:
            raise RuntimeError(f"Expected screenshot size {size}, received {image.size}.")
        image.verify()


async def screenshot_url(context, target, path, *, env, size, wait, timeout):
    result = Capture(target)
    pending = None
    page = None

    def track_response(response):
        try:
            request = response.request
            if (
                request.is_navigation_request()
                and request.resource_type == "document"
                and request.frame == page.main_frame
            ):
                result.response_code = response.status
        except BrowserError:
            # Chromium can emit navigation events before a frame exists.
            pass

    try:
        page = await asyncio.wait_for(context.new_page(), timeout=10)
        page.on("response", track_response)

        # Keep the navigation alive while scrot captures any native auth prompt.
        # Let Playwright dismiss JavaScript dialogs as it did in the original CLI.
        pending = asyncio.create_task(page.goto(target.url, wait_until="domcontentloaded", timeout=0))
        done, _ = await asyncio.wait({pending}, timeout=timeout)
        if done:
            try:
                pending.result()
            except BrowserError as exc:
                if result.response_code != 401 or "ERR_INVALID_AUTH_CREDENTIALS" not in str(exc):
                    result.error = str(exc).splitlines()[0]
        else:
            result.error = "Navigation was still pending at the timeout."
        await asyncio.sleep(max(wait, 0.2))  # Allow Chromium to paint its window UI.

        if page.url and page.url != "about:blank" and not page.url.startswith("chrome-error:"):
            result.final_url = page.url
        if pending.done() and result.final_url:
            try:
                result.title = await asyncio.wait_for(page.title(), timeout=1)
            except (BrowserError, asyncio.TimeoutError):
                pass
        result.screenshot_taken_at = datetime.now().astimezone().isoformat(timespec="seconds")
        desktop_screenshot(path, env, size)
        result.screenshot = path
    except (asyncio.TimeoutError, BrowserError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        path.unlink(missing_ok=True)
        reason = str(exc).strip() or "Chromium did not open the target tab in time."
        result.error = f"{result.error} {reason}".strip()
    finally:
        # Close popups as well as the target tab so they do not obscure the next capture.
        for tab in context.pages:
            try:
                await asyncio.wait_for(tab.close(), timeout=3)
            except (BrowserError, asyncio.TimeoutError):
                pass
        if pending is not None:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    return result


async def capture_targets(
    targets, screenshot_dir, *, width, height, wait, timeout,
    browser_executable, user_agent, ignore_cert_errors,
):
    check_system()
    size = (width, height)
    with isolated_display(*size) as env:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                executable_path=chromium_path(playwright.chromium, browser_executable),
                headless=False,
                chromium_sandbox=True,
                args=[
                    "--disable-dev-shm-usage", "--new-window", "--ozone-platform=x11",
                    "--window-position=0,0", f"--window-size={width},{height}",
                ],
                env=env,
            )
            try:
                context = await browser.new_context(
                    no_viewport=True, user_agent=user_agent, ignore_https_errors=ignore_cert_errors,
                )
                results = []
                for index, target in enumerate(targets, 1):
                    print(f"[{index}/{len(targets)}] Opening {target.url!r}", flush=True)
                    path = screenshot_dir / f"{index:05d}.png"
                    result = await screenshot_url(
                        context, target, path, env=env, size=size, wait=wait, timeout=timeout,
                    )
                    if result.error:
                        print(f"  {result.error!r}", file=sys.stderr)
                    results.append(result)
                return results
            finally:
                await browser.close()
