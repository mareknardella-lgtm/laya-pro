"""Tests for the hybrid orchestration pipeline."""

from __future__ import annotations

import pytest

from backend.app.chat.models import ChatRequest, Tier
from backend.app.systems.laya.exceptions import LayaContractError, LayaUnavailable
from backend.app.systems.laya.models import CritiqueVerdict, ReasoningDepth
from backend.app.systems.nemotron.exceptions import NemotronUnavailable
from backend.tests.conftest import run


def test_low_tier_answers_with_one_fast_call(orchestrator, nemotron, laya):
    nemotron.queue("Risposta diretta.")

    response = run(orchestrator.process_message(ChatRequest(message="Ciao", tier=Tier.LOW)))

    assert response.text == "Risposta diretta."
    assert response.tier is Tier.LOW
    assert len(nemotron.calls_of("direct")) == 1
    assert laya.reason_calls == []  # no reasoning pass was paid for
    assert response.confidence is None


def test_medium_tier_plans_then_renders(orchestrator, nemotron, laya):
    nemotron.queue("Risposta costruita sul piano.")

    response = run(orchestrator.process_message(ChatRequest(message="Progetta X", tier=Tier.MEDIUM)))

    assert laya.reason_calls[0].depth is ReasoningDepth.STANDARD
    assert len(nemotron.calls_of("answer")) == 1
    plan_prompt = nemotron.calls_of("answer")[0].prompt
    assert "PIANO DI RAGIONAMENTO" in plan_prompt
    assert "Identificare la richiesta" in plan_prompt
    assert response.confidence == pytest.approx(0.82)
    assert response.engines_used == ["laya-coreml", "nemotron-3.5-lightning-30b-a3b"]


def test_trace_names_the_engine_that_actually_answered(orchestrator, nemotron, laya):
    """A stand-in runtime must be visible in the trace, never disguised."""

    laya.queue_plan(
        {
            "steps": [{"goal": "un passo", "rationale": "motivo", "expected_output": "esito"}],
            "conclusion": "conclusione",
            "confidence": 0.9,
            "depth": "standard",
            "engine": "laya-coreml-stub",
        }
    )
    nemotron.queue("Risposta.")

    response = run(orchestrator.process_message(ChatRequest(message="Prova", tier=Tier.MEDIUM)))

    assert response.engines_used[0] == "laya-coreml-stub"
    reason_step = next(s for s in response.trace if s.stage == "laya.reason")
    assert reason_step.engine == "laya-coreml-stub"


def test_hard_tier_requests_deep_plan(orchestrator, nemotron, laya):
    nemotron.queue("Bozza.")

    run(orchestrator.process_message(ChatRequest(message="Problema complesso", tier=Tier.HARD)))

    assert laya.reason_calls[0].depth is ReasoningDepth.DEEP
    assert len(laya.reason_calls[0].problem) > 0


def test_hard_tier_refines_until_the_critic_approves(orchestrator, nemotron, laya):
    nemotron.queue("Bozza incompleta.", "Bozza corretta.")
    laya.queue_critique(
        {
            "verdict": "revisions_required",
            "issues": ["Manca il vincolo di sicurezza"],
            "summary": "La bozza ignora il vincolo.",
        },
        {"verdict": "approved", "summary": "Ora copre tutto il piano."},
    )

    response = run(orchestrator.process_message(ChatRequest(message="Audit", tier=Tier.HARD)))

    assert response.refinements == 1
    assert response.text == "Bozza corretta."
    assert response.status == "success"
    answers = nemotron.calls_of("answer")
    assert len(answers) == 2
    assert "Manca il vincolo di sicurezza" in answers[1].prompt
    assert len(laya.critique_calls) == 2


def test_hard_tier_refinement_budget_is_bounded(orchestrator, nemotron, laya):
    nemotron.queue("Bozza 1.", "Bozza 2.", "Bozza 3.")
    laya.queue_critique(
        {"verdict": "revisions_required", "issues": ["gap 1"], "summary": "gap"},
        {"verdict": "revisions_required", "issues": ["gap 2"], "summary": "gap"},
        {"verdict": "revisions_required", "issues": ["gap 3"], "summary": "gap"},
    )

    response = run(orchestrator.process_message(ChatRequest(message="Audit", tier=Tier.HARD)))

    assert response.refinements == 2  # bounded by settings.laya_max_refinements
    assert len(nemotron.calls_of("answer")) == 3
    assert response.status == "degraded"


def test_reasoning_failure_is_fail_closed(orchestrator, nemotron, laya):
    """A broken reasoning tier must not be hidden behind a fast-tier answer."""

    laya.reason_error = LayaUnavailable("laya-coreml non raggiungibile")

    with pytest.raises(LayaUnavailable):
        run(orchestrator.process_message(ChatRequest(message="Chiedi", tier=Tier.MEDIUM)))

    assert nemotron.calls == []


def test_contract_violation_surfaces_as_contract_error(orchestrator, laya):
    laya.queue_plan({"steps": [], "conclusion": "x", "confidence": 0.5})

    with pytest.raises(LayaContractError):
        run(orchestrator.process_message(ChatRequest(message="Chiedi", tier=Tier.MEDIUM)))


def test_fast_tier_failure_propagates(orchestrator, nemotron):
    nemotron.error = NemotronUnavailable("API key rifiutata")

    with pytest.raises(NemotronUnavailable):
        run(orchestrator.process_message(ChatRequest(message="Ciao", tier=Tier.LOW)))


def test_trace_records_every_hop(orchestrator, nemotron):
    nemotron.queue("Ciao!")

    response = run(orchestrator.process_message(ChatRequest(message="Ciao", tier=Tier.LOW)))

    stages = [step.stage for step in response.trace]
    assert stages == ["nemotron.generate"]
    assert response.trace[0].engine == "nemotron-3.5-lightning-30b-a3b"
    assert response.trace[0].latency_ms >= 0


def test_critique_falls_back_to_the_fast_tier(orchestrator, nemotron, laya):
    """If the structured audit is unreachable, the fast critic still guards quality."""

    nemotron.queue("Bozza.", "GAP: manca il vincolo di sicurezza", "Bozza corretta.")
    laya.critique_error = LayaUnavailable("endpoint critique assente")

    response = run(orchestrator.process_message(ChatRequest(message="Audit", tier=Tier.HARD)))

    stages = [step.stage for step in response.trace]
    assert "nemotron.critique" in stages
    assert response.refinements == 1  # the "GAP: ..." line drove one revision


def test_rejected_draft_is_returned_flagged(orchestrator, nemotron, laya):
    nemotron.queue("Bozza sbagliata.")
    laya.queue_critique(
        {"verdict": "rejected", "summary": "La risposta non è verificabile."}
    )

    response = run(orchestrator.process_message(ChatRequest(message="Audit", tier=Tier.HARD)))

    assert response.status == "degraded"
    assert response.text == "Bozza sbagliata."
    assert response.refinements == 0


def test_trace_is_persisted_for_audit(orchestrator, nemotron):
    nemotron.queue("Risposta archiviata.")

    run(orchestrator.process_message(ChatRequest(message="Chiedi", session_id="s1", tier=Tier.LOW)))

    traces = run(orchestrator._history.get_traces("s1"))
    assert len(traces) == 1
    assert traces[0]["tier"] == "LOW"
    assert traces[0]["payload"]["trace"][0]["stage"] == "nemotron.generate"


def test_unknown_tier_is_rejected_by_the_contract():
    with pytest.raises(ValueError):
        ChatRequest(message="ciao", tier="TURBO")