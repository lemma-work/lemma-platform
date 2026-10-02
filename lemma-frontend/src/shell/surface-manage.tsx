import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { AgentSurfaceResponse, SurfaceSetupResponse } from "lemma-sdk";
import { source, type Group, type Pod, type Surface } from "@/data";
import { filtersSupported, routesSupported, surfaceDraft, surfacePatch, type SurfaceDraft } from "@/data/surface-settings";
import { answering, byActivity, isGroupPlatform, sayAnswering, sayAnsweringFully } from "@/data/groups";
import { groupTitle } from "@/data/surface-groups";
import { useMe } from "@/session/use-me";
import { GroupSheets, type GroupSheet } from "@/space/group-sheets";
import { refreshGroups, useGroups } from "@/space/group-queries";
import { useFeature } from "@/site/analytics/flags";
import { SetupActions } from "./surface-setup";
import { ChannelIcon, channelName } from "./channels";
import { PlaceLink, goTo, groupHref } from "./place-link";
import { NOWHERE, writeAddress } from "./address";
import { SetUpOnThisMac } from "@/desktop/set-up-on-this-mac";
import { credentialFormForChannel } from "@/desktop/this-mac";
import { BackIcon } from "@/ui/icons";

/** One channel's settings, in plain words.
 *
 *  Whether it works, whether it is on, who answers, whether that one may
 *  write first — and, on a platform with groups, the groups it is in and the
 *  way to start or add another. Each setting is one line of what it does,
 *  said about the name that answers, never about "the channel". */
export function SurfaceManage({ pod, surface, onBack, onSaved, onLeave }: {
    pod: Pod;
    surface: Surface;
    onBack: () => void;
    onSaved: () => void;
    /** Leaving for another page — a group's, or the Groups page — so the
     *  dialog this sits in can close first. */
    onLeave?: () => void;
}) {
    const detail = useQuery({ queryKey: ["surface-detail", pod.id, surface.name], queryFn: () => source.getSurface(pod.id, surface.name) });
    const setup = useQuery({ queryKey: ["surface-setup", pod.id, surface.name], queryFn: () => source.surfaceSetup(pod.id, surface.name), gcTime: 0 });
    const name = channelName(surface.platform);
    /* Who answers here: the space, or an agent with a channel of its own. */
    const bot = surface.mine ? pod.name : surface.agentName || pod.name;
    return <div className="surface-setup smanage">
        <button className="linkish smanage__back" onClick={onBack}><BackIcon size={14} aria-hidden="true" /> All channels</button>
        <div className="smanage__head">
            <ChannelIcon platform={surface.platform} size={32} />
            <div className="smanage__title">
                <h3>{name}</h3>
                {surface.handle && <span>{surface.handle}</span>}
            </div>
        </div>
        <Health
            setup={setup.data}
            pending={setup.isPending}
            failed={setup.isError}
            checking={setup.isFetching || detail.isFetching}
            platform={surface.platform}
            onCheck={() => { void setup.refetch(); void detail.refetch(); }}
        />
        {detail.isPending && <p className="smanage__quiet" role="status">Reading its settings…</p>}
        {detail.isError && <p className="smanage__quiet" role="alert">Couldn’t read its settings. <button className="linkish" onClick={() => void detail.refetch()}>Try again</button></p>}
        {detail.data && <SurfaceForm key={detail.data.id} pod={pod} surface={detail.data} listed={surface} bot={bot} onSaved={onSaved} onLeave={onLeave} />}
    </div>;
}

