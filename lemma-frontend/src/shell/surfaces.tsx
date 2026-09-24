import { surfacesForAgent } from "@/data/surface-settings";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { source } from "@/data";
import type { Pod, Surface } from "@/data";
import { CheckIcon, CopyIcon, PlusIcon, RefreshIcon, WarningIcon } from "@/ui/icons";
import { ChannelIcon, channelKey, channelName } from "./channels";
import { ReachSheet } from "./reach";

/** The strip: where this teammate can already be reached.
 *
 *  Deliberately not also the connect flow. A row of grey icons for every
 *  platform, each opening a modal that sends you to a different app, puts the
 *  least interesting thing — five platforms you have not set up — in the most
 *  valuable space, and makes every platform look equally easy.
 *
 *  So the strip shows only what is true, the addresses that exist, and one
 *  button opens `ReachSheet`, where connecting is actually done. */

export function useSurfaces(podId: string) {
    return useQuery({
        queryKey: ["surfaces", 2, podId],
        queryFn: () => source.listSurfaces(podId),
        staleTime: 60_000,
        refetchOnWindowFocus: true,
    });
}

function CopyAddress({ surface, compact = false }: { surface: Surface; compact?: boolean }) {
    const [state, setState] = useState<"idle" | "copied" | "error">("idle");
    const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
    useEffect(() => () => clearTimeout(timer.current), []);
    const label = channelName(surface.platform);
    if (channelKey(surface.platform) === "EMAIL") {
        const address = (surface.email || surface.handle).trim().replace(/^(mailto:)+/i, "").replace(/\\@/g, "@");
        return <a className={`reach reach--connected${compact ? " reach--compact" : ""}`} href={"mailto:" + address} title={"Email " + address} aria-label={"Email " + address}>
            <span className="reach__mark"><ChannelIcon platform={surface.platform} /></span>
            <span className="reach__handle">{address}</span>
        </a>;
    }
    return (
        <button
            className={`reach reach--connected${compact ? " reach--compact" : ""}`}
            title={`${label} · ${surface.handle} — copy address`}
            aria-label={`Copy ${label} address ${surface.handle}`}
            onClick={async () => {
                clearTimeout(timer.current);
                try {
                    await navigator.clipboard.writeText(surface.email ?? surface.handle);
                    setState("copied");
                } catch {
                    setState("error");
                }
                timer.current = setTimeout(() => setState("idle"), 1800);
            }}
        >
            <span className="reach__mark">
                {state === "copied" ? <CheckIcon size={18} /> : <ChannelIcon platform={surface.platform} />}
            </span>
            <span className="reach__handle">{state === "copied" ? "Copied" : surface.handle}</span>
            {!compact && <CopyIcon size={14} className="reach__copy" />}
            <span className={compact && state !== "idle" ? "reach__feedback" : "sr-only"} role="status">
                {state === "copied" ? "Address copied" : state === "error" ? "Copy unavailable" : ""}
            </span>
        </button>
    );
}

export function Surfaces({ pod, expanded = false }: { pod: Pod; expanded?: boolean }) {
    const surfaces = useSurfaces(pod.id);
    const [sheet, setSheet] = useState(false);
    const all = surfacesForAgent(surfaces.data ?? []);
    const mine = all.filter((surface) => surface.mine && surface.active !== false && Boolean(surface.handle));

    if (surfaces.isPending) {
        return (
            <span className="reaches" aria-label="Loading channels" aria-busy="true">
                <span className="channel-skeleton" />
                <span className="channel-skeleton" />
            </span>
        );
    }
    if (surfaces.isError && !surfaces.data) {
        return (
            <button className="reach reach--retry" onClick={() => void surfaces.refetch()}>
                <RefreshIcon size={16} /> Retry channels
            </button>
        );
    }

    return (
        <>
            <div className={`reaches${expanded ? " reaches--expanded" : ""}`} aria-label="Contact channels">
                {mine.map((surface) => (
                    <CopyAddress key={surface.id} surface={surface} compact={!expanded} />
                ))}
                <button
                    className={`reach reach--add${expanded || mine.length === 0 ? " reach--labeled" : ""}`}
                    title={`Manage channels for ${pod.name}`}
                    aria-label={`Manage channels for ${pod.name}`}
                    onClick={() => setSheet(true)}
                >
                    <span className="reach__mark"><PlusIcon size={15} /></span>
                    {(expanded || mine.length === 0) && <span>{all.length ? "Manage channels" : "Add a channel"}</span>}
                </button>
                {surfaces.isError && (
                    <span title="Channel status may be out of date"><WarningIcon size={16} /></span>
                )}
            </div>
            {sheet && <ReachSheet pod={pod} onClose={() => setSheet(false)} />}
        </>
    );
}
