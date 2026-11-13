"""Module: operations — Shared content resolution and effect boundaries."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from auto.runtime.model import Content, Context
from auto.utils.data import AutoError, digest, jsonText, section

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


PATH_PATTERN = re.compile(r"^(?:\./|\.\./|/|[a-zA-Z][a-zA-Z0-9+.-]*:)")


def isReference(value: object) -> bool:
	"""Tell whether `value` uses a PATH or scheme reference form."""
	return isinstance(value, str) and bool(PATH_PATTERN.match(value))


# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def Resolve(context: Context, ref: str, base: Path | None = None) -> Content:
	"""Resolve references once, keeping exact-byte owner digests and provenance."""
	ref = ref.strip('"')
	target, _, selector = ref.partition("#")
	if target.startswith(("./", "../", "/")):
		path = Path(target)
		path = (path if path.is_absolute() else (base or context.project) / path).resolve()
		try:
			if path.is_dir():
				# A directory reference pins a sorted content manifest, not mtimes.
				manifest = {}
				for file in sorted(path.rglob("*")):
					relative = file.relative_to(path)
					if file.is_file() and not file.is_symlink() and not any(part in {".git", ".auto", ".venv"} for part in relative.parts):
						manifest[str(relative)] = digest(file.read_bytes())
				data = jsonText(manifest).encode()
			else:
				with path.open("rb") as stream:
					data = stream.read(context.maxBytes + 1)
		except OSError as error:
			raise AutoError(f"Cannot resolve {ref}: {error}") from error
		content = Content(str(path), data, digest(data), path)
	else:
		scheme, separator, name = target.partition(":")
		if not separator:
			raise AutoError(f"Expected a PATH reference: {ref}")
		if scheme == "git":
			commands = {"status": "git status --short", "diff": "git diff --no-ext-diff", "log": "git log -5"}
			if name not in commands:
				raise AutoError(f"Unsupported git operation: {name}")
			data = Shell(context, commands[name])[0].encode()
			content = Content(target, data, digest(data), context.project / "git.txt")
		else:
			content = context.runtime.resolve(target)
	if len(content.data) > context.maxBytes:
		raise AutoError(f"Reference exceeds byte limit: {ref}")
	pin = {"ref": content.ref, "digest": content.digest}
	if pin not in context.evidence:
		context.evidence.append(pin)
	return Select(content, selector, ref)


def Select(content: Content, selector: str, ref: str | None = None) -> Content:
	"""Select bytes from resolved content without changing its owner or origin."""
	ref = ref or content.ref + "#" + selector
	if selector:
		text = content.text
		match = re.fullmatch(r"L(\d+)-(\d+)", selector)
		if match:
			start, end = map(int, match.groups())
			lines = text.splitlines(keepends=True)
			if not 1 <= start <= end <= len(lines):
				raise AutoError(f"Invalid line range: {ref}")
			text = "".join(lines[start - 1:end])
		elif content.path.suffix == ".py":
			tree = ast.parse(text)
			matches = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == selector]
			if len(matches) != 1:
				raise AutoError(f"Missing or ambiguous symbol: {ref}")
			node = matches[0]
			start = min([node.lineno, *[item.lineno for item in node.decorator_list]])
			text = "".join(text.splitlines(keepends=True)[start - 1:node.end_lineno])
		elif content.path.suffix == ".md":
			text = section(text, selector)
		else:
			raise AutoError(f"Unsupported symbol selector: {ref}")
		content = Content(content.ref + "#" + selector, text.encode(), content.digest, content.path)
	return content


def Shell(context: Context, command: str, config: dict | None = None, stdin: str = "") -> tuple[str, str, int]:
	"""Run `command` through the shell unless policy disables effects."""
	if not context.allowShell:
		context.variables["_policyDeniedAction"] = command
		raise AutoError("Shell execution is disabled by runtime policy")
	from auto.integration.process import capture
	return capture(context.process, ["/bin/sh", "-c", command], config or {}, stdin)


def Value(context: Context, value: object, base: Path | None = None, config: dict | None = None):
	"""Coerce a literal, shell value or reference into its resolved form."""
	if not isinstance(value, str):
		return value
	if value.startswith("\\"):
		return value[1:]
	if value.startswith("!"):
		stdout, stderr, code = Shell(context, value[1:], config)
		if code:
			raise AutoError(f"Shell value failed ({code}): {stderr}")
		return stdout.removesuffix("\n")
	if isReference(value):
		return Resolve(context, value, base).text
	return value


def Engine(context: Context, name: str):
	"""Resolve an engine name through the runtime's integration registry."""
	return context.runtime.engine(name)


def Knowledge(context: Context):
	"""Resolve the knowledge integration through the runtime."""
	return context.runtime.knowledgeFor()

# EOF
