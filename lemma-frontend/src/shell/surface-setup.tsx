import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { SurfaceSetupAction, SurfaceSetupActionField } from "lemma-sdk";
import { source } from "@/data";

export function SetupField({ field }: { field: SurfaceSetupActionField }) {
    const [revealed, setRevealed] = useState(false);
    const [notice, setNotice] = useState("");
    return <div className="surface-setup__field">
        <span>{field.label}</span>
        <code>{field.secret && !revealed ? "••••••••" : field.value}</code>
        <div className="surface-setup__actions">
            {field.secret && <button className="btn" onClick={() => setRevealed(!revealed)}>{revealed ? "Hide" : "Reveal"} {field.label}</button>}
            <button className="btn" onClick={async () => {
                try { await navigator.clipboard.writeText(field.value); setNotice("Copied"); }
                catch { setNotice("Could not copy. Select and copy the value."); }
            }}>Copy {field.label}</button>
        </div>
        <small role="status">{notice}</small>
    </div>;
}

export function SetupActions({ actions }: { actions: SurfaceSetupAction[] }) {
    return <>{actions.map(action => <section className="surface-setup__section" key={action.key}>
        <h4>{action.title}</h4>
        <p>{action.description}</p>
        {action.steps?.length ? <ol>{action.steps.map((step, index) => <li key={index}>{step}</li>)}</ol> : null}
        {action.fields?.map((field, index) => <SetupField key={index} field={field} />)}
        {action.link && <a className="btn" href={action.link} target="_blank" rel="noreferrer">{action.link_label || "Open dashboard"}</a>}
    </section>)}</>;
}

export function SurfaceGuide({ podId, platform }: { podId: string; platform: string }) {
    const guide = useQuery({ queryKey: ["surface-guide", podId, platform], queryFn: () => source.surfaceGuide(podId, platform) });
    return <div className="surface-setup">
        {guide.isPending && <p role="status">Loading setup instructions…</p>}
        {guide.isError && <button className="btn" onClick={() => void guide.refetch()}>Retry setup instructions</button>}
        {guide.data && <details><summary>Setup instructions</summary>
            <p>{guide.data.summary}</p>
            {guide.data.connectors?.map((connector, index) => <section key={index}>
                <h4>{connector.title}</h4><p>{connector.summary}</p>
                <ol>{connector.steps?.map((step, at) => <li key={at}><b>{step.title}</b> — {step.description}</li>)}</ol>
                {connector.notes?.map((note, at) => <p key={at}>{note}</p>)}
            </section>)}
        </details>}
    </div>;
}
