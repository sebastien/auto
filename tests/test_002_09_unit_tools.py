"""Module: test_tools — What tool calls touched, without their contents."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auto.runtime.tools import recordFiles, toolAccess, toolSummary


class ToolTests(unittest.TestCase):
	def testAccessByName(self):
		for name, access in (("read", "read"), ("grep", "read"), ("Edit", "write"), ("apply_patch", "write"), ("fs_write_file", "write"),
				("bash", "use"), ("webfetch", "use")):
			self.assertEqual(toolAccess(name), access, name)

	def testSummaryNamesFilesRelativeToTheProject(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory).resolve()
			self.assertEqual(toolSummary("write", {"filePath": str(root / "a/b.md"), "content": "body"}, root),
				{"access": "write", "files": ["a/b.md"]})
			self.assertEqual(toolSummary("read", {"path": "c.py"}, root, root / "sub"), {"access": "read", "files": ["sub/c.py"]})
			self.assertEqual(toolSummary("read", {"path": "/etc/hosts"}, root), {"access": "read", "files": ["/etc/hosts"]})
			patch = "*** Begin Patch\n*** Update File: x.py\n@@\n-a\n+b\n*** Add File: y.py\n+c\n*** End Patch"
			self.assertEqual(toolSummary("apply_patch", {"patchText": patch}, root)["files"], ["x.py", "y.py"])
			self.assertEqual(toolSummary("multiedit", '{"edits": [{"filePath": "m.py"}]}', root)["files"], ["m.py"])
			grep = toolSummary("grep", {"pattern": "def  main", "path": "src"}, root)
			self.assertEqual(grep, {"access": "read", "files": ["src"], "detail": "def main"})
			self.assertEqual(len(toolSummary("bash", {"command": "x" * 500})["detail"]), 200)
			self.assertEqual(toolSummary("todo", None), {"access": "use"})

	def testReceiptsKeepCompletedFilesOnce(self):
		run = {}
		self.assertFalse(recordFiles(run, {"content": "running", "access": "write", "files": ["a"]}))
		self.assertFalse(recordFiles(run, {"content": "error", "access": "write", "files": ["a"]}))
		self.assertTrue(recordFiles(run, {"content": "completed", "access": "write", "files": ["a", "b"]}))
		self.assertTrue(recordFiles(run, {"content": "completed", "access": "read", "files": ["a"]}))
		self.assertFalse(recordFiles(run, {"content": "completed", "access": "write", "files": ["b"]}))
		self.assertEqual(run["files"], {"read": ["a"], "written": ["a", "b"]})


if __name__ == "__main__":
	unittest.main()

# EOF
