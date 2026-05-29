#!/usr/bin/env python3
"""test_nexus_hooks.py — Hook registry and hook implementations tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_hook_registry_singleton():
    """get_registry returns same instance."""
    from agent.hook_registry import get_registry
    r1 = get_registry()
    r2 = get_registry()
    assert r1 is r2, "Registry should be singleton"
    print("✓ test_hook_registry_singleton")


def test_hook_registry_register():
    """register_hook adds a hook."""
    from agent.hook_registry import get_registry, HookEvent, register_hook, reset_registry
    reset_registry()
    registry = get_registry()

    async def dummy_handler(event, context):
        return {"ok": True}

    register_hook(HookEvent.TOOL_COMPLETE, dummy_handler, name="test_hook", priority=50)
    hooks = registry.get_hooks(HookEvent.TOOL_COMPLETE)
    assert len(hooks) >= 1
    assert any(h.name == "test_hook" for h in hooks)
    print("✓ test_hook_registry_register")


def test_hook_auto_discover():
    """auto_discover loads hook files from hooks/ directory."""
    from agent.hook_registry import get_registry, reset_registry
    reset_registry()
    registry = get_registry()
    count = registry.auto_discover()
    assert count >= 3, f"Expected at least 3 hooks, got {count}"
    hooks = registry.list_all()
    names = [h["name"] for h in hooks]
    assert "quality_check" in names
    assert "stop_audit" in names
    assert "post_compact_check" in names
    print(f"✓ test_hook_auto_discover: {count} files, {len(hooks)} hooks")


def test_hook_emit_tool_complete():
    """TOOL_COMPLETE event fires quality_check hook."""
    from agent.hook_registry import get_registry, HookEvent, reset_registry
    reset_registry()
    registry = get_registry()
    registry.auto_discover()

    results = registry.emit_sync(HookEvent.TOOL_COMPLETE, {
        "tool": "test",
        "result": "hello world test output",
    })
    assert len(results) >= 1
    assert results[0].success
    assert results[0].result["passed"] is True
    print(f"✓ test_hook_emit_tool_complete: {results[0].elapsed_ms:.1f}ms")


def test_hook_emit_tool_complete_error():
    """TOOL_COMPLETE detects error in result."""
    from agent.hook_registry import get_registry, HookEvent, reset_registry
    reset_registry()
    registry = get_registry()
    registry.auto_discover()

    results = registry.emit_sync(HookEvent.TOOL_COMPLETE, {
        "tool": "test",
        "result": "Error: connection refused",
    })
    assert len(results) >= 1
    assert results[0].success
    assert results[0].result["passed"] is False
    assert any("错误" in i for i in results[0].result["issues"])
    print("✓ test_hook_emit_tool_complete_error")


def test_hook_emit_session_end():
    """SESSION_END event fires stop_audit hook."""
    from agent.hook_registry import get_registry, HookEvent, reset_registry
    reset_registry()
    registry = get_registry()
    registry.auto_discover()

    results = registry.emit_sync(HookEvent.SESSION_END, {
        "session_id": "test-session",
        "messages": [],
    })
    assert len(results) >= 1
    assert results[0].success
    print("✓ test_hook_emit_session_end")


def test_hook_emit_post_compact():
    """POST_COMPACT event fires post_compact_check hook."""
    from agent.hook_registry import get_registry, HookEvent, reset_registry
    reset_registry()
    registry = get_registry()
    registry.auto_discover()

    results = registry.emit_sync(HookEvent.POST_COMPACT, {
        "before_count": 100,
        "after_count": 20,
        "reduction_pct": 80.0,
    })
    assert len(results) >= 1
    assert results[0].success
    assert results[0].result["valid"] is True
    print("✓ test_hook_emit_post_compact")


def test_hook_emit_post_compact_high_reduction():
    """POST_COMPACT detects excessive reduction."""
    from agent.hook_registry import get_registry, HookEvent, reset_registry
    reset_registry()
    registry = get_registry()
    registry.auto_discover()

    results = registry.emit_sync(HookEvent.POST_COMPACT, {
        "before_count": 100,
        "after_count": 5,
        "reduction_pct": 95.0,
    })
    assert len(results) >= 1
    assert results[0].success
    # 95% reduction should trigger an issue
    assert results[0].result["valid"] is False
    print("✓ test_hook_emit_post_compact_high_reduction")


def test_hook_metrics():
    """Hook metrics track executions."""
    from agent.hook_registry import get_registry, HookEvent, reset_registry
    reset_registry()
    registry = get_registry()
    registry.auto_discover()

    registry.emit_sync(HookEvent.TOOL_COMPLETE, {"tool": "t", "result": "ok"})
    registry.emit_sync(HookEvent.TOOL_COMPLETE, {"tool": "t", "result": "ok"})

    metrics = registry.get_metrics()
    assert metrics["total_executions"] >= 2
    assert metrics["total_failures"] == 0
    print(f"✓ test_hook_metrics: {metrics['total_executions']} executions")


def test_hook_unregister():
    """unregister removes a hook."""
    from agent.hook_registry import get_registry, HookEvent, register_hook, reset_registry
    reset_registry()
    registry = get_registry()

    async def temp_hook(event, context):
        return {}

    register_hook(HookEvent.TOOL_COMPLETE, temp_hook, name="temp_hook")
    assert registry.unregister("temp_hook")
    hooks = registry.get_hooks(HookEvent.TOOL_COMPLETE)
    assert not any(h.name == "temp_hook" for h in hooks)
    print("✓ test_hook_unregister")


if __name__ == "__main__":
    tests = [
        test_hook_registry_singleton,
        test_hook_registry_register,
        test_hook_auto_discover,
        test_hook_emit_tool_complete,
        test_hook_emit_tool_complete_error,
        test_hook_emit_session_end,
        test_hook_emit_post_compact,
        test_hook_emit_post_compact_high_reduction,
        test_hook_metrics,
        test_hook_unregister,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__}: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed, {len(tests)} total")
    if failed:
        sys.exit(1)
