"""An answer a run gave before the person said more.

The person's next message can arrive as a run is finishing: it is steered into
the run in flight (``capabilities/pending_user_messages``), which goes on to
answer it too. The answer the run had already written was then taken for
narration -- not final, since the run went on -- and never sent: somebody who
said two things heard back once, about the second.

Such an answer is marked as given before a steer. It stays out of the run's
output, whose final answer is still the last, and is shipped as a message of
its own ahead of the final one (``contracts/run_replies``).

A retry is not a steer. A response the model is sent back to try again -- its
output refused, or a tool it named that does not exist -- is followed by a
retry, not by the person's words, and its text stays narration.
"""

from __future__ import annotations

#: The message metadata key marking an answer given before a steer.
ANSWERED_BEFORE_STEER = "answered_before_steer"
#: The most of them one run ships: past that the person is talking over a run
#: that keeps answering, and the final answer covers the rest.
MAX_STEERED_ANSWERS = 5
