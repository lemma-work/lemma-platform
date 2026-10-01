## Decisions

`decide` puts a closed-set question (which of these, yes or no, how much) to
rules, a fast classifier and a model, and records each answer where a person
can correct it. Use it when one judgement is made over many things, such as
every row of a table or a CSV, or when the pod should act on a recorded
judgement. Investigating, using other tools and writing stay your own work.

Pass `questions` for a one-off. For a judgement the pod will ask again, save it
with `define_decider`, and try a change with `test_decider` before saving it. A
question left `open` means nothing was sure: if it matters, ask the person with
`ask_user` and record their answer with `answer_decision`. Your answer never
teaches the decider; the person teaches it by correcting the decision in Lemma.
