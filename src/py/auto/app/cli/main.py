"""Module: main — CLI projection of operation metadata, with stable exit codes."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from auto.app.operations import EXPOSURES, coerceArguments, exitStatus, render
from auto.app.cli.frontend import Frontend
from auto.runtime.config import contextFor
from auto.utils.config import RUNTIME_SETTINGS
from auto.utils.data import AutoError, merge, yamlLoad


def assignments(items):
	result = {}
	for item in items:
		key, separator, value = item.partition("=")
		if not separator or not key or key in result:
			raise AutoError("Inputs require unique name=value assignments")
		result[key] = yamlLoad(value) if value else ""
	return result


def parser():
	try:
		packageVersion = version("auto-orchestrator")
	except PackageNotFoundError:
		packageVersion = "unknown"
	root = argparse.ArgumentParser(prog="auto", description=f"Composable agent scripts, tasks and plans (version {packageVersion})")
	root.add_argument("--version", action="version", version=f"%(prog)s {packageVersion}")
	nouns = root.add_subparsers(dest="noun")
	groups = {}
	for exposure in EXPOSURES:
		noun, verb = exposure.words
		if noun not in groups:
			groups[noun] = nouns.add_parser(noun).add_subparsers(dest="verb", required=True)
		command = groups[noun].add_parser(verb, help=exposure.need, description=exposure.need)
		command.set_defaults(exposure=exposure)
		for argument in exposure.arguments:
			command.add_argument(argument)
		for argument in exposure.optional:
			command.add_argument("--" + argument)
		for name in ("plan", "engine", "provider", "model", "agent"):
			command.add_argument("--" + name)
		command.add_argument("--format", choices=("text", "markdown", "json"), default="text")
		properties = exposure.schema["properties"]
		if "inputs" in properties:
			command.add_argument("--input", action="append", default=[])
		if "params" in properties:
			command.add_argument("--param", action="append", default=[])
	mcp = nouns.add_parser("mcp", help="Serve the exposed MCP tools over stdio")
	for name in ("plan", "engine", "provider", "model", "agent"):
		mcp.add_argument("--" + name)
	config = nouns.add_parser("config", help="Show effective configuration and its sources")
	for name in ("plan", "engine", "provider", "model", "agent"):
		config.add_argument("--" + name)
	nouns.add_parser("help", help="Show the operation reference")
	return root


def main(argv=None) -> int:
	cli = parser()
	args = cli.parse_args(argv)
	if args.noun in (None, "help"):
		cli.print_help()
		return 0
	frontend = None
	try:
		sources = {} if args.noun == "config" else None
		context = contextFor(plan=args.plan, engine=args.engine, provider=args.provider, model=args.model, agent=args.agent, sources=sources)
		if args.noun == "config":
			for key in ("engine", "isolated"):
				if key not in context.defaults and key not in context.config:
					sources[("runtime", key)] = "built-in"
			settings = merge({"engine": "pi", "isolated": False}, context.defaults, context.config)
			runtime = {key: settings.pop(key) for key in RUNTIME_SETTINGS if key in settings}
			showConfig({**settings, "runtime": runtime}, sources)
			return 0
		if args.noun == "mcp":
			from auto.app.mcp.main import serve
			serve(context)
			return 0
		exposure = args.exposure
		arguments = {name: getattr(args, name) for name in (*exposure.arguments, *exposure.optional) if getattr(args, name) is not None}
		if hasattr(args, "input"):
			context.inputs = assignments(args.input)
		if hasattr(args, "param"):
			context.params = assignments(args.param)
		arguments = coerceArguments(exposure, arguments, context)
		if exposure.payload:
			name = exposure.payload
			value = arguments[name]
			if value == "-" or name == "elaboration":
				text = sys.stdin.read() if value == "-" else Path(value).read_text()
				arguments[name] = {"content": text, "path": str(Path.cwd() / "stdin.md")} if name == "script" else text
		if args.format == "text":
			frontend = Frontend(args.format)
			context.observe = frontend.event
			context.tick = frontend.tick
		def question(event):
			if frontend:
				frontend.clear()
			return inputQuestion(event)
		context.question = question if sys.stdin.isatty() and sys.stderr.isatty() else None
		result = exposure.call(context, arguments)
		if exposure.name in {"TaskStart", "PlanCreate", "PlanRefine"} and context.question:
			from auto.features.task.operations import RunAnswer, RunGet
			run = result if exposure.name == "TaskStart" else result["run"]
			if exposure.name != "TaskStart":
				context = context.derive(plan=Path(result["plan"]))
			while run["status"] == "Waiting":
				RunAnswer(context, run, context.question(run["question"]), "Answered interactively")
				run = RunGet(context, run)
			if exposure.name == "TaskStart":
				result = run
			else:
				from auto.features.plan.store import journal
				revision = next((row for row in journal(context.plan) if row.get("elaborationRun") == run["id"]), None)
				result = {**result, "run": run, "applied": revision is not None}
				if revision:
					result["revision"] = revision
		if exposure.stream:
			stream = result
			try:
				for result in stream:
					if frontend and exposure.name == "ScriptStart":
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
		return exitStatus(result, exposure.name)
	except (AutoError, OSError, ValueError) as error:
		if frontend and frontend.terminal:
			frontend.error(error)
		else:
			print(str(error), file=sys.stderr)
		if getattr(args, "format", None) == "json":
			print(render({"failure": {"kind": getattr(error, "kind", "system"), "reason": str(error)}}, "json"))
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
	def width(text):
		return sum(0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)
	nameWidth = max([len("SETTING"), *(width(name) for name, _, _ in items)])
	valueWidth = max([len("VALUE"), *(width(value) for _, value, _ in items)])
	color = "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
	def style(text, code):
		return f"\033[{code}m{text}\033[0m" if color else text
	print(style(f"{'SETTING':<{nameWidth}}  {'VALUE':<{valueWidth}}  SOURCE", "1;2"))
	for name, value, source in items:
		print(f"{style(name, '36')}{' ' * (nameWidth - width(name) + 2)}{value}{' ' * (valueWidth - width(value) + 2)}{style(source, '2')}")


def inputQuestion(event):
	print(event["content"], file=sys.stderr)
	if event.get("fields"):
		print("Answer as a YAML mapping keyed by form field id:", event["fields"], file=sys.stderr)
	return yamlLoad(sys.stdin.readline())


if __name__ == "__main__":
	raise SystemExit(main())

# EOF
