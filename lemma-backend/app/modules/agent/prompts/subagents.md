# Sub-agents

Hand a self-contained piece of work to another pod agent with `spawn_subagent`,
or omit `agent_name` to run another instance of yourself. Write the task so it
stands alone: the child sees nothing of this conversation. Delegate through
`spawn_subagent` and never through a task or sub-agent tool of your own
runtime: Lemma cannot see that work, so it never reaches this conversation or
`query_subagents`.

`spawn_subagent` returns at once with a `run_id`. Spawn every independent child
first, then call `wait_for(subagent_run_id=...)` for the one your next step
needs and end the turn; you wake with its result. A child you did not wait for
reports back later as a new message, so do not poll. Use `interact_subagent`
with `action='await'` only for a child that finishes in seconds, since it holds
this run open while it blocks; it also sends a child a follow-up or stops it.
`query_subagents` lists your children and reads their messages.
