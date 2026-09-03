# Recipe — a workflow inbox (FORM waits)

Show the human half of a workflow: FORM nodes parked waiting on the signed-in
member, rendered as forms they submit to advance the run. (← `apps.md`)

## The model

A workflow `FORM` node creates a **wait** assigned to a pod member. Until they
submit, the run is parked. A wait carries `run_id`, `node_id`, `wait_type`
(`"HUMAN"` for a form), `assigned_pod_member_id`, `status`, and a `payload` — and
**the form's JSON Schema lives at `wait.payload.input_schema`**, not at
`wait.input_schema`. Submitting resumes the run.

## Inbox + submit

```tsx
import { useWorkflowRunWaitAssignments, useWorkflowResume } from "lemma-sdk/react";

// FORM waits assigned to the current member:
const { assignments, total, isLoading, refresh, loadMore } =
  useWorkflowRunWaitAssignments({ client, podId: client.podId });

const { resume } = useWorkflowResume({ client, podId: client.podId });

// render assignment.payload.input_schema as fields, then on submit:
await resume(formValues, { runId: assignment.run_id, nodeId: assignment.node_id });
// formValues are the submitted field values; the run advances past the FORM node.
```

## Render the form from its schema

`<WorkflowForm>` and `useWorkflowForm` take **the run**, not ids: they read
`run.active_wait` themselves, derive the fields from its `payload.input_schema`, hold
the values, and hand you a submit. Fetch the run (`useWorkflowRun`) and pass it in:

```tsx
import { WorkflowForm } from "lemma-sdk/react";

<WorkflowForm
  run={run}
  onSubmit={({ nodeId, inputs }) => resume(inputs, { runId: run.id, nodeId })}
>
  {(f) => (
    <Fields
      fields={f.fields}          // SchemaFormField[], derived from the wait
      values={f.values}
      onChange={f.setValue}      // (name, value)
      onSubmit={f.submit}
      disabled={!f.canSubmit || f.isSubmitting}
    />
  )}
</WorkflowForm>
```

`useWorkflowForm(options)` is the same thing headless, returning `{ fields, values,
setValue, setValues, reset, isWaitingForInput, nodeId, canSubmit, isSubmitting, error,
submit }`. It renders nothing itself — the app owns the inputs and buttons.

## Make the work visible

Pair the inbox with run status so operators see *where* a process is stuck: show
`useWorkflowRun` (current node label, status) and `useFlowRunHistory` (step
history) next to the form. This is the app side of human-agent collaboration —
don't hide waits in logs.

> Exact fields: `cat /sdk/lemma-typescript/src/react/{useWorkflowRunWaitAssignments,useWorkflowForm,useWorkflowResume}.ts`.
