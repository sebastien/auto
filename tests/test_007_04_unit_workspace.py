"""Module: test_workspace — The files a guided run changed in the project's git working tree."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from auto.utils.workspace import changes, snapshot


class WorkspaceTests(unittest.TestCase):
	def setUp(self):
		temp = tempfile.TemporaryDirectory()
		self.addCleanup(temp.cleanup)
		self.root = Path(temp.name)

	def git(self, *args, cwd=None):
		subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args], cwd=cwd or self.root,
			check=True, capture_output=True)

	def write(self, name, text):
		path = self.root / name
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(text)

	def repository(self):
		self.git("init", "-q")
		self.write("kept.txt", "kept\n")
		self.write("edited.txt", "before\n")
		self.write("removed.txt", "removed\n")
		self.write(".gitignore", "build/\n")
		self.git("add", ".")
		self.git("commit", "-qm", "Initial")

	def testOutsideGitThereIsNothingToReport(self):
		self.assertIsNone(snapshot(self.root))
		self.assertIsNone(changes(self.root, None))

	def testAddedModifiedAndDeletedFilesAreReported(self):
		self.repository()
		# Uncommitted work from before the run is not the run's.
		self.write("draft.txt", "mine\n")
		self.write("kept.txt", "dirty before\n")
		before = snapshot(self.root)
		self.write("edited.txt", "after\n")
		(self.root / "removed.txt").unlink()
		self.write("docs/new.md", "# New\n")
		self.write("build/out.bin", "ignored\n")
		self.write(".auto/pln-000-demo/tsk/root.md", "plan\n")
		self.assertEqual(changes(self.root, before), [
			{"path": "docs/new.md", "change": "added"},
			{"path": "edited.txt", "change": "modified"},
			{"path": "removed.txt", "change": "deleted"},
		])

	def testWorkCommittedDuringTheRunIsReported(self):
		self.repository()
		self.write("draft.txt", "mine\n")
		before = snapshot(self.root)
		self.write("edited.txt", "after\n")
		self.write("draft.txt", "mine\n")
		self.write("feature.py", "x = 1\n")
		self.git("add", "edited.txt", "feature.py", "draft.txt")
		self.git("commit", "-qm", "Work")
		self.assertEqual(changes(self.root, before), [
			{"path": "edited.txt", "change": "modified"},
			{"path": "feature.py", "change": "added"},
		])

	def testARepositoryWithoutCommitsAndASubdirectoryProject(self):
		self.git("init", "-q")
		before = snapshot(self.root)
		self.write("first.txt", "1\n")
		self.assertEqual(changes(self.root, before), [{"path": "first.txt", "change": "added"}])
		self.git("add", ".")
		self.git("commit", "-qm", "First")
		project = self.root / "app"
		self.write("app/main.py", "print()\n")
		self.write("outside.txt", "not the project's\n")
		before = snapshot(project)
		self.write("app/main.py", "print(1)\n")
		self.write("outside.txt", "changed\n")
		self.assertEqual(changes(project, before), [{"path": "main.py", "change": "modified"}])

# EOF
