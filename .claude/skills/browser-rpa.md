# Skill: Browser RPA

Playwright executor patterns for this project. LD and IIM are browser-based — this is the primary executor.

## Key Rule

Never use pixel coordinates for browser automation. Always resolve via selector hierarchy first.

## Selector Resolution Order

```python
# executors/browser.py — resolve_selector()
SELECTOR_PRIORITY = [
    lambda desc: f'[data-testid="{desc}"]',          # 1. data-testid (most stable)
    lambda desc: f'[aria-label="{desc}"]',            # 2. aria-label
    lambda desc: f'[name="{desc}"]',                  # 3. name attribute
    lambda desc: None,                                # 4. LLM-generated (ask planner)
]

def resolve_selector(self, description: str, page: Page) -> str | None:
    # First: check config/locators/rdweb.py for known selectors
    known = rdweb_locators.get(description)
    if known and page.query_selector(known):
        return known

    # Try selector hierarchy
    for strategy in SELECTOR_PRIORITY[:3]:
        sel = strategy(description)
        if sel and page.query_selector(sel):
            return sel

    # Ask LLM for selector suggestion (cache result in ChromaDB)
    sel = self.planner.suggest_selector(description, self._screenshot())
    if sel and page.query_selector(sel):
        self.knowledge.store_ui_pattern(description, sel)
        return sel

    return None   # flag_for_human in caller
```

## Core Action Methods

```python
class BrowserExecutor:

    def click(self, page: Page, target: str, force: bool = False) -> ActionResult:
        sel = self.resolve_selector(target, page)
        if not sel:
            return ActionResult(status="fail", error=f"Selector not found: {target}")
        try:
            page.click(sel, force=force, timeout=8_000)
            return ActionResult(status="success")
        except Exception as e:
            # Fallback: JS click (bypasses pointer-event interception)
            try:
                page.evaluate(f"document.querySelector('{sel}')?.click()")
                return ActionResult(status="success", note="js_fallback")
            except Exception:
                return ActionResult(status="fail", error=str(e))

    def fill(self, page: Page, target: str, value: str) -> ActionResult:
        sel = self.resolve_selector(target, page)
        if not sel:
            return ActionResult(status="fail", error=f"Field not found: {target}")
        page.fill(sel, "")         # clear first
        page.type(sel, value, delay=60)   # human-like typing
        return ActionResult(status="success")

    def wait_and_read(self, page: Page, selector: str, timeout: int = 8_000) -> str:
        try:
            page.wait_for_selector(selector, state="visible", timeout=timeout)
            el = page.query_selector(selector)
            # Use text_content() not inner_text() — works even in CSS-hidden elements
            return (el.text_content() or "").strip() if el else ""
        except Exception:
            return ""

    def select_option(self, page: Page, target: str, label: str) -> ActionResult:
        sel = self.resolve_selector(target, page)
        if not sel:
            return ActionResult(status="fail", error=f"Select not found: {target}")
        page.select_option(sel, label=label)
        return ActionResult(status="success")
```

## Tab Coordination (Cases 2, 3, 4)

```python
class TabRegistry:
    """Track all open Playwright pages across browser context."""

    def __init__(self, context: BrowserContext):
        self.context = context
        self._registry: dict[str, Page] = {}   # name → page

    def register(self, name: str, page: Page) -> None:
        self._registry[name] = page

    def get(self, name: str) -> Page | None:
        return self._registry.get(name)

    def open_pdf_tab(self, trigger_page: Page, link_selector: str) -> Page:
        """Click a link that opens a PDF in a new tab, return that tab."""
        with self.context.expect_page() as new_page_info:
            trigger_page.click(link_selector)
        pdf_page = new_page_info.value
        pdf_page.wait_for_load_state("load")
        self.register("pdf_tab", pdf_page)
        return pdf_page

    def capture_pdf_bytes(self, pdf_page: Page, pdf_url: str) -> bytes:
        """Fetch PDF bytes without re-rendering — uses Playwright's request context."""
        response = pdf_page.request.get(pdf_url)
        return response.body()

    def close_tab(self, name: str) -> None:
        page = self._registry.pop(name, None)
        if page:
            page.close()
```

