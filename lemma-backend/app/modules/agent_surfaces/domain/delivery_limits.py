"""How often a failure is retried, and by whom: once per kind of failure, at most 3.

Retries multiply when they stack. An adapter retrying a send, a job retrying the
run that made it, and an inbox redelivering the event that started the job would
each repeat the last -- three layers of three is nine sends to an API that is
already failing, and the inbox default of ten made it worse than that.

The rule that prevents it is that a failure is retried by the layer that owns it
and by no layer above:

- **A send** is retried by the adapter, which is closest to the failure and the
  only layer that can tell a rate limit carrying ``Retry-After`` from a permanent
  4xx. Once it gives up, ``deliver`` raises ``AgentSurfacePlatformError``, which
  the inbox treats as terminal, so nothing above repeats the send.
- **Anything before a send** -- a database or queue blip while preparing or
  enqueueing a message -- is retried by the inbox or the job that hit it, with no
  send underneath to multiply against.

Every count is bounded by ``MAX_ATTEMPTS``, first try included.

Redelivery after a *crash* is not a retry of an error: the inbox still hands an
entry that was claimed and never finished to the next consumer.
"""

#: The most times one thing is attempted, at any layer, first try included.
MAX_ATTEMPTS = 3

#: An outbound send, retried by the adapter.
MAX_DELIVERY_ATTEMPTS = MAX_ATTEMPTS

#: An inbox consumer or the surface job, for failures that are not sends.
CONSUMER_ATTEMPTS = MAX_ATTEMPTS
