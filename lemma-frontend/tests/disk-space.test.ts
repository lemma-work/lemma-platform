import test from "node:test";
import assert from "node:assert/strict";
import { backupExpiry, backupSize, cleanupSaid, diskUsageLine, formatBytes, readDiskUsage } from "../src/desktop/disk-space.ts";
import { readSnapshot } from "../src/desktop/this-mac.ts";

/* Measured on a real installation: the backup's blocks are almost all shared
   with the live disk, so deleting it frees 183 MB, not 16 GB. */
const SHELL = {
    data_disk: { allocated_bytes: 18_791_067_648 },
    update_backup: { allocated_bytes: 16_033_103_872, reclaimable_bytes: 183_316_480, size_is_upper_bound: false, expires_at_unix: 1_000_000 },
    runtime_releases: {
        allocated_bytes: 4_400_000_000,
        releases: [{ name: "0.8.0-a", allocated_bytes: 2_200_000_000, kept: true }, { name: "0.7.9-b", allocated_bytes: 2_200_000_000, kept: true }],
    },
};

test("sizes read the way Finder shows them", () => {
    assert.equal(formatBytes(0), "0 bytes");
    assert.equal(formatBytes(1_500), "1.5 KB");
    assert.equal(formatBytes(999_999), "1.0 MB");
    assert.equal(formatBytes(183_316_480), "183 MB");
    assert.equal(formatBytes(16_033_103_872), "16 GB");
    assert.equal(formatBytes(2_200_000_000), "2.2 GB");
});

test("the backup shows what deleting it frees, and 'up to' when that is unknown", () => {
    const usage = readDiskUsage(SHELL)!;
    assert.equal(backupSize(usage.update_backup!), "183 MB");
    const unknown = readDiskUsage({ update_backup: { allocated_bytes: 16_033_103_872, reclaimable_bytes: null } })!;
    assert.equal(unknown.update_backup!.size_is_upper_bound, true);
    assert.equal(backupSize(unknown.update_backup!), "up to 16 GB");
});

test("the row's line names each part, and is absent when there is nothing to say", () => {
    assert.equal(diskUsageLine(readDiskUsage(SHELL)), "Data 19 GB · backup 183 MB · 2 runtimes 4.4 GB");
    assert.equal(diskUsageLine(readDiskUsage({ data_disk: { allocated_bytes: 5_000_000_000 } })), "Data 5.0 GB");
    assert.equal(diskUsageLine(readDiskUsage({})), null);
    assert.equal(diskUsageLine(null), null);
    assert.equal(readDiskUsage(undefined), null);
});

test("the snapshot carries the disk figures, and an older shell's reads as none", () => {
    assert.equal(readSnapshot({ disk_usage: SHELL }).disk_usage?.runtime_releases?.count, 2);
    assert.equal(readSnapshot({}).disk_usage, null);
});

test("the backup's own deadline is said in hours, then days", () => {
    const backup = readDiskUsage(SHELL)!.update_backup!;
    assert.equal(backupExpiry(backup, 1_000_000_000 - 30 * 60_000), "Lemma deletes it within the hour.");
    assert.equal(backupExpiry(backup, 1_000_000_000 - 5 * 3_600_000), "Lemma deletes it on its own within 5 hours.");
    assert.equal(backupExpiry(backup, 1_000_000_000 - 70 * 3_600_000), "Lemma deletes it on its own within 3 days.");
});

test("what Free up space did, in one sentence", () => {
    assert.equal(cleanupSaid({ cancelled: true }), null);
    assert.equal(
        cleanupSaid({ backup_freed_bytes: 183_316_480, removed_releases: 1, images: "removed 2 unused image(s)" }),
        "Deleted the backup (183 MB), removed an old runtime, removed unused sandbox images and returned freed space to macOS.",
    );
    assert.equal(cleanupSaid({ removed_releases: 0 }), "Nothing to free up right now.");
    assert.equal(
        cleanupSaid({ images_error: "Lemma is not running, so its images were left for next time" }),
        "Lemma is not running, so its images were left for next time.",
    );
});
