"""The agent module's repositories: one per aggregate, concrete, over one session.

They replace a single ``ConversationRepository`` that served every table the
module owns through a 56-method port. Each class here answers for one thing:

- ``conversations.Conversations``: the conversation row, its status, its lock,
  the history list, the sub-agent tree, and (for now) the metadata key/value
  writes;
- ``runs.Runs``: a run's lifecycle and the reads about one run;
- ``messages.Messages``: appending and reading a conversation's messages;
- ``queued_messages.QueuedMessages``: messages sent while a run was working --
  counting, claiming, steering and withdrawing them;
- ``history_reads.HistoryReads``: the runs and messages a prompt is built from;
- ``pauses.Pauses``: pausing tool calls and what answers them;
- ``approvals.ApprovalDecisions``: the durable record of an approval.

Construct the ones a piece of code uses from its unit of work. They share the
unit of work's session, so several of them in one transaction are one
transaction.
"""
