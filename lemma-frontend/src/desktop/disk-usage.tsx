"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { SettingRow, useThisMacSnapshot } from "./this-mac-settings";
import { friendlyError, thisMac } from "./this-mac";
import { backupExpiry, backupSize, cleanupSaid, diskUsageLine } from "./disk-space";
import { useThisComputer } from "./this-computer";

/** Overview's disk rows: what Lemma takes on this Mac, and giving it back.
 *
 *  Both actions go through the shell, which asks natively before the backup
 *  from before the last update is deleted; the page never draws its own "are
 *  you sure". Freeing space is otherwise safe by construction: it removes only
 *  runtimes and sandbox images nothing can use, and trims the data disk. */
export function DiskUsageRows() {
    const noun = useThisComputer();
    const snapshot = useThisMacSnapshot();
    const queryClient = useQueryClient();
    const [said, setSaid] = useState<string | null>(null);
    const [bad, setBad] = useState(false);
    const done = (answer: unknown) => {
        setBad(false);
        setSaid(cleanupSaid(answer));
        void queryClient.invalidateQueries({ queryKey: ["this-mac"] });
    };
    const failed = (problem: unknown) => {
        setBad(true);
        setSaid(friendlyError(problem));
    };
    const deleteBackup = useMutation({ mutationFn: () => thisMac.deleteUpdateBackup(), onSuccess: done, onError: failed });
    const freeUp = useMutation({ mutationFn: () => thisMac.freeUpSpace(), onSuccess: done, onError: failed });
    const usage = snapshot.data?.disk_usage ?? null;
    const line = diskUsageLine(usage);
    if (!line) return null;
    const busy = deleteBackup.isPending || freeUp.isPending;
    const backup = usage?.update_backup ?? null;
    return (
        <>
            <SettingRow name="Disk space" consequence={`${line}. Free up space removes runtimes and sandbox images nothing uses and returns freed space to ${noun}.`}>
                <button className="btn" disabled={busy} onClick={() => { setSaid(null); freeUp.mutate(); }}>
                    {freeUp.isPending ? "Freeing up…" : "Free up space"}
                </button>
            </SettingRow>
            {backup && (
                <SettingRow
                    name="Backup from before the last update"
                    consequence={`${backupSize(backup)}. Kept in case the update went wrong. ${backupExpiry(backup, Date.now())}`}
                >
                    <button className="btn" disabled={busy} onClick={() => { setSaid(null); deleteBackup.mutate(); }}>
                        {deleteBackup.isPending ? "Deleting…" : "Delete"}
                    </button>
                </SettingRow>
            )}
            {said && <p className={bad ? "thismac-said thismac-said--bad" : "thismac-said"} role={bad ? "alert" : "status"}>{said}</p>}
        </>
    );
}
