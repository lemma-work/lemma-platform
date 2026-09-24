import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
    source,
    agentDefaultLabel,
    agentSettingsChanges,
    saidAbout,
    type AgentSettings,
    type Computer,
    type LocalAgent,
    type Runtime,
} from "@/data";
import { Modal } from "@/shell/modal";

/** A coding agent's model and options, as a form.
 *
 *  Generic on purpose: which options exist is the agent's to say, and the
 *  four agents Lemma drives name the same idea four ways. What is fixed is
 *  the empty answer. For the model it is "Agent default" — dispatch sends no
 *  model, and the agent runs whatever it is set to on that computer — and for
 *  an option it is "As on <computer>". Both are real choices, not blanks. */
export function AgentSettingsFields({
    agent,
    computer,
    settings,
    onChange,
}: {
    agent: LocalAgent;
    computer: Computer;
    settings: AgentSettings;
    onChange: (next: AgentSettings) => void;
}) {
    const choose = (key: string, value: string) =>
        onChange({ ...settings, selections: { ...settings.selections, [key]: value } });

    return (
        <>
            {agent.models.length > 0 && (
                <div className="field">
                    <label htmlFor="agent-model">Model</label>
                    <select
                        id="agent-model"
                        value={settings.model}
                        onChange={(event) => onChange({ ...settings, model: event.target.value })}
                    >
                        <option value="">{agentDefaultLabel(agent.defaultModel)}</option>
                        {agent.models.map((one) => (
                            <option key={one.name} value={one.name}>{one.label}</option>
                        ))}
                    </select>
                </div>
            )}
            {agent.options.map((option) => {
                const now = option.choices.find((choice) => choice.value === option.current)?.label;
                return (
                    <div className="field" key={option.key}>
                        <label htmlFor={"agent-option-" + option.key}>{option.label}</label>
                        <select
                            id={"agent-option-" + option.key}
                            value={settings.selections[option.key] ?? ""}
                            onChange={(event) => choose(option.key, event.target.value)}
                        >
                            <option value="">{"As on " + computer.name + (now ? " (" + now + ")" : "")}</option>
                            {option.choices.map((choice) => (
                                <option key={choice.value} value={choice.value}>{choice.label}</option>
                            ))}
                        </select>
                        {option.description && <span>{option.description}</span>}
                    </div>
                );
            })}
        </>
    );
}

/** Keep only selections the agent still offers. A value it stopped
 *  publishing would be refused on save, with no control left to clear it. */
function offered(agent: LocalAgent, selections: Record<string, string>): Record<string, string> {
    return Object.fromEntries(
        Object.entries(selections).filter(([key, value]) =>
            agent.options.some((option) => option.key === key && option.choices.some((choice) => choice.value === value)),
        ),
    );
}

/** Change what an added agent runs with. Offered only while its computer is
 *  awake: the backend checks every change against the live agent. */
export function EditAgentSettings({
    agent,
    computer,
    runtime,
    orgId,
    onClose,
    onSaved,
}: {
    agent: LocalAgent;
    computer: Computer;
    runtime: Runtime;
    orgId: string;
    onClose: () => void;
    onSaved: () => void;
}) {
    const initial: AgentSettings = {
        model: agent.models.some((one) => one.name === runtime.defaultModel) ? runtime.defaultModel : "",
        selections: offered(agent, runtime.selections),
    };
    const [settings, setSettings] = useState(initial);
    /* Compared with what is stored rather than with the cleaned-up form, so a
       selection the agent dropped is cleared by saving. */
    const changes = agentSettingsChanges({ model: runtime.defaultModel, selections: runtime.selections }, settings);
    const dirty = Object.keys(changes).length > 0;

    const save = useMutation({
        mutationFn: () => source.updateLocalAgent(orgId, runtime.id, changes),
        onSuccess: () => { onSaved(); onClose(); },
    });

    return (
        <Modal title={runtime.name} subtitle={"on " + computer.name} narrow onClose={onClose}>
            <AgentSettingsFields agent={agent} computer={computer} settings={settings} onChange={setSettings} />
            {agent.models.length === 0 && agent.options.length === 0 && (
                <p className="empty-row">This agent publishes nothing to choose.</p>
            )}
            {save.isError && <p className="reachrow__error" role="alert">{saidAbout(save.error, "That could not be saved.")}</p>}
            <div className="modal__acts">
                <button className="linkish" onClick={onClose}>Cancel</button>
                <button className="btn btn--primary" disabled={save.isPending || !dirty} onClick={() => save.mutate()}>
                    {save.isPending ? "Saving…" : "Save"}
                </button>
            </div>
        </Modal>
    );
}
