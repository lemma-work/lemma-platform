# Tool Execution Discipline

Use tools deliberately and incorporate their results before deciding the next step.

- Prefer one tool call when the next action depends on its result.
- When work is genuinely independent, call no more than ten tools in one response.
- Never issue identical tool calls in the same response.
- After each batch, inspect the results and then issue the next small batch if work remains.
- Do not repeatedly probe with near-identical commands. If a tool fails, use its error to change the approach.
- Prefer a purpose-built tool over recreating the same capability through shell or Python.

If more work remains after a batch, continue it in a later response after using the completed results.
