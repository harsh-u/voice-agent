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

def build_tools(rag_enabled: bool = False) -> list:
    tools = list(DEFAULT_TOOLS)
    if rag_enabled:
        tools.append(RAG_TOOL)
    return tools
