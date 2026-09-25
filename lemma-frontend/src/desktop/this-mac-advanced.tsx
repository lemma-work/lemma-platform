"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useThisComputer } from "./this-computer";
import {
    CREDENTIAL_FORMS, formConfigured, friendlyError, sectionPayloads, thisMac,
    type CredentialFormSpec, type Draft, type SecretIntent, type ThisMacSnapshot,
} from "./this-mac";
import { SettingRow, useThisMacSnapshot } from "./this-mac-settings";

/** The things a developer sets up once: the OAuth apps connectors sign in
 *  through and the bots channels answer as, then diagnostics and the
 *  install-health switch.
 *
 *  Each form is closed until asked for, and most people reach one from the
 *  connector or channel that needed it ("Set up on this Mac"), which opens
 *  this page with that form open. Secrets go to this computer's credential
 *  vault and are never read back: a stored one shows as "Saved", and
 *  replacing or removing it is explicit. */

/** Unsaved edits, kept for as long as this page is open.
 *
 *  Settings unmounts a section when you move to another one and the whole
 *  dialog when you close it, and a form's own state went with it: half a
 *  Slack setup, gone because you glanced at Sharing. Kept here instead, so a
 *  draft survives moving around, a health refresh and closing Settings, and
 *  is marked unsaved until it is saved. Memory only, never storage — some of
 *  it is credentials — so a reload is the one thing that forgets it. */
const drafts = new Map<string, { draft: Draft; secrets: Record<string, SecretIntent> }>();

