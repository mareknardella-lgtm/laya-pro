"""Tests for the Nemotron wire behaviour discovered against the live API.

Three things were true of the real endpoint and are easy to regress on:
the model is a reasoning model, the shared endpoint is slow, and httpx
exceptions often carry an empty message.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.app.config import Settings
from backend.app.systems.nemotron.client import NemotronClient
from backend.app.systems.nemotron.exceptions import (
    NemotronContractError,
    NemotronUnavailable,
)
from backend.app.systems.nemotron.models import GenerationRequest, RenderRole


def run(coroutine):
    return asyncio.run(coroutine)


async def call(settings: Settings, handler, prompt: str = "ciao"):
    """Send one generation through a mock transport; return (body, response)."""

    sent = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        sent["body"] = json.loads(request.read().decode("utf-8"))
        # Tests may pass a ready-made response or a callable that raises.
        return handler(request) if callable(handler) else handler

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        response = await NemotronClient(settings, client=client).generate(
            GenerationRequest(prompt=prompt, role=RenderRole.DIRECT)
        )
        return sent["body"], response
    finally:
        await client.aclose()


def ok(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


def completion(content: str, model: str = "nvidia/nemotron-3.5-lightning-30b-a3b") -> httpx.Response:
    return ok({"model": model, "choices": [{"message": {"content": content}}]})


def test_thinking_is_disabled_by_default(settings):
    body, _ = run(call(settings, completion("ciao")))

    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_thinking_can_be_re_enabled(tmp_path):
    settings = Settings(
        _env_file=None,
        nemotron_api_key="k",
        nemotron_enable_thinking=True,
        db_path=tmp_path / "x.db",
    )

    body, _ = run(call(settings, completion("ciao")))

    assert "chat_template_kwargs" not in body


def test_shipped_defaults_can_finish_an_answer():
    """Below ~600 tokens the model runs out of reasoning budget mid-answer."""

    settings = Settings(_env_file=None)

    assert settings.nemotron_max_output_tokens >= 600
    assert settings.nemotron_timeout_seconds >= 90


def test_timeout_is_reported_with_a_usable_hint(settings):
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(NemotronUnavailable) as error:
        run(call(settings, slow))

    message = str(error.value)
    assert "timed out" in message
    assert "NEMOTRON_TIMEOUT_SECONDS" in message


def test_transport_errors_are_never_reported_as_empty_strings(settings):
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("", request=request)

    with pytest.raises(NemotronUnavailable) as error:
        run(call(settings, broken))

    assert "ConnectError" in str(error.value)


def test_completion_is_extracted_and_trimmed(settings):
    """The client normalises the envelope; typed validation is the adapter's job."""

    _, payload = run(call(settings, completion("  risposta  ")))

    assert payload["text"] == "risposta"
    assert payload["engine"] == "nvidia/nemotron-3.5-lightning-30b-a3b"


def test_adapter_turns_the_normalised_payload_into_the_contract(settings):
    from backend.app.systems.nemotron.adapter import NemotronAdapter

    async def go():
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: completion("risposta")))
        try:
            return await NemotronAdapter(settings, client=NemotronClient(settings, client)).generate(
                GenerationRequest(prompt="ciao")
            )
        finally:
            await client.aclose()

    response = run(go())

    assert response.text == "risposta"
    assert response.request_id


def test_reasoning_content_alone_is_not_mistaken_for_an_answer(settings):
    """With thinking on, a truncated reply can carry only a preamble."""

    def reasoning_only(request: httpx.Request) -> httpx.Response:
        return ok(
            {
                "model": "m",
                "choices": [
                    {"message": {"content": "", "reasoning_content": "Here's a thinking process:"}}
                ],
            }
        )

    from backend.app.systems.nemotron.adapter import NemotronAdapter

    async def go():
        client = httpx.AsyncClient(transport=httpx.MockTransport(reasoning_only))
        try:
            return await NemotronAdapter(
                settings, client=NemotronClient(settings, client)
            ).generate(GenerationRequest(prompt="ciao"))
        finally:
            await client.aclose()

    with pytest.raises(NemotronContractError):
        run(go())


def test_malformed_completion_is_a_contract_error(settings):
    from backend.app.systems.nemotron.adapter import NemotronAdapter

    async def go():
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: ok({"choices": []})))
        try:
            return await NemotronAdapter(
                settings, client=NemotronClient(settings, client)
            ).generate(GenerationRequest(prompt="ciao"))
        finally:
            await client.aclose()

    with pytest.raises(NemotronContractError):
        run(go())


def test_http_error_status_carries_the_response_body(settings):
    def denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid api key")

    with pytest.raises(NemotronUnavailable) as error:
        run(call(settings, denied))

    assert "401" in str(error.value)
    assert "invalid api key" in str(error.value)


# --------------------------------------------------------------- degeneracy

