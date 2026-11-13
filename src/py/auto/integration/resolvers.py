"""Module: resolvers — Owner-configured, bounded reference excerpt commands."""

from __future__ import annotations

import json
import re
import shlex
import shutil

from auto.integration.process import capture
from auto.runtime.model import Content
from auto.utils.data import AutoError, ReferenceUnavailable, digest, jsonText


RESERVED_SCHEMES = frozenset({"auto", "git", "memo", "task", "run", "plan", "gap", "decision", "revision"})


def validateResolvers(value):
	"""Validate configured schemes and commands without executing them."""
	if not isinstance(value, dict):
		raise AutoError("resolvers must be a mapping of schemes to commands")
	for scheme, command in value.items():
		if not isinstance(scheme, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*", scheme) or scheme.lower() in RESERVED_SCHEMES:
			raise AutoError(f"Invalid or reserved resolver scheme: {scheme}")
		if not isinstance(command, str) or not command.strip():
			raise AutoError(f"Resolver {scheme} requires a command")
		try:
			if not shlex.split(command):
				raise ValueError("Empty command")
		except ValueError as error:
			raise AutoError(f"Invalid resolver command for {scheme}: {error}") from error
	return value


def configuredContent(context, ref: str) -> Content:
	"""Read a JSON excerpt using argv, an explicit byte budget and exit 3 semantics."""
	scheme = ref.partition(":")[0]
	configured = validateResolvers(context.defaults.get("resolvers", {}))
	if scheme not in configured:
		raise ReferenceUnavailable(ref, "no resolver is configured")
	args = shlex.split(configured[scheme])
	if "/" in args[0] and not args[0].startswith("/"):
		args[0] = str((context.project / args[0]).resolve())
	env = {**context.defaults.get("env", {}), **context.config.get("env", {})}
	if shutil.which(args[0], path=env.get("PATH", context.environ.get("PATH", ""))) is None:
		raise ReferenceUnavailable(ref, "the resolver executable is not installed or executable")
	try:
		stdout, stderr, code = capture(context.process, [*args, ref, "--budget", str(context.maxBytes)], {"cwd": str(context.project), "env": env})
	except AutoError as error:
		if isinstance(error.__cause__, FileNotFoundError):
			raise ReferenceUnavailable(ref, "the resolver executable or interpreter is missing") from error
		raise
	except UnicodeError as error:
		raise AutoError(f"Resolver {scheme} returned invalid UTF-8", "system") from error
	if code == 3:
		raise ReferenceUnavailable(ref, stderr.strip() or "the source is not available on this host")
	if code:
		raise AutoError(f"Resolver {scheme} failed ({code}): {stderr.strip()}", "system")
	try:
		value = json.loads(stdout)
		if not isinstance(value, dict):
			raise ValueError("Expected a JSON excerpt object")
		if "source" in value and value["source"] != ref:
			raise ValueError("Excerpt source does not match the requested reference")
		jsonText(value)  # Reject non-finite numbers even when the JSON parser accepts them.
	except ValueError as error:
		raise AutoError(f"Resolver {scheme} returned an invalid JSON excerpt: {error}", "system") from error
	data = stdout.encode()
	return Content(ref, data, digest(data), context.project / "reference.json")
