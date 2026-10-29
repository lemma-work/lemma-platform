# Delegation

`delegate` hands independent tasks to sub-agents that run in parallel, each in
a fresh conversation of its own. It is how you split work that is too big for
one pass, or that would flood your own context with material you only need the
conclusion of. Delegate through `delegate`, never through a task or sub-agent
tool of your own runtime: Lemma cannot see that work.

**When to split.** Delegate parts that do not depend on each other: research
across several sources or vendors, one question per table, a frontend and a
backend in separate directories. Keep the work yourself when it is small, when
each step needs the last one's result, or when the parts would edit the same
files. Two to five children is usual; more costs more than it saves.

**How to brief.** A child sees only its brief, none of this conversation. Say
the goal, the inputs (paths, ids, names), the constraints, the directory it
works in -- a different one for each child -- and the shape and length of the
report you want back ("a table of 4 vendors, at most 300 words"). Omit
`agent` to start another instance of yourself; name a pod agent to use its
skills. `model` runs a simple lookup on a faster model of your runtime.

**Parallel or in turn.** Everything in one `delegate` call runs at once. When
one part needs another's output, delegate the first, read its report, then
delegate the next with what it found.

**Waiting.** With `wait="all"` (the default) your turn ends and you wake once,
with every child's report, status and error in `results`. A child that stops
to ask a question wakes you early: answer it with
`interact_subagent(action='send')`, or ask the person, then
`wait_for(subagent_run_ids=[...])` on the ones still going. With
`wait="none"` you carry on, and the reports arrive later together as one
message; do not poll.

**Merging.** Check each report against what you asked for before building on
it. Reconcile where children disagree, say which part failed and why rather
than filling it in yourself, and name the files they wrote by path.
