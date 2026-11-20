"use client";

import { useEffect, useRef, useState, type CSSProperties, type RefObject } from "react";
import type { Mood } from "@/app/characters/loop/loop-puppet";
import { CharacterPuppet } from "@/shell/character-puppet";
import { characterOf, markTint } from "@/shell/mark";
import { ChannelIcon } from "@/shell/channels";
import { TeammateFace } from "@/space/teammate-face";
import { GlyphIcon } from "./glyphs";
import type { PlaceKey } from "./reach";
import { PLACE_ANGLES, YOU_ANGLE, slotAngles, type StageSlot } from "./stage-model";

/** Everything the left side of the way in shows, at one moment. */
export interface StageModel {
    /** Your Lemma, once it is made; until then Lemma's own bars speak. */
    pod: { id: string; name: string; iconUrl: string | null } | null;
    slots: StageSlot[];
    /** Your initial, beside it: who is in it. */
    you: string | null;
    /** Where it can be reached, docked under it. */
    reach: PlaceKey[];
    mood: Mood;
    /** Bump to make it wave. */
    greeting: number;
    /** Bump on each key typed, to make the bars listen. */
    typing: number;
    /** Where your first hello was just heard, while it is being said. */
    heard: PlaceKey | null;
    /** Bump for a burst of the logo's bars: made, and ready. */
    burst: number;
    /** The part of a Lemma the reader is pointing at, lit here too. */
    part: "ai" | "apps" | "people" | null;
}

const PLACE_ORDER: PlaceKey[] = ["SLACK", "WHATSAPP", "TELEGRAM", "EMAIL"];
const PLACE_NAMES: Record<PlaceKey, string> = { EMAIL: "Email", WHATSAPP: "WhatsApp", TELEGRAM: "Telegram", SLACK: "Slack" };

/** The scene: who is speaking, big, and what attaches to it. */
export function ArrivalStage({ model }: { model: StageModel }) {
    const box = useRef<HTMLDivElement>(null);
    const width = useWidth(box);
    const face = Math.round(width * 0.56);
    const angles = slotAngles(model.slots.length);
    const born = Boolean(model.pod);
    return (
        <div
            ref={box}
            className="ast"
            data-born={born ? "" : undefined}
            data-part={model.part ?? undefined}
            data-heard={model.heard ? "" : undefined}
            aria-hidden="true"
        >
            <span className="ast__glow" />
            <Bars typing={model.typing} />
            {model.heard && <span key={model.heard} className="ast__ripples" data-place={model.heard}><i /><i /><i /></span>}
            <span className="ast__face" style={model.pod ? { background: markTint(model.pod.id).background } : undefined}>
                {model.pod && face > 0 && <Face pod={model.pod} size={face} mood={model.mood} greeting={model.greeting} />}
            </span>
            {model.slots.map((slot, index) => (
                <span
                    key={slot.key}
                    className="ast__slot"
                    data-slot={slot.key}
                    data-state={slot.state}
                    data-landing={slot.landing ? "" : undefined}
                    style={{ "--a": `${angles[index] ?? 270}deg` } as CSSProperties}
                >
                    <svg className="ast__ring" viewBox="0 0 62 62">
                        <circle className="ast__track" cx="31" cy="31" r="28" />
                        <circle className="ast__done" cx="31" cy="31" r="28" pathLength={100} strokeDasharray="100" strokeDashoffset={100 - slot.progress * 100} />
                    </svg>
                    <span className="ast__tile"><GlyphIcon slug={slot.slug} size={slot.slug ? 22 : 16} /></span>
                    <span className="ast__tick"><Tick /></span>
                </span>
            ))}
            {model.you && <span className="ast__you" style={{ "--a": `${YOU_ANGLE}deg` } as CSSProperties}>{model.you}</span>}
            {PLACE_ORDER.map((key) => (
                <span key={key} className="ast__place" data-place={key} data-on={model.reach.includes(key) ? "" : undefined} style={{ "--a": `${PLACE_ANGLES[key]}deg` } as CSSProperties}>
                    <PlaceLogo place={key} size={18} />
                </span>
            ))}
            <span className="ast__said" data-on={model.heard ? "" : undefined}>
                {model.heard && <><PlaceLogo place={model.heard} size={14} />Heard you on {PLACE_NAMES[model.heard]}</>}
            </span>
            <Burst trigger={model.burst} />
        </div>
    );
}

