import { createContext, Fragment, memo, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Text as RNText, View, type ImageStyle, type TextStyle } from "react-native";
import { useReducedMotion } from "react-native-reanimated";
import { MarkedLexer, Renderer, useMarkdown } from "react-native-marked";
import { blocksOf, withoutTags, type Block } from "../thread/blocks";
import { copyText } from "../thread/clipboard";
import { Glyph } from "../thread/glyphs";
import { TICK_MS, ageFromEnd, arrived, fadeOf, nextShown, splitTail, stillFading, wholeWords, wordsUpTo, type Arrivals } from "../thread/reveal";
import { Icon } from "./icons";
import { Press } from "./press";
import { Text } from "./text";
import { color, font } from "./tokens";

/** A Lemma's words, as markdown, in the app's faces. Weight stays at 500:
 *  bold and headings are medium, never heavier.
 *
 *  Drawn block by block (`thread/blocks.ts`), so a reply streaming in only
 *  redraws its last block, and so a code block, a table and a quote each
 *  carry a Copy of their own, as on the web. HTML an agent wrote is read as
 *  its words, and a picture it pointed at is not fetched: in chat the web
 *  draws no `<img>` either.
 *
 *  A reply still streaming in is `StreamingMarkdown`: let in word by word
 *  rather than in the chunks it arrives in (`thread/reveal.ts`), each new
 *  word fading in, and the paragraphs before the one being written drawn
 *  as they will stay. */

class ChatRenderer extends Renderer {
    html(text: string | ReactNode[], styles?: TextStyle): ReactNode {
        return super.html(typeof text === "string" ? withoutTags(text) : text, styles);
    }

    image(_uri: string, alt?: string, _style?: ImageStyle): ReactNode {
        return alt ? super.text(alt) : null;
    }
}

function styles(size: number) {
    return {
        text: { fontFamily: font.regular, fontSize: size, lineHeight: size * 1.45, color: color.ink },
        paragraph: { paddingVertical: 0, marginVertical: 2 },
        strong: { fontFamily: font.medium, fontWeight: "normal" as const },
        em: { fontFamily: font.regular, fontStyle: "italic" as const },
        link: { color: color.violetInk, textDecorationLine: "underline" as const },
        h1: { fontFamily: font.medium, fontWeight: "normal" as const, fontSize: size + 4, lineHeight: (size + 4) * 1.3, color: color.ink, marginTop: 6, borderBottomWidth: 0, paddingBottom: 0 },
        h2: { fontFamily: font.medium, fontWeight: "normal" as const, fontSize: size + 2, lineHeight: (size + 2) * 1.3, color: color.ink, marginTop: 6, borderBottomWidth: 0, paddingBottom: 0 },
        h3: { fontFamily: font.medium, fontWeight: "normal" as const, fontSize: size, color: color.ink, marginTop: 4 },
        h4: { fontFamily: font.medium, fontWeight: "normal" as const, fontSize: size, color: color.ink },
        li: { fontFamily: font.regular, fontSize: size, lineHeight: size * 1.45, color: color.ink },
        codespan: { fontFamily: font.num, fontSize: size - 1.5, backgroundColor: color.wash, color: color.ink },
        code: { backgroundColor: color.chrome, borderRadius: 10, padding: 10, marginVertical: 4 },
        codeText: { fontFamily: font.num, fontSize: 13, color: color.ink },
        blockquote: { borderLeftWidth: 0, backgroundColor: color.chrome, borderRadius: 10, padding: 10 },
        hr: { backgroundColor: color.line2, height: 1, marginVertical: 8 },
        table: { borderWidth: 1, borderColor: color.line2, borderRadius: 8, marginVertical: 4 },
        tableRow: { borderColor: color.line2 },
        tableCell: { padding: 6, borderColor: color.line2 },
    };
}

/** Copy, under the block it copies. Says Copied for a moment after. */
export function CopyLine({ text, label }: { text: string; label: string }) {
    const [copied, setCopied] = useState(false);
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
    useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
    return (
        <Press
            label={copied ? "Copied" : label}
            onPress={() => {
                copyText(text);
                setCopied(true);
                if (timer.current) clearTimeout(timer.current);
                timer.current = setTimeout(() => setCopied(false), 1800);
            }}
            hitSlop={10}
            style={{ alignSelf: "flex-end", flexDirection: "row", alignItems: "center", gap: 5, height: 26, paddingHorizontal: 4 }}
        >
            {copied ? <Icon name="check" size={13} width={2.2} tone={color.ink2} /> : <Glyph name="copy" size={13} tone={color.ink3} />}
            <Text size={12.5} tone={copied ? color.ink2 : color.ink3}>{copied ? "Copied" : label}</Text>
        </Press>
    );
}

