"""Module: opencode — Durable OpenCode V2 sessions and pending forms."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from auto.integration.process import capture
from auto.runtime.model import Event, RunStatus
from auto.utils.data import AutoError, jsonText

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def permissionEffect(action, resources, project, cwd):
	"""Map known tool actions; unknown plugin actions require confirmation."""
	known = {"read": "repo.read", "glob": "repo.read", "grep": "repo.read", "edit": "repo.write",
		"shell": "shell.run", "webfetch": "net.read", "websearch": "net.read"}
	if action in {"read", "edit"} and any(not (Path(cwd) / resource).resolve().is_relative_to(Path(project).resolve()) for resource in resources):
		return "engine.external-" + action
	return known.get(action, "engine.tool-" + (re.sub(r"[^a-z0-9_-]+", "-", action.lower()).strip("-") or "unknown"))

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


class OpenCode:
	"""Use the authenticated V2 client bridge, with server-side cancellation."""

	def api(self, context, method, path, data=None, config=None):
		args = ["node", str(Path(__file__).with_name("bridge.mjs"))]
		stdin = jsonText({"method": method, "path": path, "data": data})
		stdout, stderr, code = capture(context.process, args, config or {}, stdin)
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
		if "tools" in config:
			raise AutoError("OpenCode API cannot restrict tools per run; use agent permissions, or an engine supporting tools")
		if config.get("isolated", False):
			raise AutoError("OpenCode API cannot guarantee ambient-context isolation; use isolated: false or an isolating engine")
		if "prompt" in config:
			raise AutoError("OpenCode session API does not expose system prompt overrides")
		complete = False
		try:
			if not request.get("session"):
				request["session"] = "ses" + uuid4().hex
				payload = {"id": request["session"], "location": {"directory": str(config.get("cwd", context.project))}}
				if "policy" in request:
					# Subagents use independent rules and cannot inherit this session's policy.
					payload["permissions"] = [
						{"action": "*", "resource": "*", "effect": "ask"},
						{"action": "question", "resource": "*", "effect": "allow"},
						{"action": "subagent", "resource": "*", "effect": "deny"},
					]
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
				yield {"type": Event.SCRIPT_META, "name": "session", "value": request["session"]}
				request["message"] = "msg_" + uuid4().hex
				self.api(context, "post", f"/api/session/{request['session']}/prompt", {"id": request["message"], "text": request["prompt"]}, config)
			base = f"/api/session/{request['session']}"
			if "answer" in request:
				if "permission" in request:
					decision = "once" if request.pop("answer") is True else "reject"
					self.api(context, "post", base + f"/permission/{request.pop('permission')}/reply", {"decision": decision}, config)
				else:
					self.api(context, "post", base + f"/form/{request['form']}/reply", {"answer": request.pop("answer")}, config)
			seen = request.setdefault("seen", {})
			accounted = request.setdefault("usage", {"tokens": 0, "cost": 0})
			# Keep the last text seen across polls: a session can be reported idle
			# just before the final message content becomes readable.
			output, settleUntil = "", None
			while True:
				if context.cancelled():
					raise AutoError("Execution cancelled", "cancelled")
				if context.limits:
					context.limits.check()
				if "policy" in request:
					permissions = self.api(context, "get", base + "/permission", config=config).get("data", [])
					if permissions:
						permission = permissions[0]
						request["permission"] = permission["id"]
						action = permission["action"]
						resources = permission.get("resources", [])
						effect = permissionEffect(action, resources, context.project, config.get("cwd", context.project))
						answer = yield {"type": Event.POLICY_REQUEST, "effect": effect, "action": action + ": " + ", ".join(resources)}
						self.api(context, "post", base + f"/permission/{request.pop('permission')}/reply", {"decision": "once" if answer is True else "reject"}, config)
				forms = self.api(context, "get", base + "/form", config=config).get("data", [])
				if forms:
					form = forms[0]
					request["form"] = form["id"]
					answer = yield {"type": Event.SCRIPT_QUESTION, "name": form["id"], "content": form["title"], "fields": form["fields"]}
					if answer is None:
						raise AutoError("Question requires an answer", "input")
					self.api(context, "post", base + f"/form/{form['id']}/reply", {"answer": answer}, config)
				response = self.api(context, "get", base + "/message?order=asc&limit=100", config=config)
				messages = list(response.get("data", []))
				while response.get("cursor", {}).get("next"):
					response = self.api(context, "get", base + "/message?cursor=" + quote(response["cursor"]["next"], safe=""), config=config)
					messages.extend(response.get("data", []))
				step, cost, tokens = "", 0, 0
				lastAssistant, lastText = None, ""
				for message in messages:
					if message["type"] != "assistant":
						continue
					lastAssistant = message
					text = "".join(part["text"] for part in message.get("content", []) if part["type"] == "text")
					lastText = text or seen.get(message["id"], "")
					if lastText:
						step = lastText
					if message.get("model"):
						yield {"type": Event.SCRIPT_META, "name": "runtime", "value": {"provider": message["model"]["providerID"], "model": message["model"]["id"]}}
					previous = seen.get(message["id"], "")
					if text != previous:
						seen[message["id"]] = text
						yield {"type": Event.SCRIPT_MESSAGE, "content": text[len(previous):] if text.startswith(previous) else text}
					cost += message.get("cost", 0)
					usage = message.get("tokens", {})
					tokens += sum(usage.get(key, 0) for key in ("input", "output", "reasoning")) + sum(usage.get("cache", {}).values())
				if step:
					output = step
				for key, value in (("tokens", tokens), ("cost", cost)):
					if context.limits:
						context.limits.usage[key] += value - accounted[key]
					accounted[key] = value
				yield {"type": Event.SCRIPT_META, "name": "usage", "value": dict(accounted)}
				if context.limits:
					context.limits.check()
				if messages and messages[-1]["type"] == "idle":
					# A session can report idle while its final message is still
					# content-less in the listing; settle briefly before giving up.
					if not lastText and lastAssistant is not None:
						settleUntil = settleUntil if settleUntil is not None else time.monotonic() + 5
						if time.monotonic() < settleUntil:
							time.sleep(.2)
							continue
					complete = True
					if messages[-1]["outcome"] != "succeeded":
						raise AutoError(jsonText(messages[-1].get("error", messages[-1]["outcome"])), "system")
					yield {"type": Event.SCRIPT_ENDED, "status": RunStatus.SUCCEEDED, "output": output}
					return
				time.sleep(.2)
		finally:
			if not complete and not context.variables.get("_detach"):
				self.cancel(context, request)

# EOF
