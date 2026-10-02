import { isLandingPreview } from "@/marketing/preview-mode";

type Fixtures = typeof import("./fixtures");

/** What the views read: wire payloads, which every reader parses from
 *  `unknown` anyway, so a sample is free to carry its own fields. */
export type Samples = Omit<Fixtures, "SAMPLE_WAITING" | "SAMPLE_WORKFLOWS"> & {
    SAMPLE_WAITING: unknown[];
    SAMPLE_WORKFLOWS: unknown[];
};

/** The sample constants for this page: the sample teammates' own on the
 *  landing's demo, narrowed to the space being looked at when the caller
 *  knows it; the general fixtures everywhere else. Views that read their
 *  sample straight from the fixtures, rather than through `PodSource`, go
 *  through this. */
export async function samples(podId?: string): Promise<Samples> {
    if (!isLandingPreview()) return import("./fixtures");
    const preview = await import("@/marketing/preview-fixtures");
    return { ...preview, ...preview.forPod(podId) };
}
