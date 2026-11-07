import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { OpenCode } from "@opencode/client";
import { Service } from "@opencode/client/service";

// Keep the Python engine's wire contract while the client owns HTTP and auth.
export async function request(client, method, path, data) {
	if (method === "post" && path === "/api/session") {
		return client.session.create(data);
	}
	const url = new URL(path, "http://localhost");
	const match = url.pathname.match(/^\/api\/session\/([^/]+)\/(prompt|interrupt|message|form|permission)(?:\/([^/]+)\/reply)?$/);
	if (!match) throw new Error(`Unsupported OpenCode request: ${method} ${path}`);
	const [, sessionID, resource, id] = match;
	const input = { ...data, sessionID };
	if (method === "post") {
		if (resource === "prompt" && !id) return client.session.prompt(input);
		if (resource === "interrupt" && !id) return client.session.interrupt(input);
		if (resource === "form" && id) return client.session.form.reply({ ...input, formID: id });
		if (resource === "permission" && id) return client.permission.reply({ ...input, requestID: id });
	}
	if (method === "get" && !id) {
		if (resource === "form") return { data: await client.session.form.list(input) };
		if (resource === "permission") return { data: await client.permission.list(input) };
		if (resource === "message") {
			const query = Object.fromEntries(url.searchParams);
			if (query.limit !== undefined) query.limit = Number(query.limit);
			return client.message.list({ ...query, sessionID });
		}
	}
	throw new Error(`Unsupported OpenCode request: ${method} ${path}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
	try {
		const { method, path, data } = JSON.parse(readFileSync(0, "utf8"));
		const endpoint = await Service.ensure();
		const client = OpenCode.make({ baseUrl: endpoint.url, headers: Service.headers(endpoint) });
		const result = await request(client, method, path, data);
		process.stdout.write(JSON.stringify(result ?? {}) + "\n");
	} catch (error) {
		console.error(error.message);
		process.exitCode = 1;
	}
}
