"""Module: prompt — Interactive completion of incomplete commands (see spec-011)."""

from __future__ import annotations

import os
import sys
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import quote

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from auto.app.operations import Descriptor
from auto.runtime.model import Event
from auto.utils.data import AutoError, jsonText, yamlLoad

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def interactive(argv) -> bool:
	"""Tell whether a human can be prompted for this invocation."""
	if find_spec("prompt_toolkit") is None or "--no-input" in argv or os.environ.get("AUTO_NO_INPUT") or os.environ.get("CI"):
		return False
	if any(item == "--format=json" or item == "--format" and argv[index + 1:index + 2] == ["json"] for index, item in enumerate(argv)):
		return False
	return sys.stdin.isatty() and sys.stderr.isatty() and os.environ.get("TERM") != "dumb"


def session(argv) -> Prompter | None:
	"""Return a prompter when the invocation is interactive."""
	return Prompter() if interactive(argv) else None


def candidatesFor(name: str, descriptor: Descriptor, context, arguments: dict) -> list:
	"""Return `(value, description)` candidates, or none when they cannot be listed."""
	if not descriptor.candidates:
		return []
	try:
		return list(descriptor.candidates(context, arguments))
	except (AutoError, OSError, ValueError):
		return []


def fuzzy(query: str, text: str) -> tuple | None:
	"""Score `text` against space-separated subsequence terms, lower is better; None when it does not match."""
	text, spread, first = text.lower(), 0, len(text)
	for term in query.lower().split():
		start = position = -1
		for char in term:
			position = text.find(char, position + 1)
			if position < 0:
				return None
			start = position if start < 0 else start
		spread, first = spread + position - start, min(first, start)
	return spread, first

# -----------------------------------------------------------------------------
#
# API
#
# -----------------------------------------------------------------------------


