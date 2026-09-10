//! Commands and checkpoints, and what is still owed to the workspace.

use super::{
    AcceptOutcome, Checkpoint, Command, CommandRejection, Journal, JournalError, JsonMap,
    PendingControl, RunCheckpoint, RunState, TransactionBehavior, Utc, Uuid, enum_json, enum_parse,
    params,
};

impl Journal {
    pub fn record_simple_command(
        &self,
        target_id: Uuid,
        command: &Command,
    ) -> Result<AcceptOutcome, JournalError> {
        self.register_target(target_id)?;
        let connection = self.connection();
        let now = Utc::now().to_rfc3339();
        let changed = connection.execute(
            r#"
            INSERT INTO command_receipts(
                target_id, command_id, kind, payload_digest, state,
                ack_pending, received_at, updated_at
            ) VALUES (?1, ?2, ?3, '', 'ACCEPTED', 1, ?4, ?4)
            ON CONFLICT(target_id, command_id) DO NOTHING
            "#,
            params![
                target_id.to_string(),
                command.command_id.to_string(),
                format!("{:?}", command.kind).to_uppercase(),
                now
            ],
        )?;
        if changed == 1 {
            return Ok(AcceptOutcome::New);
        }
        Ok(AcceptOutcome::Duplicate)
    }

    pub fn checkpoint(
        &self,
        target_id: Uuid,
        run_id: Uuid,
        lease_epoch: u32,
        state: RunState,
        detail: &JsonMap,
    ) -> Result<(), JournalError> {
        let changed = self.connection().execute(
            r#"
            UPDATE runs
               SET checkpoint=?4, state=?5, checkpoint_detail=?6,
                   checkpoint_pending=1, updated_at=?7
             WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3
            "#,
            params![
                target_id.to_string(),
                run_id.to_string(),
                i64::from(lease_epoch),
                enum_json(Checkpoint::for_state(state))?,
                enum_json(state)?,
                serde_json::to_string(detail)?,
                Utc::now().to_rfc3339()
            ],
        )?;
        if changed == 0 {
            return Err(JournalError::RunMissing(run_id));
        }
        Ok(())
    }

    pub fn mark_dispatch_intent(
        &self,
        target_id: Uuid,
        run_id: Uuid,
        lease_epoch: u32,
        provider_session_id: &str,
    ) -> Result<(), JournalError> {
        let changed = self.connection().execute(
            r#"
            UPDATE runs
               SET checkpoint='DISPATCH_INTENT', state='DISPATCHING',
                   provider_session_id=?4, prompt_dispatched=1,
                   checkpoint_pending=1, updated_at=?5
             WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3
                   AND prompt_dispatched=0
            "#,
            params![
                target_id.to_string(),
                run_id.to_string(),
                i64::from(lease_epoch),
                provider_session_id,
                Utc::now().to_rfc3339()
            ],
        )?;
        if changed == 0 {
            let exists: bool = self.connection().query_row(
                "SELECT EXISTS(SELECT 1 FROM runs WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3)",
                params![target_id.to_string(), run_id.to_string(), i64::from(lease_epoch)],
                |row| row.get(0),
            )?;
            return if exists {
                Err(JournalError::LeaseConflict(run_id))
            } else {
                Err(JournalError::RunMissing(run_id))
            };
        }
        Ok(())
    }

