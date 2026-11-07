"""Module: pi — Pi JSONL event streaming with measured usage."""

from __future__ import annotations

import codecs
import json

from auto.integration.process import streamProcess
from auto.runtime.model import Event, RunStatus
from auto.utils.data import AutoError

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


class Pi:
	"""Run a single ephemeral session, preserving native tools and explicit isolation."""

	def start(self, context, request):
		config = request["config"]
		if config.get("provider"):
			raise AutoError("Pi integration does not support a separate provider setting")
		if config.get("agent"):
			raise AutoError("Pi CLI does not expose named agents")
		args = ["pi", "--mode", "json", "--no-session"]
		if config.get("isolated", False):
			args += ["--no-context-files", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-mcp", "--no-approve"]
		if config.get("model"):
			args += ["--model", config["model"]]
		if "prompt" in config:
			args += ["--system-prompt", config["prompt"] + "\n"]
		decoder, pending, output, settled = codecs.getincrementaldecoder("utf-8")(), "", "", False
		stderr, currentUsage = [], {"tokens": 0, "cost": 0}
		stream = streamProcess(context.process, args, config, request["prompt"])
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
					if kind == "message_start":
						currentUsage = {"tokens": 0, "cost": 0}
					usage = event.get("usage") or event.get("message", {}).get("usage")
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
						yield {"type": Event.SCRIPT_META, "name": "runtime", "value": {key: message[key] for key in ("provider", "model") if key in message}}
						if message.get("stopReason") in {"error", "aborted"}:
							raise AutoError(message.get("errorMessage", "Pi execution failed"), "system")
						output = "".join(part["text"] for part in message["content"] if part["type"] == "text")
					elif kind.startswith("tool_execution"):
						yield {"type": Event.SCRIPT_ACTION, "name": event["toolName"], "content": kind}
					elif kind == "agent_settled":
						if event.get("aborted"):
							raise AutoError("Pi aborted", "cancelled")
						settled = True
		finally:
			stream.close()

# EOF
