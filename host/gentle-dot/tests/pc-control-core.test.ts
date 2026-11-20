// Tests for the pure logic and the agent.sock client of the pc-control extension (S13).
//
//   node --test host/gentle-dot/tests/*.test.ts   (Node 24+: strips TS types)
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { type Server, type Socket, createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, describe, it } from "node:test";
import {
	HELPER_TIMEOUT_MS,
	agentSocketPath,
	bashBlockReason,
	buildRequest,
	controlDirPath,
	decodeResponse,
	encodeRequest,
	isProtectedPath,
	requestHelper,
} from "../extensions/pc-control-core.ts";

describe("socket paths", () => {
	it("agent.sock lives in $XDG_RUNTIME_DIR/jarvis", () => {
		assert.equal(agentSocketPath({ XDG_RUNTIME_DIR: "/run/user/1000" }, 1000), "/run/user/1000/jarvis/agent.sock");
	});
	it("falls back to /run/user/<uid> when unset or relative", () => {
		assert.equal(agentSocketPath({}, 1234), "/run/user/1234/jarvis/agent.sock");
		assert.equal(agentSocketPath({ XDG_RUNTIME_DIR: "rel" }, 1234), "/run/user/1234/jarvis/agent.sock");
	});
	it("control dir is $XDG_RUNTIME_DIR/jarvis-control", () => {
		assert.equal(controlDirPath({ XDG_RUNTIME_DIR: "/run/user/1000" }, 1000), "/run/user/1000/jarvis-control");
	});
	it("timeout leaves room for the helper's 30 s confirmation", () => {
		assert.ok(HELPER_TIMEOUT_MS >= 40_000 && HELPER_TIMEOUT_MS <= 60_000);
	});
});

describe("buildRequest (S13 agent.sock ops)", () => {
	it("pc_open_app → open with app", () => {
		assert.deepEqual(buildRequest("pc_open_app", { app: "brave-browser" }), { op: "open", app: "brave-browser" });
	});
	it("pc_open_url → open with url", () => {
		assert.deepEqual(buildRequest("pc_open_url", { url: "https://example.com/a?b=1" }), {
			op: "open",
			url: "https://example.com/a?b=1",
		});
	});
	it("pc_type → type with text and intent", () => {
		assert.deepEqual(buildRequest("pc_type", { text: "hola\nmundo", intent: "saludar" }), {
			op: "type",
			text: "hola\nmundo",
			intent: "saludar",
		});
	});
	it("pc_key → key with keys and intent", () => {
		assert.deepEqual(buildRequest("pc_key", { keys: "ctrl+l", intent: "barra" }), { op: "key", keys: "ctrl+l", intent: "barra" });
	});
	it("pc_screenshot → screenshot, scale default 0.5 and clamped to [0.1, 1]", () => {
		assert.deepEqual(buildRequest("pc_screenshot", {}), { op: "screenshot", scale: 0.5 });
		assert.deepEqual(buildRequest("pc_screenshot", { scale: 0.25 }), { op: "screenshot", scale: 0.25 });
		assert.deepEqual(buildRequest("pc_screenshot", { scale: 5 }), { op: "screenshot", scale: 1 });
		assert.deepEqual(buildRequest("pc_screenshot", { scale: 0 }), { op: "screenshot", scale: 0.1 });
		assert.deepEqual(buildRequest("pc_screenshot", { scale: Number.NaN }), { op: "screenshot", scale: 0.5 });
	});
	it("rejects missing or non-string fields and unknown tools", () => {
		assert.throws(() => buildRequest("pc_open_app", {}));
		assert.throws(() => buildRequest("pc_open_app", { app: "" }));
		assert.throws(() => buildRequest("pc_open_url", { url: 3 }));
		assert.throws(() => buildRequest("pc_type", { text: "x" }));
		assert.throws(() => buildRequest("pc_key", { intent: "x" }));
		assert.throws(() => buildRequest("pc_nope", {}));
	});
});

