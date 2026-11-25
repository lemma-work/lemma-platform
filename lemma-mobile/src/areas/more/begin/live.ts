import type { LemmaClient } from "lemma-sdk";
import type { LiveContext } from "../../context";
import { modelsLive } from "../../admin/models/live";
import { modelSetupState } from "@web/shell/runs-on-state";
import { joinWire, readJoinRequest } from "@web/data/joining";
import { castIcon } from "./model";
import type { BeginSource } from "./source";

/** A new pod's config as the SDK types it (the web's `NewPodConfig`). */
type NewPodConfig = NonNullable<Parameters<LemmaClient["pods"]["create"]>[0]["config"]>;

/** The calls the web makes to start a Lemma and to ask to join one, read
 *  the way the web reads them (`outcomes/data.ts`). */
export function beginLive(context: LiveContext): BeginSource {
    const models = modelsLive(context);
    return {
        async start(request) {
            const purpose = request.purpose.trim();
            const pod = await context.client().pods.create({
                organization_id: request.orgId,
                name: request.name.trim(),
                ...(purpose ? { description: purpose } : {}),
                ...(castIcon(request.face) ? { icon_url: castIcon(request.face)! } : {}),
                config: { join_policy: joinWire(request.reach) as NewPodConfig["join_policy"] },
            });
            return { id: pod.id, name: pod.name?.trim() || request.name.trim(), standsIn: null };
        },

        async needsModel(orgId) {
            /* The same reading as the "runs on" picker: a new Lemma follows
               the organization's default, so it has no choice of its own. */
            const [runtimes, inherited] = await Promise.all([models.runtimes(orgId), models.inherited(orgId)]);
            return modelSetupState(runtimes.filter((one) => !one.archived), inherited, null) !== null;
        },

        async homeOf(podId) {
            try {
                const pod = (await context.client(podId).pods.get(podId)) as { organization_id?: string | null } | null;
                return pod?.organization_id ?? null;
            } catch {
                /* Refused or gone: it is not one this person can open. */
                return null;
            }
        },

        async myJoinRequest(podId) {
            return readJoinRequest(await context.client().podJoinRequests.me(podId));
        },

        async askToJoin(podId) {
            const request = readJoinRequest(await context.client().podJoinRequests.create(podId));
            /* The answer decides whether the person is now in, so one that
               cannot be read is a failure, not a quiet "waiting". */
            if (!request) throw new Error("The ask went through, but its answer could not be read. Look again to see where you stand.");
            return request;
        },
    };
}
