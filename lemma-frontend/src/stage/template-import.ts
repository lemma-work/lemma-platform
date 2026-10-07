/** Bringing a role's template into a teammate that has just been made.
 *
 *  A role on the shelf is a bundle the backend ships (`pod_bundle/templates/`),
 *  imported through the same pipeline a GitHub template or an uploaded bundle
 *  takes: start, wait for the plan, apply it, wait for the steps. The install
 *  page drives that pipeline with a person reviewing the plan; a hire does not
 *  ask, because the person already chose the role and a role's template asks
 *  for nothing — it carries no connected accounts, so every variable it could
 *  declare has a default.
 *
 *  Pulled out of the hiring page so the order and the stopping conditions can
 *  be tested without a server: the request function and the clock are passed
 *  in. */

export type TemplateImportStatus =
    | "QUEUED"
    | "FETCHING"
    | "PLANNING"
    | "AWAITING_CONFIRMATION"
    | "APPLYING"
    | "COMPLETED"
    | "FAILED"
    | "CANCELLED"
    | "PARTIALLY_CANCELLED";

export interface TemplateImportJob {
    import_id: string;
    pod_id: string;
    status: TemplateImportStatus;
    plan: { variables: { name: string; default: string | null }[] } | null;
    error: string | null;
}

export type Call = <T>(method: "GET" | "POST", path: string, body?: Record<string, unknown>) => Promise<T>;

/** Still moving on its own: poll again. */
const MOVING: TemplateImportStatus[] = ["QUEUED", "FETCHING", "PLANNING", "APPLYING"];

/** How often to look. The install page uses the same interval. */
export const POLL_MS = 1200;

/** Long enough for a template, which is a few tables, a few files and a
 *  handful of rows. A job past this is stuck rather than slow, and the hire
 *  should say so instead of spinning. */
export const GIVE_UP_MS = 120_000;

export class TemplateImportError extends Error {
    readonly job: TemplateImportJob | null;
    constructor(message: string, job: TemplateImportJob | null) {
        super(message);
        this.name = "TemplateImportError";
        this.job = job;
    }
}

export async function importTemplate({
    podId,
    template,
    call,
    sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    now = () => Date.now(),
    onStatus,
}: {
    podId: string;
    template: string;
    call: Call;
    sleep?: (ms: number) => Promise<void>;
    now?: () => number;
    onStatus?: (status: TemplateImportStatus) => void;
}): Promise<TemplateImportJob> {
    const base = "/pods/" + podId + "/bundle/imports";
    const started = now();

    const settle = async (job: TemplateImportJob): Promise<TemplateImportJob> => {
        let current = job;
        onStatus?.(current.status);
        while (MOVING.includes(current.status)) {
            if (now() - started > GIVE_UP_MS) {
                throw new TemplateImportError("Setting up the role is taking too long. Check About to see what arrived.", current);
            }
            await sleep(POLL_MS);
            current = await call<TemplateImportJob>("GET", base + "/" + current.import_id);
            onStatus?.(current.status);
        }
        return current;
    };

    let job = await settle(await call<TemplateImportJob>("POST", base, { kind: "TEMPLATE", template }));

    if (job.status === "AWAITING_CONFIRMATION") {
        const variables = Object.fromEntries((job.plan?.variables ?? []).map((one) => [one.name, one.default ?? ""]));
        job = await settle(await call<TemplateImportJob>("POST", base + "/" + job.import_id + "/apply", {
            variables,
            confirm_destructive: false,
        }));
    }

    if (job.status !== "COMPLETED") {
        throw new TemplateImportError(job.error || "The role’s setup stopped before it finished.", job);
    }
    return job;
}
