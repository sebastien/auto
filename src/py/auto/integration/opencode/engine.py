"""Module: opencode — Durable OpenCode V2 sessions and pending forms."""

from __future__ import annotations

import json
import time
from uuid import uuid4

from auto.integration.process import capture
from auto.utils.data import AutoError, jsonText


class OpenCode:
	"""Use the authenticated CLI API transport, with server-side cancellation."""

	def api(self, context, method, path, data=None, config=None):
		args = ["opencode", "api", method, path]
		if data is not None:
			args += ["--data", jsonText(data)]
		stdout, stderr, code = capture(context.process, args, config or {})
		if code:
			raise AutoError(f"OpenCode {method} {path} failed: {stderr}", "system")
		try:
			return json.loads(stdout) if stdout.strip() else {}
		except ValueError as error:
			raise AutoError("OpenCode returned an invalid API response", "system") from error

	def cancel(self, context, request):
		if request.get("session"):
			self.api(context.derive(limits=None, cancelled=lambda: False), "post", f"/api/session/{request['session']}/interrupt", config=request["config"])

	def start(self, context, request):
		config = request["config"]
		if config.get("isolated", True):
			raise AutoError("OpenCode API cannot guarantee ambient-context isolation; use isolated: false or an isolating engine")
		if "prompt" in config:
			raise AutoError("OpenCode session API does not expose system prompt overrides")
		if "temperature" in config:
			yield {"type": "ScriptMeta", "name": "warning", "value": "OpenCode session API does not expose temperature; ignoring temperature setting"}
		complete = False
		try:
			if not request.get("session"):
				request["session"] = "ses" + uuid4().hex
				payload = {"id": request["session"], "location": {"directory": str(config.get("cwd", context.project))}}
				if config.get("agent"):
					payload["agent"] = config["agent"]
				if config.get("provider") or config.get("model"):
					if config.get("provider"):
						provider, model = config["provider"], config.get("model")
						if not model:
							raise AutoError("OpenCode provider requires a model")
					else:
						provider, separator, model = config["model"].partition("/")
						if not separator or not provider or not model:
							raise AutoError("OpenCode model must be provider/model")
					payload["model"] = {"providerID": provider, "id": model}
				self.api(context, "post", "/api/session", payload, config)
				yield {"type": "ScriptMeta", "name": "session", "value": request["session"]}
				request["message"] = "msg" + uuid4().hex
				self.api(context, "post", f"/api/session/{request['session']}/prompt", {"id": request["message"], "text": request["prompt"]}, config)
			base = f"/api/session/{request['session']}"
			if "answer" in request:
				self.api(context, "post", base + f"/form/{request['form']}/reply", {"answer": request.pop("answer")}, config)
			seen = request.setdefault("seen", {})
			accounted = request.setdefault("usage", {"tokens": 0, "cost": 0})
			while True:
				if context.cancelled():
					raise AutoError("Execution cancelled", "cancelled")
				if context.limits:
					context.limits.check()
				forms = self.api(context, "get", base + "/form", config=config).get("data", [])
				if forms:
					form = forms[0]
					request["form"] = form["id"]
					answer = yield {"type": "ScriptQuestion", "name": form["id"], "content": form["title"], "fields": form["fields"]}
					if answer is None:
						raise AutoError("Question requires an answer", "input")
					self.api(context, "post", base + f"/form/{form['id']}/reply", {"answer": answer}, config)
				response = self.api(context, "get", base + "/message?order=asc&limit=100", config=config)
				messages = list(response.get("data", []))
				while response.get("cursor", {}).get("next"):
					from urllib.parse import quote
					response = self.api(context, "get", base + "/message?cursor=" + quote(response["cursor"]["next"], safe=""), config=config)
					messages.extend(response.get("data", []))
				output, cost, tokens = "", 0, 0
				for message in messages:
					if message["type"] != "assistant":
						continue
					text = "".join(part["text"] for part in message.get("content", []) if part["type"] == "text")
					if text:
						output = text
					if message.get("model"):
						yield {"type": "ScriptMeta", "name": "runtime", "value": {"provider": message["model"]["providerID"], "model": message["model"]["id"]}}
					previous = seen.get(message["id"], "")
					if text != previous:
						seen[message["id"]] = text
						yield {"type": "ScriptMessage", "content": text[len(previous):] if text.startswith(previous) else text}
					cost += message.get("cost", 0)
					usage = message.get("tokens", {})
					tokens += sum(usage.get(key, 0) for key in ("input", "output", "reasoning")) + sum(usage.get("cache", {}).values())
				for key, value in (("tokens", tokens), ("cost", cost)):
					if context.limits:
						context.limits.usage[key] += value - accounted[key]
					accounted[key] = value
				yield {"type": "ScriptMeta", "name": "usage", "value": dict(accounted)}
				if context.limits:
					context.limits.check()
				if messages and messages[-1]["type"] == "idle":
					complete = True
					if messages[-1]["outcome"] != "succeeded":
						raise AutoError(jsonText(messages[-1].get("error", messages[-1]["outcome"])), "system")
					yield {"type": "ScriptEnded", "status": "Succeeded", "output": output}
					return
				time.sleep(.2)
		finally:
			if not complete and not context.variables.get("_detach"):
				self.cancel(context, request)

# EOF
