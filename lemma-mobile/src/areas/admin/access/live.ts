import { readSiteConfig } from "../../../session/flags";
import type { LiveContext } from "../../context";
import type { Member } from "@web/org/membership";
import type { AccessSource } from "./source";

export function accessLive(context: LiveContext): AccessSource {
    return {
        async podActions(podId) {
            const answer = await context.client(podId).podPermissions.me(podId);
            return (answer as { actions?: string[] }).actions ?? [];
        },
        async orgMembers(orgId) {
            const listed = await context.client().organizations.members.list(orgId, { limit: 100 });
            return ((listed as { items?: unknown[] }).items ?? []) as Member[];
        },
        async deployment() {
            const response = await fetch(`${context.server.web}/site-config.js`);
            if (!response.ok) return null;
            return readSiteConfig(await response.text())?.deployment ?? null;
        },
    };
}
