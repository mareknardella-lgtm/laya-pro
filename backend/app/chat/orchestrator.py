from __future__ import annotations

import logging
from typing import Optional

from .models import ChatRequest, ChatResponse, JevPlan
from .jev import JevAdapter, JEVUnavailable
from .history import ChatHistoryManager
from .memory import ChatMemoryManager
from ..systems.system2.adapter import System2Adapter
from ..systems.system2.models import GenerationRequest
from ..config import Settings

logger = logging.getLogger("laya.chat.orchestrator")


class HybridOrchestrator:
    def __init__(self, settings: Settings, system2: System2Adapter, jev_engine: JevAdapter, history: ChatHistoryManager = None, memory: ChatMemoryManager = None) -> None:
        self._settings = settings
        self._system2 = system2
        self._jev = jev_engine
        self._history = history
        self._memory = memory

    async def process_message(self, request: ChatRequest) -> ChatResponse:
        mode = request.mode
        logger.info(f"Ricevuta richiesta chat in modalità {mode} (session: {request.session_id})")

        # 1. Save user message
        if self._history:
            self._history.add_message(request.session_id, "user", request.message, mode, {})

        # 2. Retrieve history and memory context
        past_msgs = []
        if self._history:
            past_msgs = self._history.get_messages(request.session_id)
            
        context_str = ""
        retrieved_count = 0
        if self._memory:
            context_str = await self._memory.retrieve_context(request.message, past_msgs)
            if context_str:
                retrieved_count = 1

        original_msg = request.message
        enriched_msg = original_msg
        
        if context_str:
            enriched_msg = f"CONTESTO PERSONALE (Memoria):\n{context_str}\n\nMessaggio Attuale: {original_msg}"
            
        if past_msgs:
            # exclude the message we just added
            recent = past_msgs[-10:-1]
            if recent:
                history_str = "\n".join([f"{m['role']}: {m['content']}" for m in recent])
                enriched_msg = f"CRONOLOGIA CHAT RECENTE:\n{history_str}\n\n{enriched_msg}"

        # Override request message temporarily for the prompt
        request.message = enriched_msg

        # 3. Execute
        if mode == "LOW":
            response = await self._process_low(request)
        elif mode == "MEDIUM":
            response = await self._process_medium(request)
        elif mode == "HARD":
            response = await self._process_hard(request)
        else:
            raise ValueError(f"Unknown mode: {mode}")

        # Restore original message just in case
        request.message = original_msg

        # 4. Save assistant message
        if self._history and response.text:
            self._history.add_message(request.session_id, "assistant", response.text, mode, {})

        # 5. Extract memory
        extracted_count = 0
        if self._memory and request.auto_memory and response.status == "success":
            memories = await self._memory.extract_memory([{"role": "user", "content": original_msg}, {"role": "assistant", "content": response.text}])
            from ..memory.models import MemoryScope
            for k, v in memories:
                # Save to global memory
                self._memory._memory.put("__global__", k, v, scope=MemoryScope.PERSISTENT)
                extracted_count += 1
                
        response.extracted_memories = extracted_count
        response.retrieved_memories = retrieved_count
        
        return response

    async def _process_low(self, request: ChatRequest) -> ChatResponse:
        """LOW: Utente -> Nemotron -> Risposta"""
        if not self._system2.runtime_configured:
            return ChatResponse(
                text="Il provider Nemotron (System 2) non è configurato.",
                requested_mode="LOW",
                executed_mode="NONE",
                providers_used=[],
                status="error",
                error_message="Nemotron (System 2) non disponibile."
            )

        gen_req = GenerationRequest(
            prompt=request.message,
            context={"mode": "LOW"}
        )

        try:
            gen_res = await self._system2.generate(gen_req)
            return ChatResponse(
                text=gen_res.text,
                requested_mode="LOW",
                executed_mode="LOW",
                providers_used=["nemotron"],
                status="success",
                metadata={"engine": gen_res.engine, "request_id": gen_res.request_id}
            )
        except Exception as e:
            logger.error(f"Errore Nemotron in modalità LOW: {e}")
            return ChatResponse(
                text="Si è verificato un errore durante la generazione della risposta.",
                requested_mode="LOW",
                executed_mode="LOW",
                providers_used=["nemotron"],
                status="error",
                error_message=str(e)
            )

    async def _process_medium(self, request: ChatRequest) -> ChatResponse:
        """MEDIUM: Utente -> JEV -> Piano -> Nemotron -> Risposta"""
        try:
            plan = await self._jev.analyze_and_plan(request.message)
        except JEVUnavailable as e:
            return ChatResponse(
                text="Modalità MEDIUM non disponibile: manca il collegamento al motore JEV.",
                requested_mode="MEDIUM",
                executed_mode="NONE",
                providers_used=[],
                status="error",
                error_message=str(e)
            )
        except Exception as e:
            return ChatResponse(
                text="Errore durante l'analisi JEV.",
                requested_mode="MEDIUM",
                executed_mode="NONE",
                providers_used=["jev"],
                status="error",
                error_message=str(e)
            )

        # Costruiamo il prompt per Nemotron basato sul piano
        prompt = (
            f"L'utente ha chiesto: {request.message}\n\n"
            f"Il sistema di ragionamento (JEV) ha prodotto il seguente piano:\n"
            f"- Intento: {plan.intent}\n"
            f"- Strategia: {plan.strategy}\n"
            f"Genera una risposta naturale e utile per l'utente seguendo questa strategia."
        )

        gen_req = GenerationRequest(prompt=prompt, context={"mode": "MEDIUM"})
        try:
            gen_res = await self._system2.generate(gen_req)
            return ChatResponse(
                text=gen_res.text,
                requested_mode="MEDIUM",
                executed_mode="MEDIUM",
                providers_used=["jev", "nemotron"],
                status="success",
                metadata={"jev_plan": plan.model_dump()}
            )
        except Exception as e:
            return ChatResponse(
                text="Errore durante la generazione della risposta finale.",
                requested_mode="MEDIUM",
                executed_mode="MEDIUM",
                providers_used=["jev", "nemotron"],
                status="error",
                error_message=str(e)
            )

    async def _process_hard(self, request: ChatRequest) -> ChatResponse:
        """HARD: Utente -> JEV (Piano Approfondito) -> Nemotron -> Risposta"""
        try:
            plan = await self._jev.analyze_and_plan(request.message, context={"depth": "hard"})
        except JEVUnavailable as e:
            return ChatResponse(
                text="Modalità HARD non disponibile: manca il collegamento al motore JEV.",
                requested_mode="HARD",
                executed_mode="NONE",
                providers_used=[],
                status="error",
                error_message=str(e)
            )
        except Exception as e:
            return ChatResponse(
                text="Errore durante l'analisi profonda JEV.",
                requested_mode="HARD",
                executed_mode="NONE",
                providers_used=["jev"],
                status="error",
                error_message=str(e)
            )

        steps_text = "\n".join(f"{i+1}. {s.description}" for i, s in enumerate(plan.steps))
        criteria_text = "\n".join(f"- {c}" for c in plan.validation_criteria)

        prompt = (
            f"L'utente ha chiesto: {request.message}\n\n"
            f"Il sistema di ragionamento (JEV) ha elaborato un piano dettagliato:\n"
            f"Passaggi:\n{steps_text}\n\n"
            f"Criteri di validazione:\n{criteria_text}\n\n"
            f"Redigi la risposta finale per l'utente attenendoti strettamente a questi passaggi e criteri. "
            f"Non inventare informazioni aggiuntive. Limitati a trasformare questo piano in una risposta fluida."
        )

        gen_req = GenerationRequest(prompt=prompt, context={"mode": "HARD"})
        try:
            gen_res = await self._system2.generate(gen_req)
            return ChatResponse(
                text=gen_res.text,
                requested_mode="HARD",
                executed_mode="HARD",
                providers_used=["jev", "nemotron"],
                status="success",
                metadata={"jev_plan": plan.model_dump()}
            )
        except Exception as e:
            return ChatResponse(
                text="Errore durante la generazione della risposta finale.",
                requested_mode="HARD",
                executed_mode="HARD",
                providers_used=["jev", "nemotron"],
                status="error",
                error_message=str(e)
            )
