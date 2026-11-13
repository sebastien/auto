"""Module: llm — Ephemeral text streaming through Simon Willison's LLM CLI."""

from __future__ import annotations

import codecs

from auto.integration.process import streamProcess
from auto.runtime.model import Event, RunStatus
from auto.utils.data import AutoError

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


class Llm:
	"""Map supported settings without inventing unavailable capabilities."""

	def start(self, context, request):
		config = request["config"]
		if config.get("tools"):
			raise AutoError("llm runs without tools and cannot enable them; use tools: [] or another engine")
		if config.get("provider"):
			raise AutoError("llm integration does not support a separate provider setting")
		if config.get("agent"):
			raise AutoError("llm does not support named harness agents")
		if any(key in (context.limits.budget if context.limits else {}) for key in ("tokens", "cost")):
			raise AutoError("llm CLI does not report live usage; token/cost limits require a usage-reporting engine")
		args = ["llm", "prompt", "--no-log"]
		for key, flag in (("model", "--model"), ("prompt", "--system")):
			if key in config:
				args += [flag, config[key]]
		if "temperature" in config:
			args += ["-o", "temperature", str(config["temperature"])]
		decoder = codecs.getincrementaldecoder("utf-8")()
		parts, errors = [], []
		stream = streamProcess(context.process, args, config, request["prompt"])
		try:
			for number, chunk in stream:
				if number == 0:
					text = decoder.decode(chunk)
					parts.append(text)
					yield {"type": Event.SCRIPT_MESSAGE, "content": text}
				elif number == 1:
					errors.append(chunk.decode("utf-8", errors="replace"))
				else:
					parts.append(decoder.decode(b"", final=True))
					if chunk:
						raise AutoError("llm failed: " + "".join(errors), "system")
					yield {"type": Event.SCRIPT_ENDED, "status": RunStatus.SUCCEEDED, "output": "".join(parts)}
		finally:
			stream.close()

# EOF
