/** Reading a SKILL.md the way the thing that loads it reads one.
 *
 *  A skill is a directory under the pod's `/skills/<name>/` holding a
 *  `SKILL.md`, and that file has to open with YAML frontmatter delimited by
 *  `---` carrying `name` and `description`. None of that is convention: it is
 *  `_parse_frontmatter` in the backend's `agent/tools/skills/skill_loader.py`,
 *  which raises on every one of those rules — and `_build_pod_skill_catalog`
 *  catches the raise and moves on. So a pod skill that breaks one of them is
 *  not a skill with a blemish on it. It is a folder the teammate cannot see.
 *
 *  This is deliberately a copy of that parser rather than a lenient YAML read.
 *  Being generous here would draw a confident card for something the agent
 *  will never load, which is a subtler version of the bug this section
 *  replaced — a heading saying Skills over things that were not skills.
 *
 *  What it does differently is refuse to throw. The loader's job is to skip
 *  the broken one; this page's job is to show it, because the person who can
 *  fix it is the one reading. So every rule returns a sentence instead, and
 *  whatever could still be read is handed back alongside it.
 */

const NAME_SHAPE = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;

export interface SkillFront {
    /** `name` from the frontmatter. Empty when the file did not carry one. */
    name: string;
    /** `description` from the frontmatter. Empty when there was none. */
    description: string;
    /** Why the loader would refuse this file, in a sentence — or `null` when
     *  it would take it. Only the first failing rule is reported, in the
     *  loader's own order, because that is the one it would actually raise on
     *  and a list of consequences of one mistake reads as several mistakes. */
    problem: string | null;
}

function unquote(value: string): string {
    /* The loader strips one layer of each quote character in turn:
       `.strip('"').strip("'")`. Copied rather than improved, so a value this
       app prints is the value the agent was handed. */
    return value.replace(/^"+|"+$/g, "").replace(/^'+|'+$/g, "");
}

/** The frontmatter of one SKILL.md, and whether the loader would accept it.
 *
 *  `folder` is the directory the file sits in. The loader requires the two to
 *  match — `skill_md.parent.name != name` raises — so a skill whose
 *  frontmatter renamed it without its folder being renamed is invisible to
 *  the teammate, and nothing else in this product would ever say so. */
export function readFrontmatter(text: string, folder?: string): SkillFront {
    const blank = { name: "", description: "" };

    /* The loader tests `content.startswith("---\n")` against bytes it decoded
       itself, with no newline translation on the way — see `_download_text_file`.
       A file saved with Windows line endings starts `---\r\n`, fails that test
       and raises, so it is worth its own sentence rather than being reported
       as "no frontmatter": the frontmatter is right there, and being told it
       is missing would send somebody looking in the wrong place.

       (A skill shipped *with* the backend survives this, because `read_text`
       translates newlines. A pod's own skill — which is all this app can
       ever list — does not.) */
    const windows = text.startsWith("---\r\n");
    if (!text.startsWith("---\n") && !windows) {
        return { ...blank, problem: "This file does not open with a `---` frontmatter block, so the teammate cannot load it." };
    }

    const lines = text.split(/\r\n|\r|\n/);
    let close = -1;
    for (let index = 1; index < lines.length; index += 1) {
        if (lines[index].trim() === "---") { close = index; break; }
    }
    if (close === -1) {
        return { ...blank, problem: "The frontmatter block is never closed with a second `---`, so the teammate cannot load it." };
    }

    const fields: Record<string, string> = {};
    for (const raw of lines.slice(1, close)) {
        const line = raw.trim();
        if (!line || line.startsWith("#")) continue;
        /* An indented line is a continuation or a nested key. The loader skips
           it rather than reading it, so a `description:` written as a block
           over three indented lines comes back empty there — and has to come
           back empty here, or this page would print a description the agent
           does not have. */
        if (/^[ \t]/.test(raw)) continue;
        const at = line.indexOf(":");
        if (at === -1) continue;
        const key = line.slice(0, at).trim();
        /* Split once, not on every colon: a description reading "Use this
           when: the report is late" keeps its clause. */
        if (key) fields[key] = unquote(line.slice(at + 1).trim());
    }

    const name = fields.name ?? "";
    const description = fields.description ?? "";

    if (!name) return { name, description, problem: "The frontmatter has no `name`, which the loader requires, so the teammate cannot load it." };
    if (!description) return { name, description, problem: "The frontmatter has no `description`, which the loader requires, so the teammate cannot load it." };
    if (!NAME_SHAPE.test(name) || name.includes("--")) {
        return { name, description, problem: "The name `" + name + "` is not lowercase letters, digits and single hyphens, so the teammate cannot load it." };
    }
    if (folder && folder !== name) {
        return { name, description, problem: "The frontmatter calls this `" + name + "` but the folder is `" + folder + "`. They have to match, so the teammate cannot load it." };
    }
    if (windows) {
        return { name, description, problem: "This file has Windows line endings. The loader reads a pod's own SKILL.md byte for byte and will not accept them, so the teammate cannot load it." };
    }

    return { name, description, problem: null };
}

