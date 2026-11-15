"""Module: memory — Read a frozen Memo agent snapshot through its operations."""

from __future__ import annotations

import re
import time
from copy import deepcopy

from auto.utils.data import AutoError


class MemoMemorySnapshot:
	"""Hold material and time stable while allocating several agents' wake layers."""

	def __init__(self, project):
		from memo import Memo
		from memo.integrations.filesystem.backend import LocalBackend
		from memo.utils.clock import FixedClock
		from memo.error import MemoError
		try:
			self.memo = Memo(LocalBackend(str(project / ".memo")), FixedClock(time.time() * 1000), cwd=str(project))
			self.entries = deepcopy(self.memo.backend.listEntries())
		except (MemoError, OSError) as error:
			raise AutoError(f"Cannot capture Memo agent material: {error}", "system") from error

	def queryText(self, query):
		# Task goals are prose, rather than Memo's quoted search expressions.
		text = query.replace('"', " ").strip() if query else None
		return text if text and re.search(r"[^\W_]", text) else None

	def wake(self, agent, profile, query, budgets=None):
		from memo.error import MemoError
		from memo.features.agent.wake import agentWakeLayers
		try:
			return agentWakeLayers(self.memo, agent, profile=profile, budgets=budgets,
				query=self.queryText(query), entries=self.entries)
		except MemoError as error:
			raise AutoError(f"Memo wake failed for {agent}: {error}") from error

	def minimums(self, agent, profile, query):
		"""Measure layer admission against the same immutable material and time."""
		full = self.wake(agent, profile, query)
		result = {}
		for layer in ("world", "recent"):
			maximum = full["layers"][layer]["allowance"]
			if not maximum:
				result[layer] = 0
				continue
			low, high = 1, maximum
			while low < high:
				middle = (low + high) // 2
				try:
					self.wake(agent, profile, query, {"prompt": 0, "world": 0, "recent": 0, layer: middle})
				except AutoError:
					# A full wake was validated above. On this frozen snapshot only
					# the layer allowance differs, so this is an admission failure.
					low = middle + 1
				else:
					high = middle
			result[layer] = low
		return result

	def model(self, agent, query):
		from memo.error import MemoError
		from memo.features.agent.model import agentModel
		try:
			return agentModel(self.memo, agent, query=self.queryText(query), others=True, entries=self.entries)
		except MemoError as error:
			raise AutoError(f"Memo model failed for {agent}: {error}") from error