/** The scene on a phone: a strip over the question. */
export function StageStrip({ model, name, status }: { model: StageModel; name: string; status: string }) {
    return (
        <div className="ast-strip" aria-hidden="true">
            <span className="ast-strip__face" style={model.pod ? { background: markTint(model.pod.id).background } : undefined}>
                {model.pod
                    ? <TeammateFace key={model.heard ?? "still"} pod={model.pod} size={40} className={model.heard ? "ast-strip__hop" : undefined} />
                    : <span className="ast-strip__bars"><i /><i /><i /></span>}
            </span>
            <span className="ast-strip__said">
                <b>{name}</b>
                <span className="arh__status"><i />{status}</span>
            </span>
            <span className="ast-strip__tiles">
                {model.slots.filter((slot) => slot.slug).map((slot) => (
                    <span key={slot.key} className="ast-strip__tile" data-state={slot.state}><GlyphIcon slug={slot.slug} size={13} /></span>
                ))}
                {model.reach.map((key) => <span key={key} className="ast-strip__tile" data-state="in"><PlaceLogo place={key} size={13} /></span>)}
            </span>
        </div>
    );
}

export function PlaceLogo({ place, size }: { place: PlaceKey; size: number }) {
    if (place === "EMAIL") {
        return (
            <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <circle cx="12" cy="12" r="3.6" />
                <path d="M15.6 12v1.4a2.6 2.6 0 0 0 5.2 0V12a8.8 8.8 0 1 0-3.5 7" />
            </svg>
        );
    }
    return <ChannelIcon platform={place} size={size} />;
}

function Face({ pod, size, mood, greeting }: { pod: { id: string; name: string; iconUrl: string | null }; size: number; mood: Mood; greeting: number }) {
    const character = characterOf(pod.id, pod.iconUrl);
    if (!character) return <TeammateFace pod={pod} size={size} live />;
    return <CharacterPuppet character={character} size={Math.round(size * 0.92)} label={`${pod.name}, your Lemma`} mood={mood} greeting={greeting} />;
}

/** Lemma's three bars, listening: one jumps for each key typed. */
function Bars({ typing }: { typing: number }) {
    const [jump, setJump] = useState<{ bar: number; at: number }>({ bar: -1, at: 0 });
    const seen = useRef(typing);
    useEffect(() => {
        if (typing === seen.current) return;
        seen.current = typing;
        setJump({ bar: Math.floor(Math.random() * 3), at: typing });
    }, [typing]);
    return (
        <span className="ast__bars">
            {[0, 1, 2].map((bar) => <i key={bar === jump.bar ? `${bar}-${jump.at}` : bar} className={bar === jump.bar ? "is-jump" : undefined} />)}
        </span>
    );
}

function Tick() {
    return (
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M5 12.5l4.5 4.5L19 7.5" />
        </svg>
    );
}

/** A burst of the logo's little bars from the face, once per bump. */
function Burst({ trigger }: { trigger: number }) {
    const canvas = useRef<HTMLCanvasElement>(null);
    const seen = useRef(trigger);
    useEffect(() => {
        if (trigger === seen.current) return;
        seen.current = trigger;
        const element = canvas.current;
        if (!element || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
        const box = element.getBoundingClientRect();
        if (!box.width) return;
        const ratio = devicePixelRatio || 1;
        element.width = box.width * ratio;
        element.height = box.height * ratio;
        const context = element.getContext("2d");
        if (!context) return;
        context.scale(ratio, ratio);
        const style = getComputedStyle(element);
        const colours = [style.getPropertyValue("--brand-bars").trim() || "#5a3fd4", style.getPropertyValue("--ok").trim(), style.getPropertyValue("--info").trim(), "#ffcf28", "#8f7ae6"];
        const bits = Array.from({ length: 70 }, () => {
            const angle = Math.random() * Math.PI * 2;
            const speed = 140 + Math.random() * 240;
            return {
                x: box.width / 2, y: box.height / 2,
                vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed - 120,
                h: 6 + Math.random() * 9, r: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 8,
                colour: colours[Math.floor(Math.random() * colours.length)],
            };
        });
        const start = performance.now();
        let last = start;
        let frame = 0;
        const step = (now: number) => {
            const dt = Math.min(0.05, (now - last) / 1000);
            last = now;
            const life = (now - start) / 1700;
            context.clearRect(0, 0, box.width, box.height);
            for (const bit of bits) {
                bit.vy += 380 * dt; bit.vx *= 0.985; bit.x += bit.vx * dt; bit.y += bit.vy * dt; bit.r += bit.vr * dt;
                context.save();
                context.globalAlpha = Math.max(0, 1 - life);
                context.translate(bit.x, bit.y);
                context.rotate(bit.r);
                context.fillStyle = bit.colour;
                context.beginPath();
                context.roundRect(-1.5, -bit.h / 2, 3, bit.h, 1.5);
                context.fill();
                context.restore();
            }
            if (life < 1) frame = requestAnimationFrame(step);
            else context.clearRect(0, 0, box.width, box.height);
        };
        frame = requestAnimationFrame(step);
        return () => cancelAnimationFrame(frame);
    }, [trigger]);
    return <canvas ref={canvas} className="ast__burst" />;
}

function useWidth(ref: RefObject<HTMLElement | null>): number {
    const [width, setWidth] = useState(0);
    useEffect(() => {
        const element = ref.current;
        if (!element) return;
        const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
        observer.observe(element);
        return () => observer.disconnect();
    }, [ref]);
    return width;
}
