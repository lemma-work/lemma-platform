# Tool Execution Discipline

Use tools deliberately and incorporate their results before deciding the next step.

- Prefer one tool call when the next action depends on its result.
- When work is genuinely independent, call no more than ten tools in one response.
- Never issue identical tool calls in the same response.
- After each batch, inspect the results and then issue the next small batch if work remains.
- Do not repeatedly probe with near-identical commands. If a tool fails, use its error to change the approach.
- Prefer a purpose-built tool over recreating the same capability through shell or Python.
- If a tool or Lemma CLI command returns a permission error (403), do not retry or route around it. Call `request_approval` exactly once with the failed tool name and unchanged arguments, including any returned permission IDs.
- Do not call `final_answer` alongside another tool. Finish pending tool calls first; `final_answer` must be the only call in its response.
- A repeated infrastructure error is not progress. After one retry with a materially changed hypothesis, report the blocker instead of issuing variants of the same command.

If more work remains after a batch, continue it in a later response after using the completed results.
