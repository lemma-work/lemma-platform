//! A run's events: appending, acknowledging, and what is kept.

use super::{
    DateTime, Event, EventAck, EventBatch, EventType, Journal, JournalError, JsonMap,
    OptionalExtension, RunState, TransactionBehavior, Utc, Uuid, enum_parse, params,
};

impl Journal {
    pub fn append_event(
        &self,
        target_id: Uuid,
        run_id: Uuid,
        lease_epoch: u32,
        event_type: EventType,
        object_id: Option<String>,
        payload: JsonMap,
    ) -> Result<Event, JournalError> {
        let mut connection = self.connection();
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let sequence: i64 = transaction
            .query_row(
                r#"
                SELECT next_sequence
                  FROM runs WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3
                "#,
                params![
                    target_id.to_string(),
                    run_id.to_string(),
                    i64::from(lease_epoch)
                ],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(JournalError::RunMissing(run_id))?;
        let event = Event {
            run_id,
            lease_epoch,
            sequence: u64::try_from(sequence)
                .map_err(|_| JournalError::InvalidEnum("negative sequence".to_owned()))?,
            event_type,
            object_id,
            payload,
        };
        transaction.execute(
            r#"
            INSERT INTO event_outbox(
                target_id, run_id, lease_epoch, sequence,
                event_json, created_at
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)
            "#,
            params![
                target_id.to_string(),
                run_id.to_string(),
                i64::from(lease_epoch),
                sequence,
                serde_json::to_string(&event)?,
                Utc::now().to_rfc3339()
            ],
        )?;
        transaction.execute(
            "UPDATE runs SET next_sequence=next_sequence+1, updated_at=?4 WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3",
            params![
                target_id.to_string(),
                run_id.to_string(),
                i64::from(lease_epoch),
                Utc::now().to_rfc3339()
            ],
        )?;
        transaction.commit()?;
        Ok(event)
    }

    /// Visit a run's retained events, including acknowledged rows, in order.
    /// Recovery needs those rows even when the receiver already saw their
    /// transient chunks; visit incrementally instead of loading the whole log.
    pub fn visit_run_events(
        &self,
        target_id: Uuid,
        run_id: Uuid,
        lease_epoch: u32,
        mut visit: impl FnMut(Event),
    ) -> Result<(), JournalError> {
        let connection = self.connection();
        let mut statement = connection.prepare(
            "SELECT event_json FROM event_outbox WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3 ORDER BY sequence",
        )?;
        let mut rows = statement.query(params![
            target_id.to_string(),
            run_id.to_string(),
            i64::from(lease_epoch)
        ])?;
        while let Some(row) = rows.next()? {
            let encoded: String = row.get(0)?;
            visit(serde_json::from_str(&encoded)?);
        }
        Ok(())
    }

    pub fn pending_events(
        &self,
        target_id: Uuid,
        limit: usize,
    ) -> Result<Vec<EventBatch>, JournalError> {
        let connection = self.connection();
        let mut statement = connection.prepare(
            r#"
            SELECT event_json FROM event_outbox
             WHERE target_id=?1 AND acknowledged_at IS NULL
             -- By age first, then by run, so two concurrent runs interleave
             -- fairly. Ordering by `run_id` sorted by a UUID: the lexically
             -- lower run's whole backlog went first, and a busy one could hold
             -- back the other's terminal event for a full pass while the
             -- conversation showed nothing. `sequence` still orders within a
             -- run, which is what the contiguity check depends on.
             ORDER BY created_at, run_id, lease_epoch, sequence
             LIMIT ?2
            "#,
        )?;
        let events = statement
            .query_map(
                params![
                    target_id.to_string(),
                    i64::try_from(limit).unwrap_or(i64::MAX)
                ],
                |row| row.get::<_, String>(0),
            )?
            .collect::<Result<Vec<_>, _>>()?
            .into_iter()
            .map(|encoded| serde_json::from_str::<Event>(&encoded))
            .collect::<Result<Vec<_>, _>>()?;
        let mut batches = Vec::<EventBatch>::new();
        for event in events {
            if let Some(batch) = batches.last_mut()
                && batch.events.first().is_some_and(|first| {
                    first.run_id == event.run_id
                        && first.lease_epoch == event.lease_epoch
                        && first.sequence + u64::try_from(batch.events.len()).unwrap_or(0)
                            == event.sequence
                        && batch.events.len() < 256
                })
            {
                batch.events.push(event);
            } else {
                batches.push(EventBatch {
                    events: vec![event],
                });
            }
        }
        Ok(batches)
    }

    /// Record the backend's watermark, keeping the acknowledged events until
    /// the run terminalizes.
    ///
    /// The acknowledged copy is what makes a resend possible. The event stream
    /// on the server side is transient transport with no persistence: after a
    /// flush, eviction, or restart its watermark drops back to zero and it
    /// expects sequence 1 again. Deleting on acknowledgement threw away the
    /// only copy that could answer that, so the run could never be replayed and
    /// stayed permanently rejected. Retention is bounded by the run's own
    /// lifetime, not by the journal's.
    pub fn acknowledge_events(&self, target_id: Uuid, ack: &EventAck) -> Result<(), JournalError> {
        let mut connection = self.connection();
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let state: Option<String> = transaction
            .query_row(
                "SELECT state FROM runs WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3",
                params![
                    target_id.to_string(),
                    ack.run_id.to_string(),
                    i64::from(ack.lease_epoch)
                ],
                |row| row.get(0),
            )
            .optional()?;
        let Some(state) = state else {
            return Err(JournalError::AckMismatch);
        };
        transaction.execute(
            r#"
            UPDATE event_outbox
               SET acknowledged_at=?5
             WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3
                   AND sequence<=?4 AND acknowledged_at IS NULL
            "#,
            params![
                target_id.to_string(),
                ack.run_id.to_string(),
                i64::from(ack.lease_epoch),
                i64::try_from(ack.acked_through).unwrap_or(i64::MAX),
                Utc::now().to_rfc3339()
            ],
        )?;
        // A terminal run will never be replayed: the server refuses events for
        // one, so its acknowledged rows have no further purpose.
        if enum_parse::<RunState>(&state).is_ok_and(RunState::is_terminal) {
            transaction.execute(
                r#"
                DELETE FROM event_outbox
                 WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3
                       AND acknowledged_at IS NOT NULL
                "#,
                params![
                    target_id.to_string(),
                    ack.run_id.to_string(),
                    i64::from(ack.lease_epoch)
                ],
            )?;
        }
        transaction.commit()?;
        Ok(())
    }

    /// Make every retained event for one run pending again.
    ///
    /// Used when the server rejects a batch because its stream no longer holds
    /// the history the sequence numbers assume. Resending from the start is
    /// safe: the server deduplicates by sequence and keeps the first write.
    /// Returns how many events were re-queued.
    pub fn rewind_acknowledgements(
        &self,
        target_id: Uuid,
        run_id: Uuid,
        lease_epoch: u32,
    ) -> Result<u64, JournalError> {
        let changed = self.connection().execute(
            r#"
            UPDATE event_outbox SET acknowledged_at=NULL
             WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3
                   AND acknowledged_at IS NOT NULL
            "#,
            params![
                target_id.to_string(),
                run_id.to_string(),
                i64::from(lease_epoch)
            ],
        )?;
        Ok(u64::try_from(changed).unwrap_or_default())
    }

    /// Give up on delivering one run's events.
    ///
    /// The rows are marked delivered without ever reaching the server, so a run
    /// whose stream the server keeps refusing stops blocking its own terminal
    /// checkpoint and stops being retried on every flush. Returns how many
    /// events were dropped.
    pub fn discard_events(
        &self,
        target_id: Uuid,
        run_id: Uuid,
        lease_epoch: u32,
    ) -> Result<u64, JournalError> {
        let changed = self.connection().execute(
            r#"
            DELETE FROM event_outbox
             WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3
            "#,
            params![
                target_id.to_string(),
                run_id.to_string(),
                i64::from(lease_epoch)
            ],
        )?;
        Ok(u64::try_from(changed).unwrap_or_default())
    }

    pub fn cleanup_retained(&self, now: DateTime<Utc>) -> Result<u64, JournalError> {
        let mut connection = self.connection();
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let cutoff = (now - chrono::Duration::days(30)).to_rfc3339();
        let terminal_states = "'WAITING_INPUT','SUCCEEDED','FAILED','CANCELLED','DISPATCH_UNKNOWN'";
        let old_terminal_runs = format!(
            "SELECT target_id, run_id FROM runs \
             WHERE state IN ({terminal_states}) AND updated_at < ?1"
        );

        let mut deleted = 0_u64;
        deleted += u64::try_from(transaction.execute(
            &format!("DELETE FROM event_outbox WHERE (target_id, run_id) IN ({old_terminal_runs})"),
            params![cutoff],
        )?)
        .unwrap_or_default();
        deleted += u64::try_from(transaction.execute(
            &format!("DELETE FROM runs WHERE state IN ({terminal_states}) AND updated_at < ?1"),
            params![cutoff],
        )?)
        .unwrap_or_default();
        // Receipts for retained active runs are still referenced by `runs`, so
        // the NOT EXISTS guard protects them even if their own timestamp is old.
        deleted += u64::try_from(transaction.execute(
            r#"
            DELETE FROM command_receipts
             WHERE updated_at < ?1
               AND NOT EXISTS (
                    SELECT 1 FROM runs
                     WHERE runs.target_id=command_receipts.target_id
                       AND runs.command_id=command_receipts.command_id
               )
            "#,
            params![cutoff],
        )?)
        .unwrap_or_default();
        deleted += u64::try_from(transaction.execute(
            "DELETE FROM command_rejections WHERE created_at < ?1",
            params![cutoff],
        )?)
        .unwrap_or_default();
        transaction.commit()?;
        // Nothing closes this connection until the process exits, and it was
        // closing the last connection that used to checkpoint and truncate the
        // WAL. Retention already runs on a timer and has just freed the pages
        // this reclaims, so it is the natural place to do it deliberately.
        // TRUNCATE can find readers mid-query; that is not a cleanup failure.
        if let Err(error) = connection.pragma_update(None, "wal_checkpoint", "TRUNCATE") {
            tracing::debug!(%error, "journal WAL checkpoint deferred");
        }
        Ok(deleted)
    }
}
