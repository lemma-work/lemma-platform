# The pod

A pod is a workspace containing data, files, apps, agents, and automation.
People and agents work with these resources under permissions. Resources can
have different audiences within the same pod.

## Choose resources for the work

| Resource | Use |
| --- | --- |
| Tables | Structured records that need queries, relationships, or updates |
| Files | Documents, reference material, deliverables, and memory |
| Functions | Reusable code for a defined operation |
| Agents | Work that requires interpretation or judgment |
| Workflows | Durable sequences, branching, retries, or waits, including human steps |
| Schedules | Work started by time, a table event, or a webhook |
| Connectors | Operations on connected external systems |
| Surfaces | Conversations through channels such as Slack or email |
| Apps | Interfaces people use to inspect and work with pod resources |

Reuse what is already present. A question may need only an answer; a single
update may need only a record write. Use a workflow when the process needs a
durable sequence or checkpoint, whether or not a human step is involved. Load
`lemma-builder` for resource design and authoring details.

An inventory is a bounded snapshot. Describe or list a resource when you need
current configuration, execution status, or entries omitted from the brief.
Configured work and successful execution are different facts.

## Access and approvals

The default pod agent acts with the invoking person's access. Named agents are
also limited by their resource grants. Both remain subject to approval gates.

- `INSUFFICIENT_PERMISSION`: explain the permission that is missing.
- `MISSING_WORKLOAD_RESOURCE_GRANT`: use the available approval mechanism for
  the specific requested action, or have an authorized person grant the needed
  resource for ongoing use.
- `DELEGATION_EXCEEDS_INVOKER`: the invoking person lacks access; adding an
  agent grant cannot supply it.
- For an unclassified refusal, read the structured error and inspect access
  where permitted. Do not assume the cause or repeatedly retry the same action.

Approval of one action does not itself establish a standing resource grant.
Neither a resource listing nor an action label is authorization for a task.

## Rows and audience

`enable_rls` defaults to true. Normal record operations on an RLS table are
scoped to the invoking user's rows. Explicit admin mode can read across users
when the caller has the required table administration permission.

With `enable_rls: false`, row operations do not apply that per-user filter.
Table visibility, grants, and operation permissions still apply. RLS being off
does not mean everyone can access the table.

For a new table whose rows should be shared, set the intended row scope and
resource visibility explicitly. For an existing table, inspect both before
promising who can see it. Do not disable RLS or widen visibility just to make an
operation succeed.

Keep private information in the appropriate personal scope. Search indexing
may lag a file upload; read a known attachment by path before concluding it is
missing. Follow the runtime's directory guidance and check existing content
before changing files another person or run may be using.
