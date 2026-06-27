"""MCP client for the agent: lists the tools of an MCP server (streamable-http)
and calls them. Converts the MCP schema to the OpenAI tools format to mix them
with the local tools. Synchronous on the outside (uses asyncio.run inside)."""

import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def _list(url):
    async with streamablehttp_client(url) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return (await s.list_tools()).tools


async def _call(url, name, args):
    async with streamablehttp_client(url) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(name, arguments=args)
            parts = [getattr(c, "text", "") or "" for c in (res.content or [])]
            txt = "\n".join(p for p in parts if p)
            if getattr(res, "isError", False):
                return f"ERROR (mcp): {txt or 'tool error'}"
            return txt or "(no output)"


def list_tools_openai(url, timeout=12):
    """Returns (tool_schemas_openai, set_of_names). [] if it fails."""
    try:
        tools = asyncio.run(asyncio.wait_for(_list(url), timeout))
    except Exception:
        return [], set()
    out, names = [], set()
    for t in tools:
        out.append({"type": "function", "function": {
            "name": t.name,
            "description": t.description or "",
            "parameters": t.inputSchema or {"type": "object", "properties": {}},
        }})
        names.add(t.name)
    return out, names


def call_tool(url, name, args):
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except Exception:
            return f"ERROR: bad JSON args for {name}"
    try:
        return asyncio.run(_call(url, name, args))
    except Exception as e:
        return f"ERROR: MCP call failed ({type(e).__name__}): {e}"


if __name__ == "__main__":
    import sys
    u = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765/mcp"
    ts, names = list_tools_openai(u)
    print(f"MCP tools at {u}: {len(ts)}")
    print(" ", ", ".join(sorted(names)))
    print("run_shell('uname -a') ->", call_tool(u, "run_shell", {"command": "uname -a"}))