    pub fn pending_control(&self, target_id: Uuid) -> Result<PendingControl, JournalError> {
        let connection = self.connection();
        let mut command_statement = connection.prepare(
            "SELECT command_id FROM command_receipts WHERE target_id=?1 AND ack_pending=1 ORDER BY received_at LIMIT 256",
        )?;
        let command_ids = command_statement
            .query_map(params![target_id.to_string()], |row| {
                row.get::<_, String>(0)
            })?
            .collect::<Result<Vec<_>, _>>()?
            .into_iter()
            .map(|raw| {
                Uuid::parse_str(&raw)
                    .map_err(|_| JournalError::InvalidEnum(format!("invalid command UUID {raw}")))
            })
            .collect::<Result<Vec<_>, _>>()?;
        // Non-terminal checkpoints are also the run lease heartbeat. Keep
        // sending them after the state transition itself has been acknowledged;
        // otherwise a long-running provider turn loses its server-side lease
        // even though the Agent Host remains connected and healthy.
        let mut checkpoint_statement = connection.prepare(
            r#"
            SELECT runs.run_id, runs.lease_epoch, runs.state, runs.checkpoint_detail,
                   runs.provider_session_id
              FROM runs
             WHERE runs.target_id=?1
               AND (
                    runs.checkpoint_pending=1
                    OR runs.state NOT IN (
                        'WAITING_INPUT', 'SUCCEEDED', 'FAILED',
                        'CANCELLED', 'DISPATCH_UNKNOWN'
                    )
               )
               AND NOT (
                    runs.state IN (
                        'WAITING_INPUT', 'SUCCEEDED', 'FAILED',
                        'CANCELLED', 'DISPATCH_UNKNOWN'
                    )
                    AND EXISTS (
                        SELECT 1
                          FROM event_outbox
                         WHERE event_outbox.target_id=runs.target_id
                           AND event_outbox.run_id=runs.run_id
                           AND event_outbox.lease_epoch=runs.lease_epoch
                           AND event_outbox.acknowledged_at IS NULL
                    )
               )
             ORDER BY runs.updated_at LIMIT 256
            "#,
        )?;
        let encoded = checkpoint_statement
            .query_map(params![target_id.to_string()], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, i64>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, Option<String>>(4)?,
                ))
            })?
            .collect::<Result<Vec<_>, _>>()?;
        let checkpoints = encoded
            .into_iter()
            .map(
                |(run_id, lease_epoch, state, detail, provider_session_id)| {
                    let mut detail: JsonMap = serde_json::from_str(&detail)?;
                    // Carried on every checkpoint, not just the one written when the
                    // session opened. A run holds a single pending-checkpoint slot,
                    // so the `Running` state that follows the first streamed token
                    // overwrites the detail of the checkpoint before it - and the
                    // first token lands milliseconds after the prompt, well inside
                    // one poll. Lemma needs this id to keep the conversation on one
                    // provider session, and reads it idempotently.
                    if let Some(session_id) = provider_session_id {
                        detail.insert(
                            "provider_session_id".to_owned(),
                            serde_json::Value::String(session_id),
                        );
                    }
                    Ok(RunCheckpoint {
                        run_id: Uuid::parse_str(&run_id).map_err(|_| {
                            JournalError::InvalidEnum(format!("invalid run UUID {run_id}"))
                        })?,
                        lease_epoch: u32::try_from(lease_epoch).map_err(|_| {
                            JournalError::InvalidEnum(format!("invalid lease epoch {lease_epoch}"))
                        })?,
                        state: enum_parse(&state)?,
                        detail,
                    })
                },
            )
            .collect::<Result<Vec<_>, JournalError>>()?;
        let mut rejection_statement = connection.prepare(
            "SELECT rejection_json FROM command_rejections WHERE target_id=?1 ORDER BY created_at LIMIT 256",
        )?;
        let rejections = rejection_statement
            .query_map(params![target_id.to_string()], |row| {
                row.get::<_, String>(0)
            })?
            .collect::<Result<Vec<_>, _>>()?
            .into_iter()
            .map(|encoded| serde_json::from_str(&encoded).map_err(JournalError::from))
            .collect::<Result<Vec<_>, _>>()?;
        Ok((command_ids, checkpoints, rejections))
    }

    pub fn record_rejection(
        &self,
        target_id: Uuid,
        rejection: &CommandRejection,
    ) -> Result<(), JournalError> {
        let connection = self.connection();
        connection.execute(
            r#"
            INSERT INTO command_rejections(
                target_id, command_id, rejection_json, created_at
            ) VALUES (?1, ?2, ?3, ?4)
            ON CONFLICT(target_id, command_id) DO UPDATE SET
                rejection_json=excluded.rejection_json
            "#,
            params![
                target_id.to_string(),
                rejection.command_id.to_string(),
                serde_json::to_string(rejection)?,
                Utc::now().to_rfc3339(),
            ],
        )?;
        Ok(())
    }

    pub fn mark_control_applied(
        &self,
        target_id: Uuid,
        command_ids: &[Uuid],
        checkpoints: &[RunCheckpoint],
        rejections: &[CommandRejection],
    ) -> Result<(), JournalError> {
        let mut connection = self.connection();
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        for command_id in command_ids {
            transaction.execute(
                "UPDATE command_receipts SET ack_pending=0, state='ACKNOWLEDGED', updated_at=?3 WHERE target_id=?1 AND command_id=?2",
                params![
                    target_id.to_string(),
                    command_id.to_string(),
                    Utc::now().to_rfc3339()
                ],
            )?;
        }
        for checkpoint in checkpoints {
            transaction.execute(
                r#"
                UPDATE runs SET checkpoint_pending=0
                 WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3
                       AND state=?4
                "#,
                params![
                    target_id.to_string(),
                    checkpoint.run_id.to_string(),
                    i64::from(checkpoint.lease_epoch),
                    enum_json(checkpoint.state)?
                ],
            )?;
        }
        for rejection in rejections {
            transaction.execute(
                "DELETE FROM command_rejections WHERE target_id=?1 AND command_id=?2",
                params![target_id.to_string(), rejection.command_id.to_string()],
            )?;
        }
        transaction.commit()?;
        Ok(())
    }
}
