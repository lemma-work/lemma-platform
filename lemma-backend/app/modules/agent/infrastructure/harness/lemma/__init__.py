"""The in-process (Lemma) harness, on pydantic-ai.

pydantic-ai types stop here and in ``infrastructure/providers``: the node loop,
model streaming, retry, history replay and compaction, the context window and
the run's failure classification. The Agent Host harness lives in
``agent_host/harness``.
"""
