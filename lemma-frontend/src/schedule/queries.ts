import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import type { ScheduleDraft } from "./schedules";

/** The reads and writes behind Standing work.
 *
 *  Through `source` rather than through the SDK directly, so the section draws
 *  the same in the sample as it does against a pod. That is not a convenience:
 *  the states worth judging here — a schedule the breaker stopped, a run that
 *  failed and can be retried — are the ones nobody can produce on demand
 *  against a real backend, and a section that only renders in a session is a
 *  section whose worst states never get looked at.
 */

const SCHEDULES = "schedules";

export function useSchedules(podId: string) {
    return useQuery({
        queryKey: [SCHEDULES, podId],
        queryFn: () => source.listSchedules(podId),
        /* A schedule changes when somebody edits one, which is rare and always
           somewhere else. The same half-minute the agent roster holds. */
        staleTime: 30_000,
    });
}

/** The firings of one schedule, fetched only once its row is opened. A pod
 *  with twelve schedules would otherwise cost twelve run listings to draw a
 *  list nobody has asked a question of yet. */
export function useScheduleRuns(podId: string, scheduleId: string | null) {
    return useQuery({
        queryKey: [SCHEDULES, podId, scheduleId, "runs"],
        queryFn: () => source.listScheduleRuns(podId, scheduleId as string),
        enabled: Boolean(scheduleId),
        staleTime: 15_000,
    });
}

/** Pause or resume. One call, and the most useful control on this section.
 *
 *  The list is invalidated rather than patched in place. Resuming a schedule
 *  the breaker stopped clears its failure count server-side, so the row's
 *  health line changes along with its dot — writing `is_active` into the
 *  cached row by hand would leave "failed 5 times in a row" under a schedule
 *  that is now running with a clean count.
 */
export function useScheduleActive(podId: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: ({ id, active }: { id: string; active: boolean }) =>
            source.setScheduleActive(podId, id, active),
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: [SCHEDULES, podId] });
            /* The profile header counts standing jobs, and it counts the
               active ones. Pausing one and leaving the headline saying three
               is the drift this section exists to end. */
            void cache.invalidateQueries({ queryKey: ["profile", podId] });
        },
    });
}

/** Run a failed firing again, with the event it originally carried.
 *
 *  Answers the *new* run, and the ledger it belongs to is what redraws — the
 *  old row stays failed, which is right: a redrive is a second attempt in the
 *  history, not an edit of the first.
 */
export function useRetryRun(podId: string, scheduleId: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (runId: string) => source.retryScheduleRun(podId, scheduleId, runId),
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: [SCHEDULES, podId, scheduleId, "runs"] });
            void cache.invalidateQueries({ queryKey: [SCHEDULES, podId] });
        },
    });
}

export function useCreateSchedule(podId: string) {
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (draft: ScheduleDraft) => source.createSchedule(podId, draft),
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: [SCHEDULES, podId] });
            void cache.invalidateQueries({ queryKey: ["profile", podId] });
        },
    });
}

/** What a new schedule could be pointed at. Fetched when the form opens, not
 *  with the list. */
export function useScheduleTargets(podId: string, enabled: boolean) {
    return useQuery({
        queryKey: [SCHEDULES, podId, "targets"],
        queryFn: () => source.scheduleTargets(podId),
        enabled,
        staleTime: 60_000,
    });
}
