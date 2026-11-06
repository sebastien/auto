"""Module: templates — Liquid with lazy, source-relative Auto transclusions."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from liquid import Environment, Node, StrictUndefined, Tag
from liquid.token import TOKEN_TAG

from auto.features.script.model import Script
from auto.features.script.config import validateConfig
from auto.runtime.operations import Resolve, Shell, Value, isReference
from auto.utils.data import AutoError, document, jsonText, merge


class AutoEnvironment(Environment):
	context_depth_limit = 32
	loop_iteration_limit = 10000
	output_stream_limit = 4 * 1024 * 1024


class TransclusionNode(Node):
	def __init__(self, token, renderer, directive):
		super().__init__(token)
		self.renderer, self.directive = renderer, directive
		self.blank = False

	def render_to_output(self, context, buffer):
		return buffer.write(self.renderer.insert(self.directive, dict(context.scope)))


def fence(text: str, language: str = "") -> str:
	marker = "`" * max(3, max((len(m[0]) + 1 for m in re.finditer(r"`+", text)), default=3))
	return f"{marker}{language}\n{text}" + ("" if text.endswith("\n") else "\n") + f"{marker}\n"


def directives(source: str) -> tuple[str, list[tuple[str, str, bool]]]:
	"""Rewrite authored markers to inert numbered tags, never substituted content."""
	items = []
	def add(kind, text, inline):
		items.append((kind, text if kind == "literal" else text.strip(), inline))
		return "{% auto " + str(len(items) - 1) + " %}"
	parts, raw, codeFence = [], False, None
	for line in source.splitlines(keepends=True):
		if "{% raw %}" in line or "{% comment %}" in line:
			raw = True
		if raw:
			parts.append(line)
			if "{% endraw %}" in line or "{% endcomment %}" in line:
				raw = False
			continue
		marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
		if marker:
			if codeFence is None:
				codeFence = marker[1]
			elif marker[1][0] == codeFence[0] and len(marker[1]) >= len(codeFence):
				codeFence = None
			parts.append(line)
			continue
		if codeFence:
			# Fenced examples are data, including Liquid and Auto syntax.
			parts.append(add("literal", line.rstrip("\n"), True) + ("\n" if line.endswith("\n") else ""))
			continue
		stripped = line.strip()
		if stripped.startswith("@") and isReference(stripped[1:].strip('"')):
			parts.append(add("file", stripped[1:], False) + "\n")
		elif stripped.startswith("!") and not stripped.startswith(("![", "!?")):
			parts.append(add("shell", stripped[1:], False) + "\n")
		elif stripped.startswith("!?"):
			parts.append(add("check", stripped[2:], False) + "\n")
		else:
			line = re.sub(r"{{([@!])(.*?)}}", lambda m: add("file" if m[1] == "@" else "shell", m[2], True), line)
			parts.append(re.sub(r"({%[-]?\s*)elif\b", r"\1elsif", line))
	return "".join(parts), items


class Renderer:
	"""Keep origins, caller values and defaults distinct across composition."""

	def __init__(self, context, script: Script, inherited: dict | None = None, stack: tuple = ()):
		self.context, self.script = context, script
		self.stack = stack
		if len(stack) >= context.maxDepth or str(script.path) in stack:
			raise AutoError(f"Recursive transclusion: {script.path}")
		self.defaults = merge(script.meta, inherited or {})
		for key, value in self.defaults.get("output", {}).items():
			if isinstance(value, str) and value.startswith(("./", "../")):
				self.defaults["output"][key] = str((script.path.parent / value).resolve())
		if isinstance(self.defaults.get("cwd"), str) and not Path(self.defaults["cwd"]).is_absolute():
			self.defaults["cwd"] = str((script.path.parent / self.defaults["cwd"]).resolve())
		self.config = merge({"isolated": True}, context.defaults, self.defaults, context.config)
		validateConfig(self.config)
		if "cwd" in self.config:
			path = Path(self.config["cwd"])
			self.config["cwd"] = str(path if path.is_absolute() else script.path.parent / path)
		self.env = AutoEnvironment(undefined=StrictUndefined)
		self.env.output_stream_limit = context.maxBytes
		self.env.add_filter("json", jsonText)
		self.env.add_filter("shell_escape", lambda value: shlex.quote(str(value)))
		self.env.add_filter("q", lambda value: shlex.quote(str(value)))
		source, self.items = directives(script.content)
		renderer = self
		class AutoTag(Tag):
			name = "auto"
			block = False
			def parse(self, stream):
				token = stream.eat(TOKEN_TAG)
				index = int(stream.current.value)
				return TransclusionNode(token, renderer, renderer.items[index])
		self.env.add_tag(AutoTag)
		self.template = self.env.from_string(source, name=str(script.path))

	def bindings(self, inherited: dict | None = None) -> dict:
		inherited = inherited or {}
		inherited = {**inherited, "inputs": {**inherited.get("inputs", {}), **self.context.resolvedInputs}}
		inputs = {key: inherited.get("inputs", {})[key] if key in inherited.get("inputs", {}) else Value(self.context, value, self.context.project if key in self.context.inputs else self.script.path.parent, self.config) for key, value in merge(self.defaults.get("inputs", {}), self.context.inputs).items()}
		params = {key: inherited.get("params", {})[key] if key in inherited.get("params", {}) else Value(self.context, value, self.context.project if key in self.context.params else self.script.path.parent, self.config) for key, value in merge(self.defaults.get("params", {}), self.context.params).items()}
		return {**inputs, "inputs": inputs, "params": params, **self.context.variables}

	def render(self, namespace: dict | None = None) -> str:
		return self.template.render(namespace if namespace is not None else self.bindings())

	def insert(self, directive, namespace: dict) -> str:
		kind, argument, inline = directive
		if kind == "literal":
			return argument
		if self.context.limits:
			self.context.limits.check()
		argument = self.shellArgument(argument, namespace) if kind in {"shell", "check"} else self.env.from_string(argument).render(namespace)
		if kind == "check":
			return "!?" + argument
		if kind == "shell":
			stdout, stderr, code = Shell(self.context, argument, self.config)
			if code:
				raise AutoError(f"Shell transclusion failed ({code}): {stderr}")
			return stdout if inline else fence(stdout)
		content = Resolve(self.context, argument, self.script.path.parent)
		if inline:
			return content.text
		if content.path.suffix != ".md":
			return fence(content.text, content.path.suffix.lstrip("."))
		meta, body = document(content.text)
		if "each" in meta:
			raise AutoError(f"A transcluded script cannot use each: {content.path}")
		# Lifecycle keys (pre, post, apply) belong to the script being run, so
		# they are dropped below with the other non-inherited keys.
		child = Renderer(self.context, Script(body, content.path, meta), self.defaults, (*self.stack, str(self.script.path)))
		childNamespace = child.bindings(namespace)
		# Loop locals are inherited; child defaults fill previously absent inputs.
		childNamespace = merge(childNamespace, namespace)
		result = child.render(childNamespace)
		# A transcluded script supplies otherwise unset execution/output settings.
		keys = {"engine", "provider", "model", "agent", "temperature", "cwd", "env", "prompt", "isolated", "output"}
		self.defaults = merge({key: value for key, value in child.defaults.items() if key in keys}, self.defaults)
		self.config = merge({"isolated": True}, self.context.defaults, self.defaults, self.context.config)
		return result

	def shellArgument(self, source: str, namespace: dict) -> str:
		"""Quote substitutions as shell data, respecting normal single/double quotes."""
		quote, escaped, position, parts = "", False, 0, []
		for match in re.finditer(r"{{(.*?)}}", source):
			literal = source[position:match.start()]
			for char in literal:
				if escaped:
					escaped = False
				elif char == "\\" and quote != "'":
					escaped = True
				elif char == quote:
					quote = ""
				elif not quote and char in "\"'":
					quote = char
			if escaped or any(token in source[:match.start()] for token in ("<<", "$(", "${")):
				raise AutoError("Shell interpolation inside escapes, heredocs or substitutions is unsupported")
			value = self.env.from_string("{{" + re.sub(r"\|\s*(?:q|shell_escape)\s*$", "", match[1]) + "}}").render(namespace)
			value = value.replace("'", "'\"'\"'") if quote == "'" else re.sub(r'([\\$`\"])', r'\\\1', value) if quote == '"' else shlex.quote(value)
			parts.extend((literal, value))
			position = match.end()
		return "".join(parts) + source[position:]

# EOF
