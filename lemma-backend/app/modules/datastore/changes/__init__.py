"""The per-pod record change log and its durable subscribers.

* ``schema`` -- the tables (pod-schema DDL version 3).
* ``cursor`` / ``reader`` -- positions, opaque cursors, and the one read path.
* ``capture`` -- the write path: slim rows always, snapshots while watched.
* ``subscribers`` / ``work`` -- durable subscribers: sync, handoff, deliver,
  retention.
* ``converter`` -- the cutover from the datastore outbox and stream.

Other modules reach this through ``app.modules.datastore.contracts.changes``.
"""