class Prompter:
	"""Ask for missing values on stderr, with completion and validation."""

	def __init__(self, *, input=None, output=None, history: bool = True):
		self.input = input
		self._output = output
		self.history = history
		self.filled = False

	@property
	def output(self):
		# prompt_toolkit is imported on first use, so that complete commands start fast.
		if self._output is None:
			from prompt_toolkit.output import create_output
			self._output = create_output(stdout=sys.stderr)
		return self._output

	def say(self, text: str, style: str = ""):
		from prompt_toolkit import print_formatted_text
		from prompt_toolkit.formatted_text import FormattedText
		print_formatted_text(FormattedText([(style, text)]), output=self.output, color_depth=self.depth())

	def depth(self):
		from prompt_toolkit.output import ColorDepth
		return ColorDepth.MONOCHROME if "NO_COLOR" in os.environ else None

	def prompt(self, label: str, *, help: str = "", name: str | None = None, default: str = "", completer=None, check=None,
			multiline: bool = False) -> str:
		"""Prompt for one value; `check` returns an error message or None."""
		from prompt_toolkit import PromptSession
		from prompt_toolkit.formatted_text import FormattedText
		from prompt_toolkit.history import FileHistory, InMemoryHistory
		from prompt_toolkit.validation import Validator
		history = InMemoryHistory()
		if name and self.history:
			root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "auto/history"
			try:
				root.mkdir(parents=True, exist_ok=True)
				history = FileHistory(str(root / name))
			except OSError:
				pass
		validator = None
		if check:
			class Check(Validator):
				def validate(self, document):
					error = check(document.text)
					if error:
						from prompt_toolkit.validation import ValidationError
						raise ValidationError(message=error, cursor_position=len(document.text))
			validator = Check()
		hint = help + (" (Esc Enter to submit)" if multiline else "")
		message = FormattedText([("bold", label)] + ([("fg:ansibrightblack", " — " + hint)] if hint else []) + [("", "\n"), ("fg:ansicyan", "› ")])
		session = PromptSession(input=self.input, output=self.output, history=history)
		try:
			return session.prompt(message, default=default, completer=completer, validator=validator, validate_while_typing=False,
				complete_while_typing=True, multiline=multiline, enable_open_in_editor=True, color_depth=self.depth())
		except EOFError:
			raise KeyboardInterrupt from None

	def choose(self, label: str, options: list, *, help: str = "", default: str = "", optional: bool = False, rows: int = 10) -> str | None:
		"""Pick one of `(value, description)` options from a list filtered as you type, like fzf; None when an optional pick is skipped."""
		from prompt_toolkit.application import Application
		from prompt_toolkit.buffer import Buffer
		from prompt_toolkit.key_binding import KeyBindings
		from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
		from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
		options = [(str(value), str(description or "")) for value, description in options]
		rows = max(1, min(rows, len(options)))
		state = {"index": next((index for index, (value, _) in enumerate(options) if value == default), 0), "offset": 0}
		def reset(_):
			state.update(index=0, offset=0)
		query = Buffer(multiline=False, on_text_changed=reset)
		def matches():
			scored = ((fuzzy(query.text, value + " " + description), index) for index, (value, description) in enumerate(options))
			return [options[index] for score, index in sorted(item for item in scored if item[0] is not None)]
		def listing():
			found = matches()
			state["index"] = min(state["index"], max(0, len(found) - 1))
			state["offset"] = min(max(state["offset"], state["index"] - rows + 1), state["index"])
			lines = []
			for index, (value, description) in enumerate(found[state["offset"]:state["offset"] + rows], state["offset"]):
				selected = index == state["index"]
				lines += [("fg:ansicyan bold" if selected else "", "› " if selected else "  "), ("bold" if selected else "", value)]
				lines += [("fg:ansibrightblack", "  " + description)] if description else []
				lines.append(("", "\n"))
			if not found:
				lines.append(("fg:ansibrightblack", "  no match\n"))
			return lines[:-1] if lines and lines[-1] == ("", "\n") else lines
		def move(step):
			found = matches()
			state["index"] = max(0, min(len(found) - 1, state["index"] + step))
		keys = KeyBindings()
		for key, step in (("up", -1), ("c-p", -1), ("s-tab", -1), ("down", 1), ("c-n", 1), ("tab", 1), ("pageup", -rows), ("pagedown", rows)):
			keys.add(key)(lambda event, step=step: move(step))
		@keys.add("enter")
		def accept(event):
			found = matches()
			if found:
				event.app.exit(result=found[state["index"]][0])
		@keys.add("c-c")
		@keys.add("c-d")
		def cancel(event):
			event.app.exit(exception=KeyboardInterrupt())
		@keys.add("escape")
		def skip(event):
			event.app.exit(result=None) if optional else cancel(event)
		hint = (help + " · " if help else "") + "type to filter, ↑↓ to move, Enter to pick" + (", Esc to skip" if optional else "")
		header = [("bold", label), ("fg:ansibrightblack", " — " + hint)]
		field = Window(BufferControl(query))
		layout = HSplit([
			Window(FormattedTextControl(header), height=1),
			VSplit([Window(FormattedTextControl([("fg:ansicyan", "› ")]), width=2), field,
				Window(FormattedTextControl(lambda: [("fg:ansibrightblack", f" {len(matches())}/{len(options)}")]), dont_extend_width=True)]),
			Window(FormattedTextControl(listing), height=rows),
		])
		application = Application(layout=Layout(layout, focused_element=field), key_bindings=keys, input=self.input, output=self.output,
			erase_when_done=True, color_depth=self.depth())
		value = application.run()
		self.say(f"{label} › {value if value is not None else '(skipped)'}", "fg:ansibrightblack")
		return value

	def confirm(self, text: str) -> bool:
		"""Ask a yes/no question, defaulting to yes."""
		def check(value):
			return None if value.strip().lower() in ("", "y", "yes", "n", "no") else "Answer y or n"
		return not self.prompt(text + " [Y/n]", check=check).strip().lower().startswith("n")

	def ask(self, name: str, descriptor: Descriptor, context, arguments: dict | None = None, *, need: str = "", default: str = "",
			optional: bool = False, check=None) -> str | None:
		"""Ask for argument `name` as its descriptor describes; None when an optional value is skipped."""
		from prompt_toolkit.completion import FuzzyCompleter, PathCompleter, WordCompleter
		if need and not self.filled:
			self.say(need, "italic")
		self.filled = True
		label = descriptor.label or name.replace("_", " ").capitalize()
		help = descriptor.help + (" (Enter to skip)" if optional else "")
		candidates = candidatesFor(name, descriptor, context, arguments or {})
		if descriptor.kind == "choice":
			if not candidates:
				if optional:
					return None
				raise AutoError(f"No {name} is available to choose from", recovery=descriptor.recovery or None)
			return self.choose(label, candidates, help=descriptor.help, default=default, optional=optional)
		completer = None
		if descriptor.kind == "path":
			suffix = descriptor.suffix
			completer = PathCompleter(expanduser=True, file_filter=lambda path: os.path.isdir(path) or path.endswith(suffix))
		elif candidates:
			completer = FuzzyCompleter(WordCompleter([value for value, _ in candidates], meta_dict=dict(candidates), sentence=True))
		def validate(text):
			if not text.strip():
				return None if optional else f"{label} is required"
			if descriptor.kind == "path" and not Path(text.strip()).expanduser().is_file():
				return f"File not found: {text.strip()}"
			try:
				check and check(text)
			except (AutoError, ValueError) as error:
				return str(error)
			except Exception as error:  # YAML parsers raise their own error types.
				return f"Invalid value: {error}".partition("\n")[0]
			return None
		value = self.prompt(label, help=help, name=name, default=default, completer=completer, check=validate, multiline=descriptor.kind == "multiline")
		value = value.strip() if descriptor.kind != "multiline" else value.strip("\n")
		if descriptor.kind == "path":
			value = str(Path(value).expanduser())
		return value or None

	def question(self, event: dict):
		"""Answer a run question: a confirmation, a form field by field, or free text."""
		if event.get("type") == "PlanConfirm":
			def choice(text):
				return None if text.strip().lower() in ("", "y", "yes", "n", "no", "c", "change") else "Answer y, c or n"
			value = self.prompt(str(event.get("content") or "Run it?") + " [Y]es / [c]hange / [n]o", check=choice).strip().lower()
			return "run" if value in ("", "y", "yes") else "change" if value.startswith("c") else "no"
		self.say(str(event.get("content", "")), "bold")
		if event.get("type") == Event.POLICY_REQUEST:
			return self.confirm("Allow?")
		if not event.get("fields"):
			return self.prompt("Answer", name="answer", check=lambda text: None if text.strip() else "An answer is required").strip()
		answer = {}
		for field in event["fields"]:
			name = field.get("id", field.get("key"))
			if not isinstance(name, str) or not name:
				raise AutoError("Question field requires an id or key")
			if any((answer.get(condition["key"]) == condition.get("value")) != (condition.get("op") == "eq") for condition in field.get("when", [])):
				continue
			if field.get("hidden"):
				if "default" in field:
					answer[name] = field["default"]
				continue
			label = str(field.get("title") or field.get("label") or name)
			options = [(str(option.get("value", option.get("label"))), str(option.get("label", ""))) if isinstance(option, dict) else (str(option), "")
				for option in field.get("options") or []]
			type = field.get("type", "text")
			kind = field.get("kind") or {"string": "text", "integer": "value", "number": "value", "boolean": "value", "multiselect": "structured"}.get(type, type)
			if options and type != "multiselect" and not field.get("custom"):
				kind = "choice"
			if kind not in {"text", "multiline", "path", "choice", "value", "structured"}:
				raise AutoError(f"Unsupported question field kind: {kind}; answer through the originating interface")
			schema = dict(field.get("schema", {}))
			if type in {"integer", "number", "boolean", "string"}:
				schema.setdefault("type", type)
			elif type == "multiselect":
				schema.setdefault("type", "array")
				schema.setdefault("items", {"type": "string", **({"enum": [value for value, _ in options]} if not field.get("custom") else {})})
			for key in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "pattern", "format"):
				if key in field:
					schema[key] = field[key]
			try:
				Draft202012Validator.check_schema(schema)
			except SchemaError as error:
				raise AutoError(f"Invalid question field schema: {error}") from error
			validator = Draft202012Validator(schema, format_checker=FormatChecker())
			def parse(text):
				try:
					value = yamlLoad(text) if kind in {"value", "structured"} or type in {"integer", "number", "boolean", "multiselect"} else text
					jsonText(value)
					validator.validate(value)
				except (ValidationError, TypeError, ValueError) as error:
					raise AutoError(f"Invalid {label}: {str(error).partition(chr(10))[0]}") from error
				return value
			default = field.get("default", "")
			default = default if isinstance(default, str) else jsonText(default)
			value = self.ask("question-" + quote(name, safe=""), Descriptor(kind, field.get("description", ""),
				field.get("suffix", ""), candidates=lambda context, arguments: options, label=label), None,
				default=default, optional=field.get("required") is False, check=parse)
			if value is not None:
				answer[name] = parse(value)
		return answer

# EOF
