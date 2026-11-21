"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CopyIcon, PeopleIcon, WarningIcon } from "@/ui/icons";
import { copyText } from "./clipboard";
import { openSettings } from "./open-settings";
import { useThisComputer } from "./this-computer";
import {
    enablePayload, friendlyError, joinPolicyCopy, readSharing, setupCommands, sharingBusy,
    sharingModeConsequence, sharingModeName, thisMac,
    type Sharing, type SharingMode, type ThisMacSnapshot, type TunnelProvider, type WhoCanJoin, sharingPhaseWords } from "./this-mac";
import { SettingRow, useThisMacSnapshot } from "./this-mac-settings";

/** Who can reach this installation, and who may make an account once they do.
 *
 *  The flows are Local settings' own, moved: the local network binds one
 *  private interface; Public goes through this Mac's own ngrok or Cloudflare
 *  account, which Lemma never installs or signs into. What changed is who
 *  asks the Public question — the shell, natively, after this page asks for
 *  it — so the consent is the person's and not this page's.
 *
 *  Turning sharing on moves this window to the shared address, where the
 *  shell deliberately answers nothing, so the page says that before it
 *  happens rather than after.
 *
 *  The three ways to be reached are cards (the kit's `.kopts`); what is in
 *  use now, and who can join, are rows on the section's hairlines. */

const MODES: SharingMode[] = ["this_computer", "local_network", "public"];

const NOT_READY = "Available once Lemma has finished starting.";

