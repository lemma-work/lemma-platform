import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Surface } from "@/data";
import { surfacesForAgent, surfaceStatus } from "@/data/surface-settings";
import { groupsSupported } from "@/data/surface-groups";
import { useSurfaces } from "./surfaces";
import { channelName } from "./channels";
import { Modal } from "./modal";
import { SurfaceManage } from "./surface-manage";
import { SurfaceGroups } from "./surface-groups";

export function AgentChannels({ podId, name, label }: { podId: string; name: string; label: string }) {
    const surfaces = useSurfaces(podId);
    const [selected, setSelected] = useState<Surface | null>(null);
    const cache = useQueryClient();
    const own = surfacesForAgent(surfaces.data ?? [], name);
    /* Read for Manage, and for a channel's groups, which say the space's name. */
    const grouped = own.some(surface => groupsSupported(surface.platform));
    const pod = useQuery({ queryKey: ["surface-pod", podId], queryFn: () => source.getPod(podId), enabled: Boolean(selected) || grouped });
    const saved = () => {
        for (const key of [["surfaces", 2, podId], ["surface-detail", podId], ["surface-setup", podId], ["surface-channels", podId], ["my-surfaces"]]) {
            void cache.invalidateQueries({ queryKey: key });
        }
        setSelected(null);
    };
    return <section aria-label={`Channels for ${label}`}>
        <h3>Channels</h3>
        {surfaces.isPending && <p role="status">Loading channels…</p>}
        {surfaces.isError && <button className="btn" onClick={() => void surfaces.refetch()}>Retry channels</button>}
        {surfaces.isSuccess && !own.length && <p>No channels connected to this agent.</p>}
        <ul className="reachrows">{own.map(surface => <li className="reachrow" key={surface.id}>
            <div className="reachrow__body"><b>{channelName(surface.platform)}</b>
                {surface.handle && <span>{surface.handle}</span>}
                <span className="reachrow__note">{surfaceStatus(surface.status, surface.active)}</span>
                {groupsSupported(surface.platform) && pod.data && (
                    <SurfaceGroups podId={podId} surface={surface} bot={label} space={pod.data.name} members={pod.data.members} />
                )}
            </div>
            <button className="btn" onClick={() => setSelected(surface)}>Manage</button>
        </li>)}</ul>
        {selected && <Modal title={`Channels for ${label}`} onClose={() => setSelected(null)}>
            {pod.isPending && <p role="status">Loading channel settings…</p>}
            {(pod.isError || (pod.isSuccess && !pod.data)) && <p role="alert">Could not load the workspace. <button className="btn" onClick={() => void pod.refetch()}>Retry</button></p>}
            {pod.data && <SurfaceManage pod={pod.data} surface={selected} onBack={() => setSelected(null)} onSaved={saved} onLeave={() => setSelected(null)} />}
        </Modal>}
    </section>;
}
