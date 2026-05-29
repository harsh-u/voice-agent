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
