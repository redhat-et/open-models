import asyncio
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "litellm_plugin.py"
spec = importlib.util.spec_from_file_location("octo_plugin", SCRIPT)
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

hoist = plugin.hoist_system_messages


def test_mid_conversation_system_merged_to_front():
    msgs = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "system", "content": "reminder: stay terse"},
        {"role": "user", "content": "again"},
    ]
    out = hoist(msgs)
    assert out[0] == {"role": "system", "content": "You are helpful.\n\nreminder: stay terse"}
    assert [m["role"] for m in out] == ["system", "user", "assistant", "user"]


def test_leading_system_only_untouched():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    assert hoist(msgs) == msgs


def test_no_system_at_all_untouched():
    msgs = [{"role": "user", "content": "hi"}]
    assert hoist(msgs) == msgs


def test_first_system_mid_stream_with_parts():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "system", "content": [{"type": "text", "text": "part1"}, {"type": "text", "text": "part2"}]},
        {"role": "tool", "tool_call_id": "t1", "content": "result"},
    ]
    out = hoist(msgs)
    assert out[0] == {"role": "system", "content": "part1\npart2"}
    assert [m["role"] for m in out] == ["system", "user", "tool"]


def test_empty_system_dropped():
    msgs = [{"role": "user", "content": "hi"}, {"role": "system", "content": ""}]
    assert hoist(msgs) == [{"role": "user", "content": "hi"}]


def test_pre_call_hook_rewrites_data():
    handler = plugin.proxy_handler_instance
    data = {"model": "qwen38-flash-next", "messages": [{"role": "user", "content": "a"}, {"role": "system", "content": "b"}]}
    out = asyncio.run(handler.async_pre_call_hook(None, None, data, "completion"))
    assert out["messages"][0]["role"] == "system"


def test_pre_call_hook_passthrough_non_chat():
    data = {"model": "embed", "input": "hi"}
    out = asyncio.run(plugin.proxy_handler_instance.async_pre_call_hook(None, None, data, "embedding"))
    assert out == data


def test_pre_call_hook_anthropic_folds_system_into_top_level():
    # PriceTag/Claude Code repro: system-role blocks mid `messages` on the
    # /v1/messages route must not reach vLLM as messages at all - litellm's
    # anthropic->openai conversion re-orders them back past the user turn.
    handler = plugin.proxy_handler_instance
    data = {
        "model": "qwen38-flash-next",
        "system": [{"type": "text", "text": "You are Claude Code."}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "system", "content": [{"type": "text", "text": "# Environment in /tmp"}]},
        ],
    }
    out = asyncio.run(handler.async_pre_call_hook(None, None, data, "anthropic_messages"))
    assert not any(m["role"] == "system" for m in out["messages"])
    assert "Claude Code" in out["system"] and "/tmp" in out["system"]


def test_pre_call_hook_anthropic_leaves_clean_request_untouched():
    handler = plugin.proxy_handler_instance
    data = {
        "model": "m",
        "system": "You are terse.",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    }
    out = asyncio.run(handler.async_pre_call_hook(None, None, data, "anthropic_messages"))
    assert out["system"] == "You are terse."
    assert [m["role"] for m in out["messages"]] == ["user"]


def test_pre_call_hook_responses_folds_system_into_instructions():
    handler = plugin.proxy_handler_instance
    data = {
        "model": "qwen38-flash-next",
        "instructions": "You are Claude Code.",
        "input": [
            {"role": "user", "type": "message", "content": [{"type": "input_text", "text": "what dir?"}]},
            {"role": "system", "type": "message", "content": [{"type": "input_text", "text": "You are in /tmp."}]},
        ],
    }
    out = asyncio.run(handler.async_pre_call_hook(None, None, data, "aresponses"))
    assert not any(i.get("role") == "system" for i in out["input"])
    assert "Claude Code" in out["instructions"] and "/tmp" in out["instructions"]


def test_pre_call_hook_responses_string_content_item():
    handler = plugin.proxy_handler_instance
    data = {
        "model": "m",
        "input": [
            {"role": "user", "content": "say ok"},
            {"role": "system", "content": "just the number"},
        ],
    }
    out = asyncio.run(handler.async_pre_call_hook(None, None, data, "aresponses"))
    assert [i["role"] for i in out["input"]] == ["user"]
    assert out["instructions"] == "just the number"


def test_pre_call_hook_responses_noop_without_system():
    handler = plugin.proxy_handler_instance
    data = {"model": "m", "instructions": "terse", "input": "Say OK"}
    out = asyncio.run(handler.async_pre_call_hook(None, None, data, "aresponses"))
    assert out == data
