// Tests for pc_media (T15): volume and media through jarvis-input, independent of the focus.
//
//   node --test host/gentle-dot/tests/*.test.ts   (Node 24+: strips TS types)
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { type Server, createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, describe, it } from "node:test";
import { MEDIA_ACTIONS, buildRequest, requestHelper } from "../extensions/pc-control-core.ts";

const ACTIONS = ["play_pause", "next", "previous", "volume_up", "volume_down", "mute"];

describe("buildRequest pc_media (T15)", () => {
	it("MEDIA_ACTIONS is the closed C4 set the helper accepts", () => {
		assert.deepEqual([...MEDIA_ACTIONS], ACTIONS);
	});
	it("pc_media → media with the action", () => {
		for (const action of ACTIONS) assert.deepEqual(buildRequest("pc_media", { action }), { op: "media", action });
	});
	it("rejects missing or unknown actions before calling the helper", () => {
		for (const action of [undefined, "", "stop", "PLAY_PAUSE", "XF86AudioRaiseVolume", 3, null]) {
			assert.throws(() => buildRequest("pc_media", { action }), /action/, String(action));
		}
	});
});

describe("pc_media tool declaration", () => {
	const src = readFileSync(new URL("../extensions/pc-control.ts", import.meta.url), "utf8");
	const start = src.indexOf('name: "pc_media"');
	const block = start >= 0 ? src.slice(start, src.indexOf("registerTool", start)) : "";

	it("is registered with an enum built from MEDIA_ACTIONS", () => {
		assert.ok(start >= 0, "pc_media is registered");
		assert.match(block, /StringEnum\(MEDIA_ACTIONS/);
	});
	it("tells the agent to use it instead of pc_key for volume and media, without focus or permission", () => {
		assert.match(block, /instead of pc_key/);
		assert.match(block, /XF86Audio/);
		assert.match(block, /no (focused window|window focus)/i);
		assert.match(block, /no \/control on permission/i);
	});
	it("pc_key points volume and media to pc_media", () => {
		const k = src.indexOf('name: "pc_key"');
		assert.match(src.slice(k, src.indexOf("registerTool", k)), /pc_media/);
	});
});

describe("pc_media against a fake agent.sock", () => {
	let dir: string;
	let sockPath: string;
	let server: Server;
	let received: string[];
	let reply: string;

	before(async () => {
		dir = mkdtempSync(join(tmpdir(), "pcmedia-"));
		sockPath = join(dir, "agent.sock");
		server = createServer((sock) => {
			let buf = "";
			sock.on("data", (chunk) => {
				buf += chunk.toString("utf8");
				const nl = buf.indexOf("\n");
				if (nl >= 0) {
					received.push(buf.slice(0, nl));
					sock.write(`${reply}\n`);
				}
			});
			sock.on("error", () => {});
		});
		await new Promise<void>((r) => server.listen(sockPath, r));
	});
	after(async () => {
		await new Promise<void>((r) => server.close(() => r()));
		rmSync(dir, { recursive: true, force: true });
	});

	it("sends one media line", async () => {
		received = [];
		reply = '{"ok":true}';
		assert.deepEqual(await requestHelper(sockPath, buildRequest("pc_media", { action: "volume_up" }), { timeoutMs: 2000 }), {
			ok: true,
		});
		assert.deepEqual(received.map((l) => JSON.parse(l)), [{ op: "media", action: "volume_up" }]);
	});
	it("a no-player refusal comes back verbatim", async () => {
		received = [];
		reply = '{"ok":false,"error":"No hay ningún reproductor activo que acepte esa orden"}';
		await assert.rejects(requestHelper(sockPath, buildRequest("pc_media", { action: "next" }), { timeoutMs: 2000 }), {
			message: "No hay ningún reproductor activo que acepte esa orden",
		});
		assert.deepEqual(received.map((l) => JSON.parse(l)), [{ op: "media", action: "next" }]);
	});
});
