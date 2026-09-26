/** How much of this Mac's disk Lemma occupies, as This Mac → Overview shows it.
 *
 *  Pure: the shell's figures in, sentences out. The row itself is
 *  `disk-usage.tsx`; `tests/disk-space.test.ts` drives these. Figures are
 *  allocated bytes — what the files actually take on disk — never their
 *  length: the data disk is a sparse 24 GB file whatever it holds. */

export interface UpdateBackup {
    /** Blocks the file has, most of them shared with the live data disk. */
    allocated_bytes: number;
    /** What deleting it frees now, when the volume can say. */
    reclaimable_bytes: number | null;
    /** True when only `allocated_bytes` is known, which is then "up to". */
    size_is_upper_bound: boolean;
    /** When Lemma deletes it on its own at the latest, in Unix seconds. */
    expires_at_unix: number | null;
}

export interface DiskUsage {
    data_disk: { allocated_bytes: number } | null;
    update_backup: UpdateBackup | null;
    runtime_releases: { allocated_bytes: number; count: number; kept: number } | null;
}

const record = (value: unknown): Record<string, unknown> =>
    value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
const count = (value: unknown): number | null =>
    typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;

/** Narrow the shell's `disk_usage`. Absent (an older shell) reads as null. */
export function readDiskUsage(payload: unknown): DiskUsage | null {
    if (!payload || typeof payload !== "object") return null;
    const raw = record(payload);
    const disk = record(raw.data_disk);
    const backup = record(raw.update_backup);
    const releases = record(raw.runtime_releases);
    const listed = Array.isArray(releases.releases) ? releases.releases.map(record) : [];
    const diskBytes = count(disk.allocated_bytes);
    const backupBytes = count(backup.allocated_bytes);
    const releaseBytes = count(releases.allocated_bytes);
    return {
        data_disk: diskBytes === null ? null : { allocated_bytes: diskBytes },
        update_backup: backupBytes === null ? null : {
            allocated_bytes: backupBytes,
            reclaimable_bytes: count(backup.reclaimable_bytes),
            size_is_upper_bound: backup.size_is_upper_bound === true || count(backup.reclaimable_bytes) === null,
            expires_at_unix: count(backup.expires_at_unix),
        },
        runtime_releases: releaseBytes === null ? null : {
            allocated_bytes: releaseBytes,
            count: listed.length,
            kept: listed.filter((one) => one.kept === true).length,
        },
    };
}

/** Decimal units, as Finder shows sizes: "16 GB", "2.2 GB", "183 MB". */
export function formatBytes(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes < 1000) return `${Math.max(0, Math.round(bytes || 0))} bytes`;
    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes / 1000;
    let unit = 0;
    while (value >= 999.95 && unit + 1 < units.length) {
        value /= 1000;
        unit += 1;
    }
    return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

/** The backup's size as the row says it: what deleting it frees, or "up to"
 *  its allocated size when that cannot be known. */
export function backupSize(backup: UpdateBackup): string {
    if (backup.reclaimable_bytes !== null && !backup.size_is_upper_bound) return formatBytes(backup.reclaimable_bytes);
    return "up to " + formatBytes(backup.allocated_bytes);
}

/** When the backup goes on its own, in the row's words. */
export function backupExpiry(backup: UpdateBackup, nowMs: number): string {
    if (backup.expires_at_unix === null) return "Lemma deletes it once the update has started cleanly.";
    const hours = Math.ceil((backup.expires_at_unix * 1000 - nowMs) / 3_600_000);
    if (hours <= 1) return "Lemma deletes it within the hour.";
    if (hours < 48) return `Lemma deletes it on its own within ${hours} hours.`;
    return `Lemma deletes it on its own within ${Math.ceil(hours / 24)} days.`;
}

/** The disk row's one line: what each part takes. Null when there is
 *  nothing to report (an older shell, or a platform with no data disk). */
export function diskUsageLine(usage: DiskUsage | null): string | null {
    if (!usage) return null;
    const parts: string[] = [];
    if (usage.data_disk) parts.push(`Data ${formatBytes(usage.data_disk.allocated_bytes)}`);
    if (usage.update_backup) parts.push(`backup ${backupSize(usage.update_backup)}`);
    if (usage.runtime_releases && usage.runtime_releases.count > 0) {
        const releases = usage.runtime_releases;
        parts.push(`${releases.count === 1 ? "1 runtime" : `${releases.count} runtimes`} ${formatBytes(releases.allocated_bytes)}`);
    }
    return parts.length ? parts.join(" · ") : null;
}

/** What "Free up space" did, in one sentence. */
export function cleanupSaid(answer: unknown): string | null {
    const raw = record(answer);
    if (raw.cancelled === true) return null;
    const done: string[] = [];
    const freed = count(raw.backup_freed_bytes);
    if (freed !== null) done.push(`deleted the backup (${formatBytes(freed)})`);
    const releases = count(raw.removed_releases);
    if (releases) done.push(`removed ${releases === 1 ? "an old runtime" : `${releases} old runtimes`}`);
    if (typeof raw.images === "string") done.push("removed unused sandbox images and returned freed space to macOS");
    const problem = typeof raw.images_error === "string" && raw.images_error ? ` ${raw.images_error}.` : "";
    if (!done.length) return problem ? problem.trim() : "Nothing to free up right now.";
    const sentence = done.join(", ");
    return sentence.charAt(0).toUpperCase() + sentence.slice(1) + "." + problem;
}
