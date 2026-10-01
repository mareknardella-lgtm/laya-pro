"""A local stand-in for the laya-coreml reasoning runtime.

WHAT THIS IS
    A development placeholder that speaks the same JSON contract the real
    Core ML runtime is expected to speak, so MEDIUM and HARD can be exercised
    end to end.

WHAT THIS IS NOT
    It is not a reasoning engine. It forwards the problem to Nemotron 3.5
    Lightning - the same fast model used for generation - and asks it for a
    structured plan. The distinction System 1 / System 2 is therefore only
    simulated while this process is running.

    Every plan it returns carries ``engine: "laya-coreml-stub"``, so the trace
    in the web UI shows the stand-in rather than pretending to be the real
    runtime.

    Fail-closed is preserved: if the model does not return usable JSON, the
    stub answers with a refusal key and the orchestrator raises, rather than
    inventing a plan.

Run it with::

    python tools/laya_coreml_stub.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import httpx  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from backend.app.config import get_settings  # noqa: E402

STUB_ENGINE = "laya-coreml-stub"

app = FastAPI(title="laya-coreml stub", version="1.0.0")
settings = get_settings()

PLANNER_SYSTEM = (
    "You are the planning half of a two-model assistant. Break the problem into "
    "steps a fast writer will turn into an answer. Reply with JSON only, no prose, "
    "no code fences.\n"
    'Schema: {"steps":[{"goal":"","rationale":"","expected_output":""}],'
    '"conclusion":"","confidence":0.0,"assumptions":[],"open_questions":[]}\n'
    "Rules: between 2 and 5 steps, each concrete and verifiable. "
    "Answer in the language of the problem. confidence between 0 and 1."
)

CRITIC_SYSTEM = (
    "You audit a draft against the plan it must satisfy. Reply with JSON only, no "
    "prose, no code fences.\n"
    'Schema: {"verdict":"approved|revisions_required|rejected","issues":[],"summary":""}\n'
    "Rules: use 'approved' when the draft satisfies every plan step; "
    "'revisions_required' when concrete gaps remain and you list each in issues; "
    "'rejected' when the draft is fundamentally wrong. An empty issues list means "
    "approved. Answer in the language of the draft."
)


_http: Optional[httpx.Client] = None


def _client() -> httpx.Client:
    """One persistent client: reusing the connection avoids a TLS handshake
    on every planning call, which matters on a latency-sensitive endpoint."""

    global _http
    if _http is None:
        _http = httpx.Client(timeout=settings.nemotron_timeout_seconds)
    return _http


class ReasonPayload(BaseModel):
    request_id: Optional[str] = None
    problem: str
    depth: str = "standard"
    intent: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)


class CritiquePayload(BaseModel):
    request_id: Optional[str] = None
    draft: str
    plan: Dict[str, Any]
    requirements: List[str] = Field(default_factory=list)


def _ask(system: str, user: str, max_tokens: int = 1200) -> str:
    """One plain generation against Nemotron. Slow on a cold shared endpoint."""
    if not settings.nemotron_configured:
        raise HTTPException(status_code=503, detail="NEMOTRON_API_KEY non configurata")

    body = {
        "model": settings.nemotron_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    url = f"{settings.nemotron_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.nemotron_api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = _client().post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=504, detail=f"Nemotron unreachable: {type(exc).__name__}")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=response.text[:300])
    return response.json()["choices"][0]["message"]["content"] or ""


def _extract_json(text: str) -> Optional[dict]:
    """Tolerate code fences, leading chatter, and truncation.

    Truncation is the common case: a planner that runs out of tokens mid-object
    is far more likely than one that emits invalid JSON. Salvaging the complete
    prefix keeps a partial but useful plan instead of refusing outright.
    """

    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start = cleaned.find("{")
    if start == -1:
        return None
    body = cleaned[start:]

    parsed = _try_json(body)
    if parsed is not None:
        return parsed

    # Cut back to the last point that ends a complete value, then re-close.
    safe = _last_safe_index(body)
    if safe is None:
        return None
    return _parse_fragment(body[:safe + 1].rstrip())


def _parse_fragment(head: str) -> Optional[dict]:
    """Try to close a truncated fragment, peeling off whatever is incomplete.

    Truncation can leave a dangling ',' , ':' or a key with no value. Each pass
    removes one of those, because none can sit in front of a closing brace.
    """

    for _ in range(4):
        parsed = _try_json(head + _closers_for(head))
        if parsed is not None:
            return parsed

        stripped = head.rstrip()
        trimmed = _drop_dangling_key(stripped) if stripped.endswith('"') else stripped
        while trimmed and trimmed[-1] in ",:":
            trimmed = trimmed[:-1].rstrip()

        if trimmed == stripped and not trimmed.endswith('"'):
            return None
        head = trimmed
    return None


def _drop_dangling_key(text: str) -> str:
    """Remove a trailing "key" that never received its value."""

    if not text.endswith('"'):
        return text
    start = text.rfind('"', 0, len(text) - 1)
    if start <= 0:
        return text
    if text[start - 1] not in "{,":
        return text
    head = text[:start].rstrip()
    return head[:-1].rstrip() if head.endswith(",") else head


def _try_json(fragment: str) -> Optional[dict]:
    try:
        parsed = json.loads(fragment)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _last_safe_index(body: str) -> Optional[int]:
    """Index of the last character that ends a complete value outside a string."""

    depth = 0
    in_string = False
    escaped = False
    best: Optional[int] = None

    for index, char in enumerate(body):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
                if depth > 0 and index > 0 and body[index - 1] in ":,{":
                    best = index
            continue

        if char == '"':
            in_string = True
            if depth > 0 and index > 0 and body[index - 1] in ":,{":
                best = index - 1
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth >= 0:
                best = index
        elif char in ":," and depth > 0:
            best = index

    return best


def _closers_for(fragment: str) -> str:
    stack: List[str] = []
    in_string = False
    escaped = False
    for char in fragment:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char == "}":
            # Pop only on a match: a stray closer means the stack is already
            # balanced, and popping blindly would close the wrong bracket.
            if stack and stack[-1] == "}":
                stack.pop()
        elif char == "]":
            if stack and stack[-1] == "]":
                stack.pop()

    return "".join(reversed(stack))


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "engine": STUB_ENGINE,
        "warning": "stand-in, non e' il runtime Core ML reale",
        "nemotron_configured": settings.nemotron_configured,
    }


@app.post("/v1/reason")
async def reason(payload: ReasonPayload) -> Dict[str, Any]:
    depth = payload.depth if payload.depth in ("standard", "deep") else "standard"
    wanted = "3 to 5 steps" if depth == "deep" else "2 steps"

    text = _ask(
        PLANNER_SYSTEM,
        f"Produce a plan with {wanted} for this problem.\n\n{payload.problem}",
        max_tokens=2000,
    )
    plan = _extract_json(text)
    if plan is None or not isinstance(plan.get("steps"), list) or not plan["steps"]:
        # Fail-closed: the orchestrator turns this into LayaRefusal.
        return {"cannot_plan": "the model did not return a usable JSON plan"}

    steps = []
    for raw in plan["steps"][:8]:
        if not isinstance(raw, dict):
            continue
        goal = str(raw.get("goal") or "").strip()
        if not goal:
            continue
        steps.append(
            {
                "goal": goal,
                "rationale": str(raw.get("rationale") or goal).strip(),
                "expected_output": str(raw.get("expected_output") or goal).strip(),
            }
        )
    if not steps:
        return {"cannot_plan": "steps were missing a goal"}

    try:
        confidence = float(plan.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    return {
        "steps": steps,
        "conclusion": str(plan.get("conclusion") or steps[-1]["goal"]).strip(),
        "confidence": max(0.0, min(1.0, confidence)),
        "depth": depth,
        "assumptions": [str(a) for a in (plan.get("assumptions") or [])][:8],
        "open_questions": [str(q) for q in (plan.get("open_questions") or [])][:8],
        "engine": STUB_ENGINE,
        "runtime_configured": True,
    }


@app.post("/v1/critique")
async def critique(payload: CritiquePayload) -> Dict[str, Any]:
    requirements = "\n".join(f"- {item}" for item in payload.requirements)

    text = _ask(
        CRITIC_SYSTEM,
        f"REQUIREMENTS:\n{requirements}\n\nPLAN CONCLUSION: {payload.plan.get('conclusion', '')}\n\n"
        f"DRAFT:\n{payload.draft}",
        max_tokens=800,
    )
    parsed = _extract_json(text)
    if parsed is None:
        return {"verdict": "revisions_required", "issues": [], "summary": "critique non leggibile"}

    verdict = str(parsed.get("verdict") or "revisions_required").strip().lower()
    if verdict not in ("approved", "revisions_required", "rejected"):
        verdict = "revisions_required"

    issues = [
        str(issue).strip()
        for issue in (parsed.get("issues") or [])
        if str(issue).strip()
    ][:8]
    if verdict == "approved":
        issues = []
    elif verdict == "revisions_required" and not issues:
        # The contract forbids this combination; demote rather than invent gaps.
        verdict = "rejected"

    return {
        "verdict": verdict,
        "issues": issues,
        "summary": str(parsed.get("summary") or "critica non disponibile").strip(),
        "engine": STUB_ENGINE,
    }


@asynccontextmanager
async def lifespan(_: FastAPI):
    announce()
    yield


app.router.lifespan_context = lifespan


def announce() -> None:
    print("")
    print("  " + "=" * 64)
    print(f"   laya-coreml STUB in ascolto su {settings.laya_coreml_url}")
    print("   NON e' il runtime Core ML reale: delega a Nemotron 3.5 Lightning.")
    print(f"   Ogni piano sara' firmato '{STUB_ENGINE}'.")
    print("   Modalita' MEDIUM e HARD funzionano, ma il ragionamento e' simulato.")
    print("  " + "=" * 64)
    print("", flush=True)


if __name__ == "__main__":
    import uvicorn
    from urllib.parse import urlsplit

    parts = urlsplit(settings.laya_coreml_url)
    uvicorn.run(
        app,
        host=parts.hostname or "127.0.0.1",
        port=parts.port or 8081,
        log_level="warning",
    )