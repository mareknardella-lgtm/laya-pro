"""Hybrid orchestrator: merges laya-coreml depth with Nemotron speed.

Pipeline per tier:

    LOW     user -> Nemotron ----------------------------------> answer
    MEDIUM  user -> laya-coreml plan -> Nemotron --------------> answer
    HARD    user -> laya-coreml plan -> Nemotron -> laya-coreml critique
                   -> Nemotron revision -> ... (bounded) ------> answer

Design rules the rest of the code depends on:

* Reasoning is fail-closed. If laya-coreml cannot produce a plan, the request
  fails; it is never quietly answered by the fast tier wearing a plan's clothes.
* Every hop is recorded in ``ChatResponse.trace`` so the cost of a merged answer
  is visible instead of implied.
* The refinement loop is bounded by ``LAYA_MAX_REFINEMENTS`` and always returns
  the best draft produced so far, even if the budget runs out mid-audit.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator, List, Optional, Sequence

from ..config import Settings
from ..memory.manager import MemoryManager
from ..memory.models import MemoryScope
from ..systems.laya.adapter import LayaAdapter
from ..systems.laya.exceptions import LayaContractError, LayaUnavailable
from ..systems.laya.models import (
    Critique,
    CritiqueRequest,
    CritiqueVerdict,
    ReasoningDepth,
    ReasoningPlan,
    ReasoningRequest,
)
from ..systems.nemotron.adapter import NemotronAdapter
from ..systems.nemotron.models import RenderRole
from .history import ChatHistoryManager
from .models import ChatRequest, ChatResponse, Tier, TraceStep

logger = logging.getLogger("laya.chat.orchestrator")

_HISTORY_TURNS = 10
_APPROVED_MARKER = "APPROVED"


class HybridOrchestrator:
    def __init__(
        self,
        settings: Settings,
        nemotron: NemotronAdapter,
        laya: LayaAdapter,
        history: Optional[ChatHistoryManager] = None,
        memory: Optional[MemoryManager] = None,
    ) -> None:
        self._settings = settings
        self._nemotron = nemotron
        self._laya = laya
        self._history = history
        self._memory = memory

    # ------------------------------------------------------------------ public

    async def process_message(self, request: ChatRequest) -> ChatResponse:
        trace: List[TraceStep] = []
        logger.info(
            "chat request tier=%s session=%s chars=%d",
            request.tier.value,
            request.session_id,
            len(request.message),
        )

        if self._history:
            await self._history.add_message(
                request.session_id, "user", request.message, request.tier.value
            )

        history = await self._recent_history(request.session_id)
        memory_block, memory_hits = await self._memory_context(request, history)
        prompt_bundle = _PromptBundle(
            problem=request.message,
            memory=memory_block,
            history=_render_history(history),
        )

        if request.tier is Tier.LOW:
            outcome = await self._run_low(prompt_bundle, trace)
        elif request.tier is Tier.MEDIUM:
            outcome = await self._run_medium(prompt_bundle, trace)
        else:
            outcome = await self._run_hard(prompt_bundle, trace)

        if self._history:
            await self._history.add_message(
                request.session_id,
                "assistant",
                outcome["text"],
                request.tier.value,
                {"engines": outcome["engines"], "refinements": outcome["refinements"]},
            )

        remembered = await self._maybe_remember(request, outcome["text"])

        response = ChatResponse(
            text=outcome["text"],
            session_id=request.session_id,
            tier=request.tier,
            status=outcome["status"],
            engines_used=outcome["engines"],
            trace=trace,
            confidence=outcome["confidence"],
            refinements=outcome["refinements"],
            metadata={
                "memory_hits": memory_hits,
                "memory_written": remembered,
                "model": self._nemotron.model,
            },
        )

        if self._history:
            await self._history.add_trace(
                request.session_id, request.tier.value, response.model_dump(mode="json")
            )
        return response

    # ------------------------------------------------------------------- tiers

    async def _run_low(self, bundle: "_PromptBundle", trace: List[TraceStep]) -> dict:
        """One fast call. No reasoning pass, so nothing to audit afterwards."""

        with _step(trace, "nemotron.generate", self._nemotron.engine_label) as step:
            text = await self._nemotron.render(
                prompt=bundle.answer_prompt(None), role=RenderRole.DIRECT
            )
            step.detail = "direct answer, no reasoning pass"

        return {
            "text": text,
            "engines": [self._nemotron.model],
            "status": "success",
            "confidence": None,
            "refinements": 0,
        }

    async def _run_medium(self, bundle: "_PromptBundle", trace: List[TraceStep]) -> dict:
        plan = await self._plan(bundle, ReasoningDepth.STANDARD, trace)

        with _step(trace, "nemotron.generate", self._nemotron.engine_label) as step:
            text = await self._nemotron.render(
                prompt=bundle.answer_prompt(plan), role=RenderRole.ANSWER
            )
            step.detail = "answer rendered from plan"

        return {
            "text": text,
            "engines": [plan.engine, self._nemotron.engine_label],
            "status": "success",
            "confidence": plan.confidence,
            "refinements": 0,
        }

    async def _run_hard(self, bundle: "_PromptBundle", trace: List[TraceStep]) -> dict:
        plan = await self._plan(bundle, ReasoningDepth.DEEP, trace)

        with _step(trace, "nemotron.generate", self._nemotron.engine_label) as step:
            draft = await self._nemotron.render(
                prompt=bundle.answer_prompt(plan), role=RenderRole.ANSWER
            )
            step.detail = "first draft"

        best = draft
        issues: List[str] = []
        refinements = 0
        status = "success"
        budget = self._settings.laya_max_refinements

        for attempt in range(budget + 1):
            verdict, critique = await self._judge(best, plan, trace)
            if verdict is CritiqueVerdict.APPROVED:
                break
            if verdict is CritiqueVerdict.REJECTED:
                # The critic rejected the draft outright; refining it blindly
                # would be guesswork, so the draft is returned flagged instead.
                status = "degraded"
                break
            if attempt >= budget:
                # Out of budget with gaps still open: best effort, flagged.
                status = "degraded"
                break

            issues = list(critique.issues)
            with _step(trace, "nemotron.refine", self._nemotron.engine_label) as step:
                best = await self._nemotron.render(
                    prompt=bundle.revision_prompt(plan, best, issues),
                    role=RenderRole.ANSWER,
                )
                step.detail = f"revision {refinements + 1} after critique"
            refinements += 1

        return {
            "text": best,
            "engines": [plan.engine, self._nemotron.engine_label],
            "status": status,
            "confidence": plan.confidence,
            "refinements": refinements,
        }

    # ----------------------------------------------------------------- helpers

    async def _plan(
        self, bundle: "_PromptBundle", depth: ReasoningDepth, trace: List[TraceStep]
    ) -> ReasoningPlan:
        request = ReasoningRequest(
            problem=bundle.reasoning_problem(),
            depth=depth,
            context={"memory_entries": bundle.memory},
        )
        with _step(trace, "laya.reason", self._laya.engine_label) as step:
            plan = await self._laya.reason(request)
            # Report who actually answered, not who was configured: a stand-in
            # runtime must never be indistinguishable from the real one.
            step.engine = plan.engine
            step.detail = f"{len(plan.steps)} steps, confidence {plan.confidence:.2f}"
        return plan

    async def _judge(
        self, draft: str, plan: ReasoningPlan, trace: List[TraceStep]
    ) -> tuple[CritiqueVerdict, Critique]:
        """Audit the draft. Falls back to the fast tier if the audit is unreachable.

        The fallback is recorded explicitly in the trace: a weaker judge is
        useful, a misrepresented one is not.
        """

        request = CritiqueRequest(draft=draft, plan=plan, requirements=_requirements(plan))
        with _step(trace, "laya.critique", self._laya.engine_label) as step:
            try:
                critique = await self._laya.critique(request)
                step.detail = critique.verdict.value
                return critique.verdict, critique
            except (LayaUnavailable, LayaContractError) as exc:
                logger.warning("structured critique unavailable (%s); using fast fallback", exc)

        with _step(trace, "nemotron.critique", self._nemotron.engine_label) as step:
            text = await self._nemotron.render(
                prompt=_critique_prompt(draft, plan), role=RenderRole.CRITIQUE
            )
            verdict, issues = _parse_text_critique(text)
            step.detail = f"fallback verdict {verdict.value}"
            return verdict, Critique(
                request_id=request.request_id,
                verdict=verdict,
                issues=issues,
                summary="Produced by the fast fallback critic.",
                engine="nemotron-fallback",
            )

    async def _recent_history(self, session_id: str) -> List[dict]:
        if not self._history:
            return []
        return await self._history.get_messages(session_id, limit=_HISTORY_TURNS)

    async def _memory_context(self, request: ChatRequest, history: List[dict]) -> tuple:
        if not self._memory or not request.auto_memory:
            return "", 0
        try:
            return await self._memory.retrieve(request.message, history)
        except Exception as exc:  # memory is an enhancement, never a blocker
            logger.warning("memory retrieval failed: %s", exc)
            return "", 0

    async def _maybe_remember(self, request: ChatRequest, answer: str) -> int:
        if not self._memory or not request.auto_memory:
            return 0
        try:
            entries = await self._memory.extract(
                [
                    {"role": "user", "content": request.message},
                    {"role": "assistant", "content": answer},
                ]
            )
            if not entries:
                return 0
            for entry in entries:
                entry.scope = MemoryScope.PERSISTENT
            return await self._memory.remember(entries)
        except Exception as exc:
            logger.warning("memory extraction failed: %s", exc)
            return 0


# ------------------------------------------------------------------- prompting


class _PromptBundle:
    """Builds the four prompt shapes the pipeline needs from one context set."""

    def __init__(self, problem: str, memory: str, history: str) -> None:
        self.problem = problem
        self.memory = memory
        self.history = history

    def _context_sections(self) -> str:
        sections = []
        if self.memory:
            sections.append(f"CONTESTO PERSONALE (memoria salvata):\n{self.memory}")
        if self.history:
            sections.append(f"CRONOLOGIA RECENTE:\n{self.history}")
        return "\n\n".join(sections)

    def reasoning_problem(self) -> str:
        context = self._context_sections()
        if not context:
            return self.problem
        return f"{context}\n\nPROBLEMA:\n{self.problem}"

    def answer_prompt(self, plan: Optional[ReasoningPlan]) -> str:
        blocks = []
        if plan is not None:
            blocks.append(f"PIANO DI RAGIONAMENTO ({plan.engine}):\n{_render_plan(plan)}")
        context = self._context_sections()
        if context:
            blocks.append(context)
        blocks.append(f"PROBLEMA UTENTE:\n{self.problem}")
        return "\n\n".join(blocks)

    def revision_prompt(self, plan: ReasoningPlan, draft: str, issues: Sequence[str]) -> str:
        blocks = [
            f"PIANO DI RAGIONAMENTO ({plan.engine}):\n{_render_plan(plan)}",
            f"BOZZA DA CORREGGERE:\n{draft}",
            "PROBLEMI RILEVATI:\n" + "\n".join(f"- {issue}" for issue in issues),
        ]
        context = self._context_sections()
        if context:
            blocks.append(context)
        blocks.append(f"PROBLEMA UTENTE:\n{self.problem}")
        blocks.append(
            "Riscrivi la risposta correggendo ogni problema elencato. "
            "Non aggiungere sezioni che il piano non prevede."
        )
        return "\n\n".join(blocks)


def _render_plan(plan: ReasoningPlan) -> str:
    lines = []
    for index, step in enumerate(plan.steps, start=1):
        lines.append(
            f"{index}. OBIETTIVO: {step.goal}\n"
            f"   MOTIVO: {step.rationale}\n"
            f"   RISULTATO ATTESO: {step.expected_output}"
        )
    if plan.assumptions:
        lines.append("IPOTESI: " + "; ".join(plan.assumptions))
    if plan.open_questions:
        lines.append("DOMANDE APERTE: " + "; ".join(plan.open_questions))
    lines.append(f"CONCLUSIONE: {plan.conclusion}")
    return "\n".join(lines)


def _requirements(plan: ReasoningPlan) -> List[str]:
    requirements = [f"Copri questi obiettivi: {step.goal}" for step in plan.steps]
    requirements.append(f"Rispetta questa conclusione: {plan.conclusion}")
    return requirements


def _critique_prompt(draft: str, plan: ReasoningPlan) -> str:
    requirements = "\n".join(f"- {item}" for item in _requirements(plan))
    return (
        f"REQUISITI:\n{requirements}\n\n"
        f"BOZZA:\n{draft}\n\n"
        "Elenca i problemi concreti, uno per riga, preceduti da 'GAP: '. "
        f"Se non ci sono problemi scrivi esattamente {_APPROVED_MARKER}."
    )


def _parse_text_critique(text: str) -> tuple[CritiqueVerdict, List[str]]:
    """Interpret the fast critic's plain-text verdict."""

    stripped = text.strip()
    if stripped.upper().startswith(_APPROVED_MARKER):
        return CritiqueVerdict.APPROVED, []
    issues = [
        line.split(":", 1)[1].strip()
        for line in stripped.splitlines()
        if line.strip().upper().startswith("GAP:")
    ]
    issues = [issue for issue in issues if issue]
    if not issues:
        return CritiqueVerdict.REJECTED, []
    return CritiqueVerdict.REVISIONS_REQUIRED, issues


def _render_history(history: Sequence[dict]) -> str:
    return "\n".join(f"{turn['role']}: {turn['content']}" for turn in history)


@contextmanager
def _step(
    trace: List[TraceStep], stage: str, engine: str
) -> Iterator[TraceStep]:
    """Record one hop: engine, latency and outcome, success or failure."""

    step = TraceStep(stage=stage, engine=engine)
    started = time.monotonic()
    try:
        yield step
    except Exception:
        step.detail = step.detail or "failed"
        raise
    finally:
        step.latency_ms = int((time.monotonic() - started) * 1000)
        trace.append(step)