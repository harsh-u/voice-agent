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
