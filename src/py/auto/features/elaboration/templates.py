"""Parameter-stage Liquid expansion while retaining runtime expressions as source."""

from __future__ import annotations

import re
import json
import shlex
from io import StringIO

from liquid import Node, RenderContext, StrictUndefined, Tag, Undefined
from liquid.builtin.expressions import Path as LiquidPath
from liquid.builtin.tags.for_tag import ForLoop
from liquid.exceptions import LiquidError
from liquid.output import NullIO

from auto.features.script.templates import AutoEnvironment, directives
from auto.utils.data import AutoError, digest, identity, jsonText


class Unknown:
	"""A value assigned only by a deferred runtime branch."""


class ParameterTemplate:
	"""Keep compiler constants as literal data, including inside deferred branches."""

	def __init__(self, params, maxBytes=4 * 1024 * 1024, *, namespace=None, onFile=None):
		self.params, self.bindings, self.items, self.mode = params, {}, [], "body"
		self.namespace, self.onFile = namespace or {}, onFile
		self.pending = []
		self.deferred = 0
		self.partial, self.possibleProcess = False, False
		self.section = -1
		self.placeholder = "\0" + identity() + "\0"
		self.env = AutoEnvironment(undefined=StrictUndefined)
		self.env.output_stream_limit = maxBytes
		self.env.add_filter("json", jsonText)
		self.env.add_filter("q", lambda value: shlex.quote(str(value)))
		self.env.add_filter("shell_escape", lambda value: shlex.quote(str(value)))
		compiler = self
		for name, original in list(self.env.tags.items()):
			def wrapped(base):
				class ParameterTag(Tag):
					name, block, end = base.name, base.block, base.end
					def parse(inner, stream):
						node = base.parse(stream)
						start = node.token.start_index
						if node.token.kind == "tag":
							start = node.token.source.rfind("{%", 0, start + 1)
						if node.token.kind == "content":
							end = start + len(node.token.value)
						else:
							marker = "}}" if node.token.kind == "output" else "%}"
							end = node.token.source.find(marker, stream.current.start_index) + 2
						return ParameterNode(node, compiler, start, end)
				return ParameterTag
			self.env.add_tag(wrapped(original))
		class AutoTag(Tag):
			name, block = "auto", False
			def parse(inner, stream):
				token = stream.eat("tag")
				return ParameterDirective(token, compiler, int(stream.current.value))
		self.env.add_tag(AutoTag)

	@staticmethod
	def snapshot(value):
		def snapshot(item):
			if isinstance(item, ForLoop):
				return {key: snapshot(item[key]) for key in sorted(item._keys) if not issubclass(type(item[key]), Undefined)}
			if isinstance(item, dict):
				return {key: snapshot(value) for key, value in item.items()}
			if isinstance(item, (list, tuple)):
				return [snapshot(value) for value in item]
			return item
		return json.loads(jsonText(snapshot(value)))

	def constant(self, value):
		value = self.snapshot(value)
		key = "v" + digest(jsonText(value).encode()).removeprefix("sha256:")
		self.bindings[key] = value
		return "params.__template_values." + key

	def known(self, context, name):
		value = context.get([name], token=None)
		return not issubclass(type(value), Undefined) and not isinstance(value, Unknown)

	def paths(self, expression):
		if isinstance(expression, LiquidPath):
			yield expression
		for child in expression.children():
			yield from self.paths(child)

	def unresolved(self, value):
		if issubclass(type(value), Undefined):
			return False
		if isinstance(value, dict):
			return any(self.unresolved(item) for item in value.values())
		if isinstance(value, (list, tuple)):
			return any(self.unresolved(item) for item in value)
		return isinstance(value, Unknown)

	def dependsUnknown(self, context, expression):
		return any(self.unresolved(self.pathValue(context, path)) for path in self.paths(expression))

	def pathValue(self, context, path):
		"""Stop at an unknown ancestor or index instead of inventing a missing key."""
		segments = []
		for segment in path.path:
			if isinstance(segment, LiquidPath):
				segment = self.pathValue(context, segment)
				if type(segment) is Unknown or issubclass(type(segment), Undefined):
					return segment
			segments.append(segment)
			value = context.get(segments, token=path.token)
			if type(value) is Unknown or issubclass(type(value), Undefined):
				return value
		return value

	def ready(self, context, expression):
		for path in self.paths(expression):
			if not self.known(context, path.path[0]) or any(not self.ready(context, child) for child in path.children()):
				return False
			value = self.pathValue(context, path)
			if self.unresolved(value):
				return False
			if issubclass(type(value), Undefined):
				# A missing key in a known parameter map is statically available.
				# Let native evaluation report it only if its branch actually uses it.
				if path.path[0] != "params":
					return False
		return True

	def nodes(self, node, context):
		yield node
		for child in node.children(context, include_partials=False):
			yield from self.nodes(child, context)

	def freeze(self, text, nodes, context, start=0):
		"""Snapshot known outer names; runtime-local declarations keep their scope."""
		allNodes = [part for node in nodes for part in self.nodes(node, context)]
		local = {str(name) for node in allNodes for name in [*node.template_scope(), *node.block_scope()]}
		replacements = {}
		for node in allNodes:
			for expression in node.expressions():
				for path in self.paths(expression):
					name = path.path[0]
					index = path.token.start_index - start
					if name not in local and self.known(context, name) and text[index:index + len(str(name))] == name:
						try:
							replacements[index, index + len(name)] = self.constant(context.get([name], token=path.token))
						except TypeError:
							# Dry validation can have unresolved shell/reference parameters.
							# These stay deferred until application supplies concrete Values.
							continue
		for (left, right), value in sorted(replacements.items(), reverse=True):
			text = text[:left] + value + text[right:]
		return re.sub(r"{%\s*auto (\d+)\s*%}", lambda match: self.items[int(match[1])].source(context, conditional=True), text)

	def argument(self, text, context):
		return self.freeze(text, self.env.from_string(text).nodes, context)

	def renderArgument(self, template, namespace):
		"""Render a path as data, without emitting body-stage literal bindings."""
		previous = self.mode
		try:
			self.mode = "data"
			return template.render(namespace)
		finally:
			self.mode = previous

	def normalized(self, source):
		"""Apply Liquid's lexical whitespace controls once, before either render."""
		parts = []
		for token in self.env.tokenizer()(source):
			if token.kind == "expression":
				continue
			if token.kind == "content":
				raw = re.match(r"{%[-]?\s*raw\b", source[token.start_index:])
				parts.append("{% raw %}" + token.value + "{% endraw %}" if raw else token.value)
			elif token.kind == "tag":
				start = source.rfind("{%", 0, token.start_index + 1)
				end = source.find("%}", token.start_index) + 2
				parts.append(re.sub(r"-?%}$", "%}", re.sub(r"^{%-?", "{%", source[start:end])))
			elif token.kind == "output":
				parts.append(re.sub(r"-?}}$", "}}", re.sub(r"^{{-?", "{{", token.value)))
			elif token.kind == "doc":
				parts.append("{% doc %}" + token.value + "{% enddoc %}")
			else:
				parts.append(token.value)
		return "".join(parts)

	def literal(self, text):
		# A second render must not execute template syntax or authored-line markers
		# that came from parameter data. Ordinary prose remains reviewable text.
		if self.mode in {"body", "structure"} and (any(char in text for char in "{}#`~\r\n") or re.search(r"^\s*[@!]", text)):
			return "{{ " + self.constant(text) + " }}"
		return text

	def emit(self, text, buffer):
		if isinstance(buffer, NullIO):
			self.pending.append(text)
			return 0
		return buffer.write(text)

	def flush(self, buffer):
		if not isinstance(buffer, NullIO) and self.pending:
			text, self.pending = "".join(self.pending), []
			return buffer.write(text)
		return 0

	def value(self, value, literal=False, *, partial=False, slots=None, deferred=None, path=()):
		"""Expand metadata; dry proposals retain only independently known leaves."""
		try:
			result = self._value(value, literal, partial, slots, deferred, path)
			if type(result) is Unknown and deferred is not None:
				deferred.add(path)
			return result
		except (LiquidError, ValueError, TypeError) as error:
			raise AutoError(f"Template parameter expansion failed: {error}") from error

	def _value(self, value, literal=False, partial=False, slots=None, deferred=None, path=()):
		if isinstance(value, dict):
			result = {key: self.value(item, slots is True or isinstance(slots, set) and key in slots,
				partial=partial, deferred=deferred, path=(*path, key)) for key, item in value.items()}
			return {key: item for key, item in result.items() if not isinstance(item, Unknown)}
		if isinstance(value, list):
			result = [self.value(item, literal=False, partial=partial, deferred=deferred, path=(*path, index)) for index, item in enumerate(value)]
			return [item for item in result if not isinstance(item, Unknown)]
		if not isinstance(value, str):
			return value
		names = "|".join(re.escape(str(name)) for name in {"params", *self.namespace})
		if not re.search(r"(?:{{|{%)[^{}]*\b(?:" + names + r")\b", value):
			return value
		template = self.env.from_string(value)
		context = RenderContext(template, globals={**self.namespace, "params": self.params})
		if len(template.nodes) == 1 and isinstance(template.nodes[0], ParameterNode) and template.nodes[0].node.token.kind == "output":
			expression = template.nodes[0].node.expression
			if self.ready(context, expression):
				result = expression.evaluate(context)
				if issubclass(type(result), Undefined):
					str(result)
				result = self.snapshot(result)
				if isinstance(result, dict) and slots:
					result = {key: "\\" + item if isinstance(item, str) and (slots is True or key in slots) else item for key, item in result.items()}
				return "\\" + result if literal and isinstance(result, str) else result
			if partial and self.dependsUnknown(context, expression):
				return Unknown()
		deferred = self.deferred
		result = self.expand(value, mode="data")
		return Unknown() if partial and self.deferred != deferred else result

	def expand(self, text, mode="body", *, partial=False):
		previous = self.mode, self.partial, self.section
		self.mode, self.partial, self.section = mode, partial, -1
		if partial and mode in {"body", "structure"}:
			self.possibleProcess = False
		try:
			source, items = directives(text) if mode in {"body", "structure"} else (text, [])
			source = self.normalized(source)
			sections, section = {}, -1
			for marker in re.finditer(r"(?m)^## |{% auto (\d+) %}", source):
				if marker[1] is None:
					section = marker.start()
				else:
					sections[int(marker[1])] = section
			self.items = []
			for index, (kind, argument, inline) in enumerate(items):
				self.items.append(Directive(self, kind, argument, inline, sections.get(index, -1)))
			template = self.env.from_string(source)
			result = template.render({**self.namespace, "params": self.params})
			if partial and mode in {"body", "structure"}:
				lines = []
				for line in result.splitlines(keepends=True):
					if self.placeholder in line:
						known = line.replace(self.placeholder, "")
						if line.startswith("## ") or known.startswith("## "):
							self.possibleProcess = True
							continue
						line = known
					lines.append(line)
				result = "".join(lines)
			return result
		except (LiquidError, ValueError, TypeError) as error:
			raise AutoError(f"Template parameter expansion failed: {error}") from error
		finally:
			self.mode, self.partial, self.section = previous