## Toast Notification Pattern

Toasts are transient — always wait for them explicitly with a short timeout.

```python
def wait_for_toast(self, page: Page, selector: str, msg_selector: str,
                   timeout: int = 6_000) -> tuple[bool, str]:
    """Returns (visible, message_text). Non-fatal if toast auto-dismissed."""
    try:
        page.wait_for_selector(selector, state="visible", timeout=timeout)
        msg_el = page.query_selector(msg_selector)
        msg = (msg_el.text_content() or "").strip() if msg_el else ""
        return True, msg
    except Exception:
        # Toast may have already auto-dismissed — non-fatal
        return False, ""

# IMPORTANT: shared toast elements retain previous text.
# Always reset toast text before triggering a new action that shows it.
# Example from POC (ldCdSaveLetter bug): set toastMsgEl.textContent before display.
```

## Modal Handling

```python
def handle_modal(self, page: Page, modal_selector: str,
                 actions: list[dict], close_selector: str) -> ActionResult:
    """Open modal, perform actions, close, verify closed."""
    # Verify modal is open
    try:
        page.wait_for_selector(modal_selector, state="visible", timeout=8_000)
    except Exception:
        return ActionResult(status="fail", error="Modal did not open")

    # Execute each action inside modal
    for action in actions:
        result = self._dispatch(page, action)
        if result.status == "fail":
            return result

    # Close modal
    page.click(close_selector)

    # Verify modal is closed before returning
    try:
        page.wait_for_selector(modal_selector, state="hidden", timeout=5_000)
    except Exception:
        pass   # may already be gone
    return ActionResult(status="success")
```

## JS Evaluation Fallback

Use when: pointer-event interception blocks click, DOM needs direct manipulation.

```python
def js_click(self, page: Page, selector: str) -> ActionResult:
    """Bypass pointer-event interception with direct JS click."""
    page.evaluate(
        f"var el = document.querySelector('{selector}'); if(el) el.click();"
    )
    return ActionResult(status="success", note="js_click")

def js_set_value(self, page: Page, selector: str, value: str) -> ActionResult:
    """Set input value directly via JS (for read-only or complex inputs)."""
    escaped = value.replace("'", "\\'")
    page.evaluate(
        f"var el = document.querySelector('{selector}');"
        f"if(el) {{ el.value = '{escaped}'; el.dispatchEvent(new Event('input')); }}"
    )
    return ActionResult(status="success", note="js_set_value")
```

## State Verification After Action

Always re-check screen state after a critical action (form submit, modal close, tab switch).

```python
def verify_state(self, page: Page, expected_url_contains: str = "",
                 expected_element: str = "") -> bool:
    page.wait_for_load_state("networkidle", timeout=5_000)
    if expected_url_contains and expected_url_contains not in page.url:
        return False
    if expected_element:
        try:
            page.wait_for_selector(expected_element, state="visible", timeout=3_000)
        except Exception:
            return False
    return True
```

## Important Notes from POC

- **Tab click timeout**: `page.click()` on tabs blocked by `<nav>` overlay — always JS-click tabs
- **`text_content()` vs `inner_text()`**: use `text_content()` — works on CSS-hidden elements; `inner_text()` returns empty for hidden elements
- **ElementHandle detach**: never hold ElementHandle across DOM rebuild (e.g., after `_renderNotifRows()`). Re-query after any action that rebuilds DOM
- **`state="attached"` vs `state="visible"`**: use `"attached"` for elements that exist in DOM but may be CSS-hidden (sidebar sections, collapsed panels)
- **SAML SSO flow**: `.rdp` download triggers mstsc.exe; SAML token in browser session; keep the browser page open during RDP session
