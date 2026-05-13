"""Unexpected state detection and recovery. Must be implemented before Phase 1."""
from __future__ import annotations


class RecoveryHandler:
    def detect(self, screen: dict, working: dict):
        raise NotImplementedError

    def recover(self, action, screen: dict, working: dict, page=None) -> bool:
        raise NotImplementedError
