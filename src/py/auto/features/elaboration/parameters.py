"""Resolve template parameters against compiled, source-relative settings."""

from auto.features.elaboration.templates import ParameterTemplate, Unknown
from auto.features.script.model import Script
from auto.features.script.templates import Renderer
from auto.utils.config import RUNTIME_KEYS
from auto.utils.data import AutoError, merge


def resolveParameters(context, path, meta, values, resolve, *, namespace=None, inherited=None, dry=False):
	"""Resolve configuration-independent Values before configured shell Values.

	The resolver owns Value origins and snapshot lookup. Inherited settings are
	already compiled data, so only surviving settings authored here are expanded.
	"""
	if not isinstance(values, dict) or any(not isinstance(name, str) for name in values):
		raise AutoError("Template parameters must be string-keyed mappings")
	namespace, inherited = namespace or {}, inherited or {}
	params = dict(namespace.get("params", {}))
	variables = {key: value for key, value in context.variables.items() if key != "_policyDeniedAction"}
	readonly = context.derive(allowShell=False, variables=dict(variables))
	references, shells = [], []
	for name, value in values.items():
		if name in params:
			continue
		if isinstance(value, str) and value.startswith("!"):
			params[name] = Unknown()
			shells.append(name)
			continue
		readonly.variables.pop("_policyDeniedAction", None)
		try:
			params[name] = resolve(readonly, value, {})
		except AutoError:
			if "_policyDeniedAction" not in readonly.variables:
				raise
			params[name] = Unknown()
			references.append(name)

	def configuration(*, partial, preview):
		parent = {key: value for key, value in inherited.items() if key in RUNTIME_KEYS}
		masked = merge(parent, {key: value for key, value in context.config.items() if key in RUNTIME_KEYS}) if preview else parent
		compiler = ParameterTemplate(params, context.maxBytes, namespace=namespace)
		compiled, missing = {}, []
		for key, authored in meta.items():
			if key not in RUNTIME_KEYS:
				continue
			if key in masked and not isinstance(masked[key], dict):
				continue
			higher = masked.get(key, {})
			if isinstance(authored, dict):
				authored = {name: value for name, value in authored.items() if name not in higher}
			value = compiler.value(authored, partial=partial)
			if type(value) is Unknown:
				if key in {"cwd", "env"}:
					missing.append(key)
				continue
			if isinstance(value, dict):
				if key == "env" and isinstance(authored, dict):
					missing.extend("env." + name for name in authored if name not in value)
				value = {name: item for name, item in value.items() if name not in higher}
			compiled[key] = value
		renderer = Renderer(context, Script("", path, compiled), parent)
		return renderer.config, renderer.defaults, missing

	config, defaults, missing = configuration(partial=True, preview=True)
	if not dry:
		for name in references:
			# Reference integrations have their own context; Resolve does not take
			# the script's execution settings. They can provide settings for `!`.
			params[name] = resolve(context.derive(variables=dict(variables)), values[name], {})
		config, defaults, missing = configuration(partial=True, preview=True)
	if shells and missing and (not dry or not references):
		raise AutoError("Circular parameter configuration: shell Values require unresolved " + ", ".join(missing))
	if not dry:
		for name in shells:
			params[name] = resolve(context.derive(variables=dict(variables)), values[name], config)
		_, defaults, _ = configuration(partial=False, preview=False)
	return params, defaults
