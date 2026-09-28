import json
from typing import Any

from ..systems.system2.adapter import System2Adapter
from ..systems.system2.models import GenerationRequest
from ..memory.manager import MemoryManager

class ChatMemoryManager:
    def __init__(self, system2: System2Adapter, memory_manager: MemoryManager):
        self._system2 = system2
        self._memory = memory_manager

    async def extract_memory(self, messages: list[dict]) -> list[tuple[str, Any]]:
        if not messages:
            return []
            
        formatted = ""
        for m in messages:
            formatted += f"{m.get('role', 'unknown')}: {m.get('content', '')}\n"

        prompt = f"""
You are extracting facts, user preferences, and rules from a conversation.
Focus on information that should be remembered globally across all sessions.
Extract the info as a JSON array of objects with "key" and "value" fields.
If nothing should be remembered, return an empty array [].
Key should be a short underscore-separated string (e.g. user_name, prefer_dark_mode).

Conversation:
{formatted}

Return only the JSON array:
"""
        req = GenerationRequest(prompt=prompt)
        resp = await self._system2.generate(req)
        
        try:
            text = resp.text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            
            data = json.loads(text)
            if isinstance(data, list):
                result = []
                for item in data:
                    if isinstance(item, dict) and "key" in item and "value" in item:
                        result.append((str(item["key"]), item["value"]))
                return result
            return []
        except Exception:
            return []

    async def retrieve_context(self, query: str, session_messages: list[dict]) -> str:
        memories = self._memory.list("__global__")
        if not memories:
            return ""
        
        mem_str = "\n".join([f"- {m.key}: {json.dumps(m.value)}" for m in memories])
        
        prompt = f"""
You are provided with a list of saved memories and a query.
Select only the memories that are relevant to the query and format them as a concise context block.

Memories:
{mem_str}

Query:
{query}

Relevant Context (if any):
"""
        req = GenerationRequest(prompt=prompt)
        resp = await self._system2.generate(req)
        return resp.text.strip()
