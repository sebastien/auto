"""Module: fx — Ephemeral execution through Vercel's fx CLI."""

from __future__ import annotations

import json

from auto.integration.process import capture
from auto.runtime.model import Event, RunStatus
from auto.utils.data import AutoError

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


class Fx:
	"""Translate one final JSON result without claiming live accounting or policy."""

	def start(self, context, request):
		config = request["config"]
		if "tools" in config:
			raise AutoError("fx ask cannot restrict tools per run; use an engine supporting tools")
		if config.get("agent"):
			raise AutoError("fx ask does not support named agent profiles")
		if config.get("isolated", False):
			raise AutoError("fx ask cannot guarantee ambient-context isolation; use isolated: false")
		if "temperature" in config:
			raise AutoError("fx ask does not support temperature overrides")
		if any(key in (context.limits.budget if context.limits else {}) for key in ("tokens", "cost")):
			raise AutoError("fx ask does not report live or complete usage; token/cost limits require a usage-reporting engine")
		args = ["fx", "ask", "--json", "--no-save"]
		if "prompt" in config:
			args += ["--system", config["prompt"]]
		env = dict(config.get("env", {}))
		for key in ("model", "provider"):
			if config.get(key):
				env["FX_" + key.upper()] = config[key]
		stdout, stderr, code = capture(context.process, args, {**config, "env": env}, request["prompt"])
		if code in (130, 143, -2, -15):
			raise AutoError("fx execution interrupted: " + stderr, "cancelled")
		try:
			result = json.loads(stdout)
		except ValueError as error:
			raise AutoError(f"fx failed ({code}): {stderr}" if code else "fx returned malformed JSON", "system") from error
		if not isinstance(result, dict) or type(result.get("exit_code")) is not int:
			raise AutoError("fx returned an invalid JSON result", "system")
		usage = result.get("usage", {})
		if not isinstance(usage, dict) or any(value is not None and (type(value) is not int or value < 0)
			for value in (usage.get("input_tokens"), usage.get("output_tokens"))):
			raise AutoError("fx returned invalid token usage", "system")
		if context.limits:
			# fx excludes helpers and subagents; these are reported main-agent tokens only.
			context.limits.usage["tokens"] += sum(usage.get(key) or 0 for key in ("input_tokens", "output_tokens"))
			context.limits.check()
			if any(usage.get(key) is not None for key in ("input_tokens", "output_tokens")):
				yield {"type": Event.SCRIPT_META, "name": "usage", "value": dict(context.limits.usage)}
		if any(usage.get(key) is not None for key in ("input_tokens", "output_tokens")):
			# fx does not report turns or cache use: those stay unknown.
			yield {"type": Event.SCRIPT_META, "name": "tokens", "value": {"input": usage.get("input_tokens") or 0, "output": usage.get("output_tokens") or 0}}
		if code or result["exit_code"]:
			raise AutoError(f"fx failed ({code or result['exit_code']}): {result.get('error') or stderr or 'request failed'}", "system")
		if not isinstance(result.get("final_output"), str):
			raise AutoError("fx returned no textual final_output", "system")
		yield {"type": Event.SCRIPT_META, "name": "runtime", "value": {
			key: result[key] for key in ("model", "resolved_provider") if isinstance(result.get(key), str)}}
		tools = result.get("tool_calls", [])
		if not isinstance(tools, list) or any(not isinstance(tool, dict) or
			not all(isinstance(tool.get(key), str) for key in ("name", "status")) for tool in tools):
			raise AutoError("fx returned invalid tool_calls", "system")
		for tool in tools:
			yield {"type": Event.SCRIPT_ACTION, "name": tool["name"], "content": tool["status"]}
		yield {"type": Event.SCRIPT_MESSAGE, "content": result["final_output"]}
		yield {"type": Event.SCRIPT_ENDED, "status": RunStatus.SUCCEEDED, "output": result["final_output"]}

# EOF
