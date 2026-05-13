# Skill: HITL and Recovery

Patterns for human-in-the-loop escalation and unexpected state recovery.

## Recovery Handler — Must Exist Before Phase 1

`agent/recovery.py` must be implemented before any browser or desktop automation begins. Every executor relies on it for unexpected state handling.

```python
# agent/recovery.py
from enum import Enum

class RecoveryAction(str, Enum):
    DISMISS_DIALOG   = "dismiss_dialog"
    SCROLL_RETRY     = "scroll_and_retry"
    WAIT_RETRY       = "wait_and_retry"
    NAVIGATE_BACK    = "navigate_back"
    RE_LOGIN         = "re_login"
    RECONNECT_RDP    = "reconnect_rdp"
    FLAG_HUMAN       = "flag_for_human"

# Detection rules — checked in order on every ScreenState
DETECTION_RULES = [
    # (condition_fn, recovery_action)
    (lambda s: s.error_present and "session" in (s.blocking_issue or "").lower(),
     RecoveryAction.RE_LOGIN),

    (lambda s: s.blocking_modal and s.app_type == "browser",
     RecoveryAction.DISMISS_DIALOG),

    (lambda s: s.task_progress == "not_started" and s.app_type == "browser",
     RecoveryAction.WAIT_RETRY),

    (lambda s: s.app_type == "unknown",
     RecoveryAction.NAVIGATE_BACK),
]

SESSION_EXPIRED_SIGNALS = [
    "please log in", "session has expired", "session expired",
    "your session", "401", "403", "authentication required",
    "login required", "sign in"
]

class RecoveryHandler:

    def detect(self, screen: ScreenState, working: WorkingMemory) -> RecoveryAction | None:
        # Check session expiry by text
        summary_lower = screen.state_summary.lower()
        if any(sig in summary_lower for sig in SESSION_EXPIRED_SIGNALS):
            return RecoveryAction.RE_LOGIN

        # Check retry limit for current step
        step_key = str(working.step)
        if working.retry_counts.get(step_key, 0) >= 3:
            return RecoveryAction.FLAG_HUMAN

        # Check detection rules
        for condition, action in DETECTION_RULES:
            if condition(screen):
                return action

        # Check error_recoveries ChromaDB for known error
        if screen.blocking_issue:
            known = self.knowledge.query_error_recovery(
                error_text=screen.blocking_issue,
                app=working.current_app,
            )
            if known:
                return RecoveryAction(known["recovery_action"])

        return None   # no recovery needed

    def recover(self, action: RecoveryAction, screen: ScreenState,
                working: WorkingMemory, page=None) -> bool:
        handlers = {
            RecoveryAction.DISMISS_DIALOG:  self._dismiss_dialog,
            RecoveryAction.SCROLL_RETRY:    self._scroll_retry,
            RecoveryAction.WAIT_RETRY:      self._wait_retry,
            RecoveryAction.NAVIGATE_BACK:   self._navigate_back,
            RecoveryAction.RE_LOGIN:        self._re_login,
            RecoveryAction.RECONNECT_RDP:   self._reconnect_rdp,
            RecoveryAction.FLAG_HUMAN:      self._flag_human,
        }
        handler = handlers.get(action)
        if handler:
            return handler(screen, working, page)
        return False
```

## Recovery Implementations

```python
def _dismiss_dialog(self, screen, working, page) -> bool:
    """Try common dismiss selectors in order."""
    DISMISS_SELECTORS = [
        "button:has-text('OK')",
        "button:has-text('Close')",
        "button:has-text('Cancel')",
        "button:has-text('Dismiss')",
        "[aria-label='Close']",
        ".modal-close",
    ]
    for sel in DISMISS_SELECTORS:
        try:
            if page.query_selector(sel):
                page.click(sel)
                page.wait_for_timeout(500)
                return True
        except Exception:
            continue
    # JS escape hatch
    page.keyboard.press("Escape")
    return True

def _wait_retry(self, screen, working, page) -> bool:
    step_key = str(working.step)
    wait_ms = 3000 * (working.retry_counts.get(step_key, 0) + 1)   # 3s, 6s, 9s
    page.wait_for_timeout(min(wait_ms, 10_000))
    working.retry_counts[step_key] = working.retry_counts.get(step_key, 0) + 1
    return True

def _navigate_back(self, screen, working, page) -> bool:
    if working.current_url:
        page.goto(working.current_url)
        page.wait_for_load_state("networkidle", timeout=10_000)
        return True
    page.go_back()
    return True

def _re_login(self, screen, working, page) -> bool:
    """Re-authenticate using stored credentials."""
    from config.settings import settings
    creds = settings.get_credentials(working.current_app)
    # Navigate to login page
    page.goto(settings.rdweb_url)
    page.fill('[data-testid="rdweb-username"]', creds.username)
    page.fill('[data-testid="rdweb-password"]', creds.password)
    page.click('[data-testid="rdweb-submit"]')
    page.wait_for_load_state("networkidle", timeout=15_000)
    # Navigate back to last known position
    if working.current_url:
        page.goto(working.current_url)
    return True
```