/** How much instruction is under the frontmatter, in words.
 *
 *  The one number a skill honestly has. It costs nothing — the file is already
 *  in hand for its frontmatter — and it is the difference between a skill that
 *  is a paragraph of advice and one that is a procedure. The agents list
 *  already prints an instruction's length for the same reason.
 *
 *  Counted over the body only. Counting the frontmatter would make a skill
 *  look longer for having a longer description, which is the opposite of what
 *  the number is for. */
export function instructionWords(text: string): number {
    const words = splitFrontmatter(text).body.trim().match(/\S+/g);
    if (!words) return 0;
    /* A lone `#`, `-` or `|` is markup, not a word. Left in, a skill written
       as headings and bullets counts higher than the same instruction written
       as prose, which makes the rank a measure of formatting. */
    return words.filter((word) => !/^[#>*+\-|`~=_]+$/.test(word)).length;
}

/** A skill's description, split into what it does and when to reach for it.
 *
 *  Not a field — there is no `needs` or `produces` in a `SKILL.md`, and the
 *  loader that defines the format reads exactly `name` and `description`
 *  (`skills/skill_loader.py`). Inventing rows for facts nobody wrote is how a
 *  card ends up confidently describing a skill it has never read.
 *
 *  What *is* there is a convention, visible across every skill the platform
 *  ships: the description says what the skill does, then names its trigger —
 *  "…and updates to prior research. Use for investigations, literature or
 *  market scans…". Ten of twelve follow it.
 *
 *  The two that do not are the reason the match is narrow. `lemma-widget`
 *  says "Use an app, not a widget, when the UI needs React" — an *exclusion*,
 *  and filing that under "use it when" would tell a reader to reach for the
 *  thing precisely when they should not. So the lead-in has to be the verb
 *  followed immediately by `when` or `for`: "Use when", "Use for", "Use this
 *  skill when". Anything else is left whole, which is the safe failure.
 */
export interface SkillSplit {
    does: string;
    /** Null when the description never states one. Then the card shows one
     *  row rather than inventing a second. */
    useWhen: string | null;
}

const TRIGGER = /(^|[.!?]\s+)(use\s+(?:this\s+skill\s+)?(?:when|for)\b)\s*/i;

export function splitDescription(description: unknown): SkillSplit {
    const text = typeof description === "string" ? description.replace(/\s+/g, " ").trim() : "";
    if (!text) return { does: "", useWhen: null };

    const found = TRIGGER.exec(text);
    /* A trigger that opens the description leaves nothing on the other side.
       "Use this skill when opening web pages" is the whole of what browser's
       description says, and splitting it produces an empty first row. */
    if (!found || found.index === undefined) return { does: text, useWhen: null };
    const before = text.slice(0, found.index + (found[1] ? found[1].length : 0)).trim();
    const after = text.slice(found.index + found[0].length).trim();
    if (!before || !after) return { does: text, useWhen: null };

    return { does: before, useWhen: after.charAt(0).toUpperCase() + after.slice(1) };
}

/** A markdown file cut at its frontmatter fence.
 *
 *  Reading one and editing one want different halves of the same parse.
 *  `readFrontmatter` above wants the fields; an editor wants the block
 *  *verbatim* and the prose under it, because the block has to survive being
 *  edited around. Feeding `---\nname: x\n---` to a markdown editor renders it
 *  as a horizontal rule followed by a setext heading, and the round-trip then
 *  writes that back — which is how a SKILL.md stops being a skill without
 *  anybody touching its frontmatter.
 *
 *  So it lives here, beside the rules it belongs to, rather than being parsed
 *  a second time somewhere nearer the editor. The `---` block is one format
 *  in this product and this file is where the app knows it.
 */
export interface FrontSplit {
    /** Both fences and everything between them, exactly as written — line
     *  endings included. Null when the file does not open with one. */
    front: string | null;
    /** What is under the closing fence, with the blank lines that separated
     *  them dropped. The whole file when there is no frontmatter. */
    body: string;
}

export function splitFrontmatter(text: string): FrontSplit {
    const whole = { front: null, body: text };
    if (!text.startsWith("---\n") && !text.startsWith("---\r\n")) return whole;

    const lines = text.split("\n");
    for (let index = 1; index < lines.length; index += 1) {
        if (lines[index].trim() !== "---") continue;
        return {
            front: lines.slice(0, index + 1).join("\n"),
            body: lines.slice(index + 1).join("\n").replace(/^(\r?\n)+/, ""),
        };
    }
    /* An opening fence that never closes is not frontmatter, it is a document
       that begins with a rule. Hiding it from the editor would make the first
       paragraph of somebody's file disappear the moment they clicked into it. */
    return whole;
}

export function joinFrontmatter(front: string | null, body: string): string {
    return front ? front + "\n\n" + body : body;
}
