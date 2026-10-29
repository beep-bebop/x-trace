"""Shared pytest fixtures.

Each test gets an isolated state dir + DB so tests don't collide.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest


@pytest.fixture(autouse=True)
def isolated_state_dir(monkeypatch, tmp_path: Path) -> Iterator[Path]:
    """Redirect X_TRACE_STATE_DIR to a per-test temp dir and reset the
    cached config so each test starts fresh."""
    state = tmp_path / ".x-trace"
    state.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("X_TRACE_STATE_DIR", str(state))

    # Reset the cached config in x_trace.config so it picks up the new env
    import x_trace.config as cfg

    cfg._cached_config = None

    # Also reset the cached DB engine in x_trace.storage.repository so the
    # next request creates a fresh SQLite file in the new state dir.
    import x_trace.storage.repository as repo

    repo._engine = None

    yield state