function CredentialForm({ spec, snapshot, open }: { spec: CredentialFormSpec; snapshot: ThisMacSnapshot; open: boolean }) {
    const queryClient = useQueryClient();
    const config = snapshot.operator.config;
    const initial = (): Draft => drafts.get(spec.form)?.draft ?? Object.fromEntries(spec.fields.filter((field) => !field.secret).map((field) => {
        const holder = (field.key in config.integrations ? config.integrations : config.surfaces) as unknown as Record<string, string | boolean>;
        return [field.key, holder[field.key] ?? (field.kind === "toggle" ? false : "")];
    }));
    const [draft, setDraftState] = useState<Draft>(initial);
    const [secrets, setSecretsState] = useState<Record<string, SecretIntent>>(() => drafts.get(spec.form)?.secrets ?? {});
    const [unsaved, setUnsaved] = useState(() => drafts.has(spec.form));
    /* Written through to `drafts` from the handler, not from inside a state
       updater: an updater runs during render and may run twice. */
    const setDraft = (next: (was: Draft) => Draft) => {
        const value = next(draft);
        drafts.set(spec.form, { draft: value, secrets });
        setDraftState(value);
        setUnsaved(true);
    };
    const setSecrets = (next: (was: Record<string, SecretIntent>) => Record<string, SecretIntent>) => {
        const value = next(secrets);
        drafts.set(spec.form, { draft, secrets: value });
        setSecretsState(value);
        setUnsaved(true);
    };
    const [said, setSaid] = useState<{ text: string; bad?: boolean } | null>(null);
    const ref = useRef<HTMLDetailsElement>(null);

    useEffect(() => {
        if (open) ref.current?.scrollIntoView({ block: "start" });
    }, [open]);

    const save = useMutation({
        mutationFn: async () => {
            const payloads = sectionPayloads(snapshot, spec.form, draft, secrets);
            /* In order, and each against the revision the one before it left:
               the daemon refuses a stale revision rather than overwrite. */
            let revision = config.revision;
            for (const payload of payloads) {
                const operator = await thisMac.applySection({ ...payload, expected_revision: revision }) as { config?: { revision?: number } };
                revision = operator?.config?.revision ?? revision + 1;
            }
            return payloads.length;
        },
        onSuccess: (count) => {
            drafts.delete(spec.form);
            setUnsaved(false);
            setSecretsState({});
            setSaid({ text: count ? "Saved. Lemma restarted its server to use it." : "Nothing changed." });
            void queryClient.invalidateQueries({ queryKey: ["this-mac"] });
        },
        onError: (problem) => {
            setSaid({ text: friendlyError(problem), bad: true });
            /* Some sections may have saved before this one failed, moving the
               daemon's revision on. Refetch, so a retry is sent against the
               revision that is there now rather than refused as stale. The
               draft is kept: what did not save is still the person's. */
            void queryClient.invalidateQueries({ queryKey: ["this-mac"] });
        },
    });

    const configured = formConfigured(snapshot, spec.form);
    return (
        <details className="thismac-form" ref={ref} open={open || unsaved || undefined} id={"this-mac-" + spec.form}>
            <summary>
                <span className="thismac-row__text">
                    <span className="thismac-row__name">{spec.title}</span>
                    <span className="thismac-row__said">{spec.use}{spec.needsPublicLink ? " Needs a public link." : ""}</span>
                </span>
                <span className={"mrow__state mrow__state--" + (unsaved ? "warn" : configured ? "ok" : "muted")}>
                    <i aria-hidden="true" />{unsaved ? "Unsaved changes" : configured ? "Set up" : "Not set up"}
                </span>
            </summary>
            <form className="thismac-form__body" onSubmit={(event) => { event.preventDefault(); setSaid(null); save.mutate(); }}>
                <fieldset disabled={save.isPending}>
                    {spec.fields.map((field) => {
                        const id = "this-mac-" + spec.form + "-" + field.key.replace(/\W/g, "-");
                        if (field.kind === "toggle") {
                            return (
                                <label className="check" key={field.key}>
                                    <input type="checkbox" checked={draft[field.key] === true}
                                        onChange={(event) => setDraft((was) => ({ ...was, [field.key]: event.target.checked }))} />
                                    <span>{field.label}</span>
                                </label>
                            );
                        }
                        if (field.secret) {
                            const stored = snapshot.operator.secrets[field.key] === true;
                            const intent = secrets[field.key];
                            const removing = intent?.action === "remove";
                            return (
                                <div className="field" key={field.key}>
                                    <label htmlFor={id}>{field.label}</label>
                                    <input
                                        id={id}
                                        type="password"
                                        autoComplete="new-password"
                                        disabled={removing}
                                        placeholder={removing ? "Will be removed" : stored ? "Saved — type to replace" : ""}
                                        value={intent?.action === "replace" ? intent.value : ""}
                                        onChange={(event) => setSecrets((was) => ({ ...was, [field.key]: { action: "replace", value: event.target.value } }))}
                                    />
                                    {stored && (
                                        <button type="button" className="linkish thismac-form__remove"
                                            onClick={() => setSecrets((was) => ({ ...was, [field.key]: removing ? { action: "keep" } : { action: "remove" } }))}>
                                            {removing ? "Keep it" : "Remove"}
                                        </button>
                                    )}
                                </div>
                            );
                        }
                        return (
                            <div className="field" key={field.key}>
                                <label htmlFor={id}>{field.label}</label>
                                <input id={id} value={String(draft[field.key] ?? "")}
                                    onChange={(event) => setDraft((was) => ({ ...was, [field.key]: event.target.value }))} />
                            </div>
                        );
                    })}
                    {(spec.form === "google" || spec.form === "github" || spec.form === "microsoft") && snapshot.state.api_url && (
                        <p className="thismac-said">
                            Redirect URL for the app: <code>{snapshot.state.api_url.replace(/\/$/, "")}/api/v1/connectors/oauth/callback</code>
                        </p>
                    )}
                    <div className="modal__acts">
                        {unsaved && (
                            <button type="button" className="linkish" onClick={() => {
                                drafts.delete(spec.form);
                                setUnsaved(false);
                                setSecretsState({});
                                setDraftState(initial());
                                setSaid(null);
                            }}>Discard changes</button>
                        )}
                        <button className="btn btn--primary" type="submit">{save.isPending ? "Saving…" : "Save"}</button>
                    </div>
                </fieldset>
                {said && <p className={"thismac-said" + (said.bad ? " thismac-said--bad" : "")} role={said.bad ? "alert" : "status"}>{said.text}</p>}
            </form>
        </details>
    );
}

/* ── diagnostics ───────────────────────────────────────────────────── */

const LOG_POLL_MS = 2_000;

