/**
 * Host half of the app element picker.
 *
 * An app runs on its own subdomain, so the shell cannot read its DOM. The
 * picker lives inside the app (the bridge the backend injects into every app
 * entrypoint) and this is the contract the shell speaks to it: say hello, turn
 * select mode on, receive the element someone picked.
 *
 * Everything arriving over `postMessage` is untrusted until it has passed both
 * checks a caller must make — that it came from the app frame's own window, and
 * from the app's own origin — and then `parseAppEditorMessage`, which is the
 * only thing that turns it into a typed value.
 */

export const APP_EDITOR_MESSAGE = {
    hello: 'lemma-app-editor:hello',
    ready: 'lemma-app-editor:ready',
    selectMode: 'lemma-app-editor:select-mode',
    selection: 'lemma-app-editor:selection',
} as const;

export interface AppEditorSourceLocation {
    file: string;
    line: number | null;
    column: number | null;
}

export interface AppEditorSelection {
    app: { name: string | null; id: string | null; podId: string | null };
    route: string;
    source: AppEditorSourceLocation | null;
    component: string | null;
    componentChain: string[];
    tag: string;
    domId: string | null;
    className: string | null;
    domPath: string;
    text: string;
    html: string;
    rect: { x: number; y: number; width: number; height: number };
    styles: Record<string, string>;
}

export type AppEditorMessage =
    | { type: typeof APP_EDITOR_MESSAGE.ready }
    | { type: typeof APP_EDITOR_MESSAGE.selectMode; active: boolean }
    | { type: typeof APP_EDITOR_MESSAGE.selection; selection: AppEditorSelection };

export function buildAppEditorHelloMessage() {
    return { type: APP_EDITOR_MESSAGE.hello } as const;
}

export function buildAppEditorSelectModeMessage(active: boolean) {
    return { type: APP_EDITOR_MESSAGE.selectMode, active } as const;
}

function asRecord(value: unknown): Record<string, unknown> | null {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    return value as Record<string, unknown>;
}

function asString(value: unknown, fallback = ''): string {
    return typeof value === 'string' ? value : fallback;
}

function asNullableString(value: unknown): string | null {
    return typeof value === 'string' && value ? value : null;
}

function asNumber(value: unknown): number {
    return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function parseSource(value: unknown): AppEditorSourceLocation | null {
    const record = asRecord(value);
    const file = record ? asNullableString(record.file) : null;
    if (!file) return null;
    return {
        file,
        line: typeof record?.line === 'number' ? record.line : null,
        column: typeof record?.column === 'number' ? record.column : null,
    };
}

function parseStringMap(value: unknown): Record<string, string> {
    const record = asRecord(value);
    if (!record) return {};
    const parsed: Record<string, string> = {};
    Object.entries(record).forEach(([key, entry]) => {
        if (typeof entry === 'string') parsed[key] = entry;
    });
    return parsed;
}

function parseSelection(value: unknown): AppEditorSelection | null {
    const record = asRecord(value);
    if (!record) return null;
    const tag = asNullableString(record.tag);
    // Without a tag there is no element to talk about, so there is no selection.
    if (!tag) return null;

    const app = asRecord(record.app) ?? {};
    const rect = asRecord(record.rect) ?? {};
    const chain = Array.isArray(record.componentChain)
        ? record.componentChain.filter((entry): entry is string => typeof entry === 'string')
        : [];

    return {
        app: {
            name: asNullableString(app.name),
            id: asNullableString(app.id),
            podId: asNullableString(app.podId),
        },
        route: asString(record.route),
        source: parseSource(record.source),
        component: asNullableString(record.component),
        componentChain: chain,
        tag,
        domId: asNullableString(record.domId),
        className: asNullableString(record.className),
        domPath: asString(record.domPath),
        text: asString(record.text),
        html: asString(record.html),
        rect: {
            x: asNumber(rect.x),
            y: asNumber(rect.y),
            width: asNumber(rect.width),
            height: asNumber(rect.height),
        },
        styles: parseStringMap(record.styles),
    };
}

export function parseAppEditorMessage(value: unknown): AppEditorMessage | null {
    const record = asRecord(value);
    if (!record) return null;

    if (record.type === APP_EDITOR_MESSAGE.ready) {
        return { type: APP_EDITOR_MESSAGE.ready };
    }
    if (record.type === APP_EDITOR_MESSAGE.selectMode) {
        return { type: APP_EDITOR_MESSAGE.selectMode, active: record.active === true };
    }
    if (record.type === APP_EDITOR_MESSAGE.selection) {
        const selection = parseSelection(record.selection);
        return selection ? { type: APP_EDITOR_MESSAGE.selection, selection } : null;
    }
    return null;
}

/** How the selection reads in the composer and to the agent. */
export function describeAppEditorSelection(selection: AppEditorSelection): string {
    const component = selection.component ?? `<${selection.tag}>`;
    if (!selection.source) return component;
    const { file, line } = selection.source;
    return line ? `${component} — ${file}:${line}` : `${component} — ${file}`;
}

function styleSummary(styles: Record<string, string>): string {
    // Enough to argue about spacing and colour without pasting a whole
    // computed-style dump into the conversation.
    const wanted = ['display', 'font-size', 'font-weight', 'color', 'background-color', 'padding'];
    const parts = wanted
        .filter((name) => styles[name])
        .map((name) => `${name}: ${styles[name]}`);
    return parts.join('; ');
}

/**
 * The message the composer opens with once someone has picked an element.
 *
 * A block of facts, then a blank line for the person to type into. The agent
 * needs the file and line to edit the right thing; the DOM path is what it
 * falls back to for an app with no source stamps, where the markup itself is
 * the source.
 */
export function buildAppEditorPrefill(
    selection: AppEditorSelection,
    appName: string,
): string {
    const lines = [`Editing the "${appName}" app — I selected this element:`, ''];

    if (selection.source) {
        const { file, line, column } = selection.source;
        const position = line ? `:${line}${column ? `:${column}` : ''}` : '';
        lines.push(`- Source: ${file}${position}`);
    }
    if (selection.component) lines.push(`- Component: ${selection.component}`);
    if (selection.componentChain.length > 1) {
        lines.push(`- Inside: ${selection.componentChain.slice(1).join(' → ')}`);
    }
    if (selection.route) lines.push(`- Route: ${selection.route}`);
    if (selection.domPath) lines.push(`- DOM path: ${selection.domPath}`);
    if (selection.text) lines.push(`- Text: ${selection.text}`);

    const styles = styleSummary(selection.styles);
    if (styles) lines.push(`- Styles: ${styles}`);

    if (selection.html) {
        lines.push('', '```html', selection.html, '```');
    }

    lines.push('', '');
    return lines.join('\n');
}
