//! A real agent finding, calling and waiting on Lemma's own tools.

use super::*;

#[tokio::test]
#[ignore = "requires authenticated Codex and spends real image-generation quota"]
async fn codex_native_image_generation_creates_a_publishable_artifact() {
    if std::env::var("LEMMA_REAL_AGENT_E2E_IMAGE").as_deref() != Ok("1") {
        eprintln!("set LEMMA_REAL_AGENT_E2E_IMAGE=1 to run the native image test");
        return;
    }
    let paths = HostPaths::under(agent_host_data_directory());
    let manifest = AdapterManifest::builtin()
        .unwrap()
        .with_cache_root(paths.adapters.clone());
    let scratch = TempDir::new().unwrap();
    let callbacks = Arc::new(StreamCapture::default());
    let run = run_with_deadline(
        AcpRunRequest {
            adapter: manifest.resolve("codex").unwrap(),
            run_spec: RunSpec {
                agent_run_id: Uuid::new_v4(),
                conversation_id: Uuid::new_v4(),
                harness_id: Uuid::new_v4(),
                profile_revision: "real-image-e2e".to_owned(),
                model_name: None,
                config_selections: JsonMap::new(),
                system_prompt: concat!(
                    "Use Codex's built-in $imagegen capability for image requests. ",
                    "Do not use Pillow, SVG, canvas, Python, shell scripts, or an ",
                    "external image CLI. Copy final images into .lemma-artifacts."
                )
                .to_owned(),
                prompt: vec![json!({
                    "type": "text",
                    "text": concat!(
                        "Create a simple square diagnostic poster with a navy ",
                        "background and the exact white text LEMMA IMAGE OK. Save ",
                        "the final PNG as .lemma-artifacts/native-image-e2e.png."
                    ),
                })],
                resume_session_id: None,
                workspace_cwd: None,
                context: JsonMap::new(),
                mcp: Value::Null,
                run_deadline: Utc::now() + chrono::Duration::minutes(10),
                system_prompt_delivery: None,
            },
            scratch_directory: scratch.path().to_path_buf(),
            mcp_server: None,
            can_load_session: false,
            published_config_options: Vec::new(),
            permissions: PermissionGate::new(),
            permission_timeout: Duration::ZERO,
            cancel: lemma_agent_host::acp::never_cancelled(),
            cancel_grace: Duration::from_secs(5),
        },
        callbacks,
    );
    let outcome = tokio::time::timeout(Duration::from_secs(600), run)
        .await
        .expect("Codex image generation timed out")
        .expect("Codex image generation failed");
    assert_eq!(outcome.state, RunState::Succeeded);

    let image = scratch
        .path()
        .join(".lemma-artifacts")
        .join("native-image-e2e.png");
    let bytes = std::fs::read(&image)
        .unwrap_or_else(|error| panic!("Codex did not create {}: {error}", image.display()));
    assert!(
        bytes.starts_with(b"\x89PNG\r\n\x1a\n"),
        "Codex image artifact is not a PNG"
    );
    println!("codex native image: {} bytes", bytes.len());
}

