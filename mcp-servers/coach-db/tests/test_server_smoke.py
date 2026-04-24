"""Smoke test — server imports cleanly and ping tool returns the expected payload."""

from __future__ import annotations

import pytest

from coach_db_mcp.server import mcp


@pytest.mark.asyncio
async def test_ping_tool_is_registered() -> None:
    tools = await mcp.list_tools()
    assert any(t.name == "ping" for t in tools)


@pytest.mark.asyncio
async def test_ping_returns_ok() -> None:
    result = await mcp.call_tool("ping", {})
    payload = result.structured_content
    assert payload is not None
    assert payload.get("status") == "ok"
    assert payload.get("server") == "coach-db"
