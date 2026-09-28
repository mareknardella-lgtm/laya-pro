import logging
from .models import ChatRequest, ChatResponse
from ..systems.system2.adapter import System2Adapter
from ..systems.system2.models import GenerationRequest
from ..systems.laya.adapter import LayaAdapter
from ..systems.laya.models import DecisionRequest
from .history import ChatHistoryManager
from .memory import ChatMemoryManager

logger = logging.getLogger("laya.chat.orchestrator")

class HybridOrchestrator:
    def __init__(self, settings, system2: System2Adapter, laya_engine: LayaAdapter, history: ChatHistoryManager = None, memory: ChatMemoryManager = None) -> None:
        self._settings = settings
        self._system2 = system2
        self._laya = laya_engine
        self._history = history
        self._memory = memory

    async def process_message(self, request: ChatRequest) -> ChatResponse:
        mode = request.mode
        logger.info(f"Ricevuta richiesta chat in modalita {mode} (session: {request.session_id})")

        # 1. Save user message
        if self._history:
            self._history.add_message(request.session_id, "user", request.message, mode, {})

        # 2. Retrieve history and memory context
        past_msgs = []
        if self._history:
            past_msgs = self._history.get_messages(request.session_id)
            
        context_str = ""
        retrieved_count = 0
        if self._memory and request.auto_memory:
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
                text="The Nemotron provider (System 2) is not configured.",
                requested_mode="LOW",
                executed_mode="NONE",
                providers_used=[],
                status="error",
                error_message="Nemotron (System 2) unavailable."
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
            logger.error(f"Nemotron error in LOW mode: {e}")
            return ChatResponse(
                text="An error occurred during response generation.",
                requested_mode="LOW",
                executed_mode="LOW",
                providers_used=["nemotron"],
                status="error",
                error_message=str(e)
            )

    async def _process_medium(self, request: ChatRequest) -> ChatResponse:
        """MEDIUM: Utente -> Laya -> Nemotron -> Risposta"""
        if not self._laya.runtime_configured:
            return ChatResponse(
                text="MEDIUM mode unavailable: Laya engine connection missing.",
                requested_mode="MEDIUM",
                executed_mode="NONE",
                providers_used=[],
                status="error",
                error_message="Laya (System 1) not configured."
            )
            
        try:
            decision_env = await self._laya.decide(DecisionRequest(user_input=request.message, context={"mode": "MEDIUM"}))
            decision = decision_env.decision
        except Exception as e:
            return ChatResponse(
                text="Error during Laya analysis.",
                requested_mode="MEDIUM",
                executed_mode="NONE",
                providers_used=["laya"],
                status="error",
                error_message=str(e)
            )

        prompt = (
            f"The user asked: {request.message}\n\n"
            f"The local reasoning system (Laya) produced the following decision context:\n"
            f"- Decision kind: {decision.kind}\n"
            f"- Reasoning: {decision.reason}\n"
            f"Generate a natural and useful response for the user following this reasoning strategy."
        )

        gen_req = GenerationRequest(prompt=prompt, context={"mode": "MEDIUM"})
        try:
            gen_res = await self._system2.generate(gen_req)
            return ChatResponse(
                text=gen_res.text,
                requested_mode="MEDIUM",
                executed_mode="MEDIUM",
                providers_used=["laya", "nemotron"],
                status="success",
                metadata={"laya_decision": decision.model_dump()}
            )
        except Exception as e:
            return ChatResponse(
                text="Error during final response generation.",
                requested_mode="MEDIUM",
                executed_mode="MEDIUM",
                providers_used=["laya", "nemotron"],
                status="error",
                error_message=str(e)
            )

    async def _process_hard(self, request: ChatRequest) -> ChatResponse:
        """HARD: Utente -> Laya (Approfondito) -> Nemotron -> Risposta"""
        if not self._laya.runtime_configured:
            return ChatResponse(
                text="HARD mode unavailable: Laya engine connection missing.",
                requested_mode="HARD",
                executed_mode="NONE",
                providers_used=[],
                status="error",
                error_message="Laya (System 1) not configured."
            )

        try:
            decision_env = await self._laya.decide(DecisionRequest(user_input=request.message, context={"mode": "HARD", "depth": "deep"}))
            decision = decision_env.decision
        except Exception as e:
            return ChatResponse(
                text="Error during deep Laya analysis.",
                requested_mode="HARD",
                executed_mode="NONE",
                providers_used=["laya"],
                status="error",
                error_message=str(e)
            )

        prompt = (
            f"The user asked: {request.message}\n\n"
            f"The local reasoning system (Laya) elaborated a detailed decision:\n"
            f"- Decision kind: {decision.kind}\n"
            f"- Reasoning: {decision.reason}\n"
        )
        if decision.missing_information:
            prompt += f"- Missing info: {', '.join(decision.missing_information)}\n"
        if decision.parameters:
            prompt += f"- Parameters: {decision.parameters}\n"
            
        prompt += (
            f"\nDraft the final response for the user strictly adhering to Laya's reasoning. "
            f"Do not invent additional information. Just transform this reasoning into a fluid response."
        )

        gen_req = GenerationRequest(prompt=prompt, context={"mode": "HARD"})
        try:
            gen_res = await self._system2.generate(gen_req)
            return ChatResponse(
                text=gen_res.text,
                requested_mode="HARD",
                executed_mode="HARD",
                providers_used=["laya", "nemotron"],
                status="success",
                metadata={"laya_decision": decision.model_dump()}
            )
        except Exception as e:
            return ChatResponse(
                text="Error during final response generation.",
                requested_mode="HARD",
                executed_mode="HARD",
                providers_used=["laya", "nemotron"],
                status="error",
                error_message=str(e)
            )
