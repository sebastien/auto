"""Capture authored template companions without executing their shell directives."""

from liquid import Undefined
from liquid.exceptions import LiquidError

from auto.features.elaboration.templates import ParameterTemplate, Unknown
from auto.features.elaboration.parameters import resolveParameters
from auto.features.plan.model import createPlan
from auto.features.script.sources import ScriptSources
from auto.features.script.model import Script
from auto.features.script.config import composedMeta, validateConfig
from auto.features.task.model import createTaskFromText
from auto.runtime.operations import isReference
from auto.runtime.model import Content
from auto.utils.data import AutoError, document, documentText, digest, merge
from auto.utils.config import RUNTIME_KEYS
from auto.utils.references import relocateMeta


class TemplateSources(ScriptSources):
	def __init__(self, context, records=None, *, task=None, parameterContext=None, inherited=None, dry=True):
		super().__init__(context.derive(allowShell=False, variables=dict(context.variables)), records)
		self.records = merge(self.records)
		self.stack = []
		self.task = task
		self.parameterContext, self.inherited, self.dry = parameterContext, inherited, dry
		self.bytes = sum(len(record["text"].encode("utf-8")) for record in self.records.values())

	def enter(self, content):
		identity = Script("", content.path, ref=content.ref).identity
		if identity in self.stack:
			raise AutoError("Recursive template source transclusion: " + content.ref)
		if len(self.stack) >= self.context.maxDepth:
			raise AutoError("Template source depth exceeded: " + content.ref)
		self.stack.append(identity)

	def inheritedDefaults(self, meta, base, parent=None, runtime=None, deferred=()):
		"""Carry source-relative defaults and unresolved contract overrides down."""
		parent = parent or {}
		result = merge(relocateMeta({key: value for key, value in meta.items()
			if key in {*RUNTIME_KEYS, "inputs", "output"}}, base), parent, runtime or {})
		for path in deferred:
			if path[0] != "output":
				continue
			# A partial explicit mapping cannot make an unknown whole mapping
			# known. It may still acquire additional fields after resolution.
			higher = parent
			for name in path:
				if not isinstance(higher, dict) or name not in higher:
					break
				higher = higher[name]
			else:
				if not isinstance(higher, dict):
					continue
			target = result
			for name in path[:-1]:
				if not isinstance(target.get(name), dict):
					target[name] = {}
				target = target[name]
			target[path[-1]] = Unknown()
		return result

	@staticmethod
	def contractDefaults(values, higher):
		"""Select native contract fields that can survive the caller's overrides."""
		if type(higher) is Unknown:
			return {}
		return {key: TemplateSources.contractDefaults(value, higher[key]) if key in higher else value
			for key, value in values.items() if key not in higher or isinstance(value, dict) and isinstance(higher[key], dict)}

	@staticmethod
	def capturedScope(namespace):
		"""Copy serializable lexical values without freezing unresolved inputs."""
		scope = {}
		for name, value in namespace.items():
			if type(value) is Unknown or issubclass(type(value), Undefined):
				continue
			try:
				scope[str(name)] = ParameterTemplate.snapshot(value)
			except (TypeError, ValueError, LiquidError):
				continue
		return scope

	def scopeSource(self, content, ref, namespace, inline, bindings):
		"""Retain lexical locals around a file whose path remains runtime data."""
		meta = {"_template_scope": self.capturedScope(namespace), "_template_bindings": bindings}
		text = documentText(meta, "{{@" + ref + "}}" if inline else "@" + ref + "\n")
		# A distinct virtual source avoids confusing this scope carrier with a
		# real transclusion of the artifact itself; its directory stays the same.
		path = content.path.parent / (".auto-scope-" + digest(text.encode()).removeprefix("sha256:") + ".md")
		ref = self.compiledSource(Content(content.ref, content.data, content.digest, path), text)
		self.records[ref]["scope_wrapper"] = True
		return ref

	def capture(self, ref, base, namespace, *, expand=False, framework=False, inherited=None):
		# Runtime outputs, queries and shell-backed schemes remain runtime data.
		if isinstance(ref, str):
			ref = ref.strip('"')
		if not isReference(ref) or not (framework or ref.startswith(("./", "../", "/", "auto:catalogue/"))):
			return
		key = self.key(ref, base)
		content = self.resolve(ref, base)
		if key not in self.records:
			self.bytes += len(content.data)
			if self.bytes > self.context.maxBytes:
				raise AutoError("Template source snapshots exceed byte limit")
			self.records[key] = {"ref": content.ref, "path": str(content.path), "text": content.text,
				"digest": content.digest, "content_digest": digest(content.data)}
		if not expand or content.path.suffix != ".md":
			return content
		self.records[key]["expand"] = True
		self.enter(content)
		try:
			meta, body = document(content.text)
			meta = composedMeta(meta, content.path)
			params, defaults = self.parameters(meta, content.path, namespace, inherited=inherited)
			compiler = ParameterTemplate(params, self.context.maxBytes, namespace=namespace,
				onFile=lambda ref, scope, inline, conditional, section: self.capture(ref, content.path.parent, scope, expand=not inline, inherited=defaults))
			compiler.expand(body, partial=self.dry)
			self.metadata(meta, content.path.parent, compiler, expand=True)
		finally:
			self.stack.pop()
		return content

	def transclude(self, ref, base, namespace, *, inline=False, inherited=None, conditional=False):
		"""Compile one inclusion with its own immutable lexical scope."""
		if self.task is None:
			raise AutoError("Template source compilation requires its owning task")
		content = self.capture(ref, base, namespace)
		if content is None or inline or content.path.suffix != ".md":
			return None
		self.enter(content)
		try:
			meta, body = document(content.text)
			meta = composedMeta(meta, content.path)
			params, defaults = self.parameters(meta, content.path, namespace, resolveShell=True, inherited=inherited)
			parent = self.inherited if inherited is None else inherited
			compiler = ParameterTemplate(params, self.context.maxBytes, namespace=namespace,
				onFile=lambda ref, scope, inline, conditional, section: self.transclude(ref, content.path.parent, scope, inline=inline, inherited=defaults, conditional=conditional))
			compiled, deferred = {}, set()
			for name, value in meta.items():
				if name in {"params", "_template_sources", "_template_scope", "_template_bindings"}:
					continue
				# A composed script's lifecycle commands do not execute.
				if name == "output" and type((parent or {}).get(name)) is Unknown:
					continue
				if name in {"inputs", "output"} and isinstance(value, dict):
					higher = (parent or {}).get(name, {})
					value = self.contractDefaults(value, higher) if name == "output" else {key: item for key, item in value.items() if key not in higher}
				slots = True if name == "inputs" else {"schema", "template"} if name == "output" else None
				item = compiler.value(value, slots=slots, partial=self.dry, deferred=deferred, path=(name,))
				if not isinstance(item, Unknown):
					compiled[name] = item
			defaults = self.inheritedDefaults(compiled, content.path.parent, parent, defaults, deferred)
			body = compiler.expand(body, partial=self.dry)
			compiled["params"] = {name: meta.get("params", {}).get(name) if type(value) is Unknown else "\\" + value if isinstance(value, str) else value
				for name, value in params.items() if type(value) is not Unknown or name in meta.get("params", {})}
			compiled["_template_scope"] = self.capturedScope(namespace)
			if compiler.bindings or meta.get("_template_bindings"):
				compiled["_template_bindings"] = merge(meta.get("_template_bindings", {}), compiler.bindings)
			self.metadata(compiled, content.path.parent, compiler)
			text = documentText(compiled, body)
			ref = self.compiledSource(content, text)
			if not conditional and parent is not None:
				# Execution passes only runtime/output settings back to its caller.
				# Frozen runtime branches are inspected without changing that caller.
				parent.update(merge({key: value for key, value in defaults.items() if key in {*RUNTIME_KEYS, "output"}}, parent))
			return ref
		finally:
			self.stack.pop()

	def parameters(self, meta, path, namespace, *, resolveShell=False, inherited=None):
		inspection = self.dry or not resolveShell
		context = self.context if inspection else self.parameterContext or self.context
		def resolve(context, value, config):
			self.context.variables.pop("_policyDeniedAction", None)
			try:
				self.capture(value, path.parent, namespace)
			except AutoError:
				if self.context.variables.pop("_policyDeniedAction", None) is None:
					raise
				# Inspection cannot snapshot a shell-backed owner. Resolve the Value
				# below through the caller's policy-controlled context instead.
			return ScriptSources(context, self.records).value(value, path.parent, config)
		return resolveParameters(context, path, meta, meta.get("params", {}), resolve, namespace=namespace,
			inherited=self.inherited if inherited is None else inherited, dry=inspection)

	def compiledSource(self, content, text, *, expand=True):
		data = text.encode("utf-8")
		revision = digest((str(content.path) + "\n" + text).encode()).removeprefix("sha256:")
		ref = f"auto:{createPlan(self.context.plan).id}/task/{self.task}/source/{revision}"
		if ref not in self.records:
			self.bytes += len(data)
			if self.bytes > self.context.maxBytes:
				raise AutoError("Template source snapshots exceed byte limit")
			self.records[ref] = {"ref": ref, "path": str(content.path), "text": text,
				"digest": digest(data), "content_digest": digest(data), "expand": expand,
				"source": {"ref": content.ref, "digest": content.digest}}
		return ref

	def framework(self, meta, base, params, *, compileChecks=False, outputProjection=None, contractOnly=False):
		"""Freeze selected defaults and the artifact checks actually composed."""
		compiler = ParameterTemplate(params, self.context.maxBytes)
		if not contractOnly:
			self.capture(meta.get("profile"), base, {"params": params}, framework=True)
			self.metadata(meta, base, compiler)
		content = self.capture(meta.get("artifact"), base, {"params": params}, framework=True)
		if content is None:
			return {}
		artifactMeta, artifactBody = document(content.text)
		if compileChecks and not contractOnly:
			validateConfig({key: value for key, value in artifactMeta.items() if key in {"post", "eval"}})
		fragmentDefaults = merge(self.inherited or {})
		compiler.onFile = (lambda ref, scope, inline, conditional, section: self.transclude(ref, content.path.parent, scope,
			inline=inline, inherited=fragmentDefaults, conditional=conditional)) if compileChecks else \
			(lambda ref, scope, inline, conditional, section: self.capture(ref, content.path.parent, scope, expand=not inline))
		self.enter(content)
		try:
			compiled = dict(artifactMeta)
			if not isinstance(artifactMeta.get("output", {}), dict):
				raise AutoError("Artifact output must be a mapping")
			output = self.contractDefaults(artifactMeta.get("output", {}),
				meta.get("output", {}) if outputProjection is None else outputProjection)
			if compileChecks:
				deferred = set()
				output = compiler.value(output, slots={"schema", "template"}, partial=self.dry, deferred=deferred, path=("output",))
				if contractOnly:
					self.metadata({"output": output}, content.path.parent, compiler)
					return self.inheritedDefaults({"output": output}, content.path.parent, deferred=deferred)
				compiled["output"] = output if self.dry else merge(artifactMeta.get("output", {}), output)
				for key in ("post", "eval"):
					value = artifactMeta.get(key)
					if key in artifactMeta:
						compiled[key] = compiler.expand(value, mode="shell") if isinstance(value, str) else [compiler.expand(command, mode="shell") for command in value]
			self.metadata({"output": output}, content.path.parent, compiler, expand=not compileChecks)
			if compileChecks:
				# Carry lexical locals across the structure/body passes without
				# opening files in sections that will subsequently be discarded.
				sites = {}
				def deferSource(ref, scope, inline, conditional, section):
					site = "auto:template-site/" + digest(content.data).removeprefix("sha256:") + "/" + str(len(sites))
					if "forloop" in scope:
						# Native loop drops advance after this callback returns.
						scope = {**scope, "forloop": ParameterTemplate.snapshot(scope["forloop"])}
					sites[site] = ref, scope
					return site
				structure = ParameterTemplate(params, self.context.maxBytes, onFile=deferSource)
				artifactBody = structure.expand(artifactBody, mode="structure", partial=self.dry)
				compiler.params = merge(params, {"__template_values": structure.bindings})
				compiler.bindings.update(structure.bindings)
				compose = compiler.onFile
				def composeSource(ref, scope, inline, conditional, section):
					if ref in sites:
						ref, outer = sites[ref]
						scope = merge(scope, outer)
						template = compiler.env.from_string(ref)
						context = template.context_class(template, globals=scope)
						if not all(compiler.ready(context, expression) for node in template.nodes
							for part in compiler.nodes(node, context) for expression in part.expressions()):
							return self.scopeSource(content, ref, scope, inline, compiler.bindings)
						return compose(compiler.renderArgument(template, scope), scope, inline, conditional, section) or self.scopeSource(content, ref, scope, inline, compiler.bindings)
					return compose(ref, scope, inline, conditional, section) or ref
				compiler.onFile = composeSource
			artifact = createTaskFromText(documentText(artifactMeta, artifactBody), content.path)
			parts = {}
			for name in ("Post", "Evaluation"):
				if name in artifact.sections:
					fragmentDefaults = merge(self.inherited or {})
					parts[name] = compiler.expand(artifact.sections[name], partial=self.dry)
			if compileChecks:
				if compiler.bindings:
					compiled["_template_bindings"] = merge(artifactMeta.get("_template_bindings", {}), compiler.bindings)
				text = documentText(compiled, "\n\n".join("## " + name + "\n\n" + body for name, body in parts.items()))
				self.records[self.key(meta["artifact"], base)]["compiled_artifact"] = self.compiledSource(content, text, expand=False)
		finally:
			self.stack.pop()

	def metadata(self, meta, base, compiler, *, expand=False):
		output = meta.get("output", {})
		if expand:
			output = compiler.value(output, slots={"schema", "template"}, partial=self.dry)
		if type(output) is Unknown:
			return
		if not isinstance(output, dict) or any(not isinstance(name, str) for name in output):
			raise AutoError("output must be a string-keyed mapping")
		for value in output.values():
			if isinstance(value, str):
				self.capture(value, base, {"params": compiler.params})