## HITL Queue — Pause and Resume

```python
# hitl/queue.py
import time
from pathlib import Path

class HITLQueue:

    POLL_INTERVAL = 10   # seconds between polls
    TIMEOUT_MINUTES = 30

    def flag_and_wait(self, task_id: str, agent_id: str, reason: str,
                      page, working: WorkingMemory) -> dict:
        """
        Write HITL request, print console notice, poll until resolved.
        Keeps Playwright page and RDP session alive while waiting.
        Returns human's resolution dict.
        """
        # Take screenshot for human reviewer
        screenshot_path = self._take_screenshot(page, task_id, "hitl_trigger")

        # Write to SQLite queue
        hitl_id = self.session.write_hitl(
            task_id=task_id,
            agent_id=agent_id,
            reason=reason,
            screenshot=str(screenshot_path),
            context={
                "step": working.step,
                "task_type": working.task_type,
                "extracted_values": working.extracted_values,
                "last_action": working.last_action,
                "current_url": working.current_url,
            },
            timeout_minutes=self.TIMEOUT_MINUTES,
        )

        # Console notice (primary notification for MVP)
        print(f"\n{'='*60}")
        print(f"[HITL REQUIRED] Agent: {agent_id} | Task: {task_id}")
        print(f"  Step: {working.step} | Reason: {reason}")
        print(f"  Screenshot: {screenshot_path}")
        print(f"  Review at: http://localhost:8080/review/{task_id}")
        print(f"  Timeout: {self.TIMEOUT_MINUTES} minutes")
        print(f"{'='*60}\n")

        # Poll for resolution
        while True:
            time.sleep(self.POLL_INTERVAL)
            resolution = self.session.poll_hitl(task_id)
            if resolution is not None:
                log.info("hitl.resolved", task_id=task_id, step=working.step)
                return resolution
            # HITLTimeoutError raised by poll_hitl on timeout

    def apply_resolution(self, resolution: dict, working: WorkingMemory) -> None:
        """Apply human corrections to working memory before resuming."""
        corrections = resolution.get("corrections", {})
        working.extracted_values.update(corrections)
        working.hitl_pending = False
        log.info("hitl.applied", corrections=corrections)
```

## HITL Dashboard (FastAPI)

```python
# hitl/server.py
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI(title="Vision RPA Agent — HITL Dashboard")

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    pending = session.get_pending_hitl()
    # Render Jinja2 template showing all pending reviews
    ...

@app.get("/review/{task_id}", response_class=HTMLResponse)
async def review(task_id: str):
    item = session.get_hitl_item(task_id)
    # Show: screenshot, context, extracted_values, reason
    # Form: corrections dict, approval checkbox
    ...

@app.post("/resolve/{task_id}")
async def resolve(task_id: str, corrections: str = Form("{}"), approved: bool = Form(True)):
    resolution = {
        "approved": approved,
        "corrections": json.loads(corrections),
        "resolved_by": "human",
        "resolved_at": utcnow(),
    }
    session.resolve_hitl(task_id, resolution)
    return {"status": "resolved", "task_id": task_id}

def run_server():
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
```

## HITL Trigger Rules

```python
# planner.py — enforced unconditionally
def _check_hitl_required(self, plan: ActionPlan, working: WorkingMemory) -> ActionPlan:

    # Rule 1: confidence below threshold
    if plan.confidence < settings.confidence_threshold:
        plan.requires_hitl = True
        plan.reason += f" [HITL: confidence {plan.confidence} < {settings.confidence_threshold}]"

    # Rule 2: financial field below strict threshold
    if plan.is_financial and plan.confidence < 0.90:
        plan.action_type = "flag_for_human"
        plan.reason = f"Financial field confidence {plan.confidence} < 0.90"

    # Rule 3: write action in first 10 runs of this task type
    runs = self.session.count_completed_tasks(working.task_type)
    WRITE_ACTIONS = {"type_financial", "form_submit", "modal_save", "sg_request_submit"}
    if plan.action_type in WRITE_ACTIONS and runs < 10:
        plan.requires_hitl = True

    # Rule 4: same step failed 3+ times
    step_key = str(working.step)
    if working.retry_counts.get(step_key, 0) >= 3:
        plan.action_type = "flag_for_human"
        plan.reason = f"Step {step_key} failed {working.retry_counts[step_key]} times"

    return plan
```

## RDP Keep-Alive (Run During HITL Wait)

The RDP keep-alive thread must continue running while HITL is pending — session must not expire while human reviews.

```python
# The keep-alive thread is started when RDP session opens and stopped only
# when the task completes or the session is explicitly closed.
# During HITL wait: thread continues → RDP session stays alive.
# During page.wait (Playwright polling): browser page stays open → SAML session valid.
```