#[tokio::test]
#[ignore = "requires authenticated local agents and spends real provider quota"]
async fn real_agents_discover_and_call_a_lemma_mcp_tool() {
    // The hermetic suite proves the host wires an MCP bridge correctly and that
    // an agent which uses it reaches Lemma. This proves the remaining half that
    // only a real provider can: that a commercial agent, handed the `lemma`
    // server through ACP, actually discovers `lemma_*` tools and calls one.
    //
    // Lemma itself is still a stand-in; the endpoint here speaks the same
    // stateless JSON-RPC-over-HTTP contract `app/mcp_server.py` mounts.
    for agent in configured_agents() {
        let endpoint = support::LemmaMcpEndpoint::start(support::McpTransport::StatelessJson).await;
        let (_directory, control) = paired_real_run(
            &agent,
            concat!(
                "Call the Lemma MCP tool named lemma_echo with the argument ",
                "text set to exactly LEMMA_REAL_MCP_OK. Do not use any other ",
                "tool and do not write files. Reply with exactly the text the ",
                "tool returned."
            ),
            endpoint.run_configuration(),
            support::PermissionAnswer::AllowOnce,
            Duration::from_secs(300),
        )
        .await;

        let methods = endpoint.methods();
        assert!(
            methods.iter().any(|method| method == "tools/list"),
            "{agent} never listed Lemma's tools; methods={methods:?}"
        );
        let call = endpoint
            .requests()
            .into_iter()
            .find(|record| record.method == "tools/call")
            .unwrap_or_else(|| panic!("{agent} never called a Lemma tool; methods={methods:?}"));
        assert_eq!(
            call.params["name"], "lemma_echo",
            "{agent} called the wrong Lemma tool"
        );
        assert_eq!(
            call.agent_run_id.as_deref(),
            Some(control.run_id.to_string().as_str()),
            "{agent}'s tool call was not attributed to its run"
        );
        assert!(
            control.assistant_text().contains("LEMMA_REAL_MCP_OK"),
            "{agent} did not report the Lemma tool result: {:?}",
            control.assistant_text()
        );
        println!("{agent}: LEMMA_REAL_MCP_TOOL_CALL_OK");
    }
}

/// A per-run file outside every adapter's sandbox, plus the shell command that
/// writes it.
///
/// The trigger matters and was chosen from observed behaviour, not guessed.
/// Codex runs sandboxed commands — including `printf` in the workspace and even
/// `curl` to the public internet — without asking anyone, so a prompt like
/// "print this token" proves nothing about approvals. What Codex *does*
/// escalate is a write outside its workspace sandbox, and Claude Code asks for
/// any Bash at all, so a write to a private directory under `$HOME` is the one
/// action both adapters put behind a permission request.
///
/// Using a real filesystem effect rather than the agent's prose is deliberate:
/// a model can claim it ran a command, but it cannot fabricate the file.
struct GatedWrite {
    directory: PathBuf,
    path: PathBuf,
    token: String,
}

impl GatedWrite {
    fn new(agent: &str) -> Self {
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .expect("HOME is required to place a file outside the provider sandbox");
        let directory = home.join(".lemma-agent-host-e2e");
        let token = format!("LEMMA-GATED-{}", Uuid::new_v4().simple());
        Self {
            path: directory.join(format!("{agent}-{token}.txt")),
            directory,
            token,
        }
    }

    fn prompt(&self) -> String {
        format!(
            "Run this exact shell command and nothing else: \
             `mkdir -p {directory} && printf %s {token} > {path}`. \
             Then reply with exactly DONE.",
            directory = self.directory.display(),
            token = self.token,
            path = self.path.display(),
        )
    }

    /// Did the gated command actually run?
    fn happened(&self) -> bool {
        std::fs::read_to_string(&self.path).is_ok_and(|body| body.trim() == self.token)
    }
}

impl Drop for GatedWrite {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
        // Only succeeds once the last concurrent run has cleaned up.
        let _ = std::fs::remove_dir(&self.directory);
    }
}

