"""Module: main — Stdio MCP projection of the shared operation contract."""

from __future__ import annotations

import asyncio
import threading

from auto.app.operations import EXPOSURES, coerceArguments, exitStatus, render, resolveContextArguments, resolvePayload
from auto.runtime.config import CONTEXT_KEYS
from auto.utils.data import AutoError

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def createServer(context):
	"""Build the MCP server exposing the shared operation metadata."""
	from mcp import types
	from mcp.server import Server
	server = Server("auto")
	exposed = {"_".join(item.words): item for item in EXPOSURES if item.mcp}

	@server.list_tools()
	async def listTools():
		return [types.Tool(name=name, description=item.need, inputSchema=item.schema) for name, item in exposed.items()]

	@server.call_tool()
	async def callTool(name, arguments):
		item = exposed[name]
		arguments = dict(arguments)
		config = {key: arguments.pop(key) for key in CONTEXT_KEYS if key != "plan" and key in arguments}
		plan = arguments.pop("plan", None)
		if plan:
			from auto.features.plan.store import planPath
			plan = planPath(context, plan)
		cancel = threading.Event()
		ctx = context.derive(plan=plan or context.plan, config={**context.config, **config}, question=None,
			inputs=arguments.pop("inputs", {}), params=arguments.pop("params", {}), cancelled=cancel.is_set)
		arguments = coerceArguments(item, arguments)
		arguments = resolveContextArguments(ctx, item, arguments)
		arguments = resolvePayload(ctx, item, arguments)
		try:
			result = await asyncio.to_thread(item.call, ctx, arguments)
			if item.stream:
				stream, count = result, 0
				try:
					while True:
						pending = asyncio.create_task(asyncio.to_thread(next, stream, None))
						try:
							event = await asyncio.shield(pending)
						except asyncio.CancelledError:
							cancel.set()
							await pending
							raise
						if event is None:
							break
						result, count = event, count + 1
						request = server.request_context
						if request.meta and request.meta.progressToken is not None:
							await request.session.send_progress_notification(request.meta.progressToken, count, message=render(event, "json"))
				finally:
					stream.close()
		except AutoError as error:
			result = {"failure": {"kind": error.kind, "reason": str(error)}}
		structured = result if isinstance(result, dict) else {"result": result}
		return types.CallToolResult(content=[types.TextContent(type="text", text=render(result, "markdown"))],
			structuredContent=structured, isError=exitStatus(result, item) != 0)
	return server


def serve(context):
	"""Serve the MCP server over stdio."""
	from mcp.server.stdio import stdio_server
	server = createServer(context)
	async def run():
		async with stdio_server() as (reader, writer):
			await server.run(reader, writer, server.create_initialization_options())
	asyncio.run(run())

# EOF
