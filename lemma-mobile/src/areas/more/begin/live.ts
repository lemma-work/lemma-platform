import type { LiveContext } from "../../context";
import { modelsLive } from "../../admin/models/live";
import { modelSetupState } from "@web/shell/runs-on-state";
import { readJoinRequest } from "@web/data/joining";
import { filesLive } from "../../files/live";
import { PLAN_PATH } from "../../work/web/plan";
import type { BeginSource } from "./source";

/** The calls the web makes to start a Lemma and to ask to join one, read
 *  the way the web reads them. The plan is written as the Work area writes
 *  one that does not exist yet (`work/live.ts` `writePlan`); the setup chat
 *  is started as the Work area starts one with an agent (`talkTo`): made,
 *  then the words appended, so its run is going before the chat opens. */
export function beginLive(context: LiveContext): BeginSource {
    const files = filesLive(context);
    const models = modelsLive(context);
    return {
        async create(orgId, name, about) {
            const pod = await context.client().pods.create({
                name,
                organization_id: orgId,
                ...(about.trim() ? { description: about.trim() } : {}),
            });
            if (!pod?.id) throw new Error("The Lemma was made but came back without an id.");
            return { id: pod.id, name: pod.name?.trim() || name, standsIn: null };
        },

        async setFace(podId, icon) {
            await context.client(podId).pods.update(podId, { icon_url: icon });
        },

        async writePlan(podId, text) {
            await files.createText(podId, PLAN_PATH, text);
        },

        async askToSetUp(podId, words) {
            const client = context.client(podId);
            const made = await client.conversations.create({ pod_id: podId });
            await client.conversations.appendMessage(made.id, { content: words }, { pod_id: podId });
            return made.id;
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
