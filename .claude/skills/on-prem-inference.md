# Skill: On-Prem Inference

Patterns for calling the local LLM (Ollama dev / vLLM prod) for vision and reasoning tasks. No external API calls.

## Client Setup (Same Code for Both Environments)

```python
# agent/llm_client.py
from openai import OpenAI
from config.settings import settings

def get_client() -> OpenAI:
    """Returns OpenAI-compatible client pointing to local inference server."""
    return OpenAI(
        base_url=settings.inference_url,   # http://localhost:11434/v1  OR  http://server:8080/v1
        api_key="ignored",                 # Ollama/vLLM ignore this but require non-empty
        timeout=120.0,                     # CPU inference can take 40s — generous timeout
        max_retries=2,
    )
```

## Perception Call (Screenshot → ScreenState JSON)

```python
import base64
from PIL import Image
import io

def call_perception(client: OpenAI, image: Image.Image, context: dict) -> dict:
    """Send screenshot to local VLM, get structured ScreenState JSON."""
    # Encode image to base64
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    PERCEPTION_PROMPT = """You are a screen analysis agent for insurance claim automation.
Analyze the screenshot and return ONLY valid JSON matching this exact schema:

{{
  "app_type": "browser|desktop|rdp|file_explorer|dialog|unknown",
  "state_summary": "<one sentence describing current screen>",
  "current_url": "<URL if browser, empty otherwise>",
  "visible_elements": [
    {{"label": "<text>", "type": "button|field|table|text|dropdown|checkbox", "testid": "<data-testid if visible>"}}
  ],
  "error_present": false,
  "blocking_modal": false,
  "task_progress": "not_started|in_progress|blocked|complete",
  "blocking_issue": null,
  "confidence": 0.0
}}

Task goal: {task_goal}
Last action: {last_action}
Current step: {step}
"""

    response = client.chat.completions.create(
        model=settings.model_name,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text",
                 "text": PERCEPTION_PROMPT.format(**context)},
            ],
        }],
        max_tokens=1024,
        temperature=0.1,    # low temperature for structured output
    )

    raw = response.choices[0].message.content.strip()
    # Strip markdown fences if model wraps in ```json ... ```
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)
```

## Planning Call (ScreenState → ActionPlan JSON)

```python
PLANNING_PROMPT = """You are an RPA action planner for insurance claim automation.

TASK GOAL: {goal}
COMPLETED STEPS: {completed}
CURRENT SCREEN: {state_summary}
VISIBLE ELEMENTS: {elements}
LAST ACTION RESULT: {last_result}
BLOCKING ISSUE: {blocking_issue}
RETRY COUNT THIS STEP: {retry_count}

Choose the single next action. Return ONLY valid JSON:
{{
  "action_type": "click|type|navigate|read|extract|wait|flag_human|js_eval",
  "target": "<element description or selector>",
  "value": "<text to type, URL, or JS expression>",
  "reason": "<why this action>",
  "confidence": 0.0,
  "fallback": "<alternative selector if primary fails>",
  "is_financial": false,
  "requires_hitl": false
}}

Rules:
- One action only
- Never guess financial values (loan numbers, amounts, check numbers) — use extract or flag_human
- If confidence < 0.75 → set requires_hitl: true
- If same step failed {retry_count} times → action_type must be "flag_human"
- If error_present → action_type must be "click" on error dismiss button
- Prefer data-testid selectors from known locators
"""

def call_planning(client: OpenAI, screen_state: dict, working: dict,
                  goal: TaskGoal) -> dict:
    response = client.chat.completions.create(
        model=settings.model_name,
        messages=[{"role": "user", "content": PLANNING_PROMPT.format(
            goal=goal.description,
            completed=working["decisions_log"][-5:],   # last 5 actions for context
            state_summary=screen_state["state_summary"],
            elements=json.dumps(screen_state["visible_elements"]),
            last_result=working.get("last_result", "none"),
            blocking_issue=screen_state.get("blocking_issue"),
            retry_count=working["retry_counts"].get(str(working["step"]), 0),
        )}],
        max_tokens=512,
        temperature=0.1,
    )
    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)
```

## Extraction Call (Document → Structured Fields)

```python
EXTRACTION_PROMPT = """Extract the following fields from this document image.
Return ONLY valid JSON.

Fields to extract: {fields}

For each field return:
{{
  "field_name": {{
    "value": "<extracted value or null>",
    "confidence": 0.0,
    "location_hint": "<where on page this was found>"
  }}
}}

Rules:
- If a field is not visible: set value to null, confidence to 0.0
- For amounts: include currency symbol and commas as shown (e.g. "$10,640.58")
- For dates: return as shown in document (e.g. "04/17/2026")
- For loan/claim numbers: include all digits exactly as shown
- NEVER invent or guess values — only extract what is clearly visible
"""

def call_extraction(client: OpenAI, image: Image.Image, fields: list[str]) -> dict:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    response = client.chat.completions.create(
        model=settings.model_name,
        messages=[{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text",
             "text": EXTRACTION_PROMPT.format(fields=", ".join(fields))},
        ]}],
        max_tokens=1024,
        temperature=0.0,    # zero temperature for extraction — no creativity
    )
    ...
```

## Retry with Backoff (tenacity)

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
    reraise=True,
)
def safe_llm_call(client: OpenAI, **kwargs):
    return client.chat.completions.create(**kwargs)
```

## Confidence Calibration

Model confidence varies by task type. Calibrate expectations:

| Task | MiniCPM-V (CPU dev) | Qwen2-VL (GPU prod) |
|------|--------------------|--------------------|
| Identify page type | 0.90–0.95 | 0.95–0.99 |
| Find button by description | 0.80–0.90 | 0.90–0.95 |
| Extract text from clean PDF | 0.90–0.95 | 0.92–0.97 |
| Extract amount from scanned PDF | 0.70–0.85 | 0.80–0.92 |
| Identify table row content | 0.75–0.88 | 0.85–0.94 |

If model returns confidence < 0.70 on any field — do not use the value.
If model returns confidence < 0.90 on a financial field — route to HITL always.

## Token Cost Awareness (CPU)

Every VLM call is expensive on CPU (15–40s). Minimise calls:

```python
# Don't call VLM for these — use deterministic code:
# 1. Checking a known URL → page.url comparison
# 2. Waiting for a known element → page.wait_for_selector()
# 3. Reading a known data-testid element → page.text_content()
# 4. Comparing two values → Python ==
# 5. Any action that was just done successfully and cached

# Only call VLM when:
# 1. Cache miss in ChromaDB
# 2. Unexpected screen state
# 3. Novel element that has no known selector
# 4. Document extraction where native text fails
```

## Startup Validation

```python
def validate_inference_server() -> None:
    client = get_client()
    try:
        models = client.models.list()
        available = [m.id for m in models.data]
        if settings.model_name not in available:
            raise RuntimeError(
                f"Model '{settings.model_name}' not loaded.\n"
                f"Available: {available}\n"
                f"Run: ollama pull {settings.model_name}"
            )
        log.info("inference.ready", model=settings.model_name, url=settings.inference_url)
    except Exception as e:
        raise RuntimeError(
            f"Cannot reach inference server at {settings.inference_url}\n"
            f"Development: run 'ollama serve'\n"
            f"Production: check vLLM service on inference server\n"
            f"Error: {e}"
        )
```
