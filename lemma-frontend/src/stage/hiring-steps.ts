/** Making a teammate: the pod, then its face.
 *
 *  Pulled out of the hiring page so the one rule that matters can be tested:
 *  a pod is created at most once per hire. The face used to be a second
 *  awaited call inside the same `try`, so when it failed the page went back
 *  to the Hire button with the pod already made — and pressing it again made
 *  another one, under the same name. Now the pod a first attempt created is
 *  handed back in and reused, and the face is not allowed to fail the hire at
 *  all: a teammate with the wrong face is a teammate, and one that exists
 *  twice is a mess somebody has to find and delete. */

export interface MadePod {
    id: string;
}

export interface Making<P extends MadePod> {
    pod: P;
    /** The face variant that was stored, or 0 when none was — so the reveal
     *  draws the face the teammate keeps, not the one that failed to save. */
    variant: number;
    /** False when the face could not be saved and the default was kept. */
    faceSaved: boolean;
}

export async function makeTeammate<P extends MadePod>({
    existing,
    create,
    variantFor,
    saveFace,
    onCreated,
}: {
    /** The pod an earlier attempt at this same hire already created. */
    existing: P | null;
    create: () => Promise<P>;
    /** The variant this pod's id lands on for the chosen character. */
    variantFor: (podId: string) => number;
    saveFace: (podId: string, variant: number) => Promise<unknown>;
    /** Told the moment the pod exists, before anything else can fail, so a
     *  retry has it to reuse. */
    onCreated?: (pod: P) => void;
}): Promise<Making<P>> {
    const pod = existing ?? (await create());
    if (!existing) onCreated?.(pod);
    const variant = variantFor(pod.id);
    if (variant === 0) return { pod, variant: 0, faceSaved: true };
    try {
        await saveFace(pod.id, variant);
        return { pod, variant, faceSaved: true };
    } catch {
        /* Deliberately not rethrown — see the file comment. The caller says
           so on the reveal, which is the trace this leaves. */
        return { pod, variant: 0, faceSaved: false };
    }
}
