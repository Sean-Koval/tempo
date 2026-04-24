# coach-db MCP server

FastMCP server that exposes Tempo's SQLite (`data/coach.db`) and LanceDB
(`data/vectors/*.lance`) substrates as typed tools for the agent.

Scope: read-heavy coaching queries (load, adherence, readiness, plan-vs-actual,
knowledge / memory retrieval, session-library matching) plus one write tool
(`log_decision`). All raw-data writes to intervals.icu go through the separate
`intervals` MCP.

Run standalone:

```bash
uv run --directory mcp-servers/coach-db coach-db-mcp
```

Wired in `.claude/settings.json` under `mcpServers.coach-db`.
