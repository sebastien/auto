import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { OpenCode } from "@opencode/client";
import { request } from "./bridge.mjs";

test("bridge executable reads large JSON from stdin without contacting a real service", () => {
	const body = { text: "é\n\"".repeat(100000) };
	const script = `
		import { Service } from "@opencode/client/service";
		Service.ensure = async () => ({ url: "http://localhost:4096" });
		globalThis.fetch = async (url, init) => new Response(JSON.stringify({ data: JSON.parse(init.body) }), {
			status: 200, headers: { "content-type": "application/json" },
		});
		process.argv[1] = new URL("./bridge.mjs", import.meta.url).pathname;
		await import("./bridge.mjs");
	`;
	const result = spawnSync(process.execPath, ["--input-type=module", "-e", script], {
		cwd: new URL(".", import.meta.url),
		input: JSON.stringify({ method: "post", path: "/api/session/ses_test/prompt", data: body }),
		encoding: "utf8", maxBuffer: 4 * 1024 * 1024, timeout: 10000,
	});
	assert.equal(result.status, 0, result.stderr);
	assert.deepEqual(JSON.parse(result.stdout), body);
});

test("V2 bridge preserves payloads, pagination, forms, permissions and errors", async () => {
	let response = { data: [] };
	let status = 200;
	let sent;
	const client = OpenCode.make({
		baseUrl: "http://localhost:4096",
		headers: { authorization: "Basic test" },
		fetch: async (url, init) => {
			sent = { url, ...init };
			return new Response(status === 204 ? null : JSON.stringify(response), {
				status, headers: { "content-type": "application/json" },
			});
		},
	});
	const body = { id: "msg_test", text: "é\n\"".repeat(100000) };
	await request(client, "post", "/api/session/ses_test/prompt", body);
	assert.deepEqual(JSON.parse(sent.body), body);
	assert.equal(sent.headers.get("authorization"), "Basic test");
	assert.equal(sent.method, "POST");
	assert.equal(sent.url.pathname, "/api/session/ses_test/prompt");

	const creation = { id: "ses_test", location: { directory: "/workspace" }, permissions: [] };
	await request(client, "post", "/api/session", creation);
	assert.deepEqual(JSON.parse(sent.body), creation);

	response = { data: [{ id: "msg_test" }], cursor: { next: "a+b/c=" } };
	assert.deepEqual(await request(client, "get", "/api/session/ses_test/message?order=asc&limit=100&cursor=a%2Bb%2Fc%3D"), response);
	assert.equal(sent.url.searchParams.get("cursor"), "a+b/c=");
	assert.equal(sent.url.searchParams.get("limit"), "100");
	assert.equal(sent.url.searchParams.get("order"), "asc");

	response = { data: [{ id: "pending" }] };
	for (const resource of ["form", "permission"]) {
		assert.deepEqual(await request(client, "get", `/api/session/ses_test/${resource}`), response);
		assert.equal(sent.url.pathname, `/api/session/ses_test/${resource}`);
		status = 204;
		const reply = resource === "form" ? { answer: { value: "yes" } } : { decision: "once" };
		assert.equal(await request(client, "post", `/api/session/ses_test/${resource}/pending/reply`, reply), undefined);
		assert.deepEqual(JSON.parse(sent.body), reply);
		assert.equal(sent.url.pathname, `/api/session/ses_test/${resource}/pending/reply`);
		status = 200;
	}
	response = { interrupted: true };
	assert.deepEqual(await request(client, "post", "/api/session/ses_test/interrupt"), response);
	status = 500;
	await assert.rejects(request(client, "post", "/api/session/ses_test/prompt", body));
	await assert.rejects(request(client, "get", "/unsupported"), /Unsupported OpenCode request/);
});
