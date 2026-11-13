"""Track unresolved metadata without encoding authoring state in task files."""


class MetadataProjection:
	def __init__(self, deferred=(), overrides=None):
		self.deferred = frozenset(deferred)
		self.overrides = overrides or {}

	def known(self, *path):
		"""Explicit overrides make a value known even below a deferred ancestor."""
		value = self.overrides
		for key in path:
			if not isinstance(value, dict):
				return True
			if key not in value:
				break
			value = value[key]
		else:
			return True
		return not any(path[:len(prefix)] == prefix for prefix in self.deferred)

	def defaults(self, values, path=()):
		"""Retain default leaves whose use is independent of deferred overrides."""
		result = {}
		for key, value in values.items():
			current = (*path, key)
			if isinstance(value, dict):
				projected = self.defaults(value, current)
				if projected or self.known(*current):
					result[key] = projected
			elif self.known(*current):
				result[key] = value
		return result
