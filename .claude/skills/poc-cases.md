# Skill: POC Cases

Patterns for migrating and supporting the 4 existing POC automation cases from `insurance-agent-project`.

## Golden Rule: Reuse Locators, Don't Rewrite

The POC's `rdweb.py` has 120+ proven `data-testid` selectors. Copy it directly.

```bash
# One-time migration step
cp insurance-agent-project/automation/locators/rdweb.py \
   vision-rpa-agent/config/locators/rdweb.py
```

Then import in BrowserExecutor:

```python
# executors/browser.py
from config.locators import rdweb

# Use like:
sel = rdweb.LossDrafts.SEARCH_INPUT           # "[data-testid='ld-search-input']"
sel = rdweb.Case4Notifications.ADD_BTN         # "[data-testid='ld-cd-notifications-add']"
sel = rdweb.Case4SgRequest.MODAL               # "[data-testid='ld-cd-sg-request-modal']"
```

## Case 1 — Already Closed (Backend Only)

No browser or desktop interaction. Pure Python processing.

```python
# Wrap existing handler as a task executor
from cases.case1.handler import run as case1_handler
from cases.case1.schema import Case1Result

class Case1Executor:
    def run(self, pdf_bytes: bytes) -> Case1Result:
        # Existing logic works unchanged — just call it
        return case1_handler(pdf_bytes)

# In agent loop: when task_type == "case1"
# → skip perception, skip planning
# → call Case1Executor directly
# → store result to SQLite
# → write to audit log
# Agent loop overhead: just checkpoint + audit log
```

## Case 2 — Multi-Record Claim Operations (Browser)

8 stages. All browser-based — 100% Playwright.

```python
# Task YAML
task_id: case2_full
description: Select 3 unreviewed documents, OCR PDFs, claim search, letter request, link claim, assign docs
case_constants:
  doc_ids: ["8184371", "8184373", "8184372"]
  batch: "1584839"
  borrower_email: "DIANEBISSETT@GMAIL.COM"
  letter_template: "Monitored Claim Procedure Packet - v17"
  validated_claim_id: "0819963926"
hitl_on_confidence_below: 0.75
max_steps: 40
requires_human_approval_for: [form_submit, claim_link_save]
```

Key pattern — multi-select row handling:

```python
def select_multiple_rows(page: Page, row_ids: list[str]) -> None:
    """Select rows using click + Ctrl+click — POC-proven pattern."""
    for i, doc_id in enumerate(row_ids):
        row_sel = f'[data-testid="ld-dm-row-{doc_id}"]'
        if i == 0:
            page.click(row_sel)
        else:
            page.click(row_sel, modifiers=["Control"])
        page.wait_for_timeout(200)
```

Key pattern — PDF byte capture:

```python
def capture_pdf_from_tab(context: BrowserContext, source_link_sel: str,
                          trigger_page: Page) -> bytes:
    with context.expect_page() as new_page_info:
        trigger_page.click(source_link_sel)
    pdf_page = new_page_info.value
    pdf_page.wait_for_load_state("load")
    pdf_url = pdf_page.url
    response = pdf_page.request.get(pdf_url)
    pdf_page.close()
    return response.body()
```

## Case 3 — Hold Check (Browser + File Explorer + IIM)

13 stages. Crosses browser↔File Explorer boundary. Highest complexity for handoff management.

```python
# Context switch detection — critical pattern
def detect_context(working: WorkingMemory, screen: ScreenState) -> str:
    if screen.app_type == "file_explorer":
        return "file_explorer"
    if screen.app_type == "browser":
        if "proctor" in screen.current_url.lower():
            return "iim_browser"
        if "lossdrafts" in screen.current_url.lower():
            return "ld_browser"
        return "rdweb_browser"
    if screen.app_type == "desktop":
        return "rdp_window"
    return "unknown"
```

Key pattern — File Explorer navigation (pywinauto):

```python
def navigate_file_explorer(page: Page, path_parts: list[str]) -> bool:
    """Navigate simulated File Explorer (browser) or real File Explorer (pywinauto)."""
    # Development: simulated HTML File Explorer — use Playwright
    if settings.use_simulation:
        for folder in path_parts:
            sel = f'[data-name="{folder}"]'
            page.click(sel)
            page.wait_for_timeout(400)
        return True

    # Production: real Windows File Explorer — use pywinauto
    from executors.desktop import DesktopExecutor
    desktop = DesktopExecutor()
    explorer = desktop.find_window(title_contains="File Explorer")
    for folder in path_parts:
        desktop.double_click_item(explorer, folder)
    return True
```

Key pattern — IIM fallback (Stage 8):

```python
# Case 3 IIM recovery is now a planned fallback, not an error
# Task YAML encodes this as a conditional branch:
stages:
  - id: borrower_claim_search
    on_fail: iim_recovery     # if borrower search fails, go to IIM
  - id: iim_recovery
    optional: true            # only runs if borrower_claim_search fails
    on_fail: flag_human
```

