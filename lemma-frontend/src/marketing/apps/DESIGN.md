# The other sample apps

These are independent, functional local demos under the existing preview route, not connected production pods. All data is fictional. No external actions. State lives in versioned sessionStorage for the tab; a storage failure shows a warning and preserves edits in memory.

## Remy / Deal desk
For a sales owner, turn an account conversation into an owned next step. Account-stage board -> account detail -> email thread and editable draft -> internal approval and follow-up task. Search filters real account records, stage changes move cards, draft edits clear approval. Side panel exposes contact roles, approved document preview, commitments, notes and due dates. Use warm terracotta accents, dense sales records, mail-reader typography. No sample send action disguised as external delivery.

## June / Customer launchpad
For an onboarding owner, turn a malformed customer sample into validated import-ready data. Customer selection -> milestones -> original CSV beside editable normalized rows -> validation errors -> explicit sample import -> completion. Derived progress is driven by validation/import state. Date normalization is explicit and validates ISO dates. Invalid emails block import. Source stays immutable; reload preserves working values. Training plan becomes available only after successful sample import. Use green accents, grid editing, stepper and validation panel.

## Scout / Evidence notebook
For a researcher, turn selected interview evidence into a defensible finding and experiment. Source cards with full excerpts, include/exclude toggles, theme selection, linked evidence counts, counterexamples, finding editor and experiment form. Exclusions change denominator and chart immediately. Experiment requires question, owner and success criterion, and is saved locally as proposed. Blue ink, paper-like research columns and chart tied to included evidence, no fabricated totals.

## Shared quality floor
Keyboard labels/focus, status announcements, specific validation messages, responsive stacking at 700px and no body overflow at 375px. Views contain source content rather than summaries of work supposedly done elsewhere. Test primary journeys in-browser and pure validation functions in Node. Existing SDK and product controls remain untouched; these apps use the preview's local fixture contract.
