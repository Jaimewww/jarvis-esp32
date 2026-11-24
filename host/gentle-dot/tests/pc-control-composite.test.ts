// Tests for T10 (P2, odd/tasks/latencia.md S9): composite tools pc_write_in / pc_run, no
// screenshot by default, and the current date and time in the agent's context.
//
//   node --test host/gentle-dot/tests/*.test.ts   (Node 24+: strips TS types)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import {
	type HelperRequest,
	TOOLS_HINT,
	appMatchesWindow,
	clockContext,
	compositeRequest,
	runInApp,
} from "../extensions/pc-control-core.ts";

type Win = { id: number; app_id: string; title: string; focused: boolean };

/** A fake helper: replies to `windows` from a script of snapshots, records every request. */
function fakeHelper(snapshots: Win[][], replies: Partial<Record<string, Record<string, unknown> | Error>> = {}) {
	const sent: HelperRequest[] = [];
	let polls = 0;
	const call = async (req: HelperRequest) => {
		sent.push(req);
		if (req.op === "windows") {
			const snap = snapshots[Math.min(polls, snapshots.length - 1)];
			polls += 1;
			return { ok: true, windows: snap };
		}
		const r = replies[req.op];
		if (r instanceof Error) throw r;
		return r ?? { ok: true };
	};
	return { call, sent };
}

const noSleep = async () => {};
const term: Win = { id: 1, app_id: "com.mitchellh.ghostty", title: "jarvis", focused: true };
const code: Win = { id: 7, app_id: "code", title: "notas.md - Visual Studio Code", focused: false };

describe("appMatchesWindow", () => {
	it("matches the desktop id against the niri app_id, case-insensitive, or by its last dotted part", () => {
		assert.equal(appMatchesWindow("brave-browser", "brave-browser"), true);
		assert.equal(appMatchesWindow("org.gnome.Nautilus", "org.gnome.Nautilus"), true);
		assert.equal(appMatchesWindow("org.gnome.Nautilus", "nautilus"), true);
		assert.equal(appMatchesWindow("Code", "code"), true);
		assert.equal(appMatchesWindow("code.desktop", "code"), true);
	});
	it("does not match other apps or empty ids", () => {
		assert.equal(appMatchesWindow("spotify", "brave-browser"), false);
		assert.equal(appMatchesWindow("code", "codeblocks"), false);
		assert.equal(appMatchesWindow("", "code"), false);
		assert.equal(appMatchesWindow("code", ""), false);
	});
});

describe("compositeRequest", () => {
	it("pc_write_in → type, pc_run → key, both keep the intent", () => {
		assert.deepEqual(compositeRequest("pc_write_in", { app: "code", text: "hola", intent: "escribir hola" }), {
			app: "code",
			input: { op: "type", text: "hola", intent: "escribir hola" },
		});
		assert.deepEqual(compositeRequest("pc_run", { app: "code", keys: "ctrl+s", intent: "guardar" }), {
			app: "code",
			input: { op: "key", keys: "ctrl+s", intent: "guardar" },
		});
	});
	it("rejects missing fields before calling the helper", () => {
		assert.throws(() => compositeRequest("pc_write_in", { text: "x", intent: "i" }), /app/);
		assert.throws(() => compositeRequest("pc_write_in", { app: "code", intent: "i" }), /text/);
		assert.throws(() => compositeRequest("pc_write_in", { app: "code", text: "x" }), /intent/);
		assert.throws(() => compositeRequest("pc_run", { app: "code", intent: "i" }), /keys/);
		assert.throws(() => compositeRequest("pc_type", { app: "code", text: "x", intent: "i" }), /unknown/);
	});
});