export function ThisMacSharing() {
    const noun = useThisComputer();
    const snapshot = useThisMacSnapshot();
    const queryClient = useQueryClient();
    const [choice, setChoice] = useState<SharingMode | null>(null);
    const [lanInterface, setLanInterface] = useState("");
    const [provider, setProvider] = useState<TunnelProvider>("ngrok");
    const [cloudflareSetup, setCloudflareSetup] = useState<"automatic" | "existing">("automatic");
    const [hostname, setHostname] = useState("");
    const [tunnelId, setTunnelId] = useState("");
    const [said, setSaid] = useState<{ text: string; bad?: boolean } | null>(null);

    const remember = (sharing: unknown) => {
        if (!sharing) return;
        queryClient.setQueryData<ThisMacSnapshot>(["this-mac"], (was) => was && { ...was, sharing: readSharing(sharing) });
    };

    const act = useMutation({
        mutationFn: ({ action, payload }: { action: "enable" | "disable" | "access" | "preflight"; payload?: Record<string, unknown> }) =>
            thisMac.sharing(action, payload),
        onSuccess: (answer, { action }) => {
            if (answer?.cancelled) {
                setSaid({ text: action === "enable" ? "Nothing changed. Lemma is still private to " + noun + "." : "Nothing changed." });
                return;
            }
            if (action === "preflight") {
                /* A preflight answers for one provider; its `sharing` is the
                   cheap snapshot without any readiness, so only the readiness
                   is taken from it. */
                const preflight = answer?.preflight as { provider?: string; readiness?: unknown } | null | undefined;
                const which = preflight?.provider === "cloudflare" ? "cloudflare" : preflight?.provider === "ngrok" ? "ngrok" : null;
                if (which && preflight?.readiness) {
                    const fresh = readSharing({ provider_readiness: { [which]: preflight.readiness } }).provider_readiness[which];
                    queryClient.setQueryData<ThisMacSnapshot>(["this-mac"], (was) => was?.sharing
                        ? { ...was, sharing: { ...was.sharing, provider_readiness: { ...was.sharing.provider_readiness, [which]: fresh } } }
                        : was);
                }
                return;
            }
            remember(answer?.sharing);
            if (action === "enable") setSaid({ text: "Starting. This window reopens at the shared address when it is ready." });
            if (action === "disable") setSaid({ text: "Sharing is off. Only " + noun + " can open Lemma." });
            void queryClient.invalidateQueries({ queryKey: ["this-mac"] });
        },
        onError: (problem) => setSaid({ text: friendlyError(problem), bad: true }),
    });

    if (snapshot.isPending) return <p className="kset-note" role="status">Reading this computer’s settings…</p>;
    if (snapshot.isError) {
        return (
            <p className="kset-said kset-said--bad" role="alert">
                {friendlyError(snapshot.error)}
                <button type="button" className="b b--s b--secondary" onClick={() => void snapshot.refetch()}>Try again</button>
            </p>
        );
    }
    const sharing: Sharing | null = snapshot.data.sharing;
    if (!sharing) {
        return <p className="kset-note">This installation runs no sharing gateway, so it can only be reached from {noun}.</p>;
    }

    const busy = act.isPending || sharingBusy(sharing);
    const stackReady = snapshot.data.state.ready && snapshot.data.state.running;
    const active = sharing.mode;
    const selected = choice ?? active;
    const readiness = sharing.provider_readiness[provider];
    const providerReady = Boolean(readiness?.installed && readiness?.authenticated);
    const tunnels = sharing.provider_readiness.cloudflare?.tunnels ?? [];
    const iface = lanInterface || sharing.preferences.selected_interface || sharing.selected_interface || "";
    const cfHost = hostname || sharing.preferences.cloudflare_hostname || "";

    const enable = (kind: "lan" | "public") => {
        setSaid(null);
        const built = kind === "lan"
            ? enablePayload({ kind: "lan", interface: iface })
            : enablePayload({
                kind: "public", provider, cloudflareSetup, hostname: cfHost, tunnelId,
                tunnelName: tunnels.find((one) => one.id === tunnelId)?.name ?? "",
            });
        if ("missing" in built) {
            setSaid({ text: built.missing, bad: true });
            return;
        }
        act.mutate({ action: "enable", payload: built.payload });
    };

    const setJoin = (whoCanJoin: WhoCanJoin) => {
        setSaid(null);
        act.mutate({ action: "access", payload: { who_can_join: whoCanJoin } });
    };

    return (
        <div className="kset-stack">
            <div className="kopts" role="radiogroup" aria-label="Who can reach Lemma">
                {MODES.map((mode) => (
                    <label key={mode} className="kopt kopt--compact">
                        <input
                            type="radio"
                            name="this-mac-sharing"
                            checked={selected === mode}
                            disabled={busy}
                            onChange={() => { setSaid(null); setChoice(mode); }}
                        />
                        <span className="kopt__t">{sharingModeName(mode, noun)}</span>
                        <span className="kopt__d">{sharingModeConsequence(mode, noun)}</span>
                        <span className="kopt__dot" aria-hidden="true" />
                    </label>
                ))}
            </div>

            {active !== "this_computer" && (
                <div className="klist">
                    <SettingRow
                        name={"Shared on the " + (active === "public" ? "internet" : "local network")}
                        consequence={sharing.canonical_url || "Getting the address…"}
                    >
                        {sharing.canonical_url && (
                            <button type="button" className="b b--s b--quiet" onClick={() => void copyText(sharing.canonical_url).then(
                                () => setSaid({ text: "Copied." }),
                                () => setSaid({ text: "Couldn’t copy. Select the address and copy it.", bad: true }),
                            )}><CopyIcon /> Copy</button>
                        )}
                        <button type="button" className="b b--s b--secondary" disabled={busy} onClick={() => { setSaid(null); act.mutate({ action: "disable" }); }}>
                            Stop sharing
                        </button>
                    </SettingRow>
                </div>
            )}
            {active === "local_network" && sharing.qr_svg && (
                /* An <img> and never markup: the SVG arrives from the daemon,
                   and an image cannot run script. */
                <img
                    className="kset-qr"
                    src={"data:image/svg+xml;charset=utf-8," + encodeURIComponent(sharing.qr_svg)}
                    alt="QR code for the shared address"
                />
            )}
            {sharing.last_error && <p className="kset-said kset-said--bad" role="alert">{sharing.last_error}</p>}
            {/* The daemon's own cautions about the address in use -- said,
                not parsed and dropped. */}
            {active !== "this_computer" && sharing.warnings.map((warning) => (
                <p className="kset-said" key={warning}><WarningIcon size={12} /> {warning}</p>
            ))}
            {busy && active === "this_computer" && !act.isPending && (
                <p className="kset-note" role="status">{sharingPhaseWords(sharing.phase)}…</p>
            )}

            {selected === "local_network" && active !== "local_network" && (
                <>
                    <div className="klist">
                        <SettingRow name="Network" consequence="Lemma listens only on the interface you choose, over plain HTTP.">
                            <select className="kinput kset-input" aria-label="Network interface" value={iface} onChange={(event) => setLanInterface(event.target.value)}>
                                <option value="">Choose…</option>
                                {sharing.interfaces.map((item) => <option key={item.address} value={item.address}>{item.label}</option>)}
                            </select>
                        </SettingRow>
                    </div>
                    <div className="kset-acts">
                        <button type="button" className="b b--m b--primary" disabled={busy || !stackReady} title={!stackReady ? NOT_READY : undefined} onClick={() => enable("lan")}>Share on this network</button>
                    </div>
                </>
            )}

            {selected === "public" && active !== "public" && (
                <>
                    <div className="kset-range" role="group" aria-label="Tunnel">
                        {(["ngrok", "cloudflare"] as TunnelProvider[]).map((one) => (
                            <button key={one} type="button" className="kchip" aria-pressed={provider === one}
                                onClick={() => { setProvider(one); act.mutate({ action: "preflight", payload: { provider: one } }); }}>
                                {one === "ngrok" ? "ngrok" : "Cloudflare"}
                            </button>
                        ))}
                    </div>
                    <div className="klist">
                        <SettingRow
                            name={providerReady ? (provider === "ngrok" ? "ngrok" : "cloudflared") + " is ready" : (provider === "ngrok" ? "ngrok" : "cloudflared") + (readiness?.installed ? " needs signing in" : " isn’t installed")}
                            consequence={readiness?.message || (providerReady ? "Lemma uses your own account and configuration." : "Finish in Terminal, then come back.")}
                        />
                    </div>
                    {!providerReady && setupCommands(readiness).map((step, index) => (
                        <div className="kset-command" key={index}>
                            {step.command ? <code>{step.command}</code> : <span>{step.text}</span>}
                            {step.command && <button type="button" className="b b--s b--quiet" onClick={() => void copyText(step.command!)}><CopyIcon /> Copy</button>}
                        </div>
                    ))}
                    {provider === "cloudflare" && (
                        <div className="kset-form">
                            <div className="kfield">
                                <label htmlFor="cf-host">Public hostname</label>
                                <input id="cf-host" className="kinput" value={cfHost} placeholder="lemma.example.com" onChange={(event) => setHostname(event.target.value)} />
                            </div>
                            <label className="kset-check">
                                <input type="checkbox" checked={cloudflareSetup === "existing"}
                                    onChange={(event) => setCloudflareSetup(event.target.checked ? "existing" : "automatic")} />
                                <span>Use a named tunnel I already have
                                    <em>Otherwise Lemma creates one for this computer and a DNS route, and reuses them.</em></span>
                            </label>
                            {cloudflareSetup === "existing" && (
                                <div className="kfield">
                                    <label htmlFor="cf-tunnel">Named tunnel</label>
                                    <select id="cf-tunnel" className="kinput" value={tunnelId} onChange={(event) => setTunnelId(event.target.value)}>
                                        <option value="">Choose…</option>
                                        {tunnels.map((tunnel) => <option key={tunnel.id} value={tunnel.id}>{tunnel.name}</option>)}
                                    </select>
                                </div>
                            )}
                        </div>
                    )}
                    <p className="kset-note">{sharing.apps_limitation}</p>
                    <div className="kset-acts">
                        <button type="button" className="b b--m b--primary" disabled={busy || !stackReady || !providerReady} title={!stackReady ? NOT_READY : undefined} onClick={() => enable("public")}>
                            Create public link…
                        </button>
                    </div>
                </>
            )}
            {selected !== active && selected !== "this_computer" && (
                <p className="kset-note">
                    {!stackReady ? "Lemma has to be running before it can be shared. " : ""}
                    Sharing moves this window to the shared address, where these settings open from the menu bar.
                    Lemma restarts its server to do it, so anything an agent is running right now stops, and
                    open calls end.
                </p>
            )}

            <p className="ksub">Who can join</p>
            <div className="klist">
                <SettingRow name={sharing.who_can_join === "open" ? "Open" : "Invite-only"} consequence={joinPolicyCopy(sharing.who_can_join, active)}>
                    <div className="kset-range" role="radiogroup" aria-label="Who can join">
                        {(["invite_only", "open"] as WhoCanJoin[]).map((one) => (
                            <button key={one} type="button" role="radio" className="kchip"
                                aria-checked={sharing.who_can_join === one} aria-pressed={sharing.who_can_join === one}
                                disabled={busy || sharing.who_can_join === one}
                                onClick={() => setJoin(one)}>
                                {one === "open" ? "Open" : "Invite-only"}
                            </button>
                        ))}
                    </div>
                </SettingRow>
                {sharing.who_can_join === "invite_only" && (
                    <SettingRow name="Invite people" consequence="An invitation is what lets someone make an account here.">
                        <button type="button" className="b b--s b--secondary" onClick={() => openSettings("people")}><PeopleIcon /> Invite</button>
                    </SettingRow>
                )}
            </div>

            {said && <p className={"kset-said" + (said.bad ? " kset-said--bad" : "")} role={said.bad ? "alert" : "status"}>{said.text}</p>}
        </div>
    );
}