#[tokio::test]
#[ignore = "requires authenticated local agents, spends real provider quota, and writes a self-cleaning file under $HOME"]
async fn a_real_agents_native_tool_waits_for_lemmas_approval() {
    // The approval round trip against each real adapter's own permission shape.
    // The command cannot run unless Lemma's decision reached the parked ACP
    // responder, so the file existing afterwards is proof that the agent was
    // released and continued rather than being cancelled or left hanging.
    for agent in configured_agents() {
        let gated = GatedWrite::new(&agent);
        assert!(!gated.happened(), "the gated file must not exist up front");
        let (_directory, control) = paired_real_run(
            &agent,
            &gated.prompt(),
            json!({
                "server_name": "lemma_tools",
                "url": "https://unused.invalid/mcp",
                "authorization": "Bearer unused-real-permission-e2e",
            }),
            support::PermissionAnswer::AllowOnce,
            Duration::from_secs(300),
        )
        .await;

        let requests = control.permission_requests();
        assert!(
            !requests.is_empty(),
            "{agent} never requested permission; wrote={}, answer={:?}, terminal={:?}",
            gated.happened(),
            control.assistant_text(),
            control
                .events()
                .iter()
                .find(|event| event.event_type == EventType::Terminal)
        );
        // Lemma renders these as approval cards and addresses its decision to
        // the request id, so a request missing either is unanswerable.
        for request in &requests {
            assert!(
                request.object_id.is_some(),
                "{agent}'s permission request has no id for a decision to name"
            );
            assert!(
                request.payload.contains_key("options"),
                "{agent}'s permission request offered no options to choose between"
            );
        }
        assert!(
            gated.happened(),
            "{agent} never ran the approved command, so the approval did not \
             reach it; answer={:?}",
            control.assistant_text()
        );
        println!(
            "{agent}: LEMMA_REAL_PERMISSION_APPROVED_OK ({} request(s))",
            requests.len()
        );
    }
}

#[tokio::test]
#[ignore = "requires authenticated local agents and spends real provider quota"]
async fn a_real_agents_denied_tool_is_stopped_without_waiting_out_the_timeout() {
    // A denial has to travel the same path an approval does, and it has to
    // actually stop the tool. If the decision never arrived the agent would
    // block for the full thirty-minute permission timeout, which a user cannot
    // tell apart from a hung run.
    for agent in configured_agents() {
        let gated = GatedWrite::new(&agent);
        let started = std::time::Instant::now();
        let (_directory, control) = paired_real_run(
            &agent,
            &gated.prompt(),
            json!({
                "server_name": "lemma_tools",
                "url": "https://unused.invalid/mcp",
                "authorization": "Bearer unused-real-permission-e2e",
            }),
            support::PermissionAnswer::Deny,
            Duration::from_secs(300),
        )
        .await;

        assert!(
            !control.permission_requests().is_empty(),
            "{agent} never asked, so there was nothing to deny; wrote={}, answer={:?}, terminal={:?}",
            gated.happened(),
            control.assistant_text(),
            control
                .events()
                .iter()
                .find(|event| event.event_type == EventType::Terminal)
        );
        // A blocked command is also what a host that denies everything by
        // itself produces, so require that Lemma got to decide while the agent
        // was still waiting.
        let decisions = control.decisions();
        assert!(
            !decisions.is_empty() && decisions.iter().any(|decision| !decision.saw_terminal),
            "{agent}'s run was already over before Lemma answered, so this \
             denial did not come from Lemma: {decisions:?}"
        );
        assert!(!gated.happened(), "{agent} ran the command Lemma refused");
        assert!(
            started.elapsed() < Duration::from_secs(300),
            "{agent} waited out the permission timeout instead of being denied"
        );
        let terminal = control
            .events()
            .into_iter()
            .find(|event| event.event_type == EventType::Terminal)
            .unwrap();
        assert_ne!(
            terminal.payload["state"], "DISPATCH_UNKNOWN",
            "{agent}'s denied run must reach a definite outcome"
        );
        println!(
            "{agent}: LEMMA_REAL_PERMISSION_DENIED_OK in {:?} ({})",
            started.elapsed(),
            terminal.payload["state"]
        );
    }
}

