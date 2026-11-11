"""Server-Timing step collection (crossr_core.timing)."""

import types

from crossr_core import timing


def test_spans_are_collected_and_formatted():
    timing.start()
    with timing.span("a"):
        pass
    with timing.span("a"):
        pass
    spans = timing.collected()
    assert set(spans) == {"a"} and spans["a"] >= 0
    assert timing.header({"a": 12.34, "b": 1.0}, 20.0) == "total;dur=20.0, a;dur=12.3, b;dur=1.0"


def test_instrument_wraps_module_functions_once():
    mod = types.SimpleNamespace(f=lambda x: x + 1)
    timing.instrument(mod, "f", "missing")
    timing.instrument(mod, "f")
    timing.start()
    assert mod.f(1) == 2
    assert "f" in timing.collected()


def test_no_collection_outside_a_request():
    timing._spans.set(None)
    with timing.span("x"):
        pass
    assert timing.collected() == {}
