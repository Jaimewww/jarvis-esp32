/**
 * Jarvis pc-control: open apps and URLs, keyboard and screenshots for the Gentle Dot agent (S13,
 * odd/tasks/s2-round2.md). Written for @earendil-works/pi-coding-agent 1.5.0.
 *
 * Every tool is a thin client of the jarvis-input helper over $XDG_RUNTIME_DIR/jarvis/agent.sock
 * (one JSON line each way). The helper is the boundary: it alone has display access, keeps the
 * /control permission in memory, checks the focused window, asks the user to confirm risky
 * actions in the TUI and runs wtype/grim/gtk-launch/xdg-open. This extension reads no permission
 * file, never calls niri, wtype or grim, and returns the helper's refusals as tool errors.
 *
 * The `tool_call` hook below is defense in depth only, NOT the boundary: it is a text match the
 * agent could evade. The real barriers are the helper and the gentle-dot.service sandbox (no
 * Wayland/X11/niri/session bus, no jarvis-control/, read-only helper and extensions).
 *
 * Installed by host/systemd/install.sh as <agent home>/extensions/jarvis-pc-control/index.ts.
 */
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { StringEnum, Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
	HELPER_TIMEOUT_MS,
	MEDIA_ACTIONS,
	agentSocketPath,
	bashBlockReason,
	buildRequest,
	controlDirPath,
	formatApps,
	formatWindows,
	isProtectedPath,
	requestHelper,
} from "./pc-control-core.ts";

function uid(): number {
	return typeof process.getuid === "function" ? process.getuid() : -1;
}

function call(tool: string, params: Record<string, unknown>, signal?: AbortSignal) {
	return requestHelper(agentSocketPath(process.env, uid()), buildRequest(tool, params), {
		timeoutMs: HELPER_TIMEOUT_MS,
		signal,
	});
}

function text(t: string) {
	return { type: "text" as const, text: t };
}

function helperNote(res: Record<string, unknown>): string {
	return typeof res.message === "string" && res.message ? ` ${res.message}` : "";
}

const INTENT = Type.String({ description: "One sentence: what you are doing and why, shown to the user." });
const WINDOW_ID =
	"Target window id from pc_list_windows. The helper focuses it, types and gives focus back to the user's window; omit only when the target is unknown.";

const SCREENSHOT_WINDOW_ID =
	"Window id from pc_list_windows to capture; the helper focuses it and focus goes back to the user's window afterwards.";

