/**
 * Logic of the Jarvis pc-control extension (S13, odd/tasks/s2-round2.md): the agent.sock client
 * of the jarvis-input helper, the request builders, and the `tool_call` guards. No Pi imports, so
 * `node --test` runs it directly (Node 24 strips the types). See host/gentle-dot/README.md.
 *
 * The boundary is the jarvis-input helper (sole owner of the display, permission in memory, all
 * S12 checks) plus the sandbox of gentle-dot.service. The guards here are defense in depth only.
 */
import { connect } from "node:net";
import { isAbsolute, join, normalize, resolve, sep } from "node:path";

/** Client timeout: the helper may wait up to 30 s for the user's confirmation in the TUI. */
export const HELPER_TIMEOUT_MS = 45_000;
/** Largest reply accepted (a full-scale screenshot in base64 is well below this). */
export const MAX_REPLY_BYTES = 48 * 1024 * 1024;

function runtimeDir(env: Record<string, string | undefined>, uid: number): string {
	return env.XDG_RUNTIME_DIR && isAbsolute(env.XDG_RUNTIME_DIR) ? env.XDG_RUNTIME_DIR : `/run/user/${uid}`;
}

/** `$XDG_RUNTIME_DIR/jarvis/agent.sock` (S13), `/run/user/<uid>` when the variable is unusable. */
export function agentSocketPath(env: Record<string, string | undefined>, uid: number): string {
	return join(runtimeDir(env, uid), "jarvis", "agent.sock");
}

/** `$XDG_RUNTIME_DIR/jarvis-control`: the TUI's control socket folder, never for the agent. */
export function controlDirPath(env: Record<string, string | undefined>, uid: number): string {
	return join(runtimeDir(env, uid), "jarvis-control");
}

export type HelperRequest =
	| { op: "open"; app: string }
	| { op: "open"; url: string }
	| { op: "type"; text: string; intent: string; window_id?: number }
	| { op: "key"; keys: string; intent: string; window_id?: number }
	| { op: "screenshot"; scale: number; window_id?: number }
	| { op: "apps" }
	| { op: "windows" }
	| { op: "focus"; window_id: number }
	| { op: "media"; action: MediaAction };

/** T15: the closed media/volume set of jarvis-input's `media` op (C4: playerctl and wpctl). */
export const MEDIA_ACTIONS = ["play_pause", "next", "previous", "volume_up", "volume_down", "mute", "unmute"] as const;
export type MediaAction = (typeof MEDIA_ACTIONS)[number];

function mediaAction(params: Record<string, unknown>): MediaAction {
	const v = params.action;
	if (typeof v !== "string" || !(MEDIA_ACTIONS as readonly string[]).includes(v))
		throw new Error(`action must be one of: ${MEDIA_ACTIONS.join(", ")}`);
	return v as MediaAction;
}

/** Most lines `formatApps` returns (plus one "… more" line), to keep the tool result small. */
export const MAX_APP_LINES = 60;

/** niri window ids are non-negative integers; numeric strings are accepted and sent as numbers. */
function windowId(params: Record<string, unknown>): number {
	const v = params.window_id;
	const n = typeof v === "number" ? v : typeof v === "string" && /^\s*\d+\s*$/.test(v) ? Number(v) : Number.NaN;
	if (!Number.isSafeInteger(n) || n < 0) throw new Error("window_id must be a window id from pc_list_windows (a non-negative integer)");
	return n;
}

/** S16/S17: pc_type/pc_key/pc_screenshot target a window only when window_id is given; absent = as before. */
function optionalWindowId(params: Record<string, unknown>): { window_id?: number } {
	return params.window_id === undefined ? {} : { window_id: windowId(params) };
}

function str(params: Record<string, unknown>, key: string): string {
	const v = params[key];
	if (typeof v !== "string" || v.length === 0) throw new Error(`${key} must be a non-empty string`);
	return v;
}

