import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
    source,
    agentDefaultLabel,
    agentSettingsChanges,
    offeredSelections,
    optionsFor,
    saidAbout,
    type AgentOption,
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
 *  an option it is "As on <computer>". Both are real choices, not blanks.
 *
 *  The options follow the model, because the agent's do: a model can offer an
 *  effort another lacks. Changing the model drops whatever the new one does
 *  not offer, rather than leaving a value on the form no control shows and the
 *  backend would refuse. */
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
    const options = optionsOf(agent, settings.model);
    const choose = (key: string, value: string) =>
        onChange({ ...settings, selections: { ...settings.selections, [key]: value } });
    const pick = (model: string) =>
        onChange({ model, selections: offeredSelections(optionsOf(agent, model), settings.selections) });

    return (
        <>
            {agent.models.length > 0 && (
                <div className="field">
                    <label htmlFor="agent-model">Model</label>
                    <select
                        id="agent-model"
                        value={settings.model}
                        onChange={(event) => pick(event.target.value)}
                    >
                        <option value="">{agentDefaultLabel(agent.defaultModel)}</option>
                        {agent.models.map((one) => (
                            <option key={one.name} value={one.name}>{one.label}</option>
                        ))}
                    </select>
                </div>
            )}
            {options.map((option) => {
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

/** What the agent offers beside `model` — empty for its own default. */
function optionsOf(agent: LocalAgent, model: string): AgentOption[] {
    return optionsFor({ options: agent.options, byModel: agent.optionsByModel }, model);
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
    /* Only selections the agent still offers, for the model it still offers.
       A value it stopped publishing would be refused on save, with no
       control left to clear it. */
    const model = agent.models.some((one) => one.name === runtime.defaultModel) ? runtime.defaultModel : "";
    const initial: AgentSettings = { model, selections: offeredSelections(optionsOf(agent, model), runtime.selections) };
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
            {agent.models.length === 0 && optionsOf(agent, settings.model).length === 0 && (
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
