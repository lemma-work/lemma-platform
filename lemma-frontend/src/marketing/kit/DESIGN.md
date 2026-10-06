# Kit feedback loop

For Acme's product team, carry every piece of customer feedback from "someone said this in #feedback" to "the people who reported it say the fix works". Kit captures and sorts; people own, fix and confirm. The app is where both meet.

This is an explicitly fictional public demo, not a connected pod. Existing React/Next preview routing and sample identity remain authoritative. No external posting, ticketing or backend writes: Confirm live, assigning an owner and Kit's replies change this page only. They complete quietly, like the real thing, with a short toast; the page carries no "sample" labels, because it sits on the landing page and the visitor already knows it is a fictional workspace. State lives in versioned sessionStorage (`lemma-demo:kit-feedback:v1`) for this browser tab; restored state is validated against the shape this version writes. If storage fails, work continues in memory with a visible notice.

## Canon

Wednesday 10:24. You (PM, owner), Dev (engineer, the only one who confirms his fixes are live), Sam (support, Enterprise accounts), Alex (engineer). Nine themes, 222 reports this month, 8 of them overnight; loop closed this week 31/44. Every total, count and percentage on screen is derived from the theme data in `model.ts`; nothing is typed twice. The report page is a 02:00 snapshot and reads the seed, not the live board.

## Surface contract

- Head: breadcrumb Acme / Kit / Apps / Feedback loop. Title, "Built by Kit · used by" faces, health line (last capture, next reconcile, reports and themes this month, loop closed this week as a meter), View as You · Dev · Sam, link to the report page.
- Waiting on you: the viewer's queue. You: unowned Bulk edit (assign owner), Kit's merge suggestion (merge or keep separate), #482 waiting on Dev. Dev: Confirm #482 live, P0 Large imports. Sam: the 3 Enterprise replies, the repeat reporter. Done items stay, ticked, so the visitor sees what they changed.
- Board: No fix yet / Fix in progress / Merged / Closed. A card is a theme: report count, 7-day sparkline, one real quote, P0/new/reopened, ticket and PR state, owner or "No owner", and retry results once a fix is live.
- Drawer: Kit's summary, where the reports came from, sample reports, the #482 retry reply (editable until it goes live), retests, and a thread where a comment that mentions @Kit gets an answer.
- Rail: activity by people and Kit, grouped by day; the rules the team taught Kit, each with who taught it.
- Report page: "Feedback report, week 40", written by Kit, updated Wed 02:00, shared with Dev, Sam, Alex, with margin notes from You and Dev.
- Ask Kit: says what it would do in a real workspace; it does not pretend to open a chat.

## Journeys

1. You assigns Bulk edit: Kit creates LIN-257, the card leaves "No owner", activity records both.
2. You merges the spinner theme: Large imports becomes 46, the spinner card goes. Keep separate assigns Dev and stops the suggestion.
3. View as Dev -> Confirm live: #482 moves to Closed, Kit posts 19 replies, Sam keeps 3, loop becomes 31/66. Only Dev can confirm; You and Sam see why.
4. Skip ahead a day: 11 of 22 say it works, the one .xls failure is reopened as its own theme for Dev, loop 42/66.

A landing-tour step returns the app to its first screen (drawer and page closed, View as You, scrolled to top) without undoing any of these.

## Direction

The prototype's Editorial Canvas: cream paper #f9f6ef, ink #22251f, forest #275d4a, the warm inbox band, hairline board. Serif ("Iowan Old Style", Charter, Georgia) for display only: title, column heads, card names, counts, the report page. System sans for the tool. Nothing heavier than 500; hierarchy from size and the space above. No web fonts. People are initials in muted circles; Kit is its cast face (`/teammates/loop-v1.png`). The first screen is what the landing page crops at 1440px, so header, health, Waiting on you and the top of the board must read as a tool someone works in.

## States and responsiveness

At <=1180px the rail drops below the board and the View-as switch sits on one line with the report link. At <=860px the board scrolls sideways inside itself with snap. At <=640px the drawer and report become full-screen sheets and Ask Kit shrinks to its face. No horizontal body overflow at 375px. Focus outlines throughout, labelled controls, aria-live for the queue and toasts, Escape closes the page then the drawer, focus returns to what opened them. Reduced motion turns off flashes and slides.