/** Maps a pc_* tool call to its S13 agent.sock request. Validation of content is the helper's job. */
export function buildRequest(tool: string, params: Record<string, unknown>): HelperRequest {
	switch (tool) {
		case "pc_open_app":
			return { op: "open", app: str(params, "app") };
		case "pc_open_url":
			return { op: "open", url: str(params, "url") };
		case "pc_type":
			return { op: "type", text: str(params, "text"), intent: str(params, "intent"), ...optionalWindowId(params) };
		case "pc_key":
			return { op: "key", keys: str(params, "keys"), intent: str(params, "intent"), ...optionalWindowId(params) };
		case "pc_screenshot": {
			const s = params.scale;
			const scale = typeof s === "number" && Number.isFinite(s) ? Math.min(1, Math.max(0.1, s)) : 0.5;
			return { op: "screenshot", scale, ...optionalWindowId(params) };
		}
		case "pc_list_apps":
			return { op: "apps" }; // the optional query is applied locally by formatApps (S14)
		case "pc_list_windows":
			return { op: "windows" };
		case "pc_focus_window":
			return { op: "focus", window_id: windowId(params) };
		case "pc_media":
			return { op: "media", action: mediaAction(params) };
		default:
			throw new Error(`unknown pc-control tool: ${tool}`);
	}
}

/** T10 (S9): a composite call = the app's desktop id plus the type/key request for its window. */
export type CompositeRequest = {
	app: string;
	input: { op: "type"; text: string; intent: string } | { op: "key"; keys: string; intent: string };
};

/** pc_write_in / pc_run params → CompositeRequest; throws on missing fields before any helper call. */
export function compositeRequest(tool: string, params: Record<string, unknown>): CompositeRequest {
	switch (tool) {
		case "pc_write_in":
			return { app: str(params, "app"), input: { op: "type", text: str(params, "text"), intent: str(params, "intent") } };
		case "pc_run":
			return { app: str(params, "app"), input: { op: "key", keys: str(params, "keys"), intent: str(params, "intent") } };
		default:
			throw new Error(`unknown composite pc-control tool: ${tool}`);
	}
}

/**
 * Whether a niri window app_id belongs to a desktop id: equal (case-insensitive, `.desktop` dropped)
 * or equal to its last dotted part (org.gnome.Nautilus → nautilus). A heuristic: the helper keeps
 * every check on the window itself.
 */
export function appMatchesWindow(desktopId: string, appId: string): boolean {
	const d = desktopId.trim().toLowerCase().replace(/\.desktop$/, "");
	const a = appId.trim().toLowerCase();
	if (!d || !a) return false;
	return a === d || a === d.slice(d.lastIndexOf(".") + 1);
}

export type AppWindow = { id: number; app_id: string; title: string; focused: boolean };

function parseWindowList(res: Record<string, unknown>): AppWindow[] {
	const raw = Array.isArray(res.windows) ? res.windows : [];
	return raw
		.filter((w): w is Record<string, unknown> => typeof w === "object" && w !== null && Number.isSafeInteger((w as { id?: unknown }).id))
		.map((w) => ({
			id: w.id as number,
			app_id: typeof w.app_id === "string" ? w.app_id : "",
			title: typeof w.title === "string" ? w.title : "",
			focused: w.focused === true,
		}));
}

function pickAppWindow(windows: AppWindow[], app: string): AppWindow | undefined {
	const hits = windows.filter((w) => appMatchesWindow(app, w.app_id));
	return hits.find((w) => w.focused) ?? hits[0];
}

export type HelperCall = (req: HelperRequest) => Promise<Record<string, unknown>>;

/** How long runInApp waits for a just-opened app's window, and how often it asks. */
export const APP_WINDOW_WAIT_MS = 10_000;
export const APP_WINDOW_POLL_MS = 400;

/**
 * T10 (S9): one tool call instead of list windows → list apps → open → list windows → type.
 * Finds the app's window (focused one first); if there is none, opens the app and polls `windows`
 * until a window whose app_id matches appears. It never guesses: a new window of another app_id
 * (a dialog, another app) is not a target; it is named in the error so the agent can pick it with
 * pc_type/pc_key and its window_id. Then sends the type/key request with that window_id, so every
 * helper check (permission, blocklist, terminals, confirmation, focus return) still applies. Any
 * helper refusal stops the chain and is thrown verbatim; an aborted signal stops it between steps.
 */
