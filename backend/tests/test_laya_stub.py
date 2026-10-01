"""Tests for the laya-coreml stand-in's JSON handling.

The interesting failure is truncation: a planner that runs out of tokens
mid-object is normal, and refusing there would throw away a usable plan.
"""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.laya_coreml_stub import _closers_for, _extract_json  # noqa: E402

VALID = """{"steps":[{"goal":"a","rationale":"b","expected_output":"c"}],
"conclusion":"d","confidence":0.7,"assumptions":[],"open_questions":[]}"""


def test_clean_json_parses():
    parsed = _extract_json(VALID)
    assert parsed is not None
    assert parsed["conclusion"] == "d"


def test_json_wrapped_in_code_fences_parses():
    assert _extract_json("```json\n" + VALID + "\n```") is not None


def test_json_with_leading_prose_parses():
    assert _extract_json("Ecco il piano:\n" + VALID) is not None


def test_truncated_object_is_salvaged():
    """The common failure: the token budget ends mid-object."""

    truncated = VALID[: VALID.index('"conclusion"') + 4]

    parsed = _extract_json(truncated)

    assert parsed is not None
    assert parsed["steps"][0]["goal"] == "a"


def test_truncated_inside_a_string_still_yields_complete_values():
    truncated = (
        '{"steps":[{"goal":"primo passo","rationale":"perche","expected_output":"risultato"},'
        '{"goal":"secondo passo","rationale":" motives'
    )

    parsed = _extract_json(truncated)

    assert parsed is not None
    goals = [step["goal"] for step in parsed["steps"]]
    # The second step's goal survived; only its unfinished rationale was dropped.
    assert goals == ["primo passo", "secondo passo"]
    assert "rationale" not in parsed["steps"][1]


def test_pure_garbage_returns_none():
    assert _extract_json("Non riesco a produrre un piano.") is None
    assert _extract_json("") is None


def test_closers_balance_truncated_fragments():
    # Three open brackets: array, inner object, outer object.
    assert _closers_for('{"a": {"b": [1, 2') == "]}}"
    assert _closers_for('{"a": 1') == "}"
    assert _closers_for("{}") == ""
    # The '}' closes the object, but the array is still open: both are needed.
    assert _closers_for('{"a": [1, 2}') == "]}"


@pytest.mark.parametrize("field", ["steps", "conclusion"])
def test_extracted_plan_has_the_fields_the_contract_needs(field):
    parsed = _extract_json(VALID)
    assert field in parsed


def test_stub_declares_it_is_not_the_real_runtime():
    source = open(
        os.path.join(ROOT, "tools", "laya_coreml_stub.py"), encoding="utf-8"
    ).read()

    assert "laya-coreml-stub" in source
    assert "NON e' il runtime Core ML reale" in source or "not a reasoning engine" in source.lower()


def test_stub_refuses_instead_of_inventing_a_plan():
    """An unusable model reply must produce a refusal key, not an empty plan."""

    source = open(
        os.path.join(ROOT, "tools", "laya_coreml_stub.py"), encoding="utf-8"
    ).read()

    assert "cannot_plan" in source