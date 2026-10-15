"""Small worker pool for slow side effects (emails, quiz generation).

Keeps payment confirmation and class assignment instant: the database work
commits first, then these tasks run without blocking the request.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bg-task")


def run_in_background(task, *args, **kwargs) -> None:
    """Run `task` on the worker pool. Tasks must open their own DB session."""
    def _guarded():
        try:
            task(*args, **kwargs)
        except Exception:
            logger.exception("Background task %s failed.", getattr(task, "__name__", task))

    if os.getenv("BACKGROUND_TASKS_INLINE") == "1":
        _guarded()
        return
    _executor.submit(_guarded)