RUNAWAY = (
    "Iellsellsellsellsellsellsellsellsellsellsellsellsellsellsellsellsellsells "
    "deepellsellsells whereellsellsellsellsellsellsellsellsellsellsellsellsellsells "
    "sono strumenti di verifica contabile che confrontano i saldi del conto"
)


def test_runaway_repetition_is_detected():
    from backend.app.systems.nemotron.client import is_degenerate

    assert is_degenerate(RUNAWAY) is True


def test_a_normal_answer_is_not_flagged():
    from backend.app.systems.nemotron.client import is_degenerate

    good = (
        "Il libro contabile in partita doppia registra ogni operazione economica "
        "attraverso due voci contrapposte, debito e credito, in modo che la "
        "partita sia sempre bilanciata."
    )
    assert is_degenerate(good) is False


def test_short_answers_are_never_flagged():
    from backend.app.systems.nemotron.client import is_degenerate

    assert is_degenerate("ok") is False
    assert is_degenerate("") is False
    assert is_degenerate("Il debito e il credito.") is False


def test_degenerate_reply_is_retried_once(settings):
    seen = []

    def flaky(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode("utf-8"))
        seen.append(body["temperature"])
        content = RUNAWAY if len(seen) == 1 else "Una risposta civile e breve."
        return completion(content)

    client = httpx.AsyncClient(transport=httpx.MockTransport(flaky))
    async def go():
        try:
            return await NemotronClient(settings, client=client).generate(
                GenerationRequest(prompt="ciao")
            )
        finally:
            await client.aclose()

    payload = run(go())

    assert payload["text"] == "Una risposta civile e breve."
    assert len(seen) == 2
    assert seen[1] > seen[0], "the retry should diversify, not repeat the same draw"


def test_persistently_degenerate_reply_fails_loudly(settings):
    calls = {"n": 0}

    def broken(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return completion(RUNAWAY)

    client = httpx.AsyncClient(transport=httpx.MockTransport(broken))
    async def go():
        try:
            return await NemotronClient(settings, client=client).generate(
                GenerationRequest(prompt="ciao")
            )
        finally:
            await client.aclose()

    with pytest.raises(NemotronContractError) as error:
        run(go())

    assert calls["n"] == 2, "one retry, then give up"
    assert "degenerate" in str(error.value)


def test_prompt_echo_is_treated_as_degenerate():
    """An unhealthy instance recites its own input instead of answering."""

    from backend.app.systems.nemotron.client import is_degenerate

    prompt = "Cosa sono i saldi di controllo in contabilita? Rispondi in due frasi."
    echo = (
        "I saldi di controllo sono strumenti The user: " + prompt +
        " PROBLEMA UTENTE: " + prompt
    )

    assert is_degenerate(echo, prompt) is True


def test_a_legitimate_answer_that_quotes_terms_is_not_an_echo():
    from backend.app.systems.nemotron.client import is_degenerate

    prompt = "Spiega la partita doppia con un esempio contabile breve."
    answer = (
        "Nella partita doppia ogni operazione genera una voce di debito e una di credito. "
        "Per esempio, un acquisto di 100 euro registra debito merci e credito cassa."
    )

    assert is_degenerate(answer, prompt) is False

def test_leaked_chat_template_tail_is_stripped():
    """Seen live: the endpoint returned its own template before the answer."""

    from backend.app.systems.nemotron.client import strip_thinking_artifacts

    leaked = (
        "EOS\n"
        "</think>UUID.\n"
        "</think>Istruzioni: il modulo usa PostgreSQL e JWT.\n\n"
        "Primo passo: estrarre le credenziali in un modulo separato."
    )

    assert strip_thinking_artifacts(leaked) == (
        "Istruzioni: il modulo usa PostgreSQL e JWT.\n\n"
        "Primo passo: estrarre le credenziali in un modulo separato."
    )


def test_a_clean_answer_is_left_untouched():
    from backend.app.systems.nemotron.client import strip_thinking_artifacts

    answer = "  Il piano prevede tre passaggi: leggere, validare, scrivere.  "

    assert strip_thinking_artifacts(answer) == (
        "Il piano prevede tre passaggi: leggere, validare, scrivere."
    )


def test_a_thinking_closer_inside_the_answer_is_not_a_tearing_point():
    """Only the head is scanned, so real prose is never cut in half."""

    from backend.app.systems.nemotron.client import strip_thinking_artifacts

    answer = "Qui descrivo i dati. " + ("padding. " * 200) + "Fine </think> del ragionamento."

    assert strip_thinking_artifacts(answer) == answer.strip()


def test_template_artifacts_alone_are_a_contract_error(settings):
    """Nothing left after stripping means there was no answer to begin with."""

    with pytest.raises(NemotronContractError):
        run(call(settings, completion("EOS\n</think>UUID.\n</think>")))