const Drawn = memo(function Drawn({ block, size, copyable }: { block: Block; size: number; copyable: boolean }) {
    /* Not selectable: holding a message down opens its menu, which copies it. */
    const renderer = useMemo(() => new ChatRenderer({ selectable: false }), []);
    const elements = useMarkdown(block.source, {
        renderer,
        theme: { colors: { text: color.ink, link: color.violetInk, border: color.line2, code: color.chrome, background: "transparent" } as never },
        styles: styles(size),
    });
    return (
        <View>
            {elements.map((element, index) => <Fragment key={index}>{element}</Fragment>)}
            {copyable && block.copy && block.copyLabel ? <CopyLine text={block.copy} label={block.copyLabel} /> : null}
        </View>
    );
    /* By the block's words: the blocks are cut again for every token of a
       reply, and only the one still being written has changed. A change of
       theme remounts every screen, so colours need no say here. */
}, (before, after) => before.block.source === after.block.source && before.size === after.size && before.copyable === after.copyable);

/* ── a reply still streaming in ─────────────────────────────────────────── */

/** How faint the word `fromEnd` places from the end is, this tick. */
const FadeClock = createContext<(fromEnd: number) => number>(() => 1);

/** A colour made fainter, for a word on its way in. */
function faded(tone: unknown, alpha: number): string | undefined {
    if (typeof tone !== "string" || alpha >= 1 || !/^#[0-9a-f]{6}$/i.test(tone)) return undefined;
    return tone + Math.round(alpha * 255).toString(16).padStart(2, "0");
}

/** Counts the words it draws, so each one knows how near the end it is. */
class FadeRenderer extends ChatRenderer {
    words = 0;

    text(text: string | ReactNode[], styles?: TextStyle): ReactNode {
        if (typeof text !== "string") return super.text(text, styles);
        const parts = text.split(/(\s+)/);
        return (
            <RNText key={this.getKey()} selectable={false} style={styles}>
                {parts.map((part, index) => (part === "" || /^\s+$/.test(part) ? part : <Word key={index} at={this.words++} counted={this} tone={styles?.color}>{part}</Word>))}
            </RNText>
        );
    }
}

function Word({ at, counted, tone, children }: { at: number; counted: FadeRenderer; tone: unknown; children: string }) {
    const fade = useContext(FadeClock);
    const color = faded(tone, fade(counted.words - 1 - at));
    return <RNText style={color ? { color } : undefined}>{children}</RNText>;
}

/** The paragraph being written, word by word. */
function FadingBlock({ source, size }: { source: string; size: number }) {
    const renderer = useMemo(() => new FadeRenderer({ selectable: false }), []);
    const look = useMemo(() => styles(size), [size]);
    const theme = useMemo(() => ({ colors: { text: color.ink, link: color.violetInk, border: color.line2, code: color.chrome, background: "transparent" } as never }), []);
    /* Counted afresh for each parse, before it. */
    useMemo(() => { renderer.words = 0; }, [source, renderer]);
    const elements = useMarkdown(source, { renderer, theme, styles: look });
    return <View>{elements.map((element, index) => <Fragment key={index}>{element}</Fragment>)}</View>;
}

/** A reply as it streams: the words let in a few a tick and faded in, the
 *  last one held until it is whole. Under reduce motion, as it arrives. */
export function StreamingMarkdown({ text, size = 15.5 }: { text: string; size?: number }) {
    const still = useReducedMotion();
    const [reveal, setReveal] = useState<{ shown: number; tick: number; arrivals: Arrivals }>({ shown: 0, tick: 0, arrivals: [] });
    const available = wholeWords(text, true);
    useEffect(() => {
        if (still) return;
        if (reveal.shown >= available && !stillFading(reveal.arrivals, reveal.tick)) return;
        const timer = setTimeout(() => setReveal((was) => {
            const shown = nextShown(was.shown, available);
            const tick = was.tick + 1;
            return { shown, tick, arrivals: arrived(was.arrivals, tick, shown - was.shown) };
        }), TICK_MS);
        return () => clearTimeout(timer);
    }, [reveal, available, still]);
    const drawn = still ? text : wordsUpTo(text, reveal.shown);
    const { settled, tail } = splitTail(drawn);
    const fade = (fromEnd: number) => fadeOf(ageFromEnd(fromEnd, reveal.arrivals, reveal.tick));
    return (
        <View>
            {settled.trim() ? <Markdown text={settled} size={size} /> : null}
            {tail.trim() ? (
                <FadeClock.Provider value={fade}>
                    <FadingBlock source={tail} size={size} />
                </FadeClock.Provider>
            ) : null}
        </View>
    );
}

export function Markdown({ text, size = 15.5, copyable = true }: { text: string; size?: number; copyable?: boolean }) {
    const blocks = useMemo(() => {
        try {
            return blocksOf(MarkedLexer(text));
        } catch {
            return [{ source: text, copy: null, copyLabel: null }];
        }
    }, [text]);
    return (
        <View>
            {blocks.map((block, index) => <Drawn key={index} block={block} size={size} copyable={copyable} />)}
        </View>
    );
}
