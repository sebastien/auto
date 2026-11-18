"""Module: pi — Pi JSONL event streaming with measured usage."""

from __future__ import annotations

import codecs
import json

from auto.integration.pi.worker import streamWorker
from auto.runtime.model import DETAIL, Event, RunStatus
from auto.runtime.tools import toolSummary
from auto.utils.data import AutoError

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


class Pi:
	"""Run fresh conversations on warm Pi RPC workers, with explicit tools and isolation."""

	def start(self, context, request):
		config = request["config"]
		if config.get("provider") and not config.get("model"):
			raise AutoError("Pi needs a model to select a provider; set model as well")
		if config.get("agent"):
			raise AutoError("Pi CLI does not expose named agents")
		args = ["pi", "--mode", "rpc", "--no-session"]
		if config.get("isolated", False):
			args += ["--no-context-files", "--no-extensions", "--no-skills", "--no-prompt-templates"]
		if config.get("model"):
			args += ["--model", config["model"]]
		if config.get("provider"):
			args += ["--provider", config["provider"]]
		if "tools" in config:
			# An empty list disables every tool; a list is an allowlist of tool names.
			args += ["--tools", ",".join(config["tools"])] if config["tools"] else ["--no-tools"]
		elif config.get("access") == "read":
			# Pi's read-only tools: no edit, write or shell.
			args += ["--tools", "read,grep,find,ls"]
		if "prompt" in config:
			args += ["--system-prompt", config["prompt"] + "\n"]
		decoder, pending, output, settled = codecs.getincrementaldecoder("utf-8")(), "", "", False
		stderr, currentUsage, detail = [], {"tokens": 0, "cost": 0}, dict.fromkeys(DETAIL, 0)
		# Tool calls in flight, by id: what their arguments touch.
		calls = {}
		stream = streamWorker(context.process, args, config, request["prompt"])
		try:
			for number, chunk in stream:
				if number == 1:
					stderr.append(chunk.decode("utf-8", errors="replace"))
					continue
				if number == 2:
					if chunk or pending.strip() or not settled:
						raise AutoError("Pi did not complete cleanly: " + "".join(stderr), "system")
					yield {"type": Event.SCRIPT_ENDED, "status": RunStatus.SUCCEEDED, "output": output}
					return
				pending += decoder.decode(chunk)
				while "\n" in pending:
					line, pending = pending.split("\n", 1)
					if not line.strip():
						continue
					try:
						event = json.loads(line)
					except ValueError as error:
						raise AutoError("Pi returned malformed JSONL", "system") from error
					kind = event["type"]
					message = event.get("message", {})
					assistant = kind in {"message_start", "message_update", "message_end"} and message.get("role", "assistant") == "assistant"
					if kind == "message_start" and assistant:
						currentUsage = {"tokens": 0, "cost": 0}
					# turn_end repeats the assistant after tool-result messages; it is not another charge.
					usage = (event.get("usage") or message.get("usage")) if assistant else None
					if usage and context.limits:
						values = {"tokens": usage.get("totalTokens", 0), "cost": usage.get("cost", {}).get("total", 0)}
						for key, value in values.items():
							context.limits.usage[key] += value - currentUsage[key]
						currentUsage = values
						context.limits.check()
						yield {"type": Event.SCRIPT_META, "name": "usage", "value": dict(context.limits.usage)}
					if kind == "message_update" and event.get("assistantMessageEvent", {}).get("type") == "text_delta":
						yield {"type": Event.SCRIPT_MESSAGE, "content": event["assistantMessageEvent"]["delta"]}
					elif kind == "message_end" and event["message"]["role"] == "assistant":
						message = event["message"]
						final = message.get("usage") or {}
						detail["turns"] += 1
						for key, source in (("input", "input"), ("output", "output"), ("cache_read", "cacheRead"), ("cache_write", "cacheWrite")):
							detail[key] += final.get(source) or 0
						yield {"type": Event.SCRIPT_META, "name": "tokens", "value": dict(detail)}
						yield {"type": Event.SCRIPT_META, "name": "runtime", "value": {key: message[key] for key in ("provider", "model") if key in message}}
						if message.get("stopReason") in {"error", "aborted"}:
							raise AutoError(message.get("errorMessage", "Pi execution failed"), "system")
						output = "".join(part["text"] for part in message["content"] if part["type"] == "text")
					elif kind in {"tool_execution_start", "tool_execution_end"}:
						# Only the end tells the outcome, so it repeats what the start's arguments touched.
						call = event.get("toolCallId")
						if kind == "tool_execution_start":
							calls[call] = toolSummary(event["toolName"], event.get("args"), context.project, config.get("cwd"))
						status = "running" if kind == "tool_execution_start" else "error" if event.get("isError") else "completed"
						yield {"type": Event.SCRIPT_ACTION, "name": event["toolName"], "content": status,
							**(calls.pop(call, {}) if kind == "tool_execution_end" else calls.get(call, {}))}
					elif kind in {"agent_end", "agent_settled"}:
						if event.get("aborted"):
							raise AutoError("Pi aborted", "cancelled")
						settled = True
		finally:
			stream.close()

# EOF