describe("encodeRequest / decodeResponse", () => {
	it("encodes one JSON line, newlines in strings escaped", () => {
		const line = encodeRequest({ op: "type", text: "a\nb", intent: "i" });
		assert.ok(line.endsWith("\n"));
		assert.equal(line.indexOf("\n"), line.length - 1);
		assert.deepEqual(JSON.parse(line), { op: "type", text: "a\nb", intent: "i" });
	});
	it("returns the object when ok is true", () => {
		assert.deepEqual(decodeResponse('{"ok":true,"png_base64":"AAA"}'), { ok: true, png_base64: "AAA" });
	});
	it("throws the helper's message when ok is false", () => {
		assert.throws(
			() => decodeResponse('{"ok":false,"error":"control is off: run /control on in the Jarvis TUI"}'),
			{ message: "control is off: run /control on in the Jarvis TUI" },
		);
	});
	it("throws on ok false without a message, on garbage, and on non-objects", () => {
		assert.throws(() => decodeResponse('{"ok":false}'), /jarvis-input/);
		assert.throws(() => decodeResponse("not json"), /jarvis-input/);
		assert.throws(() => decodeResponse("[1]"), /jarvis-input/);
		assert.throws(() => decodeResponse('{"ok":"yes"}'), /jarvis-input/);
	});
});

describe("requestHelper against a fake agent.sock", () => {
	let dir: string;
	let sockPath: string;
	let server: Server;
	let received: string[];
	let behavior: (sock: Socket, line: string) => void;
	let closed: Promise<void>;
	let markClosed: () => void;

	before(async () => {
		dir = mkdtempSync(join(tmpdir(), "pcctl-"));
		sockPath = join(dir, "agent.sock");
		server = createServer((sock) => {
			let buf = "";
			sock.on("data", (chunk) => {
				buf += chunk.toString("utf8");
				const nl = buf.indexOf("\n");
				if (nl >= 0) {
					const line = buf.slice(0, nl);
					received.push(line);
					behavior(sock, line);
				}
			});
			sock.on("close", () => markClosed?.());
			sock.on("error", () => {});
		});
		await new Promise<void>((r) => server.listen(sockPath, r));
	});
	after(async () => {
		await new Promise<void>((r) => server.close(() => r()));
		rmSync(dir, { recursive: true, force: true });
	});
	const reset = (b: typeof behavior) => {
		received = [];
		behavior = b;
		closed = new Promise<void>((r) => {
			markClosed = r;
		});
	};

	it("sends one newline-terminated JSON request and resolves with the ok response", async () => {
		reset((sock) => sock.end('{"ok":true,"opened":"brave-browser"}\n'));
		const res = await requestHelper(sockPath, { op: "open", app: "brave-browser" }, { timeoutMs: 2000 });
		assert.deepEqual(res, { ok: true, opened: "brave-browser" });
		assert.deepEqual(received.map((l) => JSON.parse(l)), [{ op: "open", app: "brave-browser" }]);
	});

	it("returns the helper's refusal as an error with its message", async () => {
		reset((sock) => sock.end('{"ok":false,"error":"control is off: run /control on in the Jarvis TUI"}\n'));
		await assert.rejects(requestHelper(sockPath, { op: "type", text: "x", intent: "y" }, { timeoutMs: 2000 }), {
			message: "control is off: run /control on in the Jarvis TUI",
		});
	});

	it("assembles a large reply that arrives in pieces", async () => {
		const png = "A".repeat(3 * 1024 * 1024);
		reset((sock) => {
			const line = `${JSON.stringify({ ok: true, png_base64: png })}\n`;
			sock.write(line.slice(0, 1000));
			setTimeout(() => sock.end(line.slice(1000)), 20);
		});
		const res = await requestHelper(sockPath, { op: "screenshot", scale: 0.5 }, { timeoutMs: 5000 });
		assert.equal(res.png_base64, png);
	});

	it("fails when the reply is bigger than maxBytes", async () => {
		reset((sock) => sock.end(`${JSON.stringify({ ok: true, png_base64: "A".repeat(10_000) })}\n`));
		await assert.rejects(requestHelper(sockPath, { op: "screenshot", scale: 0.5 }, { timeoutMs: 2000, maxBytes: 1000 }), /too large/);
	});

	it("fails when the helper hangs up without answering", async () => {
		reset((sock) => sock.end());
		await assert.rejects(requestHelper(sockPath, { op: "key", keys: "a", intent: "b" }, { timeoutMs: 2000 }), /jarvis-input/);
	});

	it("fails on an unreadable reply", async () => {
		reset((sock) => sock.end("garbage\n"));
		await assert.rejects(requestHelper(sockPath, { op: "key", keys: "a", intent: "b" }, { timeoutMs: 2000 }), /jarvis-input/);
	});

	it("times out and closes the socket when the helper never answers", async () => {
		reset(() => {});
		await assert.rejects(requestHelper(sockPath, { op: "type", text: "x", intent: "y" }, { timeoutMs: 150 }), /did not answer/);
		await closed;
	});

	it("an abort closes the socket and rejects", async () => {
		reset(() => {});
		const ac = new AbortController();
		const p = requestHelper(sockPath, { op: "type", text: "x", intent: "y" }, { timeoutMs: 5000, signal: ac.signal });
		setTimeout(() => ac.abort(), 50);
		await assert.rejects(p, /cancel/);
		await closed;
	});

	it("an already-aborted signal never connects", async () => {
		reset(() => assert.fail("should not connect"));
		const ac = new AbortController();
		ac.abort();
		await assert.rejects(requestHelper(sockPath, { op: "screenshot", scale: 0.5 }, { timeoutMs: 1000, signal: ac.signal }), /cancel/);
		assert.deepEqual(received, []);
	});

	it("says jarvis-input is not running when the socket is missing", async () => {
		await assert.rejects(requestHelper(join(dir, "missing.sock"), { op: "open", app: "x" }, { timeoutMs: 1000 }), /jarvis-input is not running/);
	});

	it("says jarvis-input is not running when nothing listens on the path", async () => {
		const stale = join(dir, "stale.sock");
		writeFileSync(stale, "");
		await assert.rejects(requestHelper(stale, { op: "open", app: "x" }, { timeoutMs: 1000 }), /jarvis-input is not running/);
	});
});

