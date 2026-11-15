"""Module: test_memory_config — Agent defaults and configured record resolvers."""

from __future__ import annotations

import ast
import copy
import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto.features.elaboration.operations import PlanCreate, planName
from auto.features.memory.config import agentsFor, defaultAgents, memoryPolicy
from auto.features.memory.accounting import constraintsFor
from auto.features.plan.store import planPath
from auto.integration.resolvers import validateResolvers
from auto.runtime.config import contextFor
from auto.runtime.model import Content, Context, Limits
from auto.runtime.operations import Resolve
from auto.utils.data import AutoError, ReferenceUnavailable, document
from support import rootFile


class MemoryConfigurationTests(unittest.TestCase):
	def setUp(self):
		temp = tempfile.TemporaryDirectory()
		self.addCleanup(temp.cleanup)
		self.root = Path(temp.name)
		self.context = Context(project=self.root)

	def testDefaultAgentReferenceAndExplicitSelectionDoNotMutateConfig(self):
		self.context.defaults = {"memory": {"agents": {"ui-designer": {}, "architect": {}}, "default_agents": ["auto:agent/ui-designer"]}}
		original = copy.deepcopy(self.context.defaults)
		self.assertEqual(agentsFor(self.context)["ui-designer"]["agent"], "agent-ui-designer/agent:UiDesigner")
		self.assertEqual(defaultAgents(self.context), ["auto:agent/ui-designer"])
		with patch("auto.features.elaboration.operations.PlanRefine", side_effect=lambda context, plan, reason: plan):
			path = PlanCreate(self.context, "Selected defaults")
			explicit = PlanCreate(self.context, "Explicit selection", agents=["auto:agent/architect"])
			disabled = PlanCreate(self.context, "No agent memory", agents=[])
		self.assertEqual(document(rootFile(path).read_text())[0]["agents"], ["auto:agent/ui-designer"])
		self.assertEqual(document(rootFile(explicit).read_text())[0]["agents"], ["auto:agent/architect"])
		# No agents is the absence of the field.
		self.assertNotIn("agents", document(rootFile(disabled).read_text())[0])
		self.assertEqual(self.context.defaults, original)

	def testDefaultSelectionAndDuplicateMemoReferencesAreValidated(self):
		self.context.defaults = {"memory": {"agents": {"ux": {}, "other": {"agent": "agent-ux/agent:Ux"}}}}
		with self.assertRaisesRegex(AutoError, "more than once"):
			agentsFor(self.context)
		for selection in ("ux", ["auto:agent/missing"], ["auto:agent/ux", "auto:agent/ux"]):
			with self.subTest(selection=selection), self.assertRaises(AutoError):
				defaultAgents(self.context.derive(defaults={"memory": {"agents": {"ux": {}}, "default_agents": selection}}))
		self.assertEqual(defaultAgents(self.context.derive(defaults={"memory": {"agents": {"ux": {}}}})), [])

	def testProjectCannotLoosenUserAllowancePeriodOrAgentPolicy(self):
		home, project = self.root / "home", self.root / "project"
		(home / ".auto").mkdir(parents=True)
		(project / ".auto").mkdir(parents=True)
		(home / ".auto/config.toml").write_text('[housekeeping.allowance]\ntokens = 40\nper = "week"\n[memory.agents.ux.policy]\n"memory.evolve" = "deny"\n')
		(project / ".auto/config.toml").write_text('[housekeeping.allowance]\ntokens = 100\nper = "day"\n[memory.agents.ux.policy]\n"memory.evolve" = "allow"\n')
		with patch.dict("os.environ", {"HOME": str(home), "XDG_CONFIG_HOME": str(self.root / "xdg")}, clear=True):
			context = contextFor(project)
		self.assertEqual(memoryPolicy(context, "ux")["memory.evolve"], "deny")
		constraints = constraintsFor(context, "ux")
		self.assertEqual([(row["allowance"]["per"], row["allowance"]["tokens"], row["overridable"]) for row in constraints],
			[("week", 40, False), ("day", 100, True)])
		(home / ".auto/config.toml").write_text('[memory.agents.ux.policy]\n"memory.evolve" = "allow"\n')
		(project / ".auto/config.toml").write_text('[memory.agents.ux.policy]\nmemory = "deny"\n')
		with patch.dict("os.environ", {"HOME": str(home), "XDG_CONFIG_HOME": str(self.root / "xdg")}, clear=True):
			context = contextFor(project)
		self.assertEqual(memoryPolicy(context, "ux")["memory.evolve"], "deny")

	def testPlanIdsAreNumberedAndResolvableByNumber(self):
		plans = self.root / ".auto"
		(plans / "add-tests").mkdir(parents=True)
		(plans / "pln-001-taken").mkdir()
		with patch("auto.features.elaboration.operations.PlanRefine", side_effect=lambda context, plan, reason: plan):
			names = [PlanCreate(self.context, goal).name for goal in ("agent", "catalogue", "Fix the login bug")]
		# The prefix keeps the reserved words agent and catalogue out of ids; used numbers are skipped, hex-like slugs are not.
		self.assertEqual(names, ["pln-000-agent", "pln-002-catalogue", "pln-003-fix-login-bug"])
		self.assertEqual((plans / ".counter").read_text(), "004\n")
		self.assertEqual(planPath(self.context, "003"), (plans / "pln-003-fix-login-bug").resolve())
		self.assertEqual(planPath(self.context, "pln-003"), (plans / "pln-003-fix-login-bug").resolve())
		with patch.dict("os.environ", {"HOME": str(self.root / "home"), "XDG_CONFIG_HOME": str(self.root / "xdg")}, clear=True):
			self.assertEqual(contextFor(self.root, plan="002").plan, plans / "pln-002-catalogue")
		rootFile(plans / "pln-002-other").write_text("Other\n")
		with self.assertRaisesRegex(AutoError, "Ambiguous plan"):
			planPath(self.context, "002")

	def testPlanIdsAreCompact(self):
		for goal, name in (("Create a simple standalone HTML webpage with a chat UI. Do not add backend integration for now.", "standalone-html-webpage-chat"),
			("Document the project architecture", "document-project-architecture"),
			("Fix the login bug\nIt fails on Safari", "fix-login-bug"),
			("Create a", "create-a"), ("!!!", "plan"),
			("Supercalifragilisticexpialidocious-and-antidisestablishmentarianism", "supercalifragilisticexpialidocio")):
			with self.subTest(goal=goal):
				self.assertEqual(planName(goal), name)

	def testTriggerConfigurationRejectsUnknownAndMalformedThresholds(self):
		for triggers in ("decision", ["unknown"], ["backlog:0"], ["backlog:-1"], ["backlog:2.5"], [None]):
			with self.subTest(triggers=triggers), self.assertRaisesRegex(AutoError, "triggers must be"):
				agentsFor(self.context.derive(defaults={"memory": {"agents": {"ux": {"triggers": triggers}}}}))
		self.assertEqual(agentsFor(self.context.derive(defaults={"memory": {"agents": {"ux": {"triggers": ["decision", "plan-ended", "backlog:24"]}}}}))["ux"]["triggers"], ["decision", "plan-ended", "backlog:24"])

	def testInvalidCreationHasNoPlanDirectory(self):
		for options in ({"goal": ""}, {"goal": "Work", "budget": "invalid"}, {"goal": "Work", "agents": ["auto:agent/missing"]}):
			with self.subTest(options=options), self.assertRaises(AutoError):
				PlanCreate(self.context, **options)
		self.assertFalse((self.root / ".auto").exists())

	def testStartupValidatesFeaturesThroughCompositionRoot(self):
		(self.root / ".auto").mkdir()
		file = self.root / ".auto/config.toml"
		with patch.dict("os.environ", {"HOME": str(self.root), "XDG_CONFIG_HOME": str(self.root / "xdg")}, clear=True):
			file.write_text('[memory]\ndefault_agents = ["auto:agent/ux"]\n[memory.agents.ux]\n')
			self.assertEqual(defaultAgents(contextFor(self.root)), ["auto:agent/ux"])
			file.write_text('[resolvers]\n"auto" = "resolver"\n')
			with self.assertRaisesRegex(AutoError, "reserved resolver scheme"):
				contextFor(self.root)
		for file in Path(__file__).resolve().parents[1].joinpath("src/py/auto/runtime").glob("*.py"):
			for node in ast.walk(ast.parse(file.read_text())):
				if isinstance(node, ast.ImportFrom):
					self.assertFalse((node.module or "").startswith("auto.features"), str(file))


