// What Tasks' writes do to the in-memory sample in the "Try it" demo
// (_kit/src/demo.ts). Nothing here leaves the page.
//
// A task, its agent run (`tasks_run`) and the "could an agent do this?" call
// (`tasks_decision`) share one id, so this keeps its own note of the tasks made
// here instead of asking db.find, which could hand back the run.
(function () {
  const READ = { personal_task: "select * from personal_task order by", task: "select * from task order by" };
  const ACTIVITY = { personal_task: "tasks_personal_activity", task: "tasks_activity" };
  const ASSISTANT = "pod_default";
  const made = new Map(); // id -> { table, row }: tasks made in this page
  const runs = new Set(); // ids of runs started in this page

  const fieldsOf = (input) => (input && input.data && typeof input.data === "object" ? input.data : input || {});

  function find(db, id) {
    for (const table of ["personal_task", "task"]) {
      const row = db.rows(READ[table]).find((r) => String(r.id) === id);
      if (row) return { table, row };
    }
    return made.get(id) || null;
  }

  function patch(db, table, id, fields) {
    db.patch(id, fields, table);
    const mine = made.get(id);
    if (mine) mine.row = { ...mine.row, ...fields };
  }

  function nextNumber(db, table) {
    let n = 0;
    for (const r of db.rows(READ[table])) n = Math.max(n, Number(r.number) || 0);
    for (const m of made.values()) if (m.table === table) n = Math.max(n, Number(m.row.number) || 0);
    return n + 1;
  }

  // What the hand-over does once an agent has the task: its run starts, the
  // timeline says so, and a task still to do is in progress. The work itself is
  // the Lemma writing, so the demo says where that goes.
  function startAgent(db, id) {
    const found = find(db, id);
    if (!found) return;
    const now = new Date().toISOString();
    const agent = found.row.assignee_agent || ASSISTANT;
    const fields = { state: "working", agent, started_at: now, last_report_at: now, error: null };
    const known = runs.has(id) || db.rows("select * from tasks_run").some((r) => String(r.id) === id);
    if (!known) {
      db.add("tasks_run", { id, scope: found.table === "task" ? "team" : "personal", conversation_id: null, workflow_run_id: null, turns: 0, ...fields });
      runs.add(id);
    }
    // Also after an add: the run shares its id with the task's suggestion, whose
    // "accepted" would otherwise show on the run.
    db.patch(id, fields, "tasks_run");
    db.add(ACTIVITY[found.table], {
      task_id: id, kind: "started", actor_kind: "agent", actor_user_id: null, actor_name: agent, body: null,
      detail: { by: "You" }, state: null, decided_by: null, decided_at: null,
    });
    if (found.row.status === "todo") patch(db, found.table, id, { status: "in_progress" });
    db.note("In the demo this is where the Lemma works on the task in a conversation of its own, then reports here: its steps, any question for you, and what it made.");
  }

  window.__LEMMA_DEMO_HANDLERS__ = {
    // A new task: the table numbers it ("#7"), as the real one does.
    "POST pods/[^/]+/datastore/tables/(personal_task|task)/records"(input, db, ctx) {
      const table = /tables\/([^/]+)\/records$/.exec(ctx.path)[1];
      const fields = fieldsOf(input);
      const row = db.add(table, { ...fields, number: fields.number != null ? fields.number : nextNumber(db, table) });
      made.set(String(row.id), { table, row: { ...row } });
      if (fields.assignee_kind === "agent") setTimeout(() => startAgent(db, String(row.id)), 700);
      return { id: row.id, data: row, created_at: row.created_at, updated_at: row.updated_at };
    },

    // Any change to a task; handing it to an agent starts the agent, as the
    // tasks-*-handed schedule does.
    "PATCH pods/[^/]+/datastore/tables/(personal_task|task)/records/[^/]+"(input, db, ctx) {
      const [, table, raw] = /tables\/([^/]+)\/records\/([^/]+)$/.exec(ctx.path);
      const id = decodeURIComponent(raw);
      const fields = fieldsOf(input);
      const before = find(db, id);
      patch(db, table, id, fields);
      // After the app's own writes that follow (the timeline, the suggestion settled).
      if (fields.assignee_kind === "agent" && (!before || before.row.assignee_kind !== "agent")) setTimeout(() => startAgent(db, id), 700);
      const row = (find(db, id) || {}).row || { id, ...fields };
      return { id, data: row, ...row };
    },

    // A reply into an agent's conversation: the agent would read it and go on.
    "POST pods/[^/]+/conversations/[^/]+/messages(?:/append)?"(input, db, ctx) {
      const conversation = decodeURIComponent(ctx.path.split("/")[3] || "");
      const run = db.rows("select * from tasks_run").find((r) => r.conversation_id === conversation);
      db.note("In the demo this is where the Lemma reads your reply and picks the task up again.");
      // The app marks the run working; in the demo it settles back to where it was.
      if (run) setTimeout(() => db.patch(String(run.id), { state: run.state }, "tasks_run"), 2500);
      return { id: "demo-message", role: "user", content: input.content || "" };
    },

    // Start, or Start again, with the agent already on it.
    tasks_on_task(input, db) {
      startAgent(db, String(input.task_id));
      return { started: true };
    },

    // Sharing a task moves it between two tables under the same id, which the
    // demo's copy can't hold, so it stays put and says so.
    tasks_move(input, db) {
      const found = find(db, String(input.task_id));
      const scope = found ? (found.table === "task" ? "team" : "personal") : input.to;
      return {
        task_id: input.task_id, scope, moved: 0,
        message: input.to === "team"
          ? "In the demo the task stays private. In a Lemma it moves to the team, with its timeline."
          : "In the demo the task stays with the team. In a Lemma it becomes yours alone, with its timeline.",
      };
    },

    // Delete: the tasks, their sub-tasks, and (sharing their ids) their runs.
    tasks_delete(input, db) {
      const family = new Set((input.task_ids || []).map(String));
      for (const table of ["personal_task", "task"]) {
        for (const r of db.rows(READ[table])) if (r.parent_id && family.has(String(r.parent_id))) family.add(String(r.id));
      }
      for (const [id, m] of made) if (m.row.parent_id && family.has(String(m.row.parent_id))) family.add(id);
      let n = 0;
      for (const id of family) {
        const found = find(db, id);
        if (!found) continue;
        db.remove(id, found.table);
        made.delete(id);
        n += 1;
      }
      db.changed("tasks_run");
      return { deleted: n, corrected: 0, message: n === 1 ? "Deleted 1 task." : `Deleted ${n} tasks.` };
    },

    tasks_sample_data(input) {
      return {
        mode: input.mode, tasks: 0, projects: 0, removed: 0,
        message: input.mode === "clear"
          ? "In the demo the sample stays: it's all there is here."
          : "In the demo the sample is already here. In a Lemma this loads made-up tasks you can clear later.",
      };
    },

    // What needs the person, for Today: nothing to show in this demo.
    tasks_needs_you() {
      return { written: 0 };
    },
  };
})();
