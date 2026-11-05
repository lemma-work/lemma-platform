"""Schedules on the work runtime: fires, filters, target starts, outcomes.

``kinds`` names each kind and its payload; ``registration`` registers the
handlers. A process that enqueues a schedule kind imports ``registration``
(the services that enqueue do it for their callers).
"""
