import type { LemmaClient } from "lemma-sdk";
import type { LiveContext } from "../../context";
import type { ModelsSource } from "./source";
import {
    readChoice, readComputer, readLocalAgent, readRuntime, type Computer, type LocalAgent, type Runtime,
} from "@web/data/runtimes";

type CreateProfile = Parameters<LemmaClient["agentRuntime"]["createProfile"]>[1];
type PodUpdate = Parameters<LemmaClient["pods"]["update"]>[1];

function itemsOf(value: unknown): unknown[] {
    if (Array.isArray(value)) return value;
    const items = (value as { items?: unknown } | null)?.items;
    return Array.isArray(items) ? items : [];
}

/** The same calls the web makes (`data/live.ts`: `listRuntimes`,
 *  `getRuntime`, `defaultRuntime`, `organizationDefault`,
 *  `setOrganizationDefault`, `testRuntime`, `addProviderKey`,
 *  `archiveRuntime`, `restoreRuntime`, `listComputers`, `getPodRuntime`,
 *  `setPodRuntime`), read the way the web reads them. */
export function modelsLive(context: LiveContext): ModelsSource {
    const runtime = () => context.client().agentRuntime;
    return {
        async runtimes(orgId) {
            /* Retired ones too: the list draws them behind "Show retired". */
            const listed = await runtime().listProfiles(orgId, { includeDisabled: true });
            return itemsOf(listed).map(readRuntime).filter((one): one is Runtime => one !== null);
        },
        async runtime(orgId, runtimeId) {
            return readRuntime(await runtime().getProfile(orgId, runtimeId));
        },
        async inherited(orgId) {
            const listed = await runtime().listRuntimes(orgId);
            return readChoice(listed.default_runtime);
        },
        async organizationDefault(orgId) {
            const listed = await runtime().listProfiles(orgId);
            return readChoice(listed.organization_default_runtime);
        },
        async setOrganizationDefault(orgId, choice) {
            if (!choice) {
                await runtime().clearOrganizationDefault(orgId);
                return;
            }
            await runtime().setOrganizationDefault(orgId, { profile_id: choice.runtimeId, model_name: choice.model || null });
        },
        async test(orgId, runtimeId) {
            const answer = await runtime().testProfile(orgId, runtimeId);
            return { ok: answer.ok, message: answer.message, models: answer.models ?? null };
        },
        async addKey(orgId, key) {
            const shared = {
                name: key.name,
                api_key: key.apiKey,
                default_model_name: key.models[0] ?? null,
                model_names: key.models,
            };
            /* The kind is said out loud: the server tells the two apart by
               `source`, and refuses a body that leaves it out. */
            const payload: CreateProfile = key.protocol === "anthropic"
                ? { ...shared, source: "ANTHROPIC_COMPATIBLE", base_url: key.baseUrl || null }
                : { ...shared, source: "OPENAI_COMPATIBLE", base_url: key.baseUrl, vision_model_names: key.visionModels };
            await runtime().createProfile(orgId, payload);
        },
        async archive(orgId, runtimeId) {
            await runtime().archiveProfile(orgId, runtimeId);
        },
        async restore(orgId, runtimeId) {
            await runtime().restoreProfile(orgId, runtimeId);
        },
        async computers() {
            const listed = await context.client().agentHost.list();
            /* A removed computer can never take work again, so it has no
               place in a list of what can. */
            const computers = itemsOf(listed)
                .map(readComputer)
                .filter((one): one is Computer => one !== null && one.status !== "Removed");
            return Promise.all(computers.map(async (computer) => {
                try {
                    const harnesses = await context.client().agentHost.listHarnesses(computer.id);
                    return { ...computer, agents: itemsOf(harnesses).map(readLocalAgent).filter((agent): agent is LocalAgent => agent !== null) };
                } catch {
                    /* A computer whose agents cannot be read is still a computer. */
                    return computer;
                }
            }));
        },
        async lemmaChoice(podId) {
            const pod = (await context.client(podId).pods.get(podId)) as { config?: { default_runtime?: unknown; default_profile_id?: unknown } | null };
            const config = pod.config ?? {};
            /* The stored runtime carries the model; `default_profile_id` is a
               legacy mirror that never does, so it is only the fallback. */
            return readChoice(config.default_runtime) ?? readChoice({ profile_id: config.default_profile_id });
        },
        async setLemmaChoice(podId, choice) {
            /* Both keys when following the organization again: the backend
               re-syncs the legacy mirror only from a runtime it can read, so
               clearing one alone would leave the Lemma on the old pick.
               Selections go up only when there are some, since the runtime is
               replaced whole. */
            const selections = choice?.selections && Object.keys(choice.selections).length ? choice.selections : null;
            const config = choice
                ? { default_runtime: { profile_id: choice.runtimeId, model_name: choice.model || null, ...(selections ? { config_selections: selections } : {}) } }
                : { default_runtime: null, default_profile_id: null };
            await context.client(podId).pods.update(podId, { config } as PodUpdate);
        },
    };
}
