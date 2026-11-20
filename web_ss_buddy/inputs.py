from dataclasses import dataclass
import re
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Target:
    raw_line: str
    url: str
    host: str
    port: int


def parse_input_file(path: Path, *, skip_invalid: bool, warn) -> list[Target]:
    targets = []
    invalid = []
    with path.open(encoding="utf-8-sig", errors="replace") as source:
        for number, line in enumerate(source, 1):
            raw_line = line.rstrip("\r\n")
            if not raw_line.strip():
                continue
            match = re.match(r"\s*(https?://\S+)", raw_line, re.IGNORECASE)
            try:
                if not match:
                    raise ValueError("expected an HTTP(S) URL")
                url = match[1]
                parsed = urlparse(url)
                if not parsed.hostname or "\\" in parsed.netloc or parsed.port == 0:
                    raise ValueError("expected a hostname and a valid port")
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                targets.append(Target(raw_line, url, parsed.hostname, port))
            except ValueError:
                invalid.append(number)

    if invalid:
        numbers = ", ".join(map(str, invalid))
        hint = "Expected a URL starting with http:// or https://, optionally followed by httpx metadata."
        if not skip_invalid:
            raise ValueError(f"Invalid input on line(s) {numbers}. {hint} Use --skip-invalid to continue.")
        warn(f"Skipping invalid input on line(s) {numbers}. {hint}")
    if not targets:
        raise ValueError("No valid URLs found in the input file.")
    return targets
