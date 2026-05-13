"""Unit tests for PerceptionLayer — preprocess deterministic, understand via mock VLM."""
from __future__ import annotations

from PIL import Image

from agent.perception import MAX_DIMENSION, PerceptionLayer
from agent.schemas import ScreenState
from tests.fixtures.mock_llm import MockOpenAIClient, make_screen_state


def test_preprocess_converts_to_rgb():
    img = Image.new("RGBA", (200, 200), (255, 0, 0, 128))
    p = PerceptionLayer(client=MockOpenAIClient())
    out = p.preprocess(img)
    assert out.mode == "RGB"


def test_preprocess_downscales_large_image():
    img = Image.new("RGB", (4000, 2000))
    p = PerceptionLayer(client=MockOpenAIClient())
    out = p.preprocess(img)
    assert max(out.size) == MAX_DIMENSION


def test_preprocess_passes_through_small_image():
    img = Image.new("RGB", (800, 600))
    p = PerceptionLayer(client=MockOpenAIClient())
    out = p.preprocess(img)
    assert out.size == (800, 600)


def test_understand_degrades_when_vlm_echoes_schema():
    """Weak VLMs sometimes echo the pipe-list verbatim; loop must not crash."""
    bad_payload = {
        # placeholder echo — exactly what minicpm-v returned in the wild
        "app_type": "browser|desktop|rdp|file_explorer|dialog|unknown",
        "state_summary": "echoed schema",
        "task_progress": "in_progress|blocked",
        "confidence": "0.5",
    }
    client = MockOpenAIClient(responses=[bad_payload])
    p = PerceptionLayer(client=client)
    state = p.understand(Image.new("RGB", (100, 100)),
                         context={"task_goal": "x", "step": 0})
    assert isinstance(state, ScreenState)
    assert state.app_type == "unknown"
    assert state.task_progress == "in_progress"
    assert state.confidence == 0.5


def test_understand_returns_validated_screen_state():
    payload = make_screen_state(app_type="browser", summary="login page", confidence=0.91)
    client = MockOpenAIClient(responses=[payload])
    p = PerceptionLayer(client=client)

    img = Image.new("RGB", (100, 100))
    state = p.understand(img, context={"task_goal": "login", "step": 0})

    assert isinstance(state, ScreenState)
    assert state.app_type == "browser"
    assert state.state_summary == "login page"
    assert state.confidence == 0.91
