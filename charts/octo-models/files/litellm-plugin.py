"""LiteLLM proxy plugin: hoist mid-conversation system messages to the front.

Qwen 3.8 chat templates (transformers 5.x) reject any role=system message that
is not at index 0 ("System message must be at the beginning"). Agent harnesses
(opencode, Claude-Code-style clients) legally emit system reminders mid-turn.
This hook merges every system message into one leading system block before the
request reaches the router, matching what LiteLLM itself does for Anthropic.
"""

from __future__ import annotations

try:
    from litellm.integrations.custom_logger import CustomLogger
except ImportError:  # unit-test import without litellm installed
    class CustomLogger:  # type: ignore[no-redef]
        pass


def _system_text(msg: dict) -> str:
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content)


def hoist_system_messages(messages: list) -> list:
    """Return messages with all system blocks merged into a single one at index 0."""
    if not any(m.get("role") == "system" and i > 0 for i, m in enumerate(messages)):
        return messages
    system_parts: list[str] = []
    rest = []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "system":
            text = _system_text(msg)
            if text:
                system_parts.append(text)
        else:
            rest.append(msg)
    if system_parts:
        rest.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
    return rest


class ProxyHandler(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if isinstance(data.get("messages"), list):
            data["messages"] = hoist_system_messages(data["messages"])
        return data


proxy_handler_instance = ProxyHandler()