/** Whether it works, in one line, and the steps left where it does not. */
function Health({ setup, pending, failed, checking, platform, onCheck }: {
    setup: SurfaceSetupResponse | undefined;
    pending: boolean;
    failed: boolean;
    checking: boolean;
    platform: string;
    onCheck: () => void;
}) {
    const consent = setup?.admin_consent;
    const paused = setup?.status === "INACTIVE";
    const state = pending ? "Checking it works…"
        : failed ? "Couldn’t check whether it works"
        : !setup?.ready ? "Not finished yet. The steps left are below."
        : paused ? "Connected, and paused"
        : "Connected and working";
    const tone = pending || failed ? "quiet" : !setup?.ready ? "waiting" : paused ? "quiet" : "good";
    return <>
        <div className="smanage__health">
            <span className="smanage__state" role="status"><i data-tone={tone} aria-hidden="true" />{state}</span>
            {!pending && <button type="button" className="ghost-pill smanage__recheck" disabled={checking} onClick={onCheck}>{checking ? "Checking…" : "Check again"}</button>}
        </div>
        {consent?.required && !consent.granted && consent.consent_url && <a className="btn btn--primary" href={consent.consent_url} target="_blank" rel="noreferrer">Approve it as an administrator</a>}
        {consent?.granted && <p className="smanage__quiet">An administrator approved it.</p>}
        {setup && <SetupActions actions={setup.actions ?? []} />}
        {setup && !setup.ready && <SetUpOnThisMac form={credentialFormForChannel(platform)} />}
    </>;
}

/** What turning it off pauses, on each platform. */
function pauses(platform: string): string {
    switch (platform.toUpperCase()) {
        case "WHATSAPP": return "Off pauses every chat and group on this number.";
        case "TELEGRAM": return "Off pauses every chat and group it is in.";
        case "SLACK":
        case "TEAMS": return "Off pauses every channel and direct message.";
        default: return "Off pauses everything that arrives here.";
    }
}

