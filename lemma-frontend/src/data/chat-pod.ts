/** Which pod answers the signed-in person on a shared chat number.
 *
 *  WhatsApp has one Lemma number for everybody, so the person, not the pod,
 *  decides which of their workspaces it talks to. The choice is the same
 *  default-surface preference the router already reads; this module only reads
 *  `GET /surfaces/me` into something a picker can show.
 */

export interface ChatPodOption {
    podId: string;
    /** What the picker shows: the pod's name, or "Pod · Org" when two of the
     *  person's pods share a name and the name alone would not tell them apart. */
    label: string;
}

export interface ChatPodChoice {
    platform: string;
    /** The pod answering now, when the server could say. */
    currentPodId: string | null;
    options: ChatPodOption[];
}

interface RawPod { pod_id?: unknown; name?: unknown; organization_name?: unknown }
interface RawSurface { id?: unknown; pod_id?: unknown }
interface RawGroup {
    platform?: unknown;
    surfaces?: RawSurface[] | null;
    default_surface_id?: unknown;
    default_pod_id?: unknown;
    available_pods?: RawPod[] | null;
}

function text(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

/** Names that need their organization beside them: any name two pods share. */
export function chatPodLabels(pods: { podId: string; name: string; organizationName: string }[]): ChatPodOption[] {
    const counts = new Map<string, number>();
    for (const pod of pods) counts.set(pod.name.toLocaleLowerCase(), (counts.get(pod.name.toLocaleLowerCase()) ?? 0) + 1);
    return pods.map((pod) => ({
        podId: pod.podId,
        label: (counts.get(pod.name.toLocaleLowerCase()) ?? 0) > 1 && pod.organizationName
            ? pod.name + " · " + pod.organizationName
            : pod.name,
    }));
}

/** The choice for one platform, or null when there is nothing to choose —
 *  the platform is not on a shared number, or the person has no pods. */
export function readChatPodChoice(answer: unknown, platform: string): ChatPodChoice | null {
    const groups = (answer as { groups?: RawGroup[] | null } | null)?.groups ?? [];
    const group = groups.find((entry) => text(entry.platform).toUpperCase() === platform.toUpperCase());
    if (!group) return null;
    const pods = (group.available_pods ?? [])
        .map((pod) => ({ podId: text(pod.pod_id), name: text(pod.name), organizationName: text(pod.organization_name) }))
        .filter((pod) => pod.podId && pod.name);
    if (!pods.length) return null;

    /* The default pod when the server names it; otherwise the pod behind the
       default surface; otherwise, if exactly one surface could answer, that
       one — it is what the router would pick. */
    const surfaces = group.surfaces ?? [];
    const bySurface = surfaces.find((surface) => text(surface.id) && text(surface.id) === text(group.default_surface_id));
    const named = text(group.default_pod_id) || text(bySurface?.pod_id) || (surfaces.length === 1 ? text(surfaces[0].pod_id) : "");
    const currentPodId = named && pods.some((pod) => pod.podId === named) ? named : null;

    return {
        platform: platform.toUpperCase(),
        currentPodId,
        options: chatPodLabels(pods).sort((a, b) => a.label.localeCompare(b.label)),
    };
}
