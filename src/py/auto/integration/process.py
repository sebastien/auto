"""Module: process — Cancellable, bounded, deadlock-free subprocess streams."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time

from auto.runtime.model import ProcessOptions
from auto.utils.data import AutoError


def streamProcess(options: ProcessOptions, args: list[str], config: dict, stdin: str = ""):
	"""Yield stdout/stderr byte chunks and a final exit code; close kills descendants."""
	# Keep Context callers working while the public boundary is the effect view.
	if not isinstance(options, ProcessOptions):
		options = options.process
	try:
		process = subprocess.Popen(args, cwd=config.get("cwd", options.project),
			env={**options.environ, **config.get("env", {})}, stdin=subprocess.PIPE,
			stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
	except OSError as error:
		raise AutoError(f"Cannot launch {args[0]}: {error}", "system") from error
	data, offset, total = stdin.encode(), 0, 0
	deadline = time.monotonic() + (options.limits.remaining() if options.limits else 3600)
	try:
		with selectors.DefaultSelector() as selector:
			for number, pipe in enumerate((process.stdout, process.stderr)):
				os.set_blocking(pipe.fileno(), False)
				selector.register(pipe, selectors.EVENT_READ, number)
			if data:
				os.set_blocking(process.stdin.fileno(), False)
				selector.register(process.stdin, selectors.EVENT_WRITE, 2)
			else:
				process.stdin.close()
			while selector.get_map() or process.poll() is None:
				if options.cancelled():
					raise AutoError("Execution cancelled", "cancelled")
				if options.limits:
					options.limits.check()
				if time.monotonic() >= deadline:
					raise AutoError("Execution timed out", "timeout")
				for key, _ in selector.select(.05):
					if key.data == 2:
						try:
							offset += os.write(key.fd, data[offset:offset + 65536])
						except BrokenPipeError:
							offset = len(data)
						if offset == len(data):
							selector.unregister(key.fileobj)
							key.fileobj.close()
						continue
					chunk = os.read(key.fd, 65536)
					if not chunk:
						selector.unregister(key.fileobj)
						continue
					total += len(chunk)
					if total > options.maxBytes:
						raise AutoError("Process output exceeds byte limit", "system")
					yield key.data, chunk
		yield 2, process.wait()
	finally:
		# A server-backed engine must additionally cancel its server session.
		try:
			os.killpg(process.pid, signal.SIGKILL)
		except ProcessLookupError:
			pass
		process.wait()
		for pipe in (process.stdin, process.stdout, process.stderr):
			pipe.close()


def capture(options: ProcessOptions, args: list[str], config: dict, stdin: str = "") -> tuple[str, str, int]:
	chunks, code = [bytearray(), bytearray()], 0
	for number, chunk in streamProcess(options, args, config, stdin):
		if number == 2:
			code = chunk
		else:
			chunks[number].extend(chunk)
	return chunks[0].decode("utf-8"), chunks[1].decode("utf-8"), code

# EOF
