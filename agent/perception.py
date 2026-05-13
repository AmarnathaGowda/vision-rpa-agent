"""Screen capture and VLM-based state understanding."""
from __future__ import annotations
from PIL import Image


class PerceptionLayer:
    def capture(self, target=None) -> Image.Image:
        raise NotImplementedError

    def preprocess(self, image: Image.Image) -> Image.Image:
        raise NotImplementedError

    def understand(self, image: Image.Image, context: dict) -> dict:
        raise NotImplementedError
