import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { AgentSurfaceResponse } from "lemma-sdk";
import { source, type Pod, type Surface } from "@/data";
import { filtersSupported, routesSupported, surfaceDraft, surfacePatch, type SurfaceDraft } from "@/data/surface-settings";
import { SetupActions } from "./surface-setup";
import { channelName } from "./channels";

export function SurfaceManage({ pod, surface, onBack, onSaved }: { pod: Pod; surface: Surface; onBack: () => void; onSaved: () => void }) {
    const detail = useQuery({ queryKey: ["surface-detail", pod.id, surface.name], queryFn: () => source.getSurface(pod.id, surface.name) });
    const setup = useQuery({ queryKey: ["surface-setup", pod.id, surface.name], queryFn: () => source.surfaceSetup(pod.id, surface.name), gcTime: 0 });
    const consent = setup.data?.admin_consent;
    return <div className="surface-setup">
        <button className="linkish" onClick={onBack}>Back to channels</button>
        <h3>Manage {channelName(surface.platform)}</h3>
        {setup.isPending && <p role="status">Checking setup…</p>}
        {setup.isError && <p role="alert">Could not read setup status. <button className="btn" onClick={() => void setup.refetch()}>Retry</button></p>}
        {setup.data && <>
            <p role="status">{setup.data.ready ? "Setup is complete." : "There are steps left to finish setup."}</p>
            {consent?.required && !consent.granted && consent.consent_url && <a className="btn btn--primary" href={consent.consent_url} target="_blank" rel="noreferrer">Grant administrator consent</a>}
            {consent?.granted && <p>Administrator consent granted.</p>}
            <SetupActions actions={setup.data.actions ?? []} />
            <button className="btn" disabled={setup.isFetching} onClick={() => { void setup.refetch(); void detail.refetch(); }}>Check setup again</button>
        </>}
        {detail.isPending && <p role="status">Loading channel settings…</p>}
        {detail.isError && <p role="alert">Could not read channel settings. <button className="btn" onClick={() => void detail.refetch()}>Retry</button></p>}
        {detail.data && <SurfaceForm key={detail.data.id} pod={pod} surface={detail.data} onSaved={onSaved} />}
    </div>;
}

function SurfaceForm({ pod, surface, onSaved }: { pod: Pod; surface: AgentSurfaceResponse; onSaved: () => void }) {
    const [draft, setDraft] = useState(() => surfaceDraft(surface));
    const change = (patch: Partial<SurfaceDraft>) => setDraft(current => ({ ...current, ...patch }));
    const agents = useQuery({ queryKey: ["surface-agents", pod.id], queryFn: () => source.listAgents(pod.id) });
    const channels = useQuery({ queryKey: ["surface-channels", pod.id, surface.name], queryFn: () => source.surfaceChannels(pod.id, surface.name), enabled: routesSupported(surface.platform) });
    const save = useMutation({ mutationFn: () => source.updateSurface(pod.id, surface.name, surfacePatch(surface.platform, draft)), onSuccess: onSaved });
    const options = channels.data?.channels ?? [];
    return <form className="record-form surface-setup" onSubmit={event => { event.preventDefault(); save.mutate(); }}>
        <fieldset disabled={save.isPending} className="surface-setup__fieldset">
            <label className="connect-check"><input type="checkbox" checked={draft.enabled} onChange={event => change({ enabled: event.target.checked })} />Channel enabled</label>
            <label className="record-form__field">Who answers
                <select value={draft.agent} onChange={event => change({ agent: event.target.value })}>
                    <option value="pod_default">{pod.name}</option>
                    {draft.agent !== "pod_default" && !agents.data?.some(agent => agent.name === draft.agent) && <option value={draft.agent}>{draft.agent}</option>}
                    {agents.data?.filter(agent => !agent.front).map(agent => <option key={agent.name} value={agent.name}>{agent.label}</option>)}
                </select>
            </label>
            {agents.isError && <button type="button" className="btn" onClick={() => void agents.refetch()}>Retry responders</button>}
            {routesSupported(surface.platform) && <section className="surface-setup__section">
                <h4>Channels</h4><p>This responder answers in the channels you select.</p>
                {channels.isPending && <p role="status">Loading channels…</p>}
                {channels.isError && <p role="alert">Could not load channels. <button type="button" className="btn" onClick={() => void channels.refetch()}>Retry</button></p>}
                {draft.channels.map((route, index) => <div className="surface-setup__route record-form__field" key={route.channel_id || index}>
                    <select aria-label={`Channel ${index + 1}`} value={route.channel_id} onChange={event => {
                        const picked = options.find(option => option.id === event.target.value);
                        change({ channels: draft.channels.map((row, at) => at === index ? { channel_id: event.target.value, channel_name: picked?.name ?? null } : row) });
                    }}>
                        {!options.some(option => option.id === route.channel_id) && <option value={route.channel_id}>{route.channel_name || route.channel_id}</option>}
                        {options.filter(option => option.id === route.channel_id || !draft.channels.some(row => row.channel_id === option.id)).map(option => <option key={option.id} value={option.id}>{option.name || option.id}{option.is_member === false ? " (not invited)" : ""}</option>)}
                    </select>
                    <button type="button" className="btn" aria-label={`Remove ${route.channel_name || route.channel_id}`} onClick={() => change({ channels: draft.channels.filter((_, at) => at !== index) })}>Remove</button>
                    {options.find(option => option.id === route.channel_id)?.is_member === false && <small>Invite the bot to this channel before messages can arrive.</small>}
                </div>)}
                {!channels.isPending && !channels.isError && !options.length && <p>No channels found. Invite the bot to a channel, then refresh.</p>}
                <div className="surface-setup__actions">
                    <button type="button" className="btn" disabled={!options.some(option => !draft.channels.some(row => row.channel_id === option.id))} onClick={() => {
                        const next = options.find(option => !draft.channels.some(row => row.channel_id === option.id));
                        if (next) change({ channels: [...draft.channels, { channel_id: next.id, channel_name: next.name ?? null }] });
                    }}>Add channel</button>
                    <button type="button" className="btn" onClick={() => void channels.refetch()}>Refresh channels</button>
                </div>
            </section>}
            {filtersSupported(surface.platform) && <>
                <label className="record-form__field">Allowed sender domains<input value={draft.domains} placeholder="example.com" onChange={event => change({ domains: event.target.value })} /></label>
                <label className="record-form__field">Allowed sender email addresses<textarea value={draft.emails} placeholder="person@example.com" onChange={event => change({ emails: event.target.value })} /></label>
                <small>Separate entries with commas or new lines. Leave both empty to apply the usual access rules without a sender filter.</small>
            </>}
            <label className="connect-check"><input type="checkbox" checked={draft.allowSend} onChange={event => change({ allowSend: event.target.checked })} />Let agents speak first in existing threads</label>
            <button className="btn btn--primary" type="submit">{save.isPending ? "Saving…" : "Save channel"}</button>
        </fieldset>
        {save.isError && <p role="alert">{save.error.message}</p>}
    </form>;
}
