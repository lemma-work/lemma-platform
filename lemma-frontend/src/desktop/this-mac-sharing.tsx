"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CopyIcon, PeopleIcon } from "@/ui/icons";
import { copyText } from "./clipboard";
import { openSettings } from "./open-settings";
import { useThisComputer } from "./this-computer";
import {
    enablePayload, friendlyError, joinPolicyCopy, readSharing, setupCommands, sharingBusy,
    sharingModeConsequence, sharingModeName, thisMac,
    type Sharing, type SharingMode, type ThisMacSnapshot, type TunnelProvider, type WhoCanJoin,
} from "./this-mac";
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
 *  happens rather than after. */

const MODES: SharingMode[] = ["this_computer", "local_network", "public"];

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
                setSaid({ text: "Nothing changed. Lemma is still private to " + noun + "." });
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

    if (snapshot.isPending) return <p className="empty-row" role="status">Reading this computer’s settings…</p>;
    if (snapshot.isError) return <p className="thismac-said thismac-said--bad" role="alert">{friendlyError(snapshot.error)}</p>;
    const sharing: Sharing | null = snapshot.data.sharing;
    if (!sharing) {
        return <p className="empty-row">This installation runs no sharing gateway, so it can only be reached from {noun}.</p>;
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
        <div className="thismac">
            <div className="theme__modes" role="radiogroup" aria-label="Who can reach Lemma">
                {MODES.map((mode) => (
                    <button
                        key={mode}
                        type="button"
                        role="radio"
                        className="theme__mode"
                        aria-checked={selected === mode}
                        aria-pressed={selected === mode}
                        disabled={busy}
                        onClick={() => { setSaid(null); setChoice(mode); }}
                    >
                        {sharingModeName(mode, noun)}
                    </button>
                ))}
            </div>
            <p className="thismac-said">{sharingModeConsequence(selected, noun)}</p>

            {active !== "this_computer" && (
                <SettingRow
                    name={"Shared on the " + (active === "public" ? "internet" : "local network")}
                    consequence={sharing.canonical_url || "Getting the address…"}
                >
                    {sharing.canonical_url && (
                        <button className="linkish" onClick={() => void copyText(sharing.canonical_url).then(
                            () => setSaid({ text: "Copied." }),
                            () => setSaid({ text: "Couldn’t copy. Select the address and copy it.", bad: true }),
                        )}><CopyIcon size={13} /> Copy</button>
                    )}
                    <button className="btn" disabled={busy} onClick={() => { setSaid(null); act.mutate({ action: "disable" }); }}>
                        Stop sharing
                    </button>
                </SettingRow>
            )}
            {active === "local_network" && sharing.qr_svg && (
                /* An <img> and never markup: the SVG arrives from the daemon,
                   and an image cannot run script. */
                <img
                    className="thismac-qr"
                    src={"data:image/svg+xml;charset=utf-8," + encodeURIComponent(sharing.qr_svg)}
                    alt="QR code for the shared address"
                />
            )}
            {sharing.last_error && <p className="thismac-said thismac-said--bad" role="alert">{sharing.last_error}</p>}
            {busy && active === "this_computer" && !act.isPending && (
                <p className="thismac-said" role="status">{sharing.phase.replace(/_/g, " ")}…</p>
            )}

            {selected === "local_network" && active !== "local_network" && (
                <>
                    <SettingRow name="Network" consequence="Lemma listens only on the interface you choose, over plain HTTP.">
                        <select aria-label="Network interface" value={iface} onChange={(event) => setLanInterface(event.target.value)}>
                            <option value="">Choose…</option>
                            {sharing.interfaces.map((item) => <option key={item.address} value={item.address}>{item.label}</option>)}
                        </select>
                    </SettingRow>
                    <div className="modal__acts thismac-acts">
                        <button className="btn btn--primary" disabled={busy || !stackReady} onClick={() => enable("lan")}>Share on this network</button>
                    </div>
                </>
            )}

            {selected === "public" && active !== "public" && (
                <>
                    <div className="presets" role="group" aria-label="Tunnel">
                        {(["ngrok", "cloudflare"] as TunnelProvider[]).map((one) => (
                            <button key={one} className={"preset" + (provider === one ? " preset--on" : "")} aria-pressed={provider === one}
                                onClick={() => { setProvider(one); act.mutate({ action: "preflight", payload: { provider: one } }); }}>
                                {one === "ngrok" ? "ngrok" : "Cloudflare"}
                            </button>
                        ))}
                    </div>
                    <SettingRow
                        name={providerReady ? (provider === "ngrok" ? "ngrok" : "cloudflared") + " is ready" : (provider === "ngrok" ? "ngrok" : "cloudflared") + (readiness?.installed ? " needs signing in" : " isn’t installed")}
                        consequence={readiness?.message || (providerReady ? "Lemma uses your own account and configuration." : "Finish in Terminal, then come back.")}
                    />
                    {!providerReady && setupCommands(readiness).map((step, index) => (
                        <div className="thismac-command" key={index}>
                            {step.command ? <code>{step.command}</code> : <span>{step.text}</span>}
                            {step.command && <button className="linkish" onClick={() => void copyText(step.command!)}><CopyIcon size={13} /> Copy</button>}
                        </div>
                    ))}
                    {provider === "cloudflare" && (
                        <>
                            <div className="field">
                                <label htmlFor="cf-host">Public hostname</label>
                                <input id="cf-host" value={cfHost} placeholder="lemma.example.com" onChange={(event) => setHostname(event.target.value)} />
                            </div>
                            <label className="check">
                                <input type="checkbox" checked={cloudflareSetup === "existing"}
                                    onChange={(event) => setCloudflareSetup(event.target.checked ? "existing" : "automatic")} />
                                <span>Use a named tunnel I already have
                                    <em>Otherwise Lemma creates one for this computer and a DNS route, and reuses them.</em></span>
                            </label>
                            {cloudflareSetup === "existing" && (
                                <div className="field">
                                    <label htmlFor="cf-tunnel">Named tunnel</label>
                                    <select id="cf-tunnel" value={tunnelId} onChange={(event) => setTunnelId(event.target.value)}>
                                        <option value="">Choose…</option>
                                        {tunnels.map((tunnel) => <option key={tunnel.id} value={tunnel.id}>{tunnel.name}</option>)}
                                    </select>
                                </div>
                            )}
                        </>
                    )}
                    <p className="thismac-said">{sharing.apps_limitation}</p>
                    <div className="modal__acts thismac-acts">
                        <button className="btn btn--primary" disabled={busy || !stackReady || !providerReady} onClick={() => enable("public")}>
                            Create public link…
                        </button>
                    </div>
                </>
            )}
            {selected !== active && selected !== "this_computer" && (
                <p className="thismac-foot">
                    {!stackReady ? "Lemma has to be running before it can be shared. " : ""}
                    Sharing moves this window to the shared address, where these settings open from the menu bar.
                </p>
            )}

            <div className="thismac-group">
                <SettingRow name="Who can join" consequence={joinPolicyCopy(sharing.who_can_join, active)}>
                    <div className="theme__modes" role="radiogroup" aria-label="Who can join">
                        {(["invite_only", "open"] as WhoCanJoin[]).map((one) => (
                            <button key={one} type="button" role="radio" className="theme__mode"
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
                        <button className="btn" onClick={() => openSettings("people")}><PeopleIcon size={13} /> Invite</button>
                    </SettingRow>
                )}
            </div>

            {said && <p className={"thismac-said" + (said.bad ? " thismac-said--bad" : "")} role={said.bad ? "alert" : "status"}>{said.text}</p>}
        </div>
    );
}