export async function runInApp(
	call: HelperCall,
	req: CompositeRequest,
	opts: {
		sleep?: (ms: number) => Promise<void>;
		now?: () => number;
		waitMs?: number;
		pollMs?: number;
		signal?: AbortSignal;
	} = {},
): Promise<{ window: AppWindow; opened: boolean; result: Record<string, unknown> }> {
	const signal = opts.signal;
	const checkAbort = () => {
		if (signal?.aborted) throw new Error("cancelled");
	};
	const sleep =
		opts.sleep ??
		((ms: number) =>
			new Promise<void>((r) => {
				const t = setTimeout(r, ms);
				signal?.addEventListener("abort", () => (clearTimeout(t), r()), { once: true });
			}));
	const now = opts.now ?? Date.now;
	const waitMs = opts.waitMs ?? APP_WINDOW_WAIT_MS;
	const pollMs = opts.pollMs ?? APP_WINDOW_POLL_MS;

	const before = parseWindowList(await call({ op: "windows" }));
	let target = pickAppWindow(before, req.app);
	const opened = target === undefined;
	if (!target) {
		await call({ op: "open", app: req.app });
		const known = new Set(before.map((w) => w.id));
		const deadline = now() + waitMs;
		let fresh: AppWindow[] = [];
		for (;;) {
			checkAbort();
			const windows = parseWindowList(await call({ op: "windows" }));
			fresh = windows.filter((w) => !known.has(w.id));
			target = pickAppWindow(windows, req.app);
			if (target || now() >= deadline) break;
			await sleep(pollMs);
		}
		if (!target) {
			const seen = fresh.length
				? ` New windows: ${fresh.map((w) => `${w.id} · ${w.app_id || "?"} · ${w.title}`).join("; ")}; if one is the app, use pc_type/pc_key with its window_id.`
				: " Check with pc_list_windows, then use pc_type/pc_key with its window_id.";
			throw new Error(`Opened ${req.app}, but no window for it appeared within ${Math.round(waitMs / 1000)} s.${seen}`);
		}
	}
	checkAbort();
	const result = await call({ ...req.input, window_id: target.id });
	return { window: target, opened, result };
}

/**
 * T10: the current local date and time for the agent's context, so "qué hora es" / "qué día es"
 * need no tool call. Unknown time zones fall back to UTC.
 */
export function clockContext(date: Date, timeZone?: string): string {
	let tz = timeZone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
	try {
		new Intl.DateTimeFormat("en-US", { timeZone: tz });
	} catch {
		tz = "UTC";
	}
	const parts = Object.fromEntries(
		new Intl.DateTimeFormat("en-US", {
			timeZone: tz,
			weekday: "long",
			year: "numeric",
			month: "2-digit",
			day: "2-digit",
			hour: "2-digit",
			minute: "2-digit",
			hourCycle: "h23",
			timeZoneName: "longOffset",
		})
			.formatToParts(date)
			.map((p) => [p.type, p.value]),
	);
	const offset = parts.timeZoneName === "GMT" ? "UTC+00:00" : String(parts.timeZoneName).replace("GMT", "UTC");
	return `Current local date and time: ${parts.weekday} ${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute} (${tz}, ${offset}). Answer date and time questions from this; do not run a command to read the clock.`;
}

function fold(s: string): string {
	return s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
}

/**
 * `{"ok":true,"apps":[{id,name}]}` → "name — id" lines sorted by name, filtered by `query` on name
 * or id (case- and accent-insensitive). No match = a note plus the full list. Capped at
 * MAX_APP_LINES.
 */
