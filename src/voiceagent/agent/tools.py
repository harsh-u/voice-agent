RAG_TOOL = {
    "type": "function",
    "function": {
        "name": "query_knowledge_base",
        "description": (
            "Look up information from the agent's knowledge base. "
            "Use this when the caller asks a question about products, services, policies, "
            "or anything that might be in the documentation. "
            "Keep your follow-up response concise and conversational."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to look up in the knowledge base.",
                }
            },
            "required": ["question"],
        },
    },
}

END_CALL_TOOL = {
    "type": "function",
    "function": {
        "name": "end_call",
        "description": "End the call gracefully when the conversation is complete",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

TRANSFER_TOOL = {
    "type": "function",
    "function": {
        "name": "transfer_to_human",
        "description": "Transfer the call to a human agent",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Reason for transfer",
                }
            },
            "required": ["reason"],
        },
    },
}

DEFAULT_TOOLS = [END_CALL_TOOL, TRANSFER_TOOL]


def _to_function_schema(tool: dict):
    """Convert an OpenAI-style function tool dict into a Pipecat FunctionSchema."""
    from pipecat.adapters.schemas.function_schema import FunctionSchema

    fn = tool["function"]
    params = fn.get("parameters") or {}
    return FunctionSchema(
        name=fn["name"],
        description=fn.get("description", ""),
        properties=params.get("properties", {}),
        required=params.get("required", []),
    )


def build_tools(rag_enabled: bool = False):
    """Return a Pipecat ToolsSchema for the LLM context.

    This Pipecat version requires ``LLMContext.set_tools`` to receive a
    ``ToolsSchema`` (not a raw list of OpenAI dicts), so we convert here.
    """
    from pipecat.adapters.schemas.tools_schema import ToolsSchema

    tools = list(DEFAULT_TOOLS)
    if rag_enabled:
        tools.append(RAG_TOOL)
    return ToolsSchema(standard_tools=[_to_function_schema(t) for t in tools])


# ---------------------------------------------------------------------------
# LiveKit Agents tools (function_tool) — same semantics as the Pipecat tools
# above, used by src/voiceagent/pipeline/bot_livekit.py.
# ---------------------------------------------------------------------------

def build_livekit_tools(on_turn_end=None, rag_api_key: str | None = None) -> list:
    """Return LiveKit Agents @function_tool callables.

    Mirrors the Pipecat tool semantics: query_knowledge_base (RAG, with an
    immediate spoken filler), end_call (speak goodbye then end), and
    transfer_to_human (acknowledge). Reuses the same tool descriptions.

    Args:
        on_turn_end: async callback (role, text, latency_ms) — used to record the
            spoken filler as a transcript turn so it matches what the caller hears.
        rag_api_key: when set, the knowledge-base tool is included.
    """
    import asyncio
    from loguru import logger
    from livekit.agents import function_tool, RunContext

    @function_tool(name="end_call", description=END_CALL_TOOL["function"]["description"])
    async def end_call(ctx: RunContext) -> str:
        ctx.session.say("Thank you for calling. Goodbye!")

        async def _graceful_end() -> None:
            await asyncio.sleep(3.0)
            await ctx.session.aclose()

        asyncio.create_task(_graceful_end())
        return "ending"

    @function_tool(name="transfer_to_human", description=TRANSFER_TOOL["function"]["description"])
    async def transfer_to_human(ctx: RunContext, reason: str) -> str:
        return "Live transfer isn't available; continue helping the caller."

    tools = [end_call, transfer_to_human]

    if rag_api_key:
        from voiceagent.rag.client import query as rag_query

        @function_tool(name="query_knowledge_base", description=RAG_TOOL["function"]["description"])
        async def query_knowledge_base(ctx: RunContext, question: str) -> str:
            logger.info(f"[rag] query: {question[:80]}")
            filler = "Sure, let me check that for you."
            # Immediate filler so there's no dead air during the lookup.
            ctx.session.say(filler, add_to_chat_ctx=False)
            if on_turn_end:
                await on_turn_end("assistant", filler, None)
            return await rag_query(question, rag_api_key)

        tools.append(query_knowledge_base)

    return tools
