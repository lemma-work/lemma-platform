//! Runs: accepting one, reading it back, and the target it belongs to.

use super::{
    AcceptOutcome, Command, Connection, Journal, JournalError, JournalRun, OptionalExtension,
    RunSpec, TargetJournalStatus, TransactionBehavior, Utc, Uuid, Value, enum_parse, params,
};

/// Free rather than a method, so `recoverable_runs` can read each run while
/// still holding the connection guard it opened its cursor with. The mutex is
/// not reentrant.
pub(crate) fn read_run(
    connection: &Connection,
    target_id: Uuid,
    run_id: Uuid,
) -> Result<Option<JournalRun>, JournalError> {
    {
        let row = connection
            .query_row(
                r#"
                SELECT lease_epoch, command_id, harness_key, adapter_version,
                       state, checkpoint, spec_json, provider_session_id, prompt_dispatched
                  FROM runs WHERE target_id=?1 AND run_id=?2
                "#,
                params![target_id.to_string(), run_id.to_string()],
                |row| {
                    Ok((
                        row.get::<_, i64>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, String>(3)?,
                        row.get::<_, String>(4)?,
                        row.get::<_, String>(5)?,
                        row.get::<_, String>(6)?,
                        row.get::<_, Option<String>>(7)?,
                        row.get::<_, bool>(8)?,
                    ))
                },
            )
            .optional()?;
        row.map(
            |(
                lease_epoch,
                command_id,
                harness_key,
                adapter_version,
                state,
                checkpoint,
                spec_json,
                provider_session_id,
                prompt_dispatched,
            )| {
                Ok(JournalRun {
                    target_id,
                    run_id,
                    lease_epoch: u32::try_from(lease_epoch).map_err(|_| {
                        JournalError::InvalidEnum(format!("invalid lease epoch {lease_epoch}"))
                    })?,
                    command_id: Uuid::parse_str(&command_id).map_err(|_| {
                        JournalError::InvalidEnum(format!("invalid command UUID {command_id}"))
                    })?,
                    harness_key,
                    adapter_version,
                    state: enum_parse(&state)?,
                    checkpoint: enum_parse(&checkpoint)?,
                    spec: serde_json::from_str(&spec_json)?,
                    provider_session_id,
                    prompt_dispatched,
                })
            },
        )
        .transpose()
    }
}

impl Journal {
    pub fn register_target(&self, target_id: Uuid) -> Result<(), JournalError> {
        let connection = self.connection();
        let now = Utc::now().to_rfc3339();
        connection.execute(
            r#"
            INSERT INTO targets(target_id, connection_state, updated_at)
            VALUES (?1, 'OFFLINE', ?2)
            ON CONFLICT(target_id) DO NOTHING
            "#,
            params![target_id.to_string(), now],
        )?;
        Ok(())
    }

    pub fn remove_target(&self, target_id: Uuid) -> Result<(), JournalError> {
        let mut connection = self.connection();
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let id = target_id.to_string();
        transaction.execute("DELETE FROM event_outbox WHERE target_id=?1", params![id])?;
        transaction.execute("DELETE FROM runs WHERE target_id=?1", params![id])?;
        transaction.execute(
            "DELETE FROM command_receipts WHERE target_id=?1",
            params![id],
        )?;
        transaction.execute("DELETE FROM targets WHERE target_id=?1", params![id])?;
        transaction.commit()?;
        Ok(())
    }

    pub fn update_target_state(
        &self,
        target_id: Uuid,
        state: &str,
        error: Option<&str>,
    ) -> Result<(), JournalError> {
        self.register_target(target_id)?;
        let now = Utc::now().to_rfc3339();
        let connected_at = (state == "ONLINE").then_some(now.as_str());
        self.connection().execute(
            r#"
            UPDATE targets
               SET connection_state = ?2,
                   last_error = ?3,
                   last_connected_at = COALESCE(?4, last_connected_at),
                   updated_at = ?5
             WHERE target_id = ?1
            "#,
            params![target_id.to_string(), state, error, connected_at, now],
        )?;
        Ok(())
    }

