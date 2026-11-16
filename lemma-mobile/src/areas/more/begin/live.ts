import type { ImportStatusResponse, OutcomeTemplateCard, StartLemmaRequest } from "lemma-sdk";
import { OutcomeKind } from "lemma-sdk";
import type { LiveContext } from "../../context";
import { modelsLive } from "../../admin/models/live";
import { modelSetupState } from "@web/shell/runs-on-state";
import { readJoinRequest } from "@web/data/joining";
import type { ShelfCard } from "@web/outcomes/model";
import type { BeginSource } from "./source";

/** A template as the shelf reads it (the web's `shelfCardOf`). */
export function shelfCardOf(card: OutcomeTemplateCard): ShelfCard {
    return {
        id: card.id,
        name: card.name,
        sentence: card.sentence,
        kind: card.kind === "date" ? "date" : "week",
        work: card.function ?? null,
        face: card.face ?? null,
        place: card.place ?? null,
        brings: card.brings ?? [],
        never: card.plan?.never ?? [],
    };
}

/** The calls the web makes to start a Lemma and to ask to join one, read
 *  the way the web reads them (`outcomes/data.ts`). */
export function beginLive(context: LiveContext): BeginSource {
    const models = modelsLive(context);
    return {
        async templates() {
            return (await context.client().pods.templates()).map(shelfCardOf);
        },

        async start(request) {
            const payload: StartLemmaRequest = "template" in request
                ? { organization_id: request.orgId, template: request.template, due: request.due }
                : {
                    organization_id: request.orgId,
                    name: request.name,
                    outcome: { sentence: request.sentence, kind: request.kind === "date" ? OutcomeKind.DATE : OutcomeKind.WEEK, due: request.kind === "date" ? request.due : null },
                };
            const started = await context.client().pods.start(payload);
            return {
                id: started.pod.id,
                name: started.pod.name?.trim() || ("name" in request ? request.name : "Your Lemma"),
                conversationId: started.conversation_id,
                importId: started.import_id ?? null,
                standsIn: null,
            };
        },

        async setFace(podId, icon) {
            await context.client(podId).pods.update(podId, { icon_url: icon });
        },

        async setup(podId, importId) {
            const status = await context.client(podId).request<ImportStatusResponse>("GET", `/pods/${encodeURIComponent(podId)}/bundle/imports/${encodeURIComponent(importId)}`);
            return {
                status: String(status.status),
                steps: (status.plan?.steps ?? []).map((step) => ({ kind: String(step.kind), name: String(step.name) })),
                error: status.error ?? null,
            };
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
