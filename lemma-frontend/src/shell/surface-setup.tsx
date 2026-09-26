import { useState } from "react";
import type { SurfaceSetupAction, SurfaceSetupActionField } from "lemma-sdk";
import { copyText } from "@/desktop/clipboard";

export function SetupField({ field }: { field: SurfaceSetupActionField }) {
    const [revealed, setRevealed] = useState(false);
    const [notice, setNotice] = useState("");
    return <div className="surface-setup__field">
        <span>{field.label}</span>
        <code>{field.secret && !revealed ? "••••••••" : field.value}</code>
        <div className="surface-setup__actions">
            {field.secret && <button className="btn" onClick={() => setRevealed(!revealed)}>{revealed ? "Hide" : "Reveal"} {field.label}</button>}
            <button className="btn" onClick={async () => {
                try { await copyText(field.value); setNotice("Copied"); }
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
