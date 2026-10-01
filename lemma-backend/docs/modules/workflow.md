# Workflow module

## Purpose

`app/modules/workflow` defines and executes directed workflow graphs. A graph
can combine functions, agents, decisions, loops, waits, human forms, and end
nodes. The module persists run context and step history, pauses on external
work, and resumes from function, agent, schedule, or user-form events.

## Runtime contributions

| Contribution | Behavior |
| --- | --- |
| API routers | Workflow CRUD/graph/visualization, run create/list/detail/cancel/form/visualization |
| Redis consumers | Function completion, agent completion, and schedule-fired events |
| streaq tasks | Resume function/agent waits, ask a DECISION node's question (`ask_workflow_decision`), and start scheduled workflows |
| cron | Reconcile stale waits every five minutes |

## Main data model

| Table | Meaning |
| --- | --- |
| `workflow_flows` | Named pod graph, typed start configuration, visibility |
| `workflow_flow_runs` | Status, trigger/run context, step records, output/error |
| `workflow_run_waits` | Active/completed external or human wait assignment and payload |

## Graph vocabulary

| Node | Behavior |
| --- | --- |
| Function | Starts a function run and waits for its terminal event |
| Agent | Starts/continues an agent conversation and waits for completion |
| Decision | Evaluates ordered JMESPath rules; when none matches and it has a `question`, asks a decider and follows the answer's branch; otherwise takes its edge |
| Loop | Iterates items while maintaining a scoped loop frame |
| Form | Persists a human wait with schema/assignment and resumes on submission |
| Wait until | Pauses until a time/schedule signal |
| End | Resolves output bindings and completes the run |

## API groups

`/pods/{pod_id}/workflows` provides CRUD, graph replacement, manual run
creation, run history, and definition visualization. `/pods/{pod_id}/workflow-runs`
provides assigned waits, form submission, cancellation, detail, and run
visualization.

## Execution flow

```mermaid
stateDiagram-v2
    [*] --> RUNNING: manual/event/schedule start
    RUNNING --> RUNNING: synchronous decision/loop/end step
    RUNNING --> WAITING: agent/function/form/time/decision-question step
    WAITING --> RUNNING: terminal event or human submission
    RUNNING --> COMPLETED: end output
    RUNNING --> FAILED: validation/executor error
    WAITING --> CANCELLED: cancel API
```

`WorkflowEngine` loads a run and graph; `WorkflowStepper` selects the current
node and delegates to a typed executor. Input/output bindings use literals or
expressions over the run context. External executors create durable waits before
returning. Event consumers enqueue idempotently named resume jobs, and the cron
reconciles completion events that were missed.

A DECISION node's question never runs inside the engine. Every advance is one
transaction with the run row locked, and asking a decider can climb to System
One or the system model. So the executor resolves the question's `input`
bindings and suspends on a `DECISION` wait whose `external_ref` is the decision
subject, `workflow:<run>:<node>:<loop index>` (a cycle back to the same step
appends `:2`, `:3`…). Once that wait commits, the engine's after-commit hook asks
the `DecisionPort` to queue `ask_workflow_decision`. The job
(`DecisionStepService`) reads the wait in one short unit of work, asks through
the decisions contract with no session open, as the run's user and against the
pod's organization, and resumes the run in another. The stepper's resume then
routes on the recorded `DecisionOutcome`: the answer's branch, `on_open` for an
open question, else the default edge. A refusal (no such decider, no
`decider.execute`, no question to branch on) fails the run; any other error is
left to the job's retries. The reconcile cron re-queues a DECISION wait whose
job was lost, and expires one past the machine-wait ceiling. The subject makes
asking twice harmless: the decisions module returns the recorded decision.

## Authorization and dependencies

Workflow definition/run operations use pod permissions and resource grants.
Human form assignment resolves pod members. Adapter ports invoke agent,
function, and schedule modules; `DecisionPort` asks through the decisions
contract (`decisions/contracts/decide.py`), and a node's inline decider
definition is typed by `decisions/contracts/shapes.py`. Scheduled start
configuration imports schedule value types directly, while schedule also calls
workflow services. That cycle is in the architecture baseline and cannot grow;
see `make architecture`.

## Tests and operations

Tests cover graph validation, expressions, every node executor, waits/forms,
resumption, cancellation, authorization, visualization, schedules, and e2e
function/agent execution. Current unit coverage is 67.6% (1,618 of 2,393
statements). Event-ack reliability remains a review finding.
