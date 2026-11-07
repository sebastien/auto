"""Module: test_opencode_bridge — Stdin transport and V2 client wire contracts."""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from auto.integration.opencode import engine
from auto.integration.process import capture
from auto.runtime.model import Context
from auto.utils.data import AutoError


class OpenCodeBridgeTests(unittest.TestCase):
	def testRequestsUseStdin(self):
		body = {"text": "é\n\"" * 100000}
		context = Context()
		config = {"env": {"BRIDGE_TEST": "yes"}}
		with patch.object(engine, "capture", return_value=('{}', '', 0)) as run:
			self.assertEqual(engine.OpenCode().api(context, "post", "/api/session/ses_test/prompt", body, config), {})
			options, args, passedConfig, stdin = run.call_args.args
			self.assertEqual(options, context.process)
			self.assertEqual(args, ["node", str(Path(engine.__file__).with_name("bridge.mjs"))])
			self.assertIs(passedConfig, config)
			self.assertEqual(json.loads(stdin), {"method": "post", "path": "/api/session/ses_test/prompt", "data": body})
			self.assertGreater(len(stdin.encode()), 131072)
		for result, reason in ((('', 'bridge failed', 1), "bridge failed"), (('invalid', '', 0), "invalid API response")):
			with self.subTest(result=result), patch.object(engine, "capture", return_value=result):
				with self.assertRaisesRegex(AutoError, reason):
					engine.OpenCode().api(context, "get", "/api/session/ses_test/form")

	def testClientWireContract(self):
		root = Path(engine.__file__).parent
		if not shutil.which("node") or not (root / "node_modules/@opencode/client").exists():
			self.skipTest("Requires Node.js and npm ci --prefix src/py/auto/integration/opencode")
		stdout, stderr, code = capture(Context().process, ["node", "--test", str(root / "bridge.test.mjs")], {})
		self.assertEqual(code, 0, stdout + stderr)

# EOF