class Directive:
	def __init__(self, compiler, kind, argument, inline, section):
		self.compiler, self.kind, self.argument, self.inline, self.section = compiler, kind, argument, inline, section

	def source(self, context, *, conditional=False):
		if self.kind == "literal":
			return self.argument
		reference = None
		if self.kind == "file" and self.compiler.onFile is not None:
			template = self.compiler.env.from_string(self.argument)
			ready = all(self.compiler.ready(context, expression) for node in template.nodes for part in self.compiler.nodes(node, context) for expression in part.expressions())
			if ready or self.compiler.mode == "structure":
				section = self.section if conditional else self.compiler.section
				argument = self.compiler.argument(self.argument, context) if self.compiler.mode == "structure" else self.compiler.renderArgument(template, dict(context.scope))
				captured = self.compiler.onFile(argument, dict(context.scope), self.inline, conditional, section)
				if isinstance(captured, str):
					reference = captured
		argument = reference if reference is not None else self.compiler.argument(self.argument, context)
		marker = "@" if self.kind == "file" else "!?" if self.kind == "check" else "!"
		return "{{" + marker + argument + "}}" if self.inline else marker + argument


class ParameterDirective(Node):
	def __init__(self, token, compiler, index):
		super().__init__(token)
		self.compiler, self.index = compiler, index
		self.blank = False

	def render_to_output(self, context, buffer):
		return buffer.write(self.compiler.items[self.index].source(context))


