/**
 * The pod-name rule, mirrored from the backend so the field can answer before
 * the request goes out.
 *
 * `normalize_pod_name` in `lemma-backend/app/modules/pod/domain/pod_names.py`
 * is the authority; this is a copy, kept deliberately identical down to the
 * wording, because a rename that comes back 400 has already cost a round trip
 * to say something the field could have said as it was typed. Nothing here
 * decides anything — the server validates every name it stores, and a name this
 * accepts can still come back a conflict.
 */

export const POD_NAME_MAX_LENGTH = 255;

/** Alphanumeric at both ends; spaces, hyphens and underscores only inside. */
const POD_NAME_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9 _-]*[A-Za-z0-9])?$/;

/** What the server stores for this input. Trimming is the whole normalisation. */
export function normalizePodName(value: string): string {
    return value.trim();
}

/** Why this name cannot be used, in the server's own words — null when it can. */
export function podNameError(value: string): string | null {
    const normalized = normalizePodName(value);

    if (!normalized) {
        return 'Pod name cannot be empty';
    }
    if (normalized.length > POD_NAME_MAX_LENGTH) {
        return `Pod name must be ${POD_NAME_MAX_LENGTH} characters or fewer`;
    }
    if (!POD_NAME_PATTERN.test(normalized)) {
        return 'Pod name may contain only letters, numbers, spaces, hyphens, and underscores';
    }
    return null;
}
