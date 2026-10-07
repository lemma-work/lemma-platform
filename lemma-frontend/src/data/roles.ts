/** The roles on the shelf, as the backend lists them.
 *
 *  A role is a template the backend ships (`pod_bundle/templates/<name>/`),
 *  and its card is read off that template — the prose from the `role` block of
 *  its `pod.json`, the skills, tables and measures from what it actually
 *  carries (`GET /pods/bundle/templates`). Nothing about a role is written
 *  here: adding one is adding a template, and the shelf picks it up. */

import type { Hire } from "./hires";
import { capabilityList, POD_DEFAULT_TOOLSETS } from "@/stage/colleagues";
import { describeCron } from "@/schedule/schedules";

export interface RoleCard {
    /** What a kind=TEMPLATE import takes. */
    template: string;
    name: string;
    role: string;
    about: string;
    seed: string;
    brings: string[];
    wins: { say: string; needs?: { connector: string; label: string } }[];
    offers: { title: string; detail: string; cron: string; instruction: string }[];
    judgedOn: string[];
    /** The `SKILL.md` files the template ships. */
    skills: { name: string; description: string }[];
    /** The tables its work is kept in. */
    tables: { name: string; description: string }[];
}

const text = (value: unknown): string => (typeof value === "string" ? value.trim() : "");

const texts = (value: unknown): string[] =>
    Array.isArray(value) ? value.map(text).filter(Boolean) : [];

function named(value: unknown): { name: string; description: string }[] {
    if (!Array.isArray(value)) return [];
    return value.flatMap((item) => {
        const record = item as Record<string, unknown> | null;
        const name = text(record?.name);
        return name ? [{ name, description: text(record?.description) }] : [];
    });
}

/** One card off the wire, or null when it is missing what a shelf card has
 *  to show — a role with no name or no face is not drawn half-empty. */
export function readRoleCard(raw: unknown): RoleCard | null {
    if (!raw || typeof raw !== "object") return null;
    const card = raw as Record<string, unknown>;
    const template = text(card.template);
    const name = text(card.name);
    const seed = text(card.seed);
    if (!template || !name || !seed) return null;
    const wins = (Array.isArray(card.wins) ? card.wins : []).flatMap((item) => {
        const win = item as Record<string, unknown> | null;
        const say = text(win?.say);
        if (!say) return [];
        const needs = win?.needs as Record<string, unknown> | null | undefined;
        const connector = text(needs?.connector);
        return [connector ? { say, needs: { connector, label: text(needs?.label) || connector } } : { say }];
    });
    const offers = (Array.isArray(card.offers) ? card.offers : []).flatMap((item) => {
        const offer = item as Record<string, unknown> | null;
        const title = text(offer?.title);
        const cron = text(offer?.cron);
        const instruction = text(offer?.instruction);
        return title && cron && instruction ? [{ title, detail: text(offer?.detail), cron, instruction }] : [];
    });
    return {
        template,
        name,
        role: text(card.role),
        about: text(card.about),
        seed,
        brings: texts(card.brings),
        wins,
        offers,
        judgedOn: texts(card.judged_on),
        skills: named(card.skills),
        tables: named(card.tables),
    };
}

export function readRoleCards(raw: unknown): RoleCard[] {
    const items = (raw as { items?: unknown } | null)?.items;
    if (!Array.isArray(items)) return [];
    return items.map(readRoleCard).filter((card): card is RoleCard => card !== null);
}

/** A card as a shelf listing.
 *
 *  What the candidate page reads is what the hire will get. Its tools are the
 *  set every teammate's own responder runs with, because that is the agent a
 *  template's hire answers with; its schedules are the offers, none of them
 *  running until a person turns one on; and it suggests no apps, because a
 *  template that ships none has none to suggest. */
export function hireFromCard(card: RoleCard): Hire {
    return {
        id: card.template,
        name: card.name,
        role: card.role,
        seed: card.seed,
        brings: card.brings,
        about: card.about,
        openers: card.wins.map((win) => win.say),
        skills: capabilityList(POD_DEFAULT_TOOLSETS).map((capability) => ({
            id: capability.code,
            label: capability.word,
            blurb: capability.says,
        })),
        permits: [],
        commitments: card.offers.map((offer) => ({
            title: offer.title,
            detail: offer.detail,
            cadence: describeCron(offer.cron),
        })),
        projects: [],
        counts: { tables: card.tables.length, functions: 0, workflows: 0 },
        bundle: card.template,
        wins: card.wins,
        offers: card.offers,
        judgedOn: card.judgedOn,
        taught: card.skills,
        tables: card.tables,
    };
}
