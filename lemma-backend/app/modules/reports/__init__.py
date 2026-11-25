"""Reports: a person telling the team that runs this deployment about something
they read in Lemma -- a Lemma's reply, or another person's line in a shared
chat.

The module that holds the message (``agent`` for a chat with a Lemma,
``channels`` for a shared chat) checks the reporter may read it and takes the
copy; this module keeps the report and tells the team (``contracts``).
"""
