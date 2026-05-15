# web_ss_buddy

Python script that takes screenshots from plain URLs or `httpx` output and generates a PDF screenshot report plus a CSV summary.

Vibe coded this to help with web enumeration, especially when working with a large number of live web services. After identifying valid web targets, this tool helps screen capture what each target looks like in a browser.

Enumeration workflow:

1. Use Nmap or another scanner to discover open web ports.
2. Use `httpx` to identify valid live web services.
3. Pass the URL list or `httpx` output into `web_ss_buddy`.
4. Generate a PDF report containing browser screenshots.

## Features

- Accepts plain URL lists
- Accepts `httpx` output
- Captures browser screenshots using Chromium
- Captures browser-level prompts such as basic auth or digest auth popups
- Records the original URL and final URL
- Generates a PDF screenshot report
- Generates a CSV summary
- Saves screenshots locally

## Installation

Create a Python virtual environment:

    python3 -m venv .venv

Activate the virtual environment:

    source .venv/bin/activate

Install Python dependencies:

    pip install pillow playwright pyvirtualdisplay reportlab

Install Chromium for Playwright:

    playwright install chromium

Install system dependencies.

On Debian/Ubuntu:

    sudo apt install xvfb scrot

Make the script executable:

    chmod +x web_ss_buddy.py

## Usage

    ./web_ss_buddy.py <input_file> -o <output.pdf>

Example with URL list:

    ./web_ss_buddy.py urls.txt -o report.pdf

Example with custom CSV output:

    ./web_ss_buddy.py urls.txt -o report.pdf --csv results.csv

Show help:

    ./web_ss_buddy.py -h

## Input Formats

`web_ss_buddy` supports plain URLs and `httpx` output.

Plain URL list:

    https://example.com
    http://example.com:8080
    https://test.local/login

`httpx` output:

    https://example.com [200] [Example Domain] [nginx]
    http://192.168.1.10:8080 [401] [Login] [Apache]

URLs must start with `http://` or `https://`.

## Example Workflow

Use `httpx` to identify live web services:

    cat targets.txt | httpx \
      -ports 80,443,8080,8443,8000,8888,9000 \
      -title \
      -tech-detect \
      -status-code \
      -content-length \
      -follow-redirects \
      -o live_web.txt

Generate the screenshot report:

    ./web_ss_buddy.py live_web.txt -o report.pdf

This creates:

    screenshots/
    report.pdf
    report.csv

## Options

Increase wait time for slow pages or JavaScript redirects:

    ./web_ss_buddy.py live_web.txt -o report.pdf --wait 15

Ignore certificate errors:

    ./web_ss_buddy.py live_web.txt -o report.pdf --ignore-cert-errors

Skip invalid lines instead of stopping the script:

    ./web_ss_buddy.py live_web.txt -o report.pdf --skip-invalid

Use a custom User-Agent:

    ./web_ss_buddy.py live_web.txt -o report.pdf --user-agent "Mozilla/5.0"

Set browser screenshot size:

    ./web_ss_buddy.py live_web.txt -o report.pdf --width 1366 --height 768

Choose a custom CSV filename:

    ./web_ss_buddy.py live_web.txt -o report.pdf --csv results.csv

## PDF Output

The generated PDF report contains these fields for each target:

    Input line
    Original URL
    Final URL
    Response Code
    Title
    Date and time screenshot was taken
    Screenshot

Field meaning:

| Field | Description |
|---|---|
| Input line | The original line from the input file |
| Original URL | The URL extracted from the input line |
| Final URL | The final browser URL after redirects or JavaScript routing |
| Response Code | The browser-observed HTTP response code |
| Title | The page title reported by the browser |
| Date and time screenshot was taken | Timestamp of when the screenshot was captured |
| Screenshot | Browser screenshot of the target |

## CSV Output

The tool creates a CSV summary by default using the same output filename as the PDF.

The CSV contains these fields:

| Field | Description |
|---|---|
| Target | Hostname or IP extracted from the URL |
| Port | Port extracted from the URL, or default port based on scheme |
| URL | Original URL extracted from the input line |
| Final URL | Final browser URL after redirects or JavaScript routing |
| Response Code | Browser-observed HTTP response code |
| Title | Page title reported by the browser |

To choose a custom CSV filename:

    ./web_ss_buddy.py urls.txt -o report.pdf --csv results.csv

## Example Report Entry

Example input line:

    https://example.com [200] [Example Domain] [nginx]

The PDF report will include:

    Input line: https://example.com [200] [Example Domain] [nginx]
    Original URL: https://example.com
    Final URL: https://example.com
    Response Code: 200
    Title: Example Domain
    Date and time screenshot was taken: 2026-05-15 12:00:00
    Screenshot: Embedded screenshot image

The CSV report will include:

    Target,Port,URL,Final URL,Response Code,Title
    example.com,443,https://example.com,https://example.com,200,Example Domain
