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