class ParameterNode(Node):
	def __init__(self, node, compiler, start, end):
		super().__init__(node.token)
		self.node, self.compiler, self.start, self.end = node, compiler, start, end
		self.blank = node.blank

	def expressions(self):
		return self.node.expressions()

	def children(self, context, *, include_partials=True):
		return self.node.children(context, include_partials=include_partials)

	def template_scope(self):
		return self.node.template_scope()

	def block_scope(self):
		return self.node.block_scope()

	def render_to_output(self, context, buffer):
		compiler, node = self.compiler, self.node
		if node.token.kind == "content":
			if compiler.mode in {"body", "structure"} and re.match(r"{%[-]?\s*raw\b", node.token.source[node.token.start_index:]):
				return buffer.write("{% raw %}" + node.text + "{% endraw %}")
			if compiler.mode in {"body", "structure"}:
				# Only headings reached by the selected parameter control flow can
				# separate task lifecycle contexts. Skipped headings have no effect.
				for marker in re.finditer(r"(?m)^## ", node.text):
					position = self.start + marker.start()
					if position == 0 or node.token.source[position - 1] == "\n":
						compiler.section = position
			return node.render(context, buffer)
		kind = node.token.value if node.token.kind == "tag" else "output"
		allNodes = list(compiler.nodes(self, context))
		if compiler.mode == "structure":
			source = node.token.source[self.start:self.end]
			prefix = node.token.source[node.token.source.rfind("\n", 0, self.start) + 1:self.start]
			# Select section structure now; body expressions and controls are
			# compiled only after unused artifact sections have been discarded.
			body = kind in {"output", "echo"} and not prefix.startswith("## ")
			body = body or kind not in {"output", "echo", "assign", "capture"} and not re.search(r"(?m)^## ", source)
			body = body or kind in {"assign", "capture"} and any(issubclass(type(compiler.pathValue(context, path)), Undefined)
				for part in allNodes for expression in part.expressions() for path in compiler.paths(expression))
			# Loop exits affect later headings even when their own branch contains
			# only body text. Keep them in the parameter control-flow pass.
			body = body and not any(part.token.kind == "tag" and part.token.value in {"break", "continue"} for part in allNodes)
			if body:
				return compiler.emit(compiler.freeze(source, [self], context, self.start), buffer)
		ready = all(compiler.ready(context, expression) for expression in node.expressions())
		if kind == "capture":
			ready = ready and all(compiler.ready(context, expression) for part in allNodes for expression in part.expressions())
			ready = ready and not any(isinstance(part, ParameterDirective) and compiler.items[part.index].kind != "literal" for part in allNodes)
			ready = ready and not any(part.token.kind == "tag" and part.token.value in {"include", "render", "liquid", "cycle", "increment", "decrement", "ifchanged", "break", "continue"} for part in allNodes)
			ready = ready and not any(isinstance(part, ParameterNode) and part.node.token.value in {"for", "tablerow"} and part.loopRuntime(context) for part in allNodes)
		if kind == "case":
			ready = ready and all(compiler.ready(context, expression) for block in node.blocks for expression in block.expressions())
		if kind in {"if", "unless"}:
			ready = ready and all(compiler.ready(context, alt.expression) for alt in getattr(node, "alternatives", []))
		if kind in {"include", "render", "liquid", "cycle", "increment", "decrement", "ifchanged"}:
			ready = False
		if kind in {"for", "tablerow"}:
			if self.loopRuntime(context):
				ready = False
		if not ready:
			unknown = (compiler.mode == "data" or compiler.partial) and any(compiler.dependsUnknown(context, expression) for part in allNodes for expression in part.expressions())
			if compiler.mode == "data" and unknown:
				compiler.deferred += 1
			source = node.token.source[self.start:self.end]
			if compiler.partial and compiler.mode in {"body", "structure"} and unknown:
				# An unresolved block must not be split into unbalanced lifecycle
				# fragments or force resolution of sources in an unselected branch.
				if re.search(r"(?m)^## (?:Process[ \t]*$|[^\n]*(?:{{|{%))", source):
					compiler.possibleProcess = True
				text = compiler.placeholder
			else:
				text = compiler.freeze(source, [self], context, self.start)
			for part in allNodes:
				for name in part.template_scope():
					context.assign(str(name), Unknown())
			return compiler.emit(text, buffer)
		if kind in {"assign", "capture"}:
			previous = compiler.mode
			try:
				if kind == "capture":
					compiler.mode = "data"
				node.render(context, StringIO())
			finally:
				compiler.mode = previous
			if compiler.mode not in {"body", "structure"}:
				return 0
			names = dict.fromkeys(str(name) for part in allNodes for name in part.template_scope())
			return sum(compiler.emit("{% assign " + name + " = " + compiler.constant(context.get([name], token=node.token)) + " %}", buffer)
				for name in names if compiler.known(context, name))
		if kind in {"output", "echo"}:
			if compiler.mode == "shell":
				return buffer.write(compiler.freeze(node.token.source[self.start:self.end], [self], context, self.start))
			value = StringIO()
			node.render(context, value)
			return buffer.write(compiler.literal(value.getvalue()))
		return node.render(context, buffer) + compiler.flush(buffer)

	def loopRuntime(self, context):
		compiler = self.compiler
		nodes = list(compiler.nodes(self, context))
		locals = {str(name) for name in self.node.block_scope()}
		assigned = {str(name) for part in nodes for name in part.template_scope()}
		if locals & assigned:
			return True
		if not any(part.token.kind == "tag" and part.token.value in {"break", "continue"} for part in nodes):
			return False
		# An exit can run now only when every enclosing control is known. Loop
		# locals are known per iteration; names assigned in the body stay deferred.
		def deferredExit(part, bound, deferred=False):
			kind = part.token.value if part.token.kind == "tag" else ""
			expressions = list(part.expressions())
			original = part.node if isinstance(part, ParameterNode) else part
			if kind in {"if", "unless"}:
				expressions.extend(alt.expression for alt in getattr(original, "alternatives", []))
			elif kind == "case":
				expressions.extend(expression for block in original.blocks for expression in block.expressions())
			deferred = deferred or kind in {"capture", "include", "render", "liquid", "ifchanged"} or any(
				path.path[0] in assigned or path.path[0] not in bound and not compiler.ready(context, path)
				for expression in expressions for path in compiler.paths(expression))
			if kind in {"break", "continue"}:
				return deferred
			bound = bound | {str(name) for name in part.block_scope()}
			return any(deferredExit(child, bound, deferred) for child in part.children(context, include_partials=False))
		return deferredExit(self, set())
