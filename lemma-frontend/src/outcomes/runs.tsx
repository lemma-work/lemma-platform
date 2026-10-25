"use client";

import { useMemo, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { readableName } from "@/library/reading";
import { useWorkflowList } from "@/workflow/use-run";
import { outcomeView, type Go } from "./model";
import { AppTiles, useApps } from "./ask";
import { Icon, to, type IconName } from "./parts";

interface Chip {
    key: string;
    icon: IconName;
    label: string;
    link: { href: string; onClick?: ReturnType<typeof to>["onClick"] };
}

const CHIPS_SHOWN = 6;

/** What a Lemma runs, a shelf each: its apps, its tables, its automations.
 *  Every one opens a click away, "View all" goes to where they are all
 *  listed, and "New …" starts asking for one -- a Lemma builds what it
 *  runs, so making one is asking for it. */
export function WhatItRuns({ pod, go, onNew }: { pod: Pod; go: Go; onNew: (words: string) => void }) {
    const solo = useMemo(() => [pod], [pod]);
    const { apps } = useApps(solo);
    const tables = useQuery({ queryKey: ["outcome-tables", pod.id], queryFn: () => source.listLibrary(pod.id, "tables", "/"), staleTime: 5 * 60_000, retry: false });
    const schedules = useQuery({ queryKey: ["outcome-schedules", pod.id], queryFn: () => source.listSchedules(pod.id), staleTime: 5 * 60_000 });
    const workflows = useWorkflowList(pod.id);
    const xray = to(outcomeView(pod.id, "xray"), go);
    const tableChips: Chip[] = (tables.data?.items ?? []).filter((item) => item.kind === "table").map((item) => ({
        key: item.id, icon: "table", label: readableName(item.name), link: to({ name: "table", podId: pod.id, table: item.name }, go),
    }));
    const automationChips: Chip[] = [
        ...(workflows.data ?? []).map((flow): Chip => ({ key: "w:" + flow.id, icon: "flow", label: readableName(flow.name), link: to({ name: "workflow", podId: pod.id, workflow: flow.name }, go) })),
        ...(schedules.data ?? []).filter((job) => !job.internal).map((job): Chip => ({ key: "s:" + job.id, icon: "clock", label: job.title, link: to(outcomeView(pod.id, "settings", "schedules"), go) })),
    ];
    return (
        <>
            <Shelf title="Apps" count={apps.length} all={to(outcomeView(pod.id, "apps"), go)} newLabel="New app" onNew={() => onNew("Build an app for ")}>
                {apps.length > 0 && <AppTiles apps={apps.slice(0, 5)} go={go} showWhose={false} />}
            </Shelf>
            <Shelf title="Tables" count={tableChips.length} all={xray} newLabel="New table" onNew={() => onNew("Make a table to track ")}>
                <Chips chips={tableChips} />
            </Shelf>
            <Shelf title="Automations" count={automationChips.length} all={xray} newLabel="New automation" onNew={() => onNew("Set up an automation that ")}>
                <Chips chips={automationChips} />
            </Shelf>
        </>
    );
}

function Shelf({ title, count, all, newLabel, onNew, children }: {
    title: string;
    count: number;
    all: ReturnType<typeof to>;
    newLabel: string;
    onNew: () => void;
    children: ReactNode;
}) {
    return (
        <section className="ox-shelf" aria-label={title}>
            <div className="ox-shelf__head">
                <p className="ox-shelf__t">{title}{count > 0 && <span className="ox-shelf__n">{count}</span>}</p>
                {count > 0 && <a className="ox-shelf__all" {...all}>View all</a>}
                <button type="button" className="ox-shelf__new" onClick={onNew}><Icon name="plus" size={13} />{newLabel}</button>
            </div>
            {count === 0 ? <p className="ox-shelf__none">None yet.</p> : children}
        </section>
    );
}

function Chips({ chips }: { chips: readonly Chip[] }) {
    return (
        <div className="ox-chiplinks">
            {chips.slice(0, CHIPS_SHOWN).map((chip) => (
                <a key={chip.key} className="ox-chiplink" {...chip.link}>
                    <Icon name={chip.icon} size={14} />
                    <span>{chip.label}</span>
                </a>
            ))}
            {chips.length > CHIPS_SHOWN && <span className="ox-chiplinks__more">and {chips.length - CHIPS_SHOWN} more</span>}
        </div>
    );
}
