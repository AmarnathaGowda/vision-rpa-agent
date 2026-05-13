"""RDP session management — launch, keep-alive, reconnect."""
from __future__ import annotations
from pathlib import Path


class RDPHandler:
    def launch(self, rdp_file: Path) -> dict:
        raise NotImplementedError

    def wait_for_connection(self, timeout: int = 30) -> bool:
        raise NotImplementedError

    def find_remoteapp_window(self, app_name: str):
        raise NotImplementedError

    def start_keep_alive(self):
        raise NotImplementedError

    def detect_disconnect(self) -> bool:
        raise NotImplementedError

    def reconnect(self, session: dict, max_attempts: int = 3) -> bool:
        raise NotImplementedError
