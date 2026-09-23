/**
 * YAML frontmatter, the way the agent runtime reads it.
 *
 * A markdown file whose frontmatter is fed straight to a markdown renderer is
 * mangled: the opening `---` becomes a rule and the closing one turns the keys
 * into a setext heading. Worse, round-tripping that through the editor writes
 * the mangled form back and the file loses its contract.
 *
 * So the block is split off before rendering and re-attached on write. The
 * parse deliberately mirrors `_parse_frontmatter` in the backend's
 * `skill_loader.py` — a file the frontend calls valid must be a file the agent
 * runtime can load.
 */

const DELIMITER = '---';

export type ParsedFrontmatter = {
    /** The block verbatim, delimiters included — null when the file has none. */
    raw: string | null;
    fields: Record<string, string>;
    /** Everything after the closing delimiter, leading blank lines trimmed. */
    body: string;
};

function isDelimiter(line: string): boolean {
    return line.trim() === DELIMITER;
}

/** The backend strips every wrapping quote char, so match it exactly. */
function stripWrappingQuotes(value: string): { value: string; quoted: boolean } {
    const withoutDouble = value.replace(/^"+/, '').replace(/"+$/, '');
    const quoted = withoutDouble !== value;
    const withoutSingle = withoutDouble.replace(/^'+/, '').replace(/'+$/, '');
    return { value: withoutSingle, quoted: quoted || withoutSingle !== withoutDouble };
}

/** `\"` in a quoted value is an escaped quote, not two characters. */
function unescapeQuoted(value: string): string {
    return value.replace(/\\(["'\\])/g, '$1');
}

function escapeForQuoting(value: string): string {
    return value.replace(/([\\"])/g, '\\$1');
}

/**
 * Bare where it can be, quoted where it must be. Descriptions almost always
 * carry a colon, names almost never do — so names stay readable.
 */
function serializeValue(value: string): string {
    const collapsed = value.replace(/\s+/g, ' ').trim();
    const needsQuotes = /[:#'"]/.test(collapsed) || collapsed === '' || /^[[{&*!|>%@`-]/.test(collapsed);
    return needsQuotes ? `"${escapeForQuoting(collapsed)}"` : collapsed;
}

export function splitFrontmatter(content: string): ParsedFrontmatter {
    const absent: ParsedFrontmatter = { raw: null, fields: {}, body: content };
    if (!/^---\r?\n/.test(content)) return absent;

    const lines = content.split('\n');
    const closingIndex = lines.findIndex((line, index) => index > 0 && isDelimiter(line));
    if (closingIndex === -1) return absent;

    const fields: Record<string, string> = {};
    for (const line of lines.slice(1, closingIndex)) {
        // An indented line continues the value above it, and this parser does
        // not carry multi-line values — skip rather than mis-key it.
        if (/^[ \t]/.test(line)) continue;

        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) continue;

        const separator = trimmed.indexOf(':');
        if (separator === -1) continue;

        const key = trimmed.slice(0, separator).trim();
        if (!key) continue;

        const stripped = stripWrappingQuotes(trimmed.slice(separator + 1).trim());
        fields[key] = stripped.quoted ? unescapeQuoted(stripped.value) : stripped.value;
    }

    // A block that parsed to nothing is not frontmatter — it is a document that
    // happens to open with a rule, and hiding it would make content vanish from
    // the editor. Hand it back as body.
    if (Object.keys(fields).length === 0) return absent;

    return {
        raw: lines.slice(0, closingIndex + 1).join('\n'),
        fields,
        body: lines.slice(closingIndex + 1).join('\n').replace(/^(\r?\n)+/, ''),
    };
}

export function joinFrontmatter(raw: string | null, body: string): string {
    if (!raw) return body;
    return `${raw}\n\n${body}`;
}

/**
 * Rewrite one key in place, leaving every other line — including keys this
 * parser does not understand — exactly as the author left them.
 */
export function setFrontmatterField(raw: string | null, key: string, value: string): string {
    const serialized = `${key}: ${serializeValue(value)}`;
    if (!raw) return [DELIMITER, serialized, DELIMITER].join('\n');

    const lines = raw.split('\n');
    const closingIndex = lines.findIndex((line, index) => index > 0 && isDelimiter(line));
    const end = closingIndex === -1 ? lines.length : closingIndex;

    for (let index = 1; index < end; index += 1) {
        if (/^[ \t]/.test(lines[index])) continue;

        const trimmed = lines[index].trim();
        const separator = trimmed.indexOf(':');
        if (separator === -1) continue;
        if (trimmed.slice(0, separator).trim() !== key) continue;

        lines[index] = serialized;
        return lines.join('\n');
    }

    lines.splice(end, 0, serialized);
    return lines.join('\n');
}

export function buildFrontmatter(fields: Record<string, string>): string {
    const entries = Object.entries(fields).map(([key, value]) => `${key}: ${serializeValue(value)}`);
    return [DELIMITER, ...entries, DELIMITER].join('\n');
}
