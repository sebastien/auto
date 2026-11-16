"""Module: worker — Keep Pi RPC processes warm without sharing task conversations."""

from __future__ import annotations

import atexit
import json
import os
import selectors
import signal
import subprocess
import threading
import time
from collections import OrderedDict

from auto.utils.data import AutoError

# Two configurations usually suffice: decision (no tools) and execution.
WORKERS = OrderedDict()
# ponytail: serialize Pi calls in this process; use per-worker locks if parallel task execution is added.
LOCK = threading.RLock()
CAPACITY = 2


class Worker:
	"""One Pi RPC child, with fresh conversations for successive calls."""

	def __init__(self, args, project, env):
		try:
			self.process = subprocess.Popen(args, cwd=project, env=env, stdin=subprocess.PIPE,
				stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
		except OSError as error:
			raise AutoError(f"Cannot launch Pi: {error}", "system") from error
		self.selector = selectors.DefaultSelector()
		for number, pipe in enumerate((self.process.stdout, self.process.stderr)):
			os.set_blocking(pipe.fileno(), False)
			self.selector.register(pipe, selectors.EVENT_READ, number)
		os.set_blocking(self.process.stdin.fileno(), False)
		self.pending = b""

	def close(self):
		"""Kill the child and its descendants, including tools left running."""
		try:
			os.killpg(self.process.pid, signal.SIGKILL)
		except ProcessLookupError:
			pass
		self.process.wait()
		self.selector.close()
		for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
			pipe.close()

	def command(self, options, command):
		"""Write a command while draining stdout and stderr; yield complete JSONL records."""
		data = (json.dumps(command) + "\n").encode()
		self.selector.register(self.process.stdin, selectors.EVENT_WRITE, 2)
		deadline = time.monotonic() + (options.limits.remaining() if options.limits else 3600)
		total, errors = 0, bytearray()
		while True:
			if options.cancelled():
				raise AutoError("Execution cancelled", "cancelled")
			if options.limits:
				options.limits.check()
			if time.monotonic() >= deadline:
				raise AutoError("Execution timed out", "timeout")
			if options.tick:
				options.tick()
			for key, _ in self.selector.select(.05):
				if key.data == 2:
					try:
						data = data[os.write(key.fd, data[:65536]):]
					except BrokenPipeError as error:
						raise AutoError("Pi RPC closed its input", "system") from error
					if not data:
						self.selector.unregister(self.process.stdin)
					continue
				chunk = os.read(key.fd, 65536)
				if not chunk:
					raise AutoError("Pi RPC exited unexpectedly: " + errors.decode(errors="replace"), "system")
				total += len(chunk)
				if total > options.maxBytes:
					raise AutoError("Process output exceeds byte limit", "system")
				if key.data == 1:
					errors.extend(chunk)
				else:
					self.pending += chunk
			while b"\n" in self.pending:
				line, self.pending = self.pending.split(b"\n", 1)
				if not line.strip():
					continue
				try:
					event = json.loads(line)
				except (ValueError, UnicodeError) as error:
					raise AutoError("Pi returned malformed RPC JSONL", "system") from error
				if not isinstance(event, dict):
					raise AutoError("Pi returned an invalid RPC record", "system")
				yield line + b"\n", event

	def run(self, options, prompt):
		"""Reset history, submit a prompt and wait for agent_settled, not merely agent_end."""
		reset = self.command(options, {"id": "reset", "type": "new_session"})
		try:
			for _, event in reset:
				if event.get("type") == "response" and event.get("id") == "reset":
					if not event.get("success") or event.get("data", {}).get("cancelled"):
						raise AutoError("Pi could not reset its session", "system")
					break
		finally:
			reset.close()
		stream = self.command(options, {"id": "prompt", "type": "prompt", "message": prompt})
		try:
			for line, event in stream:
				if event.get("type") == "response":
					if not event.get("success"):
						raise AutoError("Pi RPC command failed: " + str(event.get("error")), "system")
					if event.get("data", {}).get("disposition") == "handled":
						raise AutoError("Pi handled the prompt without running the model", "output")
				else:
					yield 0, line
					if event.get("type") == "agent_settled":
						yield 2, 0
						return
		finally:
			stream.close()


def streamWorker(options, args, config, prompt):
	"""Reuse at most two compatible workers; a failed or interrupted call evicts its worker."""
	env = {**options.environ, **config.get("env", {})}
	project = str(config.get("cwd", options.project))
	key = (tuple(args), project, tuple(sorted(env.items())))
	with LOCK:
		worker = WORKERS.pop(key, None)
		if worker is not None and worker.process.poll() is not None:
			worker.close()
			worker = None
		if worker is None:
			worker = Worker(args, project, env)
		complete = False
		stream = worker.run(options, prompt)
		try:
			for number, chunk in stream:
				if number == 2:
					complete = True
				yield number, chunk
		finally:
			stream.close()
			if complete:
				WORKERS[key] = worker
				while len(WORKERS) > CAPACITY:
					WORKERS.popitem(last=False)[1].close()
			else:
				worker.close()


def closeWorkers():
	"""Release warm workers when their Auto process exits."""
	with LOCK:
		while WORKERS:
			WORKERS.popitem()[1].close()


atexit.register(closeWorkers)

# EOF
