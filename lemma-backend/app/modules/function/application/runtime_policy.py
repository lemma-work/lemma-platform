"""Fixed cross-component policy for function execution protocol v2."""

FUNCTION_JOB_CALLBACK_GRACE_SECONDS = 60

#: How long an asynchronous run must have sat PENDING before the recovery sweep
#: (``reconcile_function_runs``, once a minute) may treat it as unqueued.
#:
#: One sweep interval, and the floor is the point rather than a tuning knob.
#: Queue publication happens after the unit of work that writes ``job_id``
#: commits, so *every* asynchronous run is briefly PENDING-and-unqueued on the
#: ordinary path -- indistinguishable, to a sweep with no floor, from one whose
#: publication was genuinely lost. Republishing such a run looks free because
#: Streaq deduplicates the deterministic task identity, but deduplication only
#: protects the *publication*: nothing stops the worker that consumes the copy
#: from winning the PENDING -> RUNNING transition away from a caller that was
#: dispatching the same run in-process, which then goes on waiting for an
#: outcome it no longer owns. A run that has not survived a full sweep interval
#: has not lost anything yet, so it is left alone.
FUNCTION_RUN_REPUBLISH_MIN_AGE_SECONDS = 60
