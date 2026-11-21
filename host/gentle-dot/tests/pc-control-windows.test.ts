// Tests for the S14 additions to the agent.sock client: apps, windows and focus.
//
//   node --test host/gentle-dot/tests/*.test.ts   (Node 24+: strips TS types)
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { type Server, createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, describe, it } from "node:test";
import { MAX_APP_LINES, buildRequest, formatApps, formatWindows, requestHelper } from "../extensions/pc-control-core.ts";

describe("buildRequest (S14 agent.sock ops)", () => {
	it("pc_list_apps → apps; the query is filtered locally, never sent", () => {
		assert.deepEqual(buildRequest("pc_list_apps", {}), { op: "apps" });
		assert.deepEqual(buildRequest("pc_list_apps", { query: "notas" }), { op: "apps" });
	});
	it("pc_list_windows → windows", () => {
		assert.deepEqual(buildRequest("pc_list_windows", {}), { op: "windows" });
	});
	it("pc_focus_window → focus with window_id (numeric ids sent as numbers)", () => {
		assert.deepEqual(buildRequest("pc_focus_window", { window_id: 42 }), { op: "focus", window_id: 42 });
		assert.deepEqual(buildRequest("pc_focus_window", { window_id: "42" }), { op: "focus", window_id: 42 });
		assert.deepEqual(buildRequest("pc_focus_window", { window_id: " 7 " }), { op: "focus", window_id: 7 });
	});
	it("pc_focus_window rejects missing, empty, negative, fractional or non-numeric ids", () => {
		assert.throws(() => buildRequest("pc_focus_window", {}), /window_id/);
		assert.throws(() => buildRequest("pc_focus_window", { window_id: "" }), /window_id/);
		assert.throws(() => buildRequest("pc_focus_window", { window_id: -1 }), /window_id/);
		assert.throws(() => buildRequest("pc_focus_window", { window_id: 1.5 }), /window_id/);
		assert.throws(() => buildRequest("pc_focus_window", { window_id: "abc" }), /window_id/);
		assert.throws(() => buildRequest("pc_focus_window", { window_id: Number.NaN }), /window_id/);
	});
});

describe("formatApps", () => {
	const apps = [
		{ id: "dev.duxlabs.shell.notepad", name: "Notepad" },
		{ id: "brave-browser", name: "Brave Web Browser" },
		{ id: "org.gnome.Nautilus", name: "Archivos" },
		{ id: "spotify", name: "Spotify" },
	];
	it("lists every app as 'name — id', sorted by name", () => {
		assert.equal(
			formatApps({ ok: true, apps }),
			"Archivos — org.gnome.Nautilus\nBrave Web Browser — brave-browser\nNotepad — dev.duxlabs.shell.notepad\nSpotify — spotify",
		);
	});
	it("filters by query on name or id, case- and accent-insensitive", () => {
		assert.equal(formatApps({ ok: true, apps }, "NOTE"), "Notepad — dev.duxlabs.shell.notepad");
		assert.equal(formatApps({ ok: true, apps }, "nautilus"), "Archivos — org.gnome.Nautilus");
		assert.equal(formatApps({ ok: true, apps: [{ id: "x.musica", name: "Música" }] }, "musica"), "Música — x.musica");
	});
	it("with no match, says so and falls back to the full list", () => {
		const out = formatApps({ ok: true, apps }, "notas");
		assert.match(out.split("\n")[0], /No app matches "notas"/);
		assert.ok(out.includes("Notepad — dev.duxlabs.shell.notepad"));
	});
	it(`caps the list at ${MAX_APP_LINES} lines and says how many were left out`, () => {
		const many = Array.from({ length: 100 }, (_, i) => ({ id: `app${String(i).padStart(3, "0")}`, name: `App ${String(i).padStart(3, "0")}` }));
		const lines = formatApps({ ok: true, apps: many }).split("\n");
		assert.equal(lines.length, MAX_APP_LINES + 1);
		assert.match(lines.at(-1)!, /40 more/);
		assert.match(lines.at(-1)!, /query/);
	});
	it("skips malformed entries, uses the id when the name is missing, handles an empty list", () => {
		assert.equal(formatApps({ ok: true, apps: [{ id: "a" }, { name: "no id" }, 3, null] }), "a — a");
		assert.match(formatApps({ ok: true, apps: [] }), /No desktop apps/);
		assert.match(formatApps({ ok: true }), /No desktop apps/);
	});
});

describe("formatWindows", () => {
	it("lists 'id · app_id · title' and marks the focused one", () => {
		const out = formatWindows({
			ok: true,
			windows: [
				{ id: 3, app_id: "com.mitchellh.ghostty", title: "jarvis", focused: true },
				{ id: 9, app_id: "dev.duxlabs.shell.notepad", title: "Sin título", focused: false },
			],
		});
		assert.equal(out, "3 · com.mitchellh.ghostty · jarvis · (focused)\n9 · dev.duxlabs.shell.notepad · Sin título");
	});
	it("tolerates missing app_id/title, skips entries without id, handles an empty list", () => {
		assert.equal(formatWindows({ ok: true, windows: [{ id: 1 }, { title: "x" }] }), "1 · ? · ");
		assert.match(formatWindows({ ok: true, windows: [] }), /No open windows/);
		assert.match(formatWindows({ ok: true }), /No open windows/);
	});
});

