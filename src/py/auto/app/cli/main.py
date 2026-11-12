"""Module: main — CLI projection of operation metadata, with stable exit codes."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from auto.app.operations import (DESCRIPTORS, EXPOSURES, Descriptor, coerceArguments, exitStatus, render, resolveContextArguments, resolvePayload,
	resumeResult)
from auto.app.cli import prompt
from auto.app.cli.frontend import Frontend, width
from auto.runtime.config import CONTEXT_KEYS, RUNTIME_DEFAULTS, contextFor
from auto.utils.config import RUNTIME_KEYS
from auto.utils.data import AutoError, merge, yamlLoad

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


class ArgumentParser(argparse.ArgumentParser):
	"""Include the relevant help command in argument errors."""

	def parse_args(self, args=None, namespace=None):
		parsed, extras = self.parse_known_args(args, namespace)
		if extras:
			command = getattr(parsed, "argumentParser", self)
			recovery = []
			for index, option in enumerate(extras):
				if option == "--set" and index + 1 < len(extras):
					key, separator, value = extras[index + 1].partition("=")
					if separator and key in CONTEXT_KEYS:
						recovery.append(f"Use --{key} {shlex.quote(value)} instead of --set {shlex.quote(extras[index + 1])}.")
			command.error("unrecognized arguments: " + " ".join(extras), recovery=" ".join(recovery))
		return parsed

	def error(self, message, *, recovery=""):
		guidance = f"Run {self.prog} --help for valid arguments and options."
		super().error(f"{message}\nRecovery: {recovery + ' ' if recovery else ''}{guidance}")


# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def assignments(items):
	"""Parse repeated `name=value` arguments into a mapping."""
	result = {}
	for item in items:
		key, separator, value = item.partition("=")
		if not separator or not key or key in result:
			raise AutoError("Inputs require unique name=value assignments")
		result[key] = yamlLoad(value) if value else ""
	return result


def parser():
	"""Build the CLI argument parser from operation metadata."""
	try:
		packageVersion = version("auto-orchestrator")
	except PackageNotFoundError:
		packageVersion = "unknown"
	root = ArgumentParser(prog="auto", description=f"Composable agent scripts, tasks and plans (version {packageVersion})")
	root.epilog = "Use auto PATH.md [OPTIONS] as shorthand for auto script run PATH.md [OPTIONS]."
	root.add_argument("--version", action="version", version=f"%(prog)s {packageVersion}")
	nouns = root.add_subparsers(dest="noun")
	groups = {}
	for exposure in EXPOSURES:
		noun, verb = exposure.words
		if noun not in groups:
			group = nouns.add_parser(noun)
			group.set_defaults(argumentParser=group)
			group.add_argument("--no-input", action="store_true", help="Never prompt for missing values")
			groups[noun] = group.add_subparsers(dest="verb")
		# Required arguments are checked after parsing, so that they can be prompted for (see spec-011).
		usage = " ".join(("auto", noun, verb, *exposure.arguments, "[options]"))
		command = groups[noun].add_parser(verb, help=exposure.need, description=exposure.need, usage=usage)
		command.set_defaults(exposure=exposure, argumentParser=command)
		for argument in exposure.arguments:
			command.add_argument(argument, nargs="?", help=DESCRIPTORS.get(argument, Descriptor()).help)
		for argument in exposure.optional:
			command.add_argument("--" + argument, help=DESCRIPTORS.get(argument, Descriptor()).help)
		for name in CONTEXT_KEYS:
			command.add_argument("--" + name)
		command.add_argument("--format", choices=("text", "markdown", "json"), default="text")
		command.add_argument("--no-input", action="store_true", help="Never prompt for missing values")
		properties = exposure.schema["properties"]
		if "inputs" in properties:
			command.add_argument("--input", action="append", default=[])
		if "params" in properties:
			command.add_argument("--param", action="append", default=[])
	mcp = nouns.add_parser("mcp", help="Serve the exposed MCP tools over stdio")
	mcp.set_defaults(argumentParser=mcp)
	for name in CONTEXT_KEYS:
		mcp.add_argument("--" + name)
	config = nouns.add_parser("config", help="Show effective configuration and its sources")
	config.set_defaults(argumentParser=config)
	for name in CONTEXT_KEYS:
		config.add_argument("--" + name)
	nouns.add_parser("help", help="Show the operation reference")
	return root


def main(argv=None) -> int:
	"""Run the CLI and return its stable exit status."""
	cli = parser()
	argv = list(sys.argv[1:] if argv is None else argv)
	if argv and not argv[0].startswith("-") and ("/" in argv[0] or Path(argv[0].partition("#")[0]).suffix == ".md"):
		argv = ["script", "run", *argv]
	args = cli.parse_args(argv)
	if args.noun in (None, "help"):
		cli.print_help()
		return 0
	prompter = prompt.session(argv)
	frontend = None
	try:
		if args.noun in {exposure.noun for exposure in EXPOSURES} and getattr(args, "verb", None) is None:
			verbs = [(exposure.verb, exposure.need) for exposure in EXPOSURES if exposure.noun == args.noun]
			if not prompter:
				args.argumentParser.error("a verb is required", recovery="Choose one of: " + ", ".join(verb for verb, _ in verbs) + ".")
			argv.insert(argv.index(args.noun) + 1, prompter.choose("auto " + args.noun, verbs, help="Choose a command (Tab to complete)"))
			args = cli.parse_args(argv)
		sources = {} if args.noun == "config" else None
		context = contextFor(plan=args.plan, engine=args.engine, provider=args.provider, model=args.model, agent=args.agent, sources=sources)
		if args.noun == "config":
			for key in ("engine", "isolated"):
				if key not in context.defaults and key not in context.config:
					sources[("runtime", key)] = "built-in"
			settings = merge(RUNTIME_DEFAULTS, context.defaults, context.config)
			runtime = {key: settings.pop(key) for key in RUNTIME_KEYS if key in settings}
			showConfig({**settings, "runtime": runtime}, sources)
			return 0
		if args.noun == "mcp":
			from auto.app.mcp.main import serve
			serve(context)
			return 0
		exposure = args.exposure
		context = complete(args, exposure, context, prompter)
		arguments = {name: getattr(args, name) for name in (*exposure.arguments, *exposure.optional) if getattr(args, name) is not None}
		if exposure.payloadKind == "script":
			script = arguments[exposure.payload]
			if script != "-" and "\n" not in script and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", script):
				path, separator, selector = script.partition("#")
				arguments[exposure.payload] = str(Path(path).resolve()) + separator + selector
		if hasattr(args, "input"):
			context.inputs = assignments(args.input)
		if hasattr(args, "param"):
			context.params = assignments(args.param)
		arguments = coerceArguments(exposure, arguments)
		arguments = resolveContextArguments(context, exposure, arguments)
		arguments = resolvePayload(context, exposure, arguments)
		if prompter and exposure.payloadKind == "script":
			for name in missingInputs(context, arguments[exposure.payload]):
				value = prompter.ask(name, Descriptor("value", "Script input, as a YAML value"), context, need=commandNeed(exposure), optional=True, check=yamlLoad)
				if value is not None:
					context.inputs[name] = yamlLoad(value)
					args.input.append(f"{name}={value}")
		if prompter and prompter.filled:
			prompter.say("› " + commandLine(args, exposure), "fg:ansicyan")
			if exposure.effects and not prompter.confirm("Run?"):
				return 130
		if args.format == "text":
			frontend = Frontend(args.format)
			context.observe = frontend.event
			context.tick = frontend.tick
		def question(event):
			if frontend:
				frontend.clear()
			return prompter.question(event)
		context.question = question if prompter else None
		result = exposure.call(context, arguments)
		result = resumeResult(context, exposure, result, context.question)
		if exposure.stream:
			stream = result
			try:
				for result in stream:
					if frontend and exposure.messages:
						frontend.scriptEvent(result)
					else:
						print(render(result, args.format), flush=True)
			finally:
				stream.close()
		else:
			if frontend:
				frontend.result(result)
			else:
				print(render(result, args.format))
		return exitStatus(result, exposure)
	except (AutoError, OSError, ValueError) as error:
		helpCommand = "auto " + " ".join(str(getattr(args, name)) for name in ("noun", "verb") if getattr(args, name, None)) + " --help"
		recovery = getattr(error, "recovery", None) or f"Run {helpCommand} to check arguments and options; run auto config to inspect effective settings and their sources."
		if frontend and frontend.terminal:
			frontend.error(error)
		else:
			print(str(error), file=sys.stderr)
		print("Recovery: " + recovery, file=sys.stderr)
		if getattr(args, "format", None) == "json":
			print(render({"failure": {"kind": getattr(error, "kind", "system"), "reason": str(error), "recovery": recovery}}, "json"))
		return 1
	except KeyboardInterrupt:
		return 130
	finally:
		if frontend:
			frontend.close()


def showConfig(config: dict, sources: dict, prefix: tuple = ()) -> None:
	"""Print effective configuration leaves with their winning sources."""
	def rows(values, parent):
		for key, value in sorted(values.items(), key=lambda item: str(item[0])):
			path = (*parent, key)
			if isinstance(value, dict) and value:
				yield from rows(value, path)
			else:
				name = ".".join(str(part) if re.fullmatch(r"[A-Za-z0-9_-]+", str(part)) else json.dumps(str(part), ensure_ascii=False) for part in path)
				yield name, json.dumps(value, ensure_ascii=False, default=str), sources.get(path, "unknown")
	items = list(rows(config, prefix))
	if not sys.stdout.isatty():
		for name, value, source in items:
			print(f"{name} = {value} # source={source}")
		return
	nameWidth = max([len("SETTING"), *(width(name) for name, _, _ in items)])
	valueWidth = max([len("VALUE"), *(width(value) for _, value, _ in items)])
	color = "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
	def style(text, code):
		return f"\033[{code}m{text}\033[0m" if color else text
	print(style(f"{'SETTING':<{nameWidth}}  {'VALUE':<{valueWidth}}  SOURCE", "1;2"))
	for name, value, source in items:
		print(f"{style(name, '36')}{' ' * (nameWidth - width(name) + 2)}{value}{' ' * (valueWidth - width(value) + 2)}{style(source, '2')}")


def commandNeed(exposure) -> str:
	return f"auto {exposure.noun} {exposure.verb} — {exposure.need}"


def complete(args, exposure, context, prompter):
	"""Fill the gaps of a command by prompting, or report them as usage errors (see spec-011)."""
	def gathered():
		return {name: getattr(args, name) for name in (*exposure.arguments, *exposure.optional) if getattr(args, name) is not None}
	def ask(name, **options):
		check = lambda text: coerceArguments(exposure, {name: text})
		return prompter.ask(name, DESCRIPTORS.get(name, Descriptor()), context, gathered(), need=commandNeed(exposure), check=check, **options)
	def repair(name):
		value, descriptor = getattr(args, name), DESCRIPTORS.get(name, Descriptor())
		if prompter and value is not None and descriptor.kind == "choice":
			values = [candidate for candidate, _ in prompt.candidatesFor(name, descriptor, context, gathered())]
			if values and value not in values:
				prompter.say(f"! Unknown {name}: {value}", "fg:ansiyellow")
				setattr(args, name, ask(name, default=value))
	if prompter and exposure.needsPlan and context.plan is None:
		args.plan = ask("plan")
		context = contextFor(plan=args.plan, engine=args.engine, provider=args.provider, model=args.model, agent=args.agent)
	for name in exposure.arguments:
		if getattr(args, name) is not None:
			repair(name)
		elif prompter:
			setattr(args, name, ask(name))
		else:
			descriptor = DESCRIPTORS.get(name, Descriptor())
			values = [value for value, _ in prompt.candidatesFor(name, descriptor, context, gathered())]
			recovery = f"Give {name} ({descriptor.help.lower() or 'a value'})" + (": " + ", ".join(values[:5]) + ("…" if len(values) > 5 else "") if values else "") + "."
			args.argumentParser.error(f"the following arguments are required: {name}", recovery=recovery)
	for name in exposure.optional:
		repair(name)
	if prompter and prompter.filled:
		for name in exposure.ask:
			if getattr(args, name) is None:
				setattr(args, name, ask(name, optional=True))
	return context


def missingInputs(context, script) -> list:
	"""Return the inputs a script uses without being given or declaring them."""
	from auto.features.script.operations import ScriptInputs, loadScript
	try:
		meta = loadScript(context, script).meta
		names = ScriptInputs(context, script)["inputs"]
	except (AutoError, OSError, ValueError):
		return []
	declared = {*(meta.get("inputs") or {}), *(meta.get("each") or {})}
	return [name for name in names if name not in context.inputs and name not in declared]


def commandLine(args, exposure) -> str:
	"""Return the command equivalent to a filled invocation."""
	words = ["auto", exposure.noun, exposure.verb, *(getattr(args, name) for name in exposure.arguments)]
	for name in (*exposure.optional, *CONTEXT_KEYS):
		if getattr(args, name, None) is not None:
			words += ["--" + name, getattr(args, name)]
	for flag in ("input", "param"):
		for item in getattr(args, flag, []):
			words += ["--" + flag, item]
	if args.format != "text":
		words += ["--format", args.format]
	return " ".join(shlex.quote(str(word)) for word in words)


if __name__ == "__main__":
	raise SystemExit(main())

# EOF