    pub fn accept_start(
        &self,
        target_id: Uuid,
        command: &Command,
        spec: &RunSpec,
        harness_key: &str,
        adapter_version: &str,
    ) -> Result<AcceptOutcome, JournalError> {
        self.register_target(target_id)?;
        let run_id = command
            .run_id
            .ok_or(JournalError::RunMissing(spec.agent_run_id))?;
        let lease_epoch = command
            .lease_epoch
            .ok_or(JournalError::RunMissing(spec.agent_run_id))?;
        if run_id != spec.agent_run_id {
            return Err(JournalError::LeaseConflict(run_id));
        }
        let mut connection = self.connection();
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let exists: bool = transaction.query_row(
            "SELECT EXISTS(SELECT 1 FROM command_receipts WHERE target_id=?1 AND command_id=?2)",
            params![target_id.to_string(), command.command_id.to_string()],
            |row| row.get(0),
        )?;
        if exists {
            transaction.commit()?;
            return Ok(AcceptOutcome::Duplicate);
        }

        let existing_run: Option<i64> = transaction
            .query_row(
                "SELECT lease_epoch FROM runs WHERE target_id=?1 AND run_id=?2",
                params![target_id.to_string(), run_id.to_string()],
                |row| row.get(0),
            )
            .optional()?;
        if existing_run.is_some_and(|epoch| epoch != i64::from(lease_epoch)) {
            return Err(JournalError::LeaseConflict(run_id));
        }

        let now = Utc::now().to_rfc3339();
        transaction.execute(
            r#"
            INSERT INTO command_receipts(
                target_id, command_id, kind, payload_digest, state,
                ack_pending, received_at, updated_at
            ) VALUES (?1, ?2, 'START_RUN', '', 'ACCEPTED', 1, ?3, ?3)
            "#,
            params![target_id.to_string(), command.command_id.to_string(), now],
        )?;
        transaction.execute(
            r#"
            INSERT INTO runs(
                target_id, run_id, lease_epoch, command_id, harness_key,
                adapter_version, state, checkpoint, checkpoint_detail,
                checkpoint_pending, spec_json, created_at, updated_at
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'ACCEPTED', 'ACCEPTED', '{}', 1, ?7, ?8, ?8)
            "#,
            params![
                target_id.to_string(),
                run_id.to_string(),
                i64::from(lease_epoch),
                command.command_id.to_string(),
                harness_key,
                adapter_version,
                serde_json::to_string(spec)?,
                now
            ],
        )?;
        transaction.commit()?;
        Ok(AcceptOutcome::New)
    }

    /// Replace a live run's Lemma MCP configuration.
    ///
    /// The credential inside it expires while long turns are still running, so
    /// Lemma sends a replacement and this is where it lands. Durable rather
    /// than held in memory because the bridge is a *separate process* that
    /// reads its endpoint from here on every request — that is the whole
    /// delivery mechanism.
    ///
    /// Fenced on the lease epoch, so a credential minted for a superseded
    /// dispatch cannot overwrite the current one.
    pub fn refresh_run_mcp(
        &self,
        target_id: Uuid,
        run_id: Uuid,
        lease_epoch: u32,
        mcp: &Value,
    ) -> Result<bool, JournalError> {
        let Some(run) = self.get_run(target_id, run_id)? else {
            return Ok(false);
        };
        if run.lease_epoch != lease_epoch || run.state.is_terminal() {
            return Ok(false);
        }
        let mut spec = run.spec;
        spec.mcp = mcp.clone();
        let connection = self.connection();
        let updated = connection.execute(
            "UPDATE runs SET spec_json=?3, updated_at=?4 \
             WHERE target_id=?1 AND run_id=?2",
            params![
                target_id.to_string(),
                run_id.to_string(),
                serde_json::to_string(&spec)?,
                Utc::now().to_rfc3339(),
            ],
        )?;
        Ok(updated > 0)
    }

    pub fn get_run(
        &self,
        target_id: Uuid,
        run_id: Uuid,
    ) -> Result<Option<JournalRun>, JournalError> {
        let connection = self.connection();
        read_run(&connection, target_id, run_id)
    }
    pub fn recoverable_runs(&self, target_id: Uuid) -> Result<Vec<JournalRun>, JournalError> {
        let connection = self.connection();
        let mut statement = connection.prepare(
            r#"
            SELECT run_id FROM runs
             WHERE target_id=?1
               AND state NOT IN ('WAITING_INPUT','SUCCEEDED','FAILED','CANCELLED','DISPATCH_UNKNOWN')
             ORDER BY created_at
            "#,
        )?;
        let run_ids = statement
            .query_map(params![target_id.to_string()], |row| {
                row.get::<_, String>(0)
            })?
            .collect::<Result<Vec<_>, _>>()?;
        run_ids
            .into_iter()
            .map(|raw| {
                let run_id = Uuid::parse_str(&raw)
                    .map_err(|_| JournalError::InvalidEnum(format!("invalid run UUID {raw}")))?;
                read_run(&connection, target_id, run_id)?.ok_or(JournalError::RunMissing(run_id))
            })
            .collect()
    }

    pub fn target_status(&self, target_id: Uuid) -> Result<TargetJournalStatus, JournalError> {
        self.register_target(target_id)?;
        let connection = self.connection();
        let (connection_state, last_error, last_connected_at) = connection.query_row(
            "SELECT connection_state, last_error, last_connected_at FROM targets WHERE target_id=?1",
            params![target_id.to_string()],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )?;
        let active_runs: i64 = connection.query_row(
            r#"
            SELECT COUNT(*) FROM runs WHERE target_id=?1
              AND state NOT IN ('WAITING_INPUT','SUCCEEDED','FAILED','CANCELLED','DISPATCH_UNKNOWN')
            "#,
            params![target_id.to_string()],
            |row| row.get(0),
        )?;
        let pending_events: i64 = connection.query_row(
            "SELECT COUNT(*) FROM event_outbox WHERE target_id=?1 AND acknowledged_at IS NULL",
            params![target_id.to_string()],
            |row| row.get(0),
        )?;
        Ok(TargetJournalStatus {
            target_id,
            connection_state,
            last_error,
            last_connected_at,
            active_runs: u64::try_from(active_runs).unwrap_or_default(),
            pending_events: u64::try_from(pending_events).unwrap_or_default(),
        })
    }
}
