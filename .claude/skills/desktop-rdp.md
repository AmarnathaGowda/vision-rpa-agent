# Skill: Desktop and RDP Automation

pywinauto UIA patterns for RDP window management and File Explorer. This executor has a narrow scope — LD and IIM are browser-based (Playwright handles them). pywinauto is ONLY for: RemoteApp window detection, File Explorer navigation, native Win32 dialogs.

## Core Principle

Never use `backend="win32"`. Always use `backend="uia"` — resolution-independent, stable across app updates.

```python
from pywinauto import Desktop, Application

# Connect to existing window
desktop = Desktop(backend="uia")

# Find by title (partial match)
window = desktop.window(title_re=".*File Explorer.*")

# Find by class name (fallback)
window = desktop.window(class_name="CabinetWClass")   # File Explorer class
```

## RemoteApp Window Detection

After `mstsc.exe` launches a `.rdp` file, RemoteApp windows appear as regular Win32 windows on the Agent VM. Detect them:

```python
import subprocess
import time
from pathlib import Path
from pywinauto import Desktop

def launch_and_detect_remoteapp(rdp_file: Path, app_title_contains: str,
                                 timeout: int = 30) -> object | None:
    subprocess.Popen(["mstsc.exe", str(rdp_file)])

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        desktop = Desktop(backend="uia")
        try:
            # RemoteApp windows appear with specific title patterns
            windows = desktop.windows()
            for win in windows:
                title = win.window_text()
                if app_title_contains.lower() in title.lower():
                    return win
        except Exception:
            continue
    return None   # not found within timeout — trigger recovery
```

## File Explorer Navigation

```python
def navigate_file_explorer(path_parts: list[str]) -> bool:
    """Navigate Windows File Explorer to a folder path."""
    desktop = Desktop(backend="uia")

    # Open File Explorer if not already open
    try:
        explorer = desktop.window(class_name="CabinetWClass")
    except Exception:
        subprocess.Popen(["explorer.exe"])
        time.sleep(1)
        explorer = desktop.window(class_name="CabinetWClass")

    # Use address bar to navigate directly (faster than clicking folders)
    address_bar = explorer.child_window(auto_id="1001", control_type="Edit")
    target_path = "\\".join(path_parts)
    address_bar.set_focus()
    address_bar.type_keys("^a")            # select all
    address_bar.type_keys(target_path)
    address_bar.type_keys("{ENTER}")
    time.sleep(1)
    return True


def select_file_in_explorer(explorer_window, filename_contains: str) -> str | None:
    """Find and select a file by partial name match. Returns full path."""
    file_list = explorer_window.child_window(control_type="List")
    items = file_list.children()
    for item in items:
        name = item.window_text()
        if filename_contains.lower() in name.lower():
            item.click_input()
            return name
    return None
```

## Element Finding Strategy

```python
# Priority order for UIA element finding:
# 1. AutomationId — most stable, set by developer, survives UI changes
# 2. Name + ControlType — descriptive, usually stable
# 3. ClassName — less stable, use as last resort

def find_element(window, auto_id: str = "", name: str = "",
                 control_type: str = "") -> object | None:
    kwargs = {}
    if auto_id:
        kwargs["auto_id"] = auto_id
    if name:
        kwargs["title"] = name
    if control_type:
        kwargs["control_type"] = control_type
    try:
        return window.child_window(**kwargs)
    except Exception:
        return None
```

## Interaction Methods

```python
def click_element(window, auto_id: str = "", name: str = "",
                  control_type: str = "") -> bool:
    el = find_element(window, auto_id, name, control_type)
    if el:
        el.click_input()   # click_input() = real mouse click (more reliable than click())
        return True
    return False

def type_text(window, auto_id: str, value: str) -> bool:
    el = find_element(window, auto_id=auto_id)
    if el:
        el.set_focus()
        el.type_keys("^a{DELETE}")    # clear first
        el.type_keys(value, with_spaces=True)
        return True
    return False

def read_element_text(window, auto_id: str = "", name: str = "") -> str:
    el = find_element(window, auto_id, name)
    if el:
        return el.window_text()
    return ""
```

## RDP Keep-Alive Thread

```python
import threading
import time

class RDPKeepAlive(threading.Thread):
    """Sends a synthetic mouse movement every 4 minutes to prevent RDP disconnect."""
    INTERVAL = 240   # seconds

    def __init__(self, rdp_window_title: str) -> None:
        super().__init__(daemon=True)   # daemon=True: dies when main thread exits
        self.rdp_window_title = rdp_window_title
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(self.INTERVAL):
            try:
                desktop = Desktop(backend="uia")
                win = desktop.window(title_re=f".*{self.rdp_window_title}.*")
                if win.exists():
                    # Move mouse slightly within the window
                    rect = win.rectangle()
                    mid_x = rect.left + rect.width() // 2
                    win.set_focus()
                    import pywinauto.mouse as mouse
                    mouse.move(coords=(mid_x, rect.top + 10))
            except Exception:
                pass   # window may be temporarily unavailable — not fatal

    def stop(self) -> None:
        self._stop_event.set()
```

## Disconnect Detection

```python
def is_rdp_connected(window_title_contains: str) -> bool:
    """Returns True if RDP / RemoteApp window is still alive."""
    try:
        desktop = Desktop(backend="uia")
        windows = desktop.windows()
        for win in windows:
            title = win.window_text()
            if window_title_contains.lower() in title.lower():
                # Check it's not a "Disconnected" state window
                if "disconnected" not in title.lower():
                    return True
        return False
    except Exception:
        return False
```

## Reconnect with Backoff

```python
def reconnect_rdp(rdp_file: Path, app_title: str,
                  max_attempts: int = 3) -> bool:
    for attempt in range(max_attempts):
        wait_s = 10 * (2 ** attempt)   # 10s, 20s, 40s
        time.sleep(wait_s)
        win = launch_and_detect_remoteapp(rdp_file, app_title, timeout=30)
        if win:
            return True
    return False   # all attempts failed — route to HITL
```

## Accessibility Insights (Development Tool)

Before implementing pywinauto for any new window, run **Accessibility Insights for Windows** (free, Microsoft Store) to inspect the UIA tree:

```
Accessibility Insights → LiveInspect mode → hover over element
→ shows: AutomationId, ControlType, Name, ClassName
→ copy AutomationId for most stable selector
→ if AutomationId is empty: use Name + ControlType combination
```

## What pywinauto Does NOT Handle in This Project

- LD Module browser tabs → Playwright
- IIM browser tabs → Playwright
- RD Web login form → Playwright
- PDF viewer in browser → Playwright + `page.request.get()`
- Any `data-testid` selector → Playwright

pywinauto scope: `mstsc.exe` window, File Explorer, native Windows dialogs only.
