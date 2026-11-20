# web_ss_buddy

Capture the headed Chromium window for a list of HTTP(S) URLs. The PNGs include the browser chrome and native HTTP Basic and Digest sign-in dialogs. Open the default HTML report offline to browse captures and their metadata.

Vibe coded this to help with web enumeration, especially when working with a large number of live web services. After identifying valid web targets, this tool helps screen capture what each target looks like in a browser.

## Install

Use Linux with Python 3.10 or newer. On Debian and Kali, install system Chromium and the virtual desktop tools, then install the CLI with pipx.

```sh
sudo apt install git pipx chromium xvfb xauth scrot fonts-dejavu-core fonts-droid-fallback
git clone https://github.com/0xdbyx/web_ss_buddy.git
cd web_ss_buddy
pipx install .
web-ss-buddy --doctor
```

If your shell cannot find the CLI, run `pipx ensurepath` and open a new terminal. The CLI also accepts `web_ss_buddy` and `python3 web_ss_buddy.py` (when the project dependencies are installed for that Python).

For Playwright-managed Chromium, use `web-ss-buddy --install-browser`. On a supported Debian or Ubuntu host that needs Chromium's shared libraries, run `pipx run --spec 'playwright>=1.49,<2' playwright install-deps chromium` first. Use `--browser-executable /path/to/chromium` to select a specific binary. Run the tool as an ordinary user on a host that supports the Chromium sandbox. Desktop capture requires Xvfb and scrot and is supported on Linux.

## Capture URLs

Put one explicit HTTP(S) URL per line in `urls.txt`.

```text
https://example.com
http://example.com:8080
```

```sh
web-ss-buddy urls.txt
```

Open `report.html` directly in a browser. Keep the `screenshots/` folder alongside it when moving or sharing the report. Each run gets its own private subdirectory, so previously saved reports keep their original images. Treat reports and screenshots as sensitive when they contain internal URLs or page content.

### Optional ProjectDiscovery httpx input

The tool also accepts text lines such as `https://example.com [200] [Example Domain] [nginx]`. It uses the leading URL for capture and preserves the full original line in HTML and PDF. Status and title in the reports come from Chromium.

Use ProjectDiscovery's `httpx` if you have a list of hosts to probe for web services. Put one hostname or IP per line in `hosts.txt`.

```sh
httpx -list hosts.txt -ports http:80,https:443,http:8080,https:8443 \
  -status-code -title -tech-detect -no-color -disable-update-check -output live_web.txt
web-ss-buddy live_web.txt
```

## Reports

| Format | Contents |
| --- | --- |
| HTML | Large preview, gallery and searchable table. Switch between dark and light within the browser tab. **Copy table** copies matching rows as TSV. |
| PDF | Portrait A4, with each target starting on a new page. The full input line, original and final URLs, response code, title, capture timestamp and screenshot appear in each entry. Long entries can span pages. |
| CSV and XLSX | The same six columns in order: `Target,Port,URL,Final URL,Response Code,Title`. Unknown metadata is blank. Text that could be interpreted as a spreadsheet formula is prefixed with an apostrophe. |

```sh
web-ss-buddy urls.txt -o review.html
web-ss-buddy urls.txt --format pdf --format xlsx -o review
web-ss-buddy urls.txt --format csv -o inventory.csv
web-ss-buddy urls.txt --all-formats -o review.html
```

Multiple formats share a single capture run. An existing report file at the chosen path is replaced when its new copy is ready. Captures are stored alongside the HTML report, or alongside the first selected report if HTML is omitted.

The original `-o report.pdf` invocation still writes PDF and CSV together. Use `--csv results.csv` to name the CSV or `--format pdf` for PDF alone. With an explicit format, `-o` accepts either a filename in a selected format or a filename stem.

## Capture options

| Option | Behavior |
| --- | --- |
| `--width 1366 --height 768` | Set the virtual desktop and PNG size. The default is 1366 × 768. |
| `--wait 10` | Allow the browser to settle after navigation. Adjust for slower pages or client-side redirects. |
| `--timeout 20` | Set the navigation deadline in seconds. A stalled navigation can still produce a screenshot. |
| `--user-agent "Mozilla/5.0"` | Override the browser's User-Agent. |
| `--ignore-cert-errors` | Allow invalid HTTPS certificates. By default Chromium applies its normal certificate checks. |
| `--skip-invalid` | Report line numbers and continue with valid URLs. |

JavaScript alerts are dismissed automatically, as in the original script. The screenshot can show a new tab opened by a page. The metadata describes the target tab, and all tabs are closed before the next target. A navigation failure can still produce a screenshot of the browser window. If Chromium doesn't expose a final URL, status or title, that field is left unknown.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The automated tests use local files and a mocked capture, so they require the Python package dependencies but no browser display or target server.
