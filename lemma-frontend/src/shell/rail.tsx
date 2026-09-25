import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { MATES } from "@/copy";
import { SearchIcon, CloseIcon } from "@/ui/icons";
import { Mark } from "./mark";
import { NewTeammate } from "./new-teammate";

/** A touch screen, where the rail is a drawer on a phone.
 *
 *  Each rig is a 1254-unit SVG under a stack of filters, repainted every
 *  frame, and iOS WebKit — every browser on an iPhone — gives up under a
 *  drawer full of them: the marks keep animating and the names beside them
 *  are never painted, so the rail is a column of faces with nobody's name on
 *  it. The still is the same sculpture; what the phone gives up is the breath.
 *  Still on the server, too: a picture that becomes a rig after hydration
 *  costs nothing, and the reverse would mount a rig per row on a phone just to
 *  take them all down again. */
const TOUCH = "(hover: none)";
function subscribeTouch(changed: () => void): () => void {
    const query = window.matchMedia(TOUCH);
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
}
function useTouchScreen(): boolean {
    return useSyncExternalStore(subscribeTouch, () => window.matchMedia(TOUCH).matches, () => true);
}

export function Rail({ pods, activeId, onPick, onHire, orgId, compact = false }: {
    pods: Pod[]; activeId: string | null; onPick: (id: string) => void; onHire: () => void; orgId: string | null; compact?: boolean;
}) {
    const [search, setSearch] = useState("");
    const cache = useQueryClient();
    const still = useTouchScreen();
    const intent = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
    useEffect(() => () => clearTimeout(intent.current), []);
    const filtered = pods.filter(pod => pod.name.toLowerCase().includes(search.trim().toLowerCase()));
    function warm(pod: Pod) {
        clearTimeout(intent.current);
        intent.current = setTimeout(() => {
            void cache.prefetchQuery({ queryKey: ["pod-detail", pod.id], queryFn: () => source.getPodDetail(pod.id, pod.name, pod.iconUrl), staleTime: 5 * 60_000 });
            void cache.prefetchQuery({ queryKey: ["conversations", pod.id], queryFn: () => source.listConversations(pod.id), staleTime: 60_000 });
            void cache.prefetchQuery({ queryKey: ["tabs", pod.id], queryFn: () => source.listTabs(pod.id), staleTime: 5 * 60_000 });
            void cache.prefetchQuery({ queryKey: ["surfaces", 2, pod.id], queryFn: () => source.listSurfaces(pod.id), staleTime: 60_000 });
        }, 120);
    }
    return <nav className="rail" aria-label={MATES}>
        {!compact && <label className="rail__search"><SearchIcon size={16} /><input placeholder="Find a teammate" aria-label="Find a teammate" value={search} onChange={e => setSearch(e.target.value)} />{search && <button onClick={() => setSearch("")} aria-label="Clear search"><CloseIcon size={14} /></button>}</label>}
        <div className="rail__section"><span className="rail__label">{MATES}</span>{!compact && <span className="rail__count">{pods.length}</span>}</div>
        <div className="rail__list">
            {(compact ? pods : filtered).map(pod => <button key={pod.id} className="rail__pod" aria-current={pod.id === activeId ? "page" : undefined} onClick={() => onPick(pod.id)} title={pod.name} aria-label={pod.name}
                onMouseEnter={() => warm(pod)} onMouseLeave={() => clearTimeout(intent.current)} onFocus={() => warm(pod)} onBlur={() => clearTimeout(intent.current)}>
                <Mark seed={pod.id} name={pod.name} icon={pod.iconUrl} size={30} still={still} /><span className="rail__name">{pod.name}</span>
            </button>)}
            {!compact && filtered.length === 0 && <div className="rail__empty">{pods.length ? "No teammates match." : "Your teammates will appear here."}</div>}
        </div>
        <NewTeammate orgId={orgId} onOpen={onHire} />
    </nav>;
}