export function formatApps(res: Record<string, unknown>, query?: string): string {
	const raw = Array.isArray(res.apps) ? res.apps : [];
	const apps = raw
		.filter((a): a is Record<string, unknown> => typeof a === "object" && a !== null && typeof (a as { id?: unknown }).id === "string" && (a as { id: string }).id !== "")
		.map((a) => ({ id: a.id as string, name: typeof a.name === "string" && a.name.trim() ? a.name.trim() : (a.id as string) }))
		.sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
	if (apps.length === 0) return "No desktop apps found.";
	const q = query?.trim() ? fold(query.trim()) : "";
	let header: string[] = [];
	let list = apps;
	if (q) {
		const hits = apps.filter((a) => fold(a.name).includes(q) || fold(a.id).includes(q));
		if (hits.length > 0) list = hits;
		else header = [`No app matches "${query!.trim()}"; all apps (try an English or shorter query):`];
	}
	const lines = list.slice(0, MAX_APP_LINES).map((a) => `${a.name} — ${a.id}`);
	if (list.length > MAX_APP_LINES) lines.push(`… ${list.length - MAX_APP_LINES} more; narrow it with a query.`);
	return [...header, ...lines].join("\n");
}

/** `{"ok":true,"windows":[{id,app_id,title,focused}]}` → "id · app_id · title · (focused)" lines. */
export function formatWindows(res: Record<string, unknown>): string {
	const raw = Array.isArray(res.windows) ? res.windows : [];
	const lines = raw
		.filter((w): w is Record<string, unknown> => typeof w === "object" && w !== null && (typeof (w as { id?: unknown }).id === "number" || typeof (w as { id?: unknown }).id === "string"))
		.map((w) => {
			const appId = typeof w.app_id === "string" && w.app_id ? w.app_id : "?";
			const title = typeof w.title === "string" ? w.title : "";
			return `${w.id} · ${appId} · ${title}${w.focused === true ? " · (focused)" : ""}`;
		});
	return lines.length > 0 ? lines.join("\n") : "No open windows.";
}

/** One request = one JSON line (JSON.stringify escapes newlines inside strings). */
export function encodeRequest(req: HelperRequest): string {
	return `${JSON.stringify(req)}\n`;
}

/** `{"ok":true,...}` → the object; `{"ok":false,"error":"…"}` → throws the helper's message. */
export function decodeResponse(line: string): Record<string, unknown> {
	let data: unknown;
	try {
		data = JSON.parse(line);
	} catch {
		throw new Error("jarvis-input sent an unreadable reply");
	}
	if (typeof data !== "object" || data === null || Array.isArray(data)) throw new Error("jarvis-input sent an unreadable reply");
	const obj = data as Record<string, unknown>;
	if (obj.ok === true) return obj;
	if (obj.ok === false) {
		const msg = typeof obj.error === "string" && obj.error.trim() ? obj.error : "jarvis-input refused the action (no reason given)";
		throw new Error(msg);
	}
	throw new Error("jarvis-input sent an unreadable reply");
}

/**
 * Sends one request to the helper and waits for one reply line. Missing socket or nobody listening
 * = "jarvis-input is not running"; timeout, abort, hang-up or oversize reply = error. The socket is
 * always destroyed when the promise settles.
 */