Fuzzy matching constants (DO NOT change without SOP approval):

```python
CASE3_MATCH_THRESHOLD = 0.60    # minimum fuzzy score for borrower claim match
CASE3_IIM_MATCH_THRESHOLD = 0.70
CLAIM_STATUS_PRIORITY = ["Requested Claim", "Open", "Reopened Claim", "Open Claim", "Pending"]
CLAIM_STATUS_EXCLUDE = ["Closed Claim", "Closed"]
```

## Case 4 — Stamp and Go (Full Pipeline, 12 Stages)

Most complex case. Excel → LD → dual-PDF OCR → multiple modals → notifications.

```python
# Task YAML
task_id: case4_stamp_and_go
description: Full Stamp and Go pipeline from Excel read to notification creation
case_constants:
  loan_no: "0156312522"
  borrower: "CRYSTAL D STEWART"
  processing_as: "Stamp and Go"
  excel_pattern: "Coforge LD Daily Tasks_"
  borrower_match_threshold: 0.90    # stricter than Case 3
hitl_on_confidence_below: 0.75
financial_field_confidence: 0.90
max_steps: 80                        # 12 stages × ~6 actions each
requires_human_approval_for:
  - claim_update_save
  - sg_request_submit
  - any_financial_write
```

Key pattern — Excel read from network path:

```python
def read_excel_from_url(excel_url: str, excel_filename: str) -> dict:
    """Download Excel via HTTP and parse with openpyxl. DO NOT open in browser."""
    import urllib.request
    import openpyxl
    from io import BytesIO

    with urllib.request.urlopen(excel_url) as resp:
        data = resp.read()

    wb = openpyxl.load_workbook(BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    # Fuzzy column matching — real file column names may vary
    headers = {str(cell.value).strip().lower(): idx
               for idx, cell in enumerate(next(ws.rows)) if cell.value}
    # Map to expected field names using fuzzy match
    ...
```

Key pattern — Notification selection via JS (preserved from POC):

```python
def select_notification_via_js(page: Page, catalog_idx: int) -> None:
    """Toggle notification checkbox via JS — avoids ElementHandle detach.

    _renderNotifRows() rebuilds tbody.innerHTML after every toggle,
    invalidating any held ElementHandle. JS call bypasses this.
    In real app: verify ldCdNotifToggle exists before calling.
    """
    result = page.evaluate(
        f"typeof ldCdNotifToggle !== 'undefined' && ldCdNotifToggle({catalog_idx}, true)"
    )
    if not result:
        # JS function not available — fallback to element click
        row_sel = f'[data-testid="ld-cd-notif-row-{catalog_idx}"] input[type="checkbox"]'
        page.click(row_sel, force=True)
```

Key pattern — RCV extraction (financial field — strict confidence):

```python
def extract_rcv_values(ocr_text: str, vlm_result: dict) -> tuple[list[float], float]:
    """Extract RCV values from adjuster report. Requires confidence ≥ 0.90."""
    values = []

    # First: try regex on native text (pdfplumber) — most reliable
    import re
    matches = re.findall(r'\$?([\d,]+\.?\d{0,2})', ocr_text)
    for m in matches:
        try:
            values.append(float(m.replace(",", "")))
        except ValueError:
            pass

    if values:
        return values, 0.95   # native text extraction is high confidence

    # Second: use VLM result — but require 0.90+
    vlm_amount = vlm_result.get("rcv_total", {})
    if vlm_amount.get("confidence", 0) >= 0.90:
        return [float(vlm_amount["value"].replace("$","").replace(",",""))], vlm_amount["confidence"]

    # Below threshold — return empty, caller will route to HITL
    return [], 0.0
```

## Pydantic Schema Reuse

Copy schemas from POC, extend for new fields:

```python
# cases/schemas.py — aggregated from POC
from pydantic import BaseModel

# Reuse directly from POC:
# from insurance-agent-project: Case1Result, Case2FullResult, Case3InitResult
# StampAndGoRecord, Case4ClaimDetail, Stage8Result ... Stage12Result

class TaskResult(BaseModel):
    """Universal result wrapper for all task types."""
    task_id: str
    task_type: str
    status: str                    # "success" | "partial" | "failed" | "hitl_wait"
    case_result: dict | None       # case-specific result (Case1Result etc.)
    steps_completed: int = 0
    total_steps: int = 0
    hitl_count: int = 0
    llm_calls: int = 0
    cache_hits: int = 0
    duration_seconds: float = 0.0
```

## Migration Order and Checklist

```
□ Copy config/locators/rdweb.py from POC
□ Copy + adapt Pydantic schemas
□ Case 1: wrap handler.py as Case1Executor, test end-to-end
□ Case 2: implement 8 stages in browser executor, test against simulation
□ Case 3: implement File Explorer handoff, test browser↔pywinauto switch
□ Case 4: implement all 12 stages, test full pipeline with Excel + dual-PDF
□ Run all 4 cases with 3 agents simultaneously
```
