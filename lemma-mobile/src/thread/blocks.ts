/** A Lemma's markdown, cut into the blocks it is drawn as.
 *
 *  Two reasons, both the web's. A reply still streaming in redraws for every
 *  few tokens, and drawn as one piece every block of it is parsed and laid
 *  out again each time; drawn block by block, only the last one changes. And
 *  a code block, a table and a quote each carry a Copy of their own
 *  (`markdown.tsx` on the web copies the slice of source behind each), which
 *  needs the source of each block.
 *
 *  Pure: it reads the lexer's top-level tokens, so the tests hand it real
 *  ones from `marked`. */

export interface LexedToken {
    type: string;
    raw: string;
    text?: string;
}

export interface Block {
    /** What is drawn: the block's own markdown. */
    source: string;
    /** What Copy puts on the clipboard, for a block that has one. */
    copy: string | null;
    /** What Copy says it copies. */
    copyLabel: string | null;
}

const COPIED: Record<string, string> = { code: "Copy code", table: "Copy table", blockquote: "Copy quote" };

export function blocksOf(tokens: readonly LexedToken[]): Block[] {
    const blocks: Block[] = [];
    let loose = "";
    const settle = () => {
        if (loose.trim()) blocks.push({ source: loose, copy: null, copyLabel: null });
        loose = "";
    };
    for (const token of tokens) {
        const label = COPIED[token.type];
        if (!label) {
            /* Runs of ordinary blocks stay together: one paragraph, a list and
               the next paragraph read as one piece, and splitting every one
               would only add keys. */
            loose += token.raw;
            continue;
        }
        settle();
        blocks.push({
            source: token.raw,
            copy: token.type === "code" ? (token.text ?? token.raw) : token.raw.trim(),
            copyLabel: label,
        });
    }
    settle();
    return blocks;
}

const ENTITIES: Record<string, string> = { nbsp: " ", amp: "&", lt: "<", gt: ">", quot: "\"", "#39": "'" };

/** HTML an agent wrote, as the words in it. The web renders a small
 *  sanitised subset; a phone's markdown view would print the tags, so they
 *  go and the text stays. Tags are taken out until none is left (one split
 *  by another, `<scr<b>ipt>`, goes too) and a stray angle bracket with them;
 *  entities are read once, last, so `&amp;lt;` stays the text `&lt;`. */
export function withoutTags(html: string): string {
    let text = html
        .replace(/<\s*br\s*\/?>/gi, "\n")
        .replace(/<\/(p|div|li|tr|h[1-6]|summary|details)\s*>/gi, "\n");
    for (let before = ""; before !== text;) {
        before = text;
        text = text.replace(/<[^<>]*>/g, "");
    }
    return text
        .replace(/[<>]/g, "")
        .replace(/&(nbsp|amp|lt|gt|quot|#39);/g, (_, name: string) => ENTITIES[name])
        .replace(/\n{3,}/g, "\n\n")
        .trim();
}