function SurfaceForm({ pod, surface, listed, bot, onSaved, onLeave }: {
    pod: Pod;
    surface: AgentSurfaceResponse;
    listed: Surface;
    bot: string;
    onSaved: () => void;
    onLeave?: () => void;
}) {
    const [draft, setDraft] = useState(() => surfaceDraft(surface));
    const [dropping, setDropping] = useState(false);
    const groupsOn = useFeature("groups");
    const cache = useQueryClient();
    const change = (patch: Partial<SurfaceDraft>) => setDraft(current => ({ ...current, ...patch }));
    const agents = useQuery({ queryKey: ["surface-agents", pod.id], queryFn: () => source.listAgents(pod.id) });
    const channels = useQuery({ queryKey: ["surface-channels", pod.id, surface.name], queryFn: () => source.surfaceChannels(pod.id, surface.name), enabled: routesSupported(surface.platform) });
    const save = useMutation({
        mutationFn: () => source.updateSurface(pod.id, surface.name, surfacePatch(surface.platform, draft)),
        /* Whether its groups answer people outside rides on this save, and
           every list of them says so: read them all again. */
        onSuccess: () => { refreshGroups(cache, pod.id); onSaved(); },
    });
    const drop = useMutation({ mutationFn: () => source.disconnect(pod.id, surface.name), onSuccess: onSaved });
    const options = channels.data?.channels ?? [];
    const name = channelName(surface.platform);
    const answerer = draft.agent === "pod_default" ? pod.name : agents.data?.find(agent => agent.name === draft.agent)?.label ?? bot;
    return <form className="record-form surface-setup smanage__form" onSubmit={event => { event.preventDefault(); save.mutate(); }}>
        <fieldset disabled={save.isPending || drop.isPending} className="surface-setup__fieldset">
            <label className="smanage__check">
                <input type="checkbox" checked={draft.enabled} onChange={event => change({ enabled: event.target.checked })} />
                <span>
                    <span className="smanage__label">{answerer} answers on {name}</span>
                    <small>{pauses(surface.platform)}</small>
                </span>
            </label>
            <label className="record-form__field smanage__field">
                <span className="smanage__label">Who answers</span>
                <select value={draft.agent} onChange={event => change({ agent: event.target.value })}>
                    <option value="pod_default">{pod.name}</option>
                    {draft.agent !== "pod_default" && !agents.data?.some(agent => agent.name === draft.agent) && <option value={draft.agent}>{draft.agent}</option>}
                    {agents.data?.filter(agent => !agent.front).map(agent => <option key={agent.name} value={agent.name}>{agent.label}</option>)}
                </select>
                <small>The one that answers every chat{isGroupPlatform(surface.platform) ? " and group" : ""} here.</small>
            </label>
            {agents.isError && <button type="button" className="btn" onClick={() => void agents.refetch()}>Read who can answer again</button>}
            {routesSupported(surface.platform) && <section className="surface-setup__section smanage__section">
                <h4>Channels {answerer} answers in</h4>
                <p>Pick the channels. Invite {answerer} to a channel in {name} first.</p>
                {channels.isPending && <p role="status">Reading the channels…</p>}
                {channels.isError && <p role="alert">Couldn’t read the channels. <button type="button" className="btn" onClick={() => void channels.refetch()}>Try again</button></p>}
                {draft.channels.map((route, index) => <div className="surface-setup__route record-form__field" key={route.channel_id || index}>
                    <select aria-label={`Channel ${index + 1}`} value={route.channel_id} onChange={event => {
                        const picked = options.find(option => option.id === event.target.value);
                        change({ channels: draft.channels.map((row, at) => at === index ? { channel_id: event.target.value, channel_name: picked?.name ?? null } : row) });
                    }}>
                        {!options.some(option => option.id === route.channel_id) && <option value={route.channel_id}>{route.channel_name || route.channel_id}</option>}
                        {options.filter(option => option.id === route.channel_id || !draft.channels.some(row => row.channel_id === option.id)).map(option => <option key={option.id} value={option.id}>{option.name || option.id}{option.is_member === false ? " (not invited yet)" : ""}</option>)}
                    </select>
                    <button type="button" className="btn" aria-label={`Remove ${route.channel_name || route.channel_id}`} onClick={() => change({ channels: draft.channels.filter((_, at) => at !== index) })}>Remove</button>
                    {options.find(option => option.id === route.channel_id)?.is_member === false && <small>Invite {answerer} to this channel first, or nothing there reaches it.</small>}
                </div>)}
                {!channels.isPending && !channels.isError && !options.length && <p>No channels yet. Invite {answerer} to one, then look again.</p>}
                <div className="surface-setup__actions">
                    <button type="button" className="btn" disabled={!options.some(option => !draft.channels.some(row => row.channel_id === option.id))} onClick={() => {
                        const next = options.find(option => !draft.channels.some(row => row.channel_id === option.id));
                        if (next) change({ channels: [...draft.channels, { channel_id: next.id, channel_name: next.name ?? null }] });
                    }}>Add a channel</button>
                    <button type="button" className="btn" onClick={() => void channels.refetch()}>Look again</button>
                </div>
            </section>}
            {filtersSupported(surface.platform) && <>
                <label className="record-form__field">Only mail from these domains<input value={draft.domains} placeholder="example.com" onChange={event => change({ domains: event.target.value })} /></label>
                <label className="record-form__field">Only mail from these addresses<textarea value={draft.emails} placeholder="person@example.com" onChange={event => change({ emails: event.target.value })} /></label>
                <small>Separate them with commas or new lines. Leave both empty to take mail from anyone the usual rules allow.</small>
            </>}
            <label className="smanage__check">
                <input type="checkbox" checked={draft.allowSend} onChange={event => change({ allowSend: event.target.checked })} />
                <span>
                    <span className="smanage__label">{answerer} can write first</span>
                    <small>In chats that already exist, for a reminder or a follow-up. Off: it only replies.</small>
                </span>
            </label>
            {groupsOn && isGroupPlatform(surface.platform) && (
                <label className="smanage__check">
                    <input type="checkbox" role="switch" checked={draft.answersOutsiders} onChange={event => change({ answersOutsiders: event.target.checked })} />
                    <span>
                        <span className="smanage__label">Answer people outside {pod.name}</span>
                        <small>From what {pod.name} has made Public, in every group this bot is in.</small>
                    </span>
                </label>
            )}
        </fieldset>
        {groupsOn && isGroupPlatform(listed.platform) && <GroupsHere pod={pod} surface={listed} bot={bot} onLeave={onLeave} />}
        {save.isError && <p role="alert">{save.error.message}</p>}
        {drop.isError && <p role="alert">{drop.error instanceof Error ? drop.error.message : "Couldn’t disconnect it."}</p>}
        <div className="smanage__foot">
            {dropping ? <span className="smanage__confirm">
                <span>Disconnect {name}? {answerer} stops answering here{isGroupPlatform(listed.platform) ? ", in every chat and group" : ""}.</span>
                <button type="button" className="ghost-pill smanage__drop" disabled={drop.isPending} onClick={() => drop.mutate()}>{drop.isPending ? "Disconnecting…" : "Disconnect"}</button>
                <button type="button" className="linkish" onClick={() => setDropping(false)}>Keep it</button>
            </span> : <button type="button" className="ghost-pill" disabled={save.isPending} onClick={() => setDropping(true)}>Disconnect</button>}
            <button className="pill-button" type="submit" disabled={save.isPending || drop.isPending}>{save.isPending ? "Saving…" : "Save"}</button>
        </div>
    </form>;
}

