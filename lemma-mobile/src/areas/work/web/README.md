# plan.ts

The work area runs the web's own workflow, run, schedule and agent modules
where they live (`@web/workflow/*`, `@web/schedule/schedules`,
`@web/data/agents`, `@web/stage/colleagues`, `@web/data/runtimes`,
`@web/search/sources`; see `../../../../web-imports.js`), so a run says the
same thing on both, a schedule's health reads the same, and an agent offers
Edit and Delete on the same terms.

`plan.ts` is the one slice left: the plan section of the web's
`src/outcomes/model.ts` and `planRequest` from `outcomes/data.ts`, ported
before those reached main. Now that they have, it can become an import too.

What is the phone's own (how a run, a board or an X-ray is laid out on a
small screen) is in `../model.ts`.
