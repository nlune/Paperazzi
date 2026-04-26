from __future__ import annotations

import threading
from typing import Any


def run_background_job(target, *args: Any) -> None:
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