describe("bashBlockReason (defense in depth, not the boundary)", () => {
	const blocked = [
		"wtype hola",
		"/usr/bin/wtype -k Return",
		"echo x | wtype -",
		"grim /tmp/s.png",
		'grim -g "$(slurp)" - > a.png',
		"ydotool type hi",
		"sudo ydotoold &",
		"dotool <<< 'type x'",
		"wlrctl keyboard type x",
		"niri msg action focus-workspace 2",
		"niri msg -j action spawn -- kitty",
		"systemd-run --user wtype x",
		"systemctl --user stop jarvis-input.service",
		"systemctl stop --user gentle-dot",
		"echo '{\"op\":\"grant\"}' | socat - UNIX:$XDG_RUNTIME_DIR/jarvis-control/control.sock",
		"ls /run/user/1000/jarvis-control",
		"cat ~/.local/share/jarvis/input/jarvis_input.py",
		"sed -i s/x/y/ ~/.gentle-dot/agent/extensions/jarvis-pc-control/index.ts",
		"cp evil.ts ~/.gentle-dot/agent/extensions/x/index.ts",
		"echo {} > ~/.gentle-dot/agent/settings.json",
	];
	for (const cmd of blocked) {
		it(`blocks: ${cmd}`, () => {
			const r = bashBlockReason(cmd);
			assert.ok(r);
			assert.match(r!, /pc_/);
		});
	}
	const allowed = [
		"ls ~/Pictures",
		"pgrep -f brave",
		"niri msg -j windows",
		"echo grimace",
		"git log --grep=wtyper",
		"systemctl status sshd",
		"cat ~/.local/share/jarvisfoo.txt",
	];
	for (const cmd of allowed) {
		it(`allows: ${cmd}`, () => {
			assert.equal(bashBlockReason(cmd), null);
		});
	}
});

describe("isProtectedPath", () => {
	const opts = {
		cwd: "/home/u/work",
		home: "/home/u",
		protectedDirs: [
			"/run/user/1000/jarvis",
			"/run/user/1000/jarvis-control",
			"/home/u/.local/share/jarvis",
			"/home/u/.gentle-dot/agent/extensions",
		],
		protectedFiles: ["/home/u/.gentle-dot/agent/settings.json"],
	};
	const prot = [
		"/run/user/1000/jarvis/agent.sock",
		"/run/user/1000/jarvis-control/control.sock",
		"/run/user/1000/jarvis-control/../jarvis-control/x",
		"~/.local/share/jarvis/input/jarvis_input.py",
		"~/.gentle-dot/agent/extensions/jarvis-pc-control/index.ts",
		"../.gentle-dot/agent/extensions/new/index.ts",
		"~/.gentle-dot/agent/settings.json",
		"/home/u/.gentle-dot/agent/./settings.json",
		"/other/home/.gentle-dot/agent/settings.json",
		"/x/extensions/jarvis-pc-control/index.ts",
		"/repo/host/gentle-dot/extensions/pc-control.ts",
	];
	for (const p of prot) it(`protects ${p}`, () => assert.equal(isProtectedPath(p, opts), true));
	const free = [
		"notes.md",
		"/home/u/.gentle-dot/agent/AGENTS.md.notes",
		"/run/user/1000/other.json",
		"/home/u/.local/share/jarvisfoo",
		"/home/u/project/settings.json",
	];
	for (const p of free) it(`allows ${p}`, () => assert.equal(isProtectedPath(p, opts), false));
});
