# Booking a member's time

Status: public reads built, 2026-10-10; the booking pod is next. Builds on [forms-and-chat.md](forms-and-chat.md)
and [contacts.md](contacts.md).

## Decided

- **A booking page is not a thing of its own.** It is a page that reads one
  table and adds rows to another, as a form does. No booking object, no
  scheduling engine, no new noun (Deepak, 2026-10-10).
- **The page reads one public table and nothing else.** A table the pod opened
  for public reads: chosen columns, every row. The slots a person may pick are
  rows of that table.
- **Only functions write the public table.** One keeps it in step with the
  member's calendar on a time schedule; one takes a slot out when it is booked.
  The page never writes it, and a visitor never reads anything else.
- **A booking is a row**, added through the form path that exists. Its slot is
  a unique column, which opens only to confirmed contacts, so proving an email
  address is what lets a stranger book, and the database, not an agent, refuses
  the second booking of a slot.
- **Making the calendar event is a function step, not an agent.** A stranger's
  row starts a workflow whose step reads the row and writes the calendar. No
  model reads the stranger's words with the calendar in its hands.
- **The teammate's work is after the booking**: reading why they booked,
  writing the member a brief, answering the contact's reschedule in their own
  conversation. That is what a booking page on Lemma has that Calendly hasn't.

## The pieces

| Piece | What it is |
| --- | --- |
| Public reads | `datastore_public_reads`: a table opened for reads to `anyone` or confirmed `contacts`, which columns, opened by which member. Rows are read as that member. `PUT/GET/DELETE /pods/{pod}/datastore/tables/{table}/public-reads` |
| What a page may read | `GET /public/web/{key}/rows?table=`: the open columns of every row, at most 500, ordered by the column the member chose. Nothing about the table beyond that |
| `open_slots` | Readable to anyone: `starts_at`, `ends_at`. Holds only free, future slots. No titles, no attendees, nothing from the calendar but free and busy |
| `bookings` | Contact-owned, open for rows to `contacts`: `starts_at` (unique), `topic`, plus `contact_id` stamped by the platform. Its own `status` (`booked`, `declined`) is the functions' to set |
| Sync function | On a time schedule (every 15 minutes): the member's working hours and their calendar's free/busy become `open_slots`, replacing what was there |
| Book function | A workflow started by a `bookings` insert (`include_outside_rows`): checks the slot is still in `open_slots` and still free on the calendar, makes the event with the contact's confirmed address as the guest, deletes the slot, sets `status` |
| The page | A pod app from a template (the `lemma-form` skill grows a booking template): reads `open_slots`, shows them in the visitor's time zone, confirms the email, adds the row |

## The page

The member's EA (their teammate) introduces itself; the next five weekdays'
slots sit under it, one click to pick and one to continue; a chatbot below
answers first and offers a time only when the question needs the member; a
"these days" board in the member's own words; the cast at the close.

## Why a table and not a function the page calls

A function the page calls would answer with whatever its code computes from
whatever it can reach, so what a stranger may see is in code a member must
read. A table opened for reads answers with its rows, and its rows are only
what a function put there. What the public sees is a thing a member can open
and look at, and close.

## Authority

| Who | May |
| --- | --- |
| Visitor | Read the open columns of a table opened to `anyone`; add a row where the form path allows |
| Confirmed visitor | Read a table opened to `contacts` too; book |
| Member | Open a table they can change for reads; the grant goes when they or the table do |
| Book function | Runs as its owner; reads the row and the contact's confirmed address, writes the calendar |

Rules the read side keeps:

- **A table takes rows or is read, never both.** The form path promises that
  adding a row reads nothing back, so a stranger cannot probe what a table
  holds. A table that is also readable would break that promise, so opening one
  refuses while the other is open.
- **No per-member or contact-owned table opens for reads.** Their rows belong
  to somebody; a public read would hand one person's rows to everyone.
- **Only plain columns open.** Text, numbers, yes or no, dates, times, options.
  Never a user reference, a file, JSON, a vector or `contact_id`.
- **Read as the member who opened it**, through the same read check as their
  own hand. If they lose the right to read the table, the page reads nothing.
- **Counted per widget and address**, like every other public call, and
  nothing opens while forms are switched off (`PUBLIC_WEB_ENABLED`).

## What can go wrong

- **The calendar changes between a sync and a booking.** The book function
  checks free/busy again before it writes. If the slot is gone it sets
  `status = declined`, and the teammate writes to the contact with new times.
- **Two people book one slot at once.** `starts_at` is unique on `bookings`;
  the second insert is refused with the form path's one sentence.
- **The free/busy pattern is public.** Anyone with the page sees when the
  member is free in the coming weeks, as on every booking page. The sync
  function writes only slots inside working hours, never busy blocks.

## Build order

1. Public reads: the grant, its API, `GET /public/web/{key}/rows`, the
   widget script's `Lemma.readRows(table)`, the SDKs.
2. The booking template: two tables, two functions, one workflow, two
   schedules and the page, as a pod bundle the `lemma-form` skill installs.
3. The teammate's part: a brief before each booking, reschedules in the
   contact's conversation.

## Not building

- Round-robin or team scheduling, buffers, payments, reminders by text.
- Reading the calendar from the page, or any read of a table the pod did not
  open for reads.