/// Parking, with a real commercial agent on the other end.
///
/// The hermetic suite proves the bridge waits and rewrites the frame. Only a
/// real provider can prove the part that matters to a person: that an agent
/// handed a parked tool result *stays in its turn* while someone decides, and
/// then uses their answer — rather than treating the slow tool as a failure,
/// giving up, or answering from its own head.
///
/// The stand-in withholds the decision for two polls, so the agent genuinely
/// waits rather than being handed an answer that happened to be ready.
#[tokio::test]
#[ignore = "requires authenticated local agents and spends real provider quota"]
async fn a_real_agent_waits_inside_its_turn_for_a_parked_tool() {
    for agent in configured_agents() {
        let endpoint = support::LemmaMcpEndpoint::start(support::McpTransport::StatelessJson).await;
        let (_directory, control) = paired_real_run(
            &agent,
            concat!(
                "Call the Lemma MCP tool named lemma_park with no arguments. ",
                "It may take a while to answer; wait for it. Do not use any ",
                "other tool and do not write files. When it returns, reply ",
                "with exactly the value it gives for the key 'Pick one'."
            ),
            endpoint.run_configuration(),
            support::PermissionAnswer::AllowOnce,
            Duration::from_secs(300),
        )
        .await;

        let call = endpoint
            .requests()
            .into_iter()
            .find(|record| record.method == "tools/call")
            .unwrap_or_else(|| panic!("{agent} never called the parked tool"));
        assert_eq!(call.params["name"], support::PARK_TOOL);
        // The bridge really held the response open rather than handing the
        // placeholder straight to the agent.
        assert!(
            endpoint.interaction_polls() > 2,
            "{agent}'s bridge did not wait: {} poll(s)",
            endpoint.interaction_polls()
        );
        // And the agent used the person's answer, which it could only have
        // received as that tool's return.
        assert!(
            control.assistant_text().contains("Blue"),
            "{agent} did not use the parked answer: {:?}",
            control.assistant_text()
        );
        println!("{agent}: LEMMA_REAL_PARKED_TOOL_OK");
    }
}

/// Waking up, with a real agent on the other end.
///
/// A woken run adds no user message, so Lemma prompts it with the return it
/// synthesized for the `snooze` call it resolved — rendered exactly as
/// `remote_payload._render_history` writes it. The hermetic tests prove that
/// return is chosen and rendered. Only a real provider can prove the part that
/// decides whether the feature works: that an agent resuming its own session
/// reads a tool result arriving as a fresh prompt as *its own call returning*,
/// and carries on with what it was doing — rather than treating it as a new
/// request, apologising for a tool it does not remember calling, or starting
/// the task over.
///
/// The subject is the tell, and it appears only in the first turn. An answer
/// that names it came from the resumed session; the wake prompt says nothing
/// about what the agent was waiting for.
#[tokio::test]
#[ignore = "requires authenticated local agents and spends real provider quota"]
async fn a_real_agent_wakes_and_carries_on_where_it_slept() {
    let paths = HostPaths::under(agent_host_data_directory());
    let manifest = AdapterManifest::builtin()
        .unwrap()
        .with_cache_root(paths.adapters.clone());

    for agent in configured_agents() {
        let conversation_id = Uuid::new_v4();
        let workspace = conversation_workspace(&paths, &agent, conversation_id);
        let (session_id, _) = one_turn(
            &manifest,
            &workspace,
            &agent,
            conversation_id,
            "You are waiting for the Fenwick deployment to finish. It is not \
             ready yet, so you called the snooze tool to sleep. Reply with \
             only: sleeping.",
            None,
        )
        .await;

        // Exactly the shape `_render_history` produces for the return the wake
        // writes, prompted into the session the agent slept in. Deliberately
        // says nothing about the subject: everything the agent knows about what
        // it was doing has to come from the session it is resuming.
        let (resumed_session_id, answer) = one_turn(
            &manifest,
            &workspace,
            &agent,
            conversation_id,
            "TOOL:\nTool result snooze(lemma-mcp-1):\n{\n  \"success\": true,\n  \
             \"woke_because\": \"TIMER\",\n  \"slept_seconds\": 600,\n  \
             \"note_to_self\": \"name what you were waiting for, in one \
             sentence\",\n  \"message\": \"Your time elapsed. That is all this \
             means - check whatever you were waiting for before acting as \
             though it happened.\"\n}",
            Some(session_id.clone()),
        )
        .await;

        assert_eq!(
            resumed_session_id, session_id,
            "{agent} woke in a different session than the one it slept in"
        );
        assert!(
            answer.to_lowercase().contains("fenwick"),
            "{agent} did not carry on from where it slept; it answered {answer:?}"
        );
        println!("{agent}: LEMMA_REAL_WAKE_OK -> {answer:?}");
    }
}