describe("runInApp", () => {
	it("uses an already open window: no open, one type with its window_id", async () => {
		const h = fakeHelper([[term, code]]);
		const out = await runInApp(h.call, compositeRequest("pc_write_in", { app: "code", text: "hola", intent: "i" }), { sleep: noSleep });
		assert.deepEqual(h.sent, [{ op: "windows" }, { op: "type", text: "hola", intent: "i", window_id: 7 }]);
		assert.equal(out.opened, false);
		assert.deepEqual(out.window, code);
	});
	it("prefers the focused window when the app has several", async () => {
		const other: Win = { id: 9, app_id: "code", title: "b", focused: true };
		const h = fakeHelper([[code, other]]);
		await runInApp(h.call, compositeRequest("pc_run", { app: "code", keys: "ctrl+s", intent: "i" }), { sleep: noSleep });
		assert.deepEqual(h.sent.at(-1), { op: "key", keys: "ctrl+s", intent: "i", window_id: 9 });
	});
	it("opens the app when it has no window and waits for it to appear", async () => {
		const h = fakeHelper([[term], [term], [term, code]]);
		const out = await runInApp(h.call, compositeRequest("pc_write_in", { app: "code", text: "x", intent: "i" }), { sleep: noSleep });
		assert.deepEqual(h.sent, [
			{ op: "windows" },
			{ op: "open", app: "code" },
			{ op: "windows" },
			{ op: "windows" },
			{ op: "type", text: "x", intent: "i", window_id: 7 },
		]);
		assert.equal(out.opened, true);
	});
	it("after opening, accepts the single new window even if its app_id differs from the desktop id", async () => {
		const notes: Win = { id: 12, app_id: "notepad", title: "Notas", focused: false };
		const h = fakeHelper([[term], [term, notes]]);
		const out = await runInApp(
			h.call,
			compositeRequest("pc_write_in", { app: "dev.duxlabs.shell.notepad", text: "x", intent: "i" }),
			{ sleep: noSleep },
		);
		assert.deepEqual(h.sent.at(-1), { op: "type", text: "x", intent: "i", window_id: 12 });
		assert.equal(out.window.id, 12);
	});
	it("gives up when no window shows up in time, without typing", async () => {
		let t = 0;
		const h = fakeHelper([[term]]);
		await assert.rejects(
			runInApp(h.call, compositeRequest("pc_write_in", { app: "code", text: "x", intent: "i" }), {
				sleep: async (ms: number) => {
					t += ms;
				},
				now: () => t,
				waitMs: 2000,
				pollMs: 500,
			}),
			/code.*no window/i,
		);
		assert.equal(h.sent.filter((r) => r.op === "type").length, 0);
		assert.equal(h.sent.filter((r) => r.op === "open").length, 1);
	});
	it("a refusal stops the chain and comes back verbatim", async () => {
		const h = fakeHelper([[term]], { open: new Error("Esa app no se abre: es una terminal") });
		await assert.rejects(
			runInApp(h.call, compositeRequest("pc_write_in", { app: "kitty", text: "x", intent: "i" }), { sleep: noSleep }),
			{ message: "Esa app no se abre: es una terminal" },
		);
		assert.deepEqual(h.sent, [{ op: "windows" }, { op: "open", app: "kitty" }]);
		const h2 = fakeHelper([[term, code]], { type: new Error("type denegado: sin permiso") });
		await assert.rejects(
			runInApp(h2.call, compositeRequest("pc_write_in", { app: "code", text: "x", intent: "i" }), { sleep: noSleep }),
			{ message: "type denegado: sin permiso" },
		);
	});
});

describe("clockContext", () => {
	it("gives the local date, weekday, time, zone and offset, and says not to read the clock", () => {
		const s = clockContext(new Date("2026-11-24T13:05:00Z"), "America/Guayaquil");
		for (const part of ["2026-11-24", "Tuesday", "08:05", "America/Guayaquil", "UTC-05:00"]) assert.ok(s.includes(part), `${part} in ${s}`);
		assert.match(s, /do not run/i);
	});
	it("falls back to UTC for an unknown zone", () => {
		const s = clockContext(new Date("2026-11-24T13:05:00Z"), "Nowhere/Nope");
		assert.ok(s.includes("13:05") && s.includes("UTC"), s);
	});
});

describe("T10 declarations and agent rules", () => {
	const src = readFileSync(new URL("../extensions/pc-control.ts", import.meta.url), "utf8");
	const block = (name: string) => {
		const i = src.indexOf(`name: "${name}"`);
		return i >= 0 ? src.slice(i, src.indexOf("registerTool", i)) : "";
	};
	const agents = readFileSync(new URL("../AGENTS.md", import.meta.url), "utf8");

	it("registers pc_write_in and pc_run", () => {
		assert.match(block("pc_write_in"), /composite\("pc_write_in"/);
		assert.match(block("pc_run"), /composite\("pc_run"/);
		assert.match(src, /runInApp\(\(req\) => callRequest\(req, signal\), compositeRequest\(tool, params\)\)/);
	});
	it("pc_screenshot is for failures or when the user asks, not to verify every action", () => {
		const b = block("pc_screenshot");
		assert.doesNotMatch(b, /verify after/);
		assert.match(b, /only when/i);
	});
	it("injects the clock before each user turn", () => {
		assert.match(src, /before_agent_start/);
		assert.match(src, /clockContext/);
	});
	it("TOOLS_HINT and AGENTS.md name the composite tools and drop the screenshot after each action", () => {
		assert.match(TOOLS_HINT, /pc_write_in/);
		assert.match(TOOLS_HINT, /pc_run/);
		assert.match(agents, /pc_write_in/);
		assert.match(agents, /pc_run/);
		assert.doesNotMatch(agents, /After acting on a window, verify with `pc_screenshot`/);
		assert.match(agents, /date and time/i);
	});
});