export function requestHelper(
	socketPath: string,
	req: HelperRequest,
	opts: { timeoutMs?: number; signal?: AbortSignal; maxBytes?: number } = {},
): Promise<Record<string, unknown>> {
	const timeoutMs = opts.timeoutMs ?? HELPER_TIMEOUT_MS;
	const maxBytes = opts.maxBytes ?? MAX_REPLY_BYTES;
	return new Promise((resolvePromise, reject) => {
		if (opts.signal?.aborted) return reject(new Error("cancelled"));
		const sock = connect(socketPath);
		const chunks: Buffer[] = [];
		let size = 0;
		let settled = false;
		const finish = (error: Error | null, value?: Record<string, unknown>) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			opts.signal?.removeEventListener("abort", onAbort);
			sock.destroy();
			if (error) reject(error);
			else resolvePromise(value!);
		};
		const onAbort = () => finish(new Error("cancelled"));
		const timer = setTimeout(
			() => finish(new Error(`jarvis-input did not answer within ${Math.round(timeoutMs / 1000)} s; nothing was confirmed`)),
			timeoutMs,
		);
		opts.signal?.addEventListener("abort", onAbort, { once: true });
		sock.on("connect", () => sock.write(encodeRequest(req)));
		sock.on("data", (chunk: Buffer) => {
			const nl = chunk.indexOf(0x0a);
			const part = nl >= 0 ? chunk.subarray(0, nl) : chunk;
			size += part.length;
			if (size > maxBytes) return finish(new Error("jarvis-input reply too large"));
			chunks.push(part);
			if (nl < 0) return;
			try {
				finish(null, decodeResponse(Buffer.concat(chunks).toString("utf8")));
			} catch (error) {
				finish(error as Error);
			}
		});
		sock.on("end", () => finish(new Error("jarvis-input closed the connection without answering")));
		sock.on("close", () => finish(new Error("jarvis-input closed the connection without answering")));
		sock.on("error", (error: NodeJS.ErrnoException) => {
			if (error.code === "ENOENT" || error.code === "ECONNREFUSED" || error.code === "ENOTSOCK")
				return finish(
					new Error(
						`jarvis-input is not running (no helper at ${socketPath}); PC control is unavailable. Tell the user to start the jarvis-input service.`,
					),
				);
			finish(new Error(`jarvis-input connection failed (${error.code ?? error.message})`));
		});
	});
}

export const TOOLS_HINT =
	"Use the pc_write_in, pc_run, pc_list_apps, pc_open_app, pc_open_url, pc_list_windows, pc_focus_window, pc_type, pc_key and pc_screenshot tools.";
const INPUT_TOOLS = /(?:^|[^\w.-])(wtype|grim|ydotoold?|dotoolc?|wlrctl)(?![\w-])/;
const NIRI_ACTION = /(?:^|[^\w.-])niri\s+msg\b[^;&|\n]*?\saction\b/;
const SYSTEMD_RUN = /(?:^|[^\w.-])systemd-run(?![\w-])/;
const SYSTEMCTL_USER = /(?:^|[^\w.-])systemctl\b[^;&|\n]*?\s--user\b/;
// Helper sockets and install, agent extensions and settings, this extension's own files.
const PROTECTED_MENTION =
	/jarvis-control(?![\w-])|jarvis\/agent\.sock|\.local\/share\/jarvis(?![\w.-])|\.gentle-dot\/agent\/(?:extensions|settings\.json)|jarvis-pc-control|pc-control(?:-core)?\.ts/;

/** Why a bash command is refused, or null. Best effort on the text: NOT the security boundary. */
export function bashBlockReason(command: string): string | null {
	const tool = INPUT_TOOLS.exec(command)?.[1];
	if (tool) return `Blocked: do not run ${tool} through bash. ${TOOLS_HINT}`;
	if (NIRI_ACTION.test(command)) return `Blocked: do not run \`niri msg action\` through bash. ${TOOLS_HINT}`;
	if (SYSTEMD_RUN.test(command) || SYSTEMCTL_USER.test(command))
		return `Blocked: user services (systemd-run, systemctl --user) are not for the agent. ${TOOLS_HINT}`;
	if (PROTECTED_MENTION.test(command))
		return `Blocked: the PC control helper, its sockets, and the agent's extensions and settings are off limits. ${TOOLS_HINT} If permission is missing, ask the user to run /control on in the Jarvis TUI.`;
	return null;
}

/** Whether a write/edit target is inside a protected folder, is a protected file, or names one. */
export function isProtectedPath(
	path: string,
	opts: { cwd: string; home: string; protectedDirs: string[]; protectedFiles?: string[] },
): boolean {
	const expanded = path === "~" ? opts.home : path.startsWith("~/") ? join(opts.home, path.slice(2)) : path;
	const abs = normalize(resolve(opts.cwd, expanded));
	if (opts.protectedDirs.some((dir) => abs === normalize(dir) || abs.startsWith(normalize(dir) + sep))) return true;
	if ((opts.protectedFiles ?? []).some((f) => abs === normalize(f))) return true;
	return PROTECTED_MENTION.test(abs);
}
