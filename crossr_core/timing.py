"""Per-request step timings, returned as a standard Server-Timing header.

    with timing.span("pedigree"):
        ...
    timing.instrument(module, "function_name", ...)   # time every call of module.function_name

The API middleware starts a collection per request and writes, e.g.
``Server-Timing: build_hypo_report;dur=812.4, similar_cross_rows;dur=640.1``.
Browsers show it in the network panel; the nightly checks read it to find slow steps.
Nested spans are reported separately (a parent's time includes its children).
"""

from __future__ import annotations

import contextvars
import functools
import time
from contextlib import contextmanager

_spans: contextvars.ContextVar = contextvars.ContextVar("crossr_spans", default=None)


def start() -> None:
    _spans.set({})


def collected() -> dict:
    return dict(_spans.get() or {})


@contextmanager
def span(name: str):
    spans = _spans.get()
    if spans is None:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        spans[name] = spans.get(name, 0.0) + (time.perf_counter() - t0) * 1000


def header(spans: dict, total_ms: float | None = None) -> str:
    parts = [f"{name};dur={ms:.1f}" for name, ms in sorted(spans.items(), key=lambda kv: -kv[1])[:20]]
    if total_ms is not None:
        parts.insert(0, f"total;dur={total_ms:.1f}")
    return ", ".join(parts)


def instrument(module, *names: str) -> None:
    """Wrap module-level functions so each call is timed (calls through the module's globals too)."""
    for name in names:
        fn = getattr(module, name, None)
        if fn is None or getattr(fn, "_crossr_timed", False):
            continue

        @functools.wraps(fn)
        def wrapper(*args, __fn=fn, __name=name, **kwargs):
            with span(__name):
                return __fn(*args, **kwargs)

        wrapper._crossr_timed = True
        setattr(module, name, wrapper)