function Logs() {
    const [source, setSource] = useState<string | null>(null);
    const [body, setBody] = useState("");
    const [sources, setSources] = useState<{ id: string; label: string }[]>([]);
    const [problem, setProblem] = useState<string | null>(null);
    const cursor = useRef<string | null>(null);

    useEffect(() => {
        let stopped = false;
        cursor.current = null;
        setBody("");
        const read = async () => {
            try {
                const answer = await thisMac.diagnosticLogs(source, cursor.current);
                if (stopped) return;
                const entries = answer?.entries ?? "";
                /* The shell says "No …" rather than nothing when a log is
                   empty; appended, that sentence would repeat every tick. */
                setBody((was) => (cursor.current && entries.startsWith("No ") ? was : cursor.current ? was + entries : entries));
                cursor.current = answer?.nextCursor ?? null;
                if (answer?.sources) setSources(answer.sources);
                setProblem(null);
            } catch (cause) {
                if (!stopped) setProblem(friendlyError(cause));
            }
        };
        void read();
        const timer = window.setInterval(() => void read(), LOG_POLL_MS);
        return () => { stopped = true; window.clearInterval(timer); };
    }, [source]);

    return (
        <div className="thismac-logs">
            <div className="theme__modes" role="tablist" aria-label="Log">
                {sources.map((one) => (
                    <button key={one.id} type="button" role="tab" className="theme__mode"
                        aria-selected={(source ?? sources[0]?.id) === one.id} aria-pressed={(source ?? sources[0]?.id) === one.id}
                        onClick={() => setSource(one.id)}>{one.label}</button>
                ))}
            </div>
            {/* Text, never markup: these lines carry error strings, URLs and
                tool output the app did not write. Redacted by the shell. */}
            <pre className="thismac-log" tabIndex={0}>{problem ?? (body.trim() || "No entries yet.")}</pre>
        </div>
    );
}

function Telemetry() {
    const status = useQuery({ queryKey: ["this-mac-telemetry"], queryFn: () => thisMac.telemetryStatus(), retry: 0 });
    const queryClient = useQueryClient();
    const change = useMutation({
        mutationFn: (enabled: boolean) => thisMac.setTelemetry(enabled),
        onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["this-mac-telemetry"] }),
    });
    /* A build that cannot send anything offers nothing, rather than a switch
       that does not do anything. */
    if (!status.data?.available) return null;
    return (
        <SettingRow
            name="Anonymous install health"
            consequence={`Whether Lemma started and its runtime installed, sent to ${status.data.host ?? "Lemma"} under a random id for this installation. Nothing about your pods, files or account.`}
        >
            <input type="checkbox" role="switch" className="thismac-switch" aria-label="Send anonymous install health"
                checked={Boolean(status.data.enabled)} disabled={change.isPending}
                onChange={(event) => change.mutate(event.target.checked)} />
            {change.isError && <span className="thismac-said thismac-said--bad" role="alert">{friendlyError(change.error)}</span>}
        </SettingRow>
    );
}

export function ThisMacAdvanced({ focus }: { focus: string | null }) {
    const noun = useThisComputer();
    const snapshot = useThisMacSnapshot();
    const [showLogs, setShowLogs] = useState(false);
    if (snapshot.isPending) return <p className="empty-row" role="status">Reading this computer’s settings…</p>;
    if (snapshot.isError) return <p className="thismac-said thismac-said--bad" role="alert">{friendlyError(snapshot.error)}</p>;
    const data = snapshot.data;
    return (
        <div className="thismac">
            <h4 className="thismac-heading">Developer credentials</h4>
            <p className="thismac-said">
                OAuth apps and bots you created yourself, for connectors and channels on {noun}. Kept in {noun}’s credential vault.
            </p>
            {CREDENTIAL_FORMS.map((spec) => (
                <CredentialForm key={spec.form} spec={spec} snapshot={data} open={focus === spec.form} />
            ))}

            <h4 className="thismac-heading">Diagnostics</h4>
            {data.paths && (
                <SettingRow name="Where Lemma keeps its state" consequence={data.paths.locald}>
                    <button className="linkish" onClick={() => void thisMac.openLogs()}>Open logs folder</button>
                </SettingRow>
            )}
            {data.state.url && (
                <SettingRow name="Addresses" consequence={`Workspace ${data.state.url} · API ${data.state.api_url}`} />
            )}
            <SettingRow name="Logs" consequence="The last of each log, redacted, refreshed while open.">
                <button className="btn" aria-expanded={showLogs} onClick={() => setShowLogs((was) => !was)}>{showLogs ? "Hide" : "Show"}</button>
            </SettingRow>
            {showLogs && <Logs />}
            <Telemetry />
        </div>
    );
}