export default function pcControl(pi: ExtensionAPI) {
	const home = homedir();
	let extensionsDir = "";
	try {
		// <agent home>/extensions/jarvis-pc-control/index.ts → <agent home>/extensions
		extensionsDir = dirname(dirname(fileURLToPath(import.meta.url)));
	} catch {
		extensionsDir = "";
	}

	pi.registerTool({
		name: "pc_open_app",
		label: "Abrir app",
		description:
			"Opens a desktop application on the user's screen by its desktop id (e.g. brave-browser, spotify, org.gnome.Nautilus). Returns at once. When unsure of the exact desktop id (or the user names the app in Spanish, e.g. \"notas\"), call pc_list_apps with a query first and use the id it returns. Then pc_list_windows to find the new window. Use this instead of launching GUI programs through bash, which cannot reach the display.",
		promptSnippet: "pc_open_app: open a desktop app by desktop id",
		parameters: Type.Object({ app: Type.String({ description: "Desktop id without .desktop, e.g. brave-browser." }) }),
		executionMode: "sequential",
		async execute(_id, params, signal) {
			const res = await call("pc_open_app", params, signal);
			return { content: [text(`Opened ${params.app}.${helperNote(res)}`)], details: res };
		},
	});

	pi.registerTool({
		name: "pc_list_apps",
		label: "Apps instaladas",
		description:
			'Lists the installed desktop apps as "name — desktop id" (max 60 lines). Pass a short query (matched on name or id, case- and accent-insensitive; try English words too, e.g. "note" for "notas") to find the id for pc_open_app. No permission needed.',
		promptSnippet: "pc_list_apps: find an app's desktop id by name",
		parameters: Type.Object({
			query: Type.Optional(Type.String({ description: 'Part of the app name or id, e.g. "note", "brave".' })),
		}),
		executionMode: "sequential",
		async execute(_id, params, signal) {
			const res = await call("pc_list_apps", params, signal);
			return { content: [text(formatApps(res, params.query))], details: {} };
		},
	});

	pi.registerTool({
		name: "pc_list_windows",
		label: "Ventanas abiertas",
		description:
			'Lists the open windows on the user\'s desktop as "id · app_id · title", marking the focused one. Pass the id as window_id to pc_type/pc_key (or to pc_focus_window to bring the window forward). Needs the user\'s /control on permission.',
		promptSnippet: "pc_list_windows: list open windows and their ids",
		parameters: Type.Object({}),
		executionMode: "sequential",
		async execute(_id, params, signal) {
			const res = await call("pc_list_windows", params, signal);
			return { content: [text(formatWindows(res))], details: res };
		},
	});

	pi.registerTool({
		name: "pc_focus_window",
		label: "Enfocar ventana",
		description:
			"Brings a window forward for the user (focuses it) by its id from pc_list_windows. To type into a window, pass window_id to pc_type/pc_key instead. Terminals and blocked windows (password managers, settings, security prompts) are refused. Needs the user's /control on permission.",
		promptSnippet: "pc_focus_window: bring a window forward for the user",
		parameters: Type.Object({
			window_id: Type.Integer({ minimum: 0, description: "Window id from pc_list_windows." }),
		}),
		executionMode: "sequential",
		async execute(_id, params, signal) {
			const res = await call("pc_focus_window", params, signal);
			return { content: [text(`Focused window ${params.window_id}.${helperNote(res)}`)], details: res };
		},
	});

	pi.registerTool({
		name: "pc_open_url",
		label: "Abrir enlace",
		description: "Opens an http(s) URL in the user's default browser. Returns at once.",
		promptSnippet: "pc_open_url: open a URL in the default browser",
		parameters: Type.Object({ url: Type.String({ description: "Full URL, e.g. https://example.com." }) }),
		executionMode: "sequential",
		async execute(_id, params, signal) {
			const res = await call("pc_open_url", params, signal);
			return { content: [text(`Opened ${params.url}.${helperNote(res)}`)], details: res };
		},
	});

	pi.registerTool({
		name: "pc_type",
		label: "Escribir en la PC",
		description:
			"Types text (max 2000 characters) into a window of the user's desktop. Pass window_id (from pc_list_windows) whenever the target window is known: the helper focuses that window, types, and focus returns to the user's window afterwards, so it does not matter if the user switches focus. Without window_id it types into the focused window. Needs the user's /control on permission. Terminals, password managers, settings and security prompts are refused; sending, paying, buying or deleting asks the user first.",
		promptSnippet: "pc_type: type text into a desktop window (pass window_id)",
		parameters: Type.Object({
			text: Type.String({ description: "Text to type (max 2000 characters)." }),
			intent: INTENT,
			window_id: Type.Optional(Type.Integer({ minimum: 0, description: WINDOW_ID })),
		}),
		executionMode: "sequential",
		async execute(_id, params, signal) {
			const res = await call("pc_type", params, signal);
			return { content: [text(`Typed ${[...params.text].length} characters.${helperNote(res)}`)], details: res };
		},
	});

	pi.registerTool({
		name: "pc_key",
		label: "Teclas en la PC",
		description:
			'Presses keys or shortcuts on the user\'s desktop, e.g. "ctrl+l", "super+1", "Return", or a space-separated sequence "ctrl+a Delete". Modifiers: ctrl, shift, alt, altgr, super. Pass window_id (from pc_list_windows) whenever the target window is known: the helper focuses it, presses the keys, and focus returns to the user\'s window afterwards. Without window_id the keys go to the focused window. Same permission, blocklist and confirmation rules as pc_type. For volume and media (play/pause, next, previous, mute) use pc_media, never XF86Audio* keys.',
		promptSnippet: "pc_key: press keys or shortcuts in a desktop window (pass window_id)",
		parameters: Type.Object({
			keys: Type.String({ description: 'Key combo(s), e.g. "ctrl+l" or "Return".' }),
			intent: INTENT,
			window_id: Type.Optional(Type.Integer({ minimum: 0, description: WINDOW_ID })),
		}),
		executionMode: "sequential",
		async execute(_id, params, signal) {
			const res = await call("pc_key", params, signal);
			return { content: [text(`Pressed ${params.keys}.${helperNote(res)}`)], details: res };
		},
	});

	pi.registerTool({
		name: "pc_media",
		label: "Música y volumen",
		description:
			"Controls music and volume on the user's PC: play_pause, next, previous (the active player, via playerctl), volume_up / volume_down (5% steps, capped at 100%), mute and unmute (explicit, never a toggle), via wpctl. Use it instead of pc_key for volume and media; never send XF86Audio* keys, which go to the focused window and are refused in terminals. Needs no focused window and no /control on permission. If it says there is no active player, tell the user to start the music first.",
		promptSnippet: "pc_media: play/pause, next, previous, volume up/down, mute, unmute",
		parameters: Type.Object({
			action: StringEnum(MEDIA_ACTIONS, { description: "Media or volume action." }),
		}),
		executionMode: "sequential",
		async execute(_id, params, signal) {
			const res = await call("pc_media", params, signal);
			return { content: [text(`Done: ${params.action}.${helperNote(res)}`)], details: res };
		},
	});

	pi.registerTool({
		name: "pc_screenshot",
		label: "Captura de pantalla",
		description:
			"Takes a screenshot and returns it as an image (scale 0.5 by default). Pass window_id (from pc_list_windows) to see a given window: the helper focuses it, captures the monitor showing it, and focus returns to the user's window afterwards. Use it to verify after pc_type/pc_key on that window. Without window_id it captures the whole screen as it is now (focus may already be back on the user's window). Needs the user's /control on permission.",
		promptSnippet: "pc_screenshot: see the user's screen or a given window (pass window_id)",
		parameters: Type.Object({
			scale: Type.Optional(Type.Number({ minimum: 0.1, maximum: 1, description: "Scale factor, default 0.5." })),
			window_id: Type.Optional(Type.Integer({ minimum: 0, description: SCREENSHOT_WINDOW_ID })),
		}),
		executionMode: "sequential",
		async execute(_id, params, signal) {
			const res = await call("pc_screenshot", params, signal);
			const png = res.png_base64;
			if (typeof png !== "string" || png.length === 0) throw new Error("jarvis-input returned no screenshot");
			const { png_base64: _omit, ...rest } = res;
			const kb = Math.round((png.length * 3) / 4 / 1024);
			return {
				content: [text(`Screenshot (${kb} KB).`), { type: "image" as const, data: png, mimeType: "image/png" }],
				details: rest,
			};
		},
	});

	// Defense in depth only (see the header): steer the agent to the pc_* tools and away from the
	// helper, its sockets, user services, and the agent's own extensions and settings.
	pi.on("tool_call", (event, ctx) => {
		if (event.toolName === "bash") {
			const command = (event.input as { command?: unknown }).command;
			const reason = typeof command === "string" ? bashBlockReason(command) : null;
			if (reason) return { block: true, reason };
			return undefined;
		}
		if (event.toolName === "write" || event.toolName === "edit") {
			const path = (event.input as { path?: unknown }).path;
			if (typeof path !== "string") return undefined;
			const protectedDirs = [
				dirname(agentSocketPath(process.env, uid())),
				controlDirPath(process.env, uid()),
				join(home, ".local", "share", "jarvis"),
			];
			const protectedFiles: string[] = [];
			if (extensionsDir) {
				protectedDirs.push(extensionsDir);
				protectedFiles.push(join(dirname(extensionsDir), "settings.json"));
			}
			if (isProtectedPath(path, { cwd: ctx.cwd, home, protectedDirs, protectedFiles }))
				return {
					block: true,
					reason:
						"Blocked: the PC control helper, its sockets, and the agent's extensions and settings are not edited. Use the pc_* tools; if permission is missing, ask the user to run /control on in the Jarvis TUI.",
				};
		}
		return undefined;
	});
}