/** The groups this channel's bot is in, each opening its own page, and the
 *  way to start or add another. */
function GroupsHere({ pod, surface, bot, onLeave }: { pod: Pod; surface: Surface; bot: string; onLeave?: () => void }) {
    const me = useMe();
    const groups = useGroups(pod.id);
    const [sheet, setSheet] = useState<GroupSheet | null>(null);
    const here = byActivity((groups.data ?? []).filter((group) => group.surfaceName === surface.name));
    const platform = surface.platform.toUpperCase();
    /* Slack's are channels, and the settings above already list the ones it
       answers in — these are the same channels as the Groups page has them. */
    const heading = platform === "WHATSAPP" ? "Groups on this number" : platform === "SLACK" ? "Groups in this workspace" : "Groups " + bot + " is in";
    const add = platform === "WHATSAPP" ? "Start a WhatsApp group" : platform === "SLACK" ? "Add " + bot + " to a Slack channel" : "Add " + bot + " to a Telegram group";
    const allGroups = writeAddress({ ...NOWHERE, podId: pod.id, tabId: "space:groups" });
    const open = (group: Group) => { onLeave?.(); goTo(groupHref(pod.id, group.id)); };
    return (
        <section className="smanage__groups" aria-label={heading}>
            <div className="smanage__groups-head">
                <h4>{heading}</h4>
                <PlaceLink href={allGroups} className="smanage__all" onGo={onLeave}>See all groups</PlaceLink>
            </div>
            {groups.isPending && <p className="smanage__quiet" role="status">Reading the groups…</p>}
            {groups.isError && <p className="smanage__quiet" role="alert">Couldn’t read the groups. <button type="button" className="linkish" onClick={() => void groups.refetch()}>Try again</button></p>}
            {groups.isSuccess && here.length === 0 && <p className="smanage__quiet">None yet.</p>}
            {here.length > 0 && (
                <ul className="smanage__group-list">
                    {here.map((group) => {
                        const state = answering(group, me, pod.members);
                        return (
                            <li key={group.id}>
                                <PlaceLink href={groupHref(pod.id, group.id)} onGo={onLeave}>{groupTitle(group)}</PlaceLink>
                                {/* The bot is this one, so its own switch is said
                                    short: the full sentence would name it again. */}
                                <small>{state.kind === "bot-off" ? sayAnswering(state, pod.name) : sayAnsweringFully(state, pod.name)}</small>
                            </li>
                        );
                    })}
                </ul>
            )}
            {surface.active && (
                <button type="button" className="linkish smanage__add" onClick={() => setSheet(platform === "WHATSAPP"
                    ? { kind: "whatsapp", surface }
                    : platform === "SLACK" ? { kind: "slack", surface } : { kind: "telegram", surface })}>
                    {add}
                </button>
            )}
            {sheet && <GroupSheets pod={pod} bot={bot} sheet={sheet} onClose={() => setSheet(null)} onOpenGroup={open} />}
        </section>
    );
}
