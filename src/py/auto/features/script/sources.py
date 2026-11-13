"""Immutable source material carried by a materialised script definition."""

import os
from pathlib import Path

from auto.runtime.model import Content
from auto.runtime.operations import Resolve, Value, isReference
from auto.utils.data import AutoError, digest


class ScriptSources:
	def __init__(self, context, records=None):
		self.context, self.records = context, {} if records is None else records
		if not isinstance(self.records, dict):
			raise AutoError("Template source snapshots must be a mapping")
		size = 0
		for key, record in self.records.items():
			if not isinstance(key, str) or not isinstance(record, dict) or any(not isinstance(record.get(name), str) for name in ("ref", "path", "text", "digest", "content_digest")):
				raise AutoError("Invalid template source snapshot")
			data = record["text"].encode("utf-8")
			if digest(data) != record["content_digest"]:
				raise AutoError("Template source snapshot failed integrity/size check: " + key)
			size += len(data)
		if size > context.maxBytes:
			raise AutoError("Template source snapshots exceed byte limit")

	@staticmethod
	def key(ref, base):
		ref = ref.strip('"')
		target, marker, selector = ref.partition("#")
		if target.startswith(("./", "../", "/")):
			path = Path(target)
			target = os.path.abspath(path if path.is_absolute() else base / path)
		return target + marker + selector

	def record(self, ref, base):
		key = self.key(ref, base)
		record = self.records.get(key)
		if record is None:
			# Materialisation relocates root paths to canonical targets; nested
			# source text keeps its authored aliases, including symlinks.
			record = next((item for item in self.records.values() if isinstance(item, dict) and item.get("ref") == key), None)
		return record

	def resolve(self, ref, base):
		key = self.key(ref, base)
		record = self.record(ref, base)
		if record is None:
			return Resolve(self.context, ref, base)
		if not isinstance(record, dict) or any(not isinstance(record.get(name), str) for name in ("ref", "path", "text", "digest", "content_digest")):
			raise AutoError("Invalid template source snapshot: " + key)
		data = record["text"].encode("utf-8")
		if len(data) > self.context.maxBytes or digest(data) != record["content_digest"]:
			raise AutoError("Template source snapshot failed integrity/size check: " + key)
		pin = {"ref": record["ref"], "digest": record["digest"]}
		if pin not in self.context.evidence:
			self.context.evidence.append(pin)
		return Content(record["ref"], data, record["digest"], Path(record["path"]))

	def value(self, value, base, config):
		if isReference(value):
			return self.resolve(value, base).text
		return Value(self.context, value, base, config)
