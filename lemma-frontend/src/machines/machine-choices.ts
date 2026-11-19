/** The machines an agent can be set to run on, as a select offers them.
 *
 *  An agent names a pod machine by `pod:<name>` — relative to its own pod, so
 *  the setting survives the pod being copied — and names nothing at all to
 *  run on the default machine of whoever the run acts for. */

export interface PodMachine {
    name: string;
    display_name: string;
    kind: string;
    status: string;
}

export interface MachineChoice {
    value: string;
    label: string;
}

export const DEFAULT_MACHINE_LABEL = "Default machine (whoever the run acts for)";

export function machineChoices(machines: readonly PodMachine[] | undefined, current: string): MachineChoice[] {
    const choices: MachineChoice[] = [{ value: "", label: DEFAULT_MACHINE_LABEL }];
    for (const machine of machines ?? []) {
        if (machine.kind !== "pod" || machine.status === "deleted") continue;
        const label = machine.display_name && machine.display_name !== machine.name
            ? machine.display_name + " (" + machine.name + ")"
            : machine.name;
        choices.push({ value: "pod:" + machine.name, label });
    }
    /* A ref the list does not hold — a machine since deleted, or one set some
       other way — is still what the agent has, so it is shown as it is rather
       than the select quietly reading "default" over it. */
    if (current && !choices.some((choice) => choice.value === current)) {
        choices.push({ value: current, label: current + " (not found)" });
    }
    return choices;
}
