"""Screen capture and VLM-based state understanding.

Pipeline: mss → Pillow preprocess → local VLM (OpenAI-compatible client) →
validated ScreenState. No business logic lives here.
"""
from __future__ import annotations

import base64
import io
import json
from typing import Any

from PIL import Image
from pydantic import ValidationError

from agent.llm_client import get_client, strip_json_fence
from agent.schemas import ScreenState
from config.logging_config import get_logger
from config.settings import settings

log = get_logger(__name__)

PERCEPTION_PROMPT = """You are a screen analysis agent for insurance claim automation.
Analyze the screenshot and return ONLY valid JSON. Do NOT copy the placeholder
text — pick exactly ONE value from each list of options.

Allowed values:
- app_type: one of "browser", "desktop", "rdp", "file_explorer", "dialog", "unknown"
- element type: one of "button", "field", "table", "text", "dropdown", "checkbox"
- task_progress: one of "not_started", "in_progress", "blocked", "complete"

Example of a correctly filled response (DO NOT copy verbatim — replace with what you actually see):
{{
  "app_type": "browser",
  "state_summary": "Login page of the RD Web Access portal.",
  "current_url": "https://example.com/rdweb",
  "visible_elements": [
    {{"label": "Username", "type": "field", "testid": "user-input"}},
    {{"label": "Sign in", "type": "button", "testid": "submit-btn"}}
  ],
  "error_present": false,
  "blocking_modal": false,
  "task_progress": "in_progress",
  "blocking_issue": null,
  "confidence": 0.92
}}

Now return ONLY a JSON object with the SAME keys for the screenshot above.

Task goal: {task_goal}
Last action: {last_action}
Current step: {step}
"""

# Downscale very large screens — VLM cost scales with pixels.
MAX_DIMENSION = 1600


class PerceptionLayer:
    """mss capture + Pillow preprocess + local VLM call."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client  # injectable for tests; resolves lazily

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = get_client()
        return self._client

    def capture(self, target: dict | None = None) -> Image.Image:
        """Grab the primary monitor or a specific bbox.

        target schema (all optional): {"left": int, "top": int, "width": int, "height": int}.
        Defaults to the primary monitor.
        """
        import mss  # local import — mss isn't available on every CI image

        with mss.mss() as sct:
            monitor = target if target else sct.monitors[1]
            raw = sct.grab(monitor)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        return img

    def preprocess(self, image: Image.Image) -> Image.Image:
        """RGB normalise + downscale to keep VLM token cost predictable."""
        if image.mode != "RGB":
            image = image.convert("RGB")
        w, h = image.size
        m = max(w, h)
        if m > MAX_DIMENSION:
            scale = MAX_DIMENSION / m
            image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        return image

    def understand(self, image: Image.Image, context: dict) -> ScreenState:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        prompt = PERCEPTION_PROMPT.format(
            task_goal=context.get("task_goal", ""),
            last_action=context.get("last_action", "none"),
            step=context.get("step", 0),
        )

        response = self.client.chat.completions.create(
            model=settings.model_name,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
            max_tokens=1024,
            temperature=0.1,
        )

        raw = strip_json_fence(response.choices[0].message.content or "")
        return self._parse_screen_state(raw)

    def _parse_screen_state(self, raw: str) -> ScreenState:
        """Parse VLM output into a ScreenState — degrade gracefully on bad output.

        Weak models occasionally echo the schema or emit invalid JSON. Rather
        than crash the loop, return a low-confidence "unknown" state so the
        planner can route the step to HITL.
        """
        try:
            data = json.loads(raw)
            data = self._coerce_invalid_literals(data)
            return ScreenState(**data)
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            log.warning("perception_parse_failed", error=str(e), raw=raw[:300])
            return ScreenState(
                app_type="unknown",
                state_summary="VLM output failed validation — see audit log.",
                confidence=0.0,
                blocking_issue=f"perception_parse_failed: {type(e).__name__}",
            )

    @staticmethod
    def _coerce_invalid_literals(data: dict) -> dict:
        """Fix common VLM mistakes before strict Pydantic validation."""
        allowed_app = {"browser", "desktop", "rdp", "file_explorer", "dialog", "unknown"}
        allowed_progress = {"not_started", "in_progress", "blocked", "complete"}

        if data.get("app_type") not in allowed_app:
            data["app_type"] = "unknown"
        if data.get("task_progress") not in allowed_progress:
            data["task_progress"] = "in_progress"

        # Some models return floats outside [0,1] or strings.
        conf = data.get("confidence", 0.0)
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = 0.0
        data["confidence"] = max(0.0, min(1.0, conf))
        return data
