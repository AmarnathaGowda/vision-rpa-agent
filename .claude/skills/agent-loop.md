# Skill: Agent Loop

Core patterns for implementing the observe → reason → act → store cycle.

## The Loop Contract

Every iteration must:
1. Capture screen → produce `ScreenState`
2. Check ChromaDB cache before calling VLM
3. Produce exactly one `ActionPlan`
4. Execute the action via the correct executor
5. Write checkpoint to SQLite
6. Evaluate exit condition

```python
# agent/loop.py — canonical structure
class AgentLoop:
    MAX_STEPS = 50          # hard stop — configurable via settings
    RETRY_LIMIT = 3         # same step fails this many times → HITL

    def run(self, task: TaskGoal) -> TaskResult:
        self._init_task(task)
        while self._should_continue():
            screen   = self._observe()
            plan     = self._reason(screen)
            result   = self._act(plan)
            self._store(plan, result, screen)
        return self._finalise()

    def _should_continue(self) -> bool:
        if self.working["step"] >= self.MAX_STEPS:
            self._exit("max_steps_exceeded")
            return False
        if self.working["hitl_pending"]:
            return False   # loop paused — external resume required
        if self.working.get("task_complete"):
            return False
        return True
```

## ScreenState Model

```python
from pydantic import BaseModel

class ScreenState(BaseModel):
    app_type: str           # "browser" | "desktop" | "rdp" | "file_explorer" | "dialog" | "unknown"
    state_summary: str      # one sentence — what is currently shown
    current_url: str = ""   # populated when app_type == "browser"
    visible_elements: list[dict] = []   # [{"label": "...", "type": "button|field|text", "testid": "..."}]
    error_present: bool = False
    blocking_modal: bool = False
    task_progress: str = "in_progress"  # "not_started"|"in_progress"|"blocked"|"complete"
    blocking_issue: str | None = None   # description if blocked
    confidence: float = 0.0
```

## ActionPlan Model

```python
class ActionPlan(BaseModel):
    action_type: str        # "click"|"type"|"navigate"|"read"|"extract"|"wait"|"flag_human"|"js_eval"
    target: str             # element description or selector
    value: str = ""         # text to type, URL, JS to eval
    reason: str             # why this action (for audit log)
    confidence: float       # 0.0–1.0
    fallback: str = ""      # alternative selector if primary fails
    is_financial: bool = False      # triggers confidence ≥ 0.90 check
    requires_hitl: bool = False     # human approval before execution
    cache_hit: bool = False         # True if from ChromaDB (no VLM call)
```

## Observe Step — Cache-First Pattern

```python
def _observe(self) -> ScreenState:
    img = self.perception.capture(target=self._current_target())
    img = self.perception.preprocess(img)

    # Try ChromaDB cache before calling VLM
    ctx_key = f"{self.working['task_type']}_{self.working['step']}"
    cached = self.knowledge.query_screen_state(ctx_key)
    if cached and cached.confidence >= 0.85:
        log.info("perception.cache_hit", step=self.working["step"])
        return cached

    # VLM call — 15–40s on CPU, 1–3s on GPU
    state = self.perception.understand(img, context={
        "task_goal": self.working["goal"],
        "last_action": self.working["last_action"],
        "step": self.working["step"],
    })
    return state
```

## Reason Step — Priority Order

```python
def _reason(self, screen: ScreenState) -> ActionPlan:
    # Priority 1: error on screen — always handle first
    if screen.error_present or screen.blocking_modal:
        return self.recovery.plan_for_error(screen)

    # Priority 2: ChromaDB cached action plan for this step
    plan = self.knowledge.query_action_plan(
        task_type=self.working["task_type"],
        step=self.working["step"],
        screen_summary=screen.state_summary,
    )
    if plan and plan.confidence >= 0.85:
        plan.cache_hit = True
        return plan

    # Priority 3: deterministic rule from task YAML
    rule = self.task_goal.get_step_rule(self.working["step"])
    if rule:
        return ActionPlan(**rule, cache_hit=True)

    # Priority 4: LLM planning (most expensive — last resort)
    return self.planner.decide(screen, self.working, self.task_goal)
```

## Store Step — Always Checkpoint

```python
def _store(self, plan: ActionPlan, result: ActionResult, screen: ScreenState) -> None:
    step = self.working["step"]

    # 1. Update working memory
    self.working["last_action"] = plan.model_dump()
    self.working["last_result"] = result.model_dump()
    self.working["step"] += 1
    if result.extracted_value:
        self.working["extracted_values"][plan.target] = result.extracted_value

    # 2. SQLite checkpoint — survives crash
    self.session.write_checkpoint(
        task_id=self.working["task_id"],
        step=step,
        working_json=self.working,
    )

    # 3. SQLite action log
    self.session.log_action(
        task_id=self.working["task_id"],
        step=step,
        plan=plan,
        result=result,
        screenshot=result.screenshot_path,
    )

    # 4. Append-only audit NDJSON
    self.audit.append({
        "ts": utcnow(),
        "agent_id": settings.agent_id,
        "task_id": self.working["task_id"],
        "step": step,
        "action_type": plan.action_type,
        "target": plan.target,
        "value": plan.value,
        "result": result.status,
        "confidence": plan.confidence,
        "cache_hit": plan.cache_hit,
        "duration_ms": result.duration_ms,
    })

    # 5. Post-task: write successful patterns to ChromaDB (not mid-task)
    # This is handled in _finalise() after task_complete
```

## Exit Conditions

```python
def _finalise(self) -> TaskResult:
    reason = self.working.get("exit_reason", "unknown")
    status = "success" if reason == "task_complete" else "failed"

    # Write patterns to ChromaDB only on success
    if status == "success":
        self.knowledge.store_successful_patterns(
            task_type=self.working["task_type"],
            actions=self.session.get_actions(self.working["task_id"]),
        )

    self.session.complete_task(self.working["task_id"], status, self.working)
    return TaskResult(status=status, task_id=self.working["task_id"], ...)
```

## Startup Health Check (Required)

```python
def preflight_checks():
    # 1. Inference server reachable
    try:
        client = OpenAI(base_url=settings.inference_url, api_key="ignored")
        client.models.list()
    except Exception as e:
        raise RuntimeError(
            f"Inference server not reachable at {settings.inference_url}\n"
            f"Start with: ollama serve\n"
            f"Error: {e}"
        )

    # 2. SQLite writable
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # 3. Screenshot dir exists
    Path(settings.screenshot_dir).mkdir(parents=True, exist_ok=True)

    # 4. Check for unfinished tasks (crash recovery)
    unfinished = session.get_running_tasks(agent_id=settings.agent_id)
    if unfinished:
        log.warning("crash_recovery.found", task_id=unfinished[0]["task_id"])
        return unfinished[0]   # caller may resume this task
    return None
```

## Retry and Loop Guard

```python
def _track_retry(self, step_key: str) -> bool:
    """Returns True if retry limit exceeded → should flag_for_human."""
    count = self.working["retry_counts"].get(step_key, 0) + 1
    self.working["retry_counts"][step_key] = count
    if count >= self.RETRY_LIMIT:
        log.warning("retry_limit_exceeded", step=step_key, count=count)
        return True
    return False
```
