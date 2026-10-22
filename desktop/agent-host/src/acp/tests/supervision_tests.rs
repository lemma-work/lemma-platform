use super::without_lemmas_own_warnings;

/// What the Claude Agent SDK printed, byte for byte, for a session whose
/// `allowedTools` named one of Lemma's tools, cut to its first sentence.
const SDK_WARNING: &str = "(node:21543) [CLAUDE_SDK_CAN_USE_TOOL_SHADOWED] Warning: canUseTool will not be invoked for: mcp__lemma_tools__lemma_echo. Bare allowedTools entries auto-approve the whole tool before the callback is consulted.";
const NODE_HINT: &str = "(Use `node --trace-warnings ...` to show where the warning was created)";

/// Every Claude Code run with Lemma's tools prints the SDK's warning about
/// them; a crash quoted it as the agent's own error, ahead of the real one.
#[test]
fn a_crashed_agents_error_leaves_out_the_warning_about_lemmas_own_rules() {
    let tail =
        format!("{SDK_WARNING}\n{NODE_HINT}\nfile:///adapter/dist/acp-agent.js:1\nError: boom\n");
    assert_eq!(
        without_lemmas_own_warnings(&tail),
        "file:///adapter/dist/acp-agent.js:1\nError: boom"
    );
    // Nothing else on stderr: the error is the exit status alone.
    assert_eq!(
        without_lemmas_own_warnings(&format!("{SDK_WARNING}\n{NODE_HINT}\n")),
        ""
    );
}

/// Any other warning is the agent's own, and so is Node's hint after it.
#[test]
fn the_agents_own_warnings_stay_in_its_error() {
    let deprecation =
        "(node:21543) [DEP0040] DeprecationWarning: The `punycode` module is deprecated.";
    let hint = "(Use `node --trace-deprecation ...` to show where the warning was created)";
    let tail = format!("{deprecation}\n{hint}\n{SDK_WARNING}\nError: boom");
    assert_eq!(
        without_lemmas_own_warnings(&tail),
        format!("{deprecation}\n{hint}\nError: boom")
    );
    let other = format!("(node:1) [OTHER] Warning: something\n{NODE_HINT}\nError: boom");
    assert_eq!(without_lemmas_own_warnings(&other), other);
}