class ConfiguredResolverTests(unittest.TestCase):
	def setUp(self):
		temp = tempfile.TemporaryDirectory()
		self.addCleanup(temp.cleanup)
		self.root = Path(temp.name)
		self.file = self.root / "resolver script.py"
		self.context = Context(project=self.root, defaults={"resolvers": {"archive": shlex.join([sys.executable, str(self.file)])}})

	def testExcerptReceivesLiteralReferenceBudgetAndProjectDirectory(self):
		self.file.write_text('import json, sys\nfrom pathlib import Path\nPath("arguments.json").write_text(json.dumps(sys.argv[1:]))\nprint(json.dumps({"source": sys.argv[1], "messages": [{"text": "Recorded text"}], "truncated": True}))\n')
		ref = "archive:conversation/one;touch unwanted"
		context = self.context.derive(maxBytes=2048, allowShell=False)
		content = Resolve(context, ref)
		self.assertEqual(json.loads(content.text)["source"], ref)
		self.assertTrue(json.loads(content.text)["truncated"])
		self.assertEqual(json.loads((self.root / "arguments.json").read_text()), [ref, "--budget", "2048"])
		self.assertFalse((self.root / "unwanted").exists())
		self.assertEqual(context.evidence, [{"ref": ref, "digest": content.digest}])

	def testUnavailableAndMissingExecutablesNeverInventContent(self):
		self.file.write_text('import sys\nprint("Archive is elsewhere", file=sys.stderr)\nsys.exit(3)\n')
		with self.assertRaises(ReferenceUnavailable) as failure:
			Resolve(self.context, "archive:missing")
		self.assertIn("Archive is elsewhere", str(failure.exception))
		self.assertEqual(self.context.evidence, [])
		missing = self.context.derive(defaults={"resolvers": {"archive": "no-such-auto-resolver-4429"}})
		with self.assertRaises(ReferenceUnavailable):
			Resolve(missing, "archive:missing")

	def testMalformedWrongSourceAndFailedResolversAreErrors(self):
		for output in ("not JSON", "[]", '{"x": NaN}', '{"source": "archive:other"}'):
			with self.subTest(output=output):
				self.file.write_text("print(" + repr(output) + ")\n")
				with self.assertRaisesRegex(AutoError, "invalid JSON excerpt"):
					Resolve(self.context, "archive:record")
		self.file.write_text('import sys\nsys.exit(2)\n')
		with self.assertRaisesRegex(AutoError, "failed \\(2\\)"):
			Resolve(self.context, "archive:record")
		self.file.write_text('import sys\nsys.stdout.buffer.write(bytes([255]))\n')
		with self.assertRaisesRegex(AutoError, "invalid UTF-8"):
			Resolve(self.context, "archive:record")

	def testResolverOutputAndExecutionStayBounded(self):
		self.file.write_text('print("x" * 4096)\n')
		with self.assertRaisesRegex(AutoError, "byte limit"):
			Resolve(self.context.derive(maxBytes=128), "archive:large")
		self.file.write_text('import time\ntime.sleep(5)\n')
		with self.assertRaises(AutoError) as failure:
			Resolve(self.context.derive(limits=Limits({"time": .03})), "archive:slow")
		self.assertEqual(failure.exception.kind, "timeout")

	def testPythonResolversDoNotPreventBuiltinsOrMoreSpecificPrefixes(self):
		class Store:
			def readReference(inner, ref):
				return Content("memo:" + ref, b"Stored", "memo-revision", self.root / "stored.md")
		context = self.context.derive(defaults={}, knowledge=Store(), resolvers={"auto:": lambda ctx, ref: Content(ref, b"Custom", "custom-revision", self.root / "custom.md")})
		self.assertEqual(Resolve(context, "auto:custom").text, "Custom")
		self.assertEqual(Resolve(context, "memo:stored").digest, "memo-revision")
		self.assertIn("profile", Resolve(context, "auto:catalogue/profile/default").text)

	def testInvalidResolverConfigurationIsRejected(self):
		for value in ([], {"archive:": "reader"}, {"auto": "reader"}, {"archive": ""}, {"archive": "'unclosed"}, {"archive": ["reader"]}):
			with self.subTest(value=value), self.assertRaises(AutoError):
				validateResolvers(value)
