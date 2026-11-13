"""Housekeeping capacity, interrupted reservations and UTC window attribution."""

import fcntl
import tempfile
import subprocess
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from auto.features.memory.accounting import AllowanceExceeded, allowanceStatus, recordUsage, reserve, windowKey
from auto.runtime.model import Context
from auto.utils.data import AutoError, atomicWrite, jsonText, readJson


class AccountingTests(unittest.TestCase):
	def setUp(self):
		temp = tempfile.TemporaryDirectory()
		self.addCleanup(temp.cleanup)
		self.root = Path(temp.name)
		self.at = "2026-11-13T12:00:00+00:00"
		self.clock = patch("auto.features.memory.accounting.timestamp", side_effect=lambda: self.at)
		self.clock.start()
		self.addCleanup(self.clock.stop)
		self.context = Context(project=self.root, defaults={"housekeeping": {"allowance": {"tokens": 100, "per": "day"}},
			"memory": {"agents": {"ux": {"allowance": {"tokens": 60, "per": "week"}}, "dev": {}}}})

	@contextmanager
	def worker(self, name):
		root = self.root / ".auto/agents" / name
		root.mkdir(parents=True, exist_ok=True)
		with (root / ".lock").open("a+") as lock:
			fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
			yield

	def newRun(self, name, id, budget):
		path = self.root / ".auto/agents" / name / "runs" / id
		path.mkdir(parents=True)
		run = {"id": id, "started": self.at, "usage_at": self.at, "status": "Running", "budget": budget, "usage": {"tokens": 0, "time": 0, "cost": 0}}
		self.publish(path, run)
		return path, run

	def publish(self, path, run):
		file = "receipt.json" if run["status"] in {"Failed", "Succeeded", "Cancelled"} else "active.json"
		atomicWrite(path / file, jsonText(run))

	def testReservationsExcludeDoubleSpendingAndBoundMissingDimensions(self):
		with self.worker("ux"), self.worker("dev"):
			path, first = self.newRun("ux", "one", {"tokens": 50})
			reserve(self.context, "ux", path, first)
			path2, second = self.newRun("dev", "two", {"tokens": 60})
			with self.assertRaises(AllowanceExceeded):
				reserve(self.context, "dev", path2, second)
			second["budget"] = {}
			reserve(self.context, "dev", path2, second)
			self.assertEqual(second["budget"]["tokens"], 50)
			self.assertEqual(allowanceStatus(self.context, "dev")[0]["remaining"]["tokens"], 0)
			first["usage"]["tokens"], first["status"] = 20, "Failed"
			self.publish(path, first)
			recordUsage(self.context, "ux", first)
			self.assertEqual(allowanceStatus(self.context, "dev")[0]["remaining"]["tokens"], 30)

	def testDurableReceiptsRecoverUsageEvenIfLedgerUpdateWasInterrupted(self):
		with self.worker("ux"):
			path, run = self.newRun("ux", "one", {"tokens": 50})
			reserve(self.context, "ux", path, run)
			run["usage"]["tokens"], run["status"] = 15, "Cancelled"
			self.publish(path, run)
			project = allowanceStatus(self.context, "ux")[0]
			self.assertEqual(project["used"]["tokens"], 15)
			self.assertEqual(project["remaining"]["tokens"], 85)
			self.assertEqual(allowanceStatus(self.context, "ux")[0]["used"]["tokens"], 15)

	def testOnlyFreeKernelLockReleasesInterruptedCapacity(self):
		with self.worker("ux"):
			path, run = self.newRun("ux", "one", {"tokens": 50})
			reserve(self.context, "ux", path, run)
			run["usage"]["tokens"] = 12
			self.publish(path, run)
			self.assertEqual(allowanceStatus(self.context, "ux")[0]["remaining"]["tokens"], 50)
		self.assertEqual(allowanceStatus(self.context, "ux")[0]["remaining"]["tokens"], 88)

	def testWindowRolloverChargesMeasuredDeltasAndReservesUnspentBudget(self):
		with self.worker("ux"):
			path, run = self.newRun("ux", "one", {"tokens": 50})
			reserve(self.context, "ux", path, run)
			run["usage"]["tokens"] = 20
			self.publish(path, run)
			recordUsage(self.context, "ux", run)
			self.at = "2026-11-14T00:00:01+00:00"
			self.assertEqual(allowanceStatus(self.context, "ux")[0]["remaining"]["tokens"], 70)
			run["usage"]["tokens"], run["usage_at"] = 30, self.at
			self.publish(path, run)
			recordUsage(self.context, "ux", run)
			project, agent = allowanceStatus(self.context, "ux")
			self.assertEqual(project["used"]["tokens"], 10)
			self.assertEqual(project["remaining"]["tokens"], 70)
			self.assertEqual(agent["used"]["tokens"], 30)

	def testProjectOverrideCannotLoosenUserRestrictionOrPerRunBudget(self):
		user = {"housekeeping": {"allowance": {"tokens": 40, "per": "week"}}}
		context = self.context.derive(configurationLayers=[(str(self.root / "user/config.toml"), user),
			(str(self.root / ".auto/config.toml"), self.context.defaults)])
		with self.worker("ux"):
			path, run = self.newRun("ux", "one", {"tokens": 50})
			with self.assertRaises(AllowanceExceeded):
				reserve(context, "ux", path, run, override=True)
			run["budget"] = {"tokens": 25}
			reserve(context, "ux", path, run, override=True)
			self.assertEqual(run["budget"]["tokens"], 25)
			tightened = context.derive(defaults={"housekeeping": {"allowance": {"tokens": 10, "per": "day"}}})
			with self.assertRaises(AllowanceExceeded):
				reserve(tightened, "ux", path, run)
			with self.assertRaisesRegex(AutoError, "explicit human"):
				reserve(context.derive(author="auto:worker"), "ux", path, run, override=True)

	def testUsageCannotDecreaseAndCalendarBoundariesAreUtc(self):
		with self.worker("ux"):
			path, run = self.newRun("ux", "one", {"tokens": 50})
			reserve(self.context, "ux", path, run)
			run["usage"]["tokens"] = 20
			recordUsage(self.context, "ux", run)
			run["usage"]["tokens"] = 10
			with self.assertRaisesRegex(AutoError, "cannot decrease"):
				recordUsage(self.context, "ux", run)
		self.assertEqual(windowKey("week", "2026-11-16T00:30:00+13:00"), "week:2026-11-09")
		self.assertEqual(windowKey("month", "2026-12-01T00:30:00+13:00"), "month:2026-11-01")

	def testIndependentProcessesCannotReserveTheSameProjectCapacity(self):
		file = self.root / "worker.py"
		file.write_text('''import fcntl, json, sys
from pathlib import Path
from auto.features.memory.accounting import AllowanceExceeded, reserve
from auto.runtime.model import Context
from auto.utils.data import atomicWrite, jsonText, timestamp
root, name = Path(sys.argv[1]), sys.argv[2]
scope = root / ".auto/agents" / name
scope.mkdir(parents=True, exist_ok=True)
ctx = Context(project=root, defaults={"housekeeping": {"allowance": {"tokens": 100, "per": "day"}}})
with (scope / ".lock").open("a+") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    path = scope / "runs/one"
    path.mkdir(parents=True)
    run = {"id": "one", "started": timestamp(), "status": "Pending", "budget": {"tokens": 60}, "usage": {"tokens": 0}}
    atomicWrite(path / "active.json", jsonText(run))
    try:
        reserve(ctx, name, path, run)
    except AllowanceExceeded:
        print("blocked", flush=True)
    else:
        print("reserved", flush=True)
        sys.stdin.readline()
''')
		first = subprocess.Popen([sys.executable, str(file), str(self.root), "ux"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
		try:
			import selectors
			with selectors.DefaultSelector() as selector:
				selector.register(first.stdout, selectors.EVENT_READ)
				self.assertTrue(selector.select(timeout=10), "First reservation did not finish")
			self.assertEqual(first.stdout.readline().strip(), "reserved")
			second = subprocess.run([sys.executable, str(file), str(self.root), "dev"], input="done\n", capture_output=True, text=True, timeout=10)
			self.assertEqual(second.returncode, 0, second.stderr)
			self.assertEqual(second.stdout.strip(), "blocked")
		finally:
			if first.poll() is None:
				first.communicate(input="done\n", timeout=10)
			else:
				first.communicate(timeout=10)
		self.assertEqual(first.returncode, 0)

	def testLostOrCorruptWindowTotalsCannotResetSpentCapacity(self):
		with self.worker("ux"):
			path, run = self.newRun("ux", "one", {"tokens": 50})
			reserve(self.context, "ux", path, run)
			run["usage"]["tokens"] = 20
			recordUsage(self.context, "ux", run)
			file = self.root / ".auto/housekeeping/usage.json"
			ledger = readJson(file)
			ledger["windows"].pop(windowKey("day", self.at))
			atomicWrite(file, jsonText(ledger))
			with self.assertRaisesRegex(AutoError, "refusing to reset accounting"):
				allowanceStatus(self.context, "ux")
			self.assertEqual(readJson(file), ledger)