describe("S14 ops against a fake agent.sock", () => {
	let dir: string;
	let sockPath: string;
	let server: Server;
	let received: string[];
	let reply: string;

	before(async () => {
		dir = mkdtempSync(join(tmpdir(), "pcctl14-"));
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

	it("sends one apps line and returns the apps", async () => {
		received = [];
		reply = '{"ok":true,"apps":[{"id":"spotify","name":"Spotify"}]}';
		const res = await requestHelper(sockPath, buildRequest("pc_list_apps", { query: "spo" }), { timeoutMs: 2000 });
		assert.deepEqual(received.map((l) => JSON.parse(l)), [{ op: "apps" }]);
		assert.equal(formatApps(res, "spo"), "Spotify — spotify");
	});
	it("windows refusal comes back verbatim", async () => {
		received = [];
		reply = '{"ok":false,"error":"control is off: run /control on in the Jarvis TUI"}';
		await assert.rejects(requestHelper(sockPath, buildRequest("pc_list_windows", {}), { timeoutMs: 2000 }), {
			message: "control is off: run /control on in the Jarvis TUI",
		});
		assert.deepEqual(received.map((l) => JSON.parse(l)), [{ op: "windows" }]);
	});
	it("focus sends the numeric id; a terminal refusal comes back verbatim", async () => {
		received = [];
		reply = '{"ok":false,"error":"window 3 is a terminal and is blocked"}';
		await assert.rejects(requestHelper(sockPath, buildRequest("pc_focus_window", { window_id: "3" }), { timeoutMs: 2000 }), {
			message: "window 3 is a terminal and is blocked",
		});
		assert.deepEqual(received.map((l) => JSON.parse(l)), [{ op: "focus", window_id: 3 }]);
	});
});

describe("S16 window_id on pc_type / pc_key", () => {
	it("forwards window_id as a number when given", () => {
		assert.deepEqual(buildRequest("pc_type", { text: "hola", intent: "nota", window_id: 11 }), {
			op: "type",
			text: "hola",
			intent: "nota",
			window_id: 11,
		});
		assert.deepEqual(buildRequest("pc_key", { keys: "ctrl+n", intent: "nuevo", window_id: "11" }), {
			op: "key",
			keys: "ctrl+n",
			intent: "nuevo",
			window_id: 11,
		});
	});
	it("omits window_id when absent (unchanged behaviour)", () => {
		assert.deepEqual(buildRequest("pc_type", { text: "hola", intent: "nota" }), { op: "type", text: "hola", intent: "nota" });
		assert.deepEqual(buildRequest("pc_key", { keys: "a", intent: "x", window_id: undefined }), { op: "key", keys: "a", intent: "x" });
	});
	it("rejects a bad window_id", () => {
		for (const window_id of [-1, 1.5, "abc", "", Number.NaN, true]) {
			assert.throws(() => buildRequest("pc_type", { text: "hola", intent: "x", window_id }), /window_id/);
			assert.throws(() => buildRequest("pc_key", { keys: "a", intent: "x", window_id }), /window_id/);
		}
	});
	it("S17: pc_screenshot forwards window_id when given, omits it when absent, rejects bad ids", () => {
		assert.deepEqual(buildRequest("pc_screenshot", { window_id: 11 }), { op: "screenshot", scale: 0.5, window_id: 11 });
		assert.deepEqual(buildRequest("pc_screenshot", { scale: 1, window_id: "11" }), { op: "screenshot", scale: 1, window_id: 11 });
		assert.deepEqual(buildRequest("pc_screenshot", { window_id: undefined }), { op: "screenshot", scale: 0.5 });
		for (const window_id of [-1, 1.5, "abc", "", Number.NaN, true]) {
			assert.throws(() => buildRequest("pc_screenshot", { window_id }), /window_id/);
		}
	});
	it("S17: pc_screenshot declares an optional window_id", () => {
		const src = readFileSync(new URL("../extensions/pc-control.ts", import.meta.url), "utf8");
		const start = src.indexOf('name: "pc_screenshot"');
		assert.ok(start >= 0);
		const block = src.slice(start, src.indexOf("pi.on(", start));
		assert.match(block, /window_id: Type\.Optional\(Type\.Integer\(/);
		assert.match(block, /focus (returns|goes back)/i);
	});
	it("pc_type and pc_key declare an optional window_id and say focus goes back", () => {
		const src = readFileSync(new URL("../extensions/pc-control.ts", import.meta.url), "utf8");
		for (const name of ["pc_type", "pc_key"]) {
			const start = src.indexOf(`name: "${name}"`);
			assert.ok(start >= 0, name);
			const block = src.slice(start, src.indexOf("registerTool", start));
			assert.match(block, /window_id: Type\.Optional\(Type\.Integer\(/, name);
			assert.match(block, /focus (returns|goes back)/i, name);
		}
	});
});
