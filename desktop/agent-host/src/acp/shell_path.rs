//! Keeping the run's `lemma` first on `PATH` inside the agent's own shells.
//!
//! Every harness's process gets the chosen CLI's `bin/` first on `PATH`
//! (`session_options`). That is not where commands run, though: Codex, `OpenCode`
//! and Claude Code run each command through the person's shell, and that shell
//! reads the person's startup files first. A `~/.zshenv` or `~/.bashrc` that
//! does `export PATH="$HOME/.local/bin:$PATH"` -- the usual way a globally
//! installed `lemma` gets on `PATH` -- puts that copy back in front, and
//! Codex's shell snapshot can drop the host's entry altogether. The agent then
//! runs whatever older `lemma` the person installed, against a server it may
//! not match.
//!
//! So the agent's shells are given a last word, after the person's files:
//!
//! - **zsh** (macOS's default): `ZDOTDIR` names a folder of four startup files.
//!   Each sources the person's own file of the same name, from the folder zsh
//!   would have used, with `ZDOTDIR` set to it while it runs -- so the person's
//!   setup loads exactly as it would -- and then moves the CLI's `bin/` to the
//!   front. The technique terminal shell integrations use.
//! - **bash**: `BASH_ENV`, which bash reads after its profile files for every
//!   non-interactive shell, `bash -c` and `bash -lc` alike.
//!
//! Other shells (fish, nu) get the process `PATH` only. Nothing here applies
//! when no CLI was chosen for the run: the environment is left as it was.
//!
//! The variables these files read are named `LEMMAHOST_*`, not `LEMMA_*`:
//! a server's `LEMMA_*` reach the agent's environment (`run_environment`), and
//! which folder a shell sources, or which `bin/` goes first, is not a server's
//! to say. They are set by this host on every run that has a CLI, over
//! anything inherited.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// The chosen CLI's `bin/`.
pub(crate) const CLI_BIN: &str = "LEMMAHOST_CLI_BIN";
/// The folder the person's own zsh startup files are in.
pub(crate) const USER_ZDOTDIR: &str = "LEMMAHOST_USER_ZDOTDIR";

const ZSH_FILES: [&str; 4] = [".zshenv", ".zprofile", ".zshrc", ".zlogin"];

/// Moves `$LEMMAHOST_CLI_BIN` to the front of `PATH`, however the person's
/// files left it. POSIX, so bash and zsh both run it.
const MOVE_TO_FRONT: &str = r#"if [ -n "${LEMMAHOST_CLI_BIN:-}" ]; then
  __lemmahost_rest=""
  __lemmahost_ifs="$IFS"; IFS=:
  for __lemmahost_entry in $PATH; do
    [ "$__lemmahost_entry" = "$LEMMAHOST_CLI_BIN" ] && continue
    __lemmahost_rest="${__lemmahost_rest:+$__lemmahost_rest:}$__lemmahost_entry"
  done
  IFS="$__lemmahost_ifs"
  PATH="$LEMMAHOST_CLI_BIN${__lemmahost_rest:+:$__lemmahost_rest}"
  export PATH
  unset __lemmahost_rest __lemmahost_ifs __lemmahost_entry
fi
"#;

fn zsh_file(name: &str) -> String {
    // zsh does not split `$PATH` on `IFS` unless asked; `emulate -L sh` in an
    // anonymous function makes the shared POSIX snippet behave as in bash.
    let history = if name == ".zshrc" {
        // macOS's /etc/zshrc names the history file after `ZDOTDIR`, which
        // was this folder when it ran. Interactive history belongs with the
        // person's own.
        "if [[ \"${HISTFILE:-}\" == \"$__lemmahost_zdotdir/.zsh_history\" ]]; then\n  HISTFILE=\"$LEMMAHOST_USER_ZDOTDIR/.zsh_history\"\nfi\n"
    } else {
        ""
    };
    format!(
        "# Written by Lemma Agent Host (acp/shell_path.rs). Loads your own {name},\n\
         # then puts this run's lemma CLI first on PATH.\n\
         __lemmahost_zdotdir=\"$ZDOTDIR\"\n\
         ZDOTDIR=\"${{LEMMAHOST_USER_ZDOTDIR:-$HOME}}\"\n\
         [[ -r \"$ZDOTDIR/{name}\" ]] && source \"$ZDOTDIR/{name}\"\n\
         # A person's .zshenv may move their own ZDOTDIR; the later files follow it.\n\
         LEMMAHOST_USER_ZDOTDIR=\"$ZDOTDIR\"\n\
         export LEMMAHOST_USER_ZDOTDIR\n\
         ZDOTDIR=\"$__lemmahost_zdotdir\"\n\
         {history}\
         () {{\n\
         emulate -L sh\n\
         {MOVE_TO_FRONT}}}\n\
         unset __lemmahost_zdotdir\n"
    )
}

fn bash_env() -> String {
    format!(
        "# Written by Lemma Agent Host (acp/shell_path.rs). Read by every\n\
         # non-interactive bash after its profile files: puts this run's lemma\n\
         # CLI first on PATH.\n{MOVE_TO_FRONT}"
    )
}

/// Writes the startup files into `folder` (replacing what is there, so a
/// newer host's take effect) and returns their paths: the zsh folder and the
/// bash file.
fn write_files(folder: &Path) -> std::io::Result<(PathBuf, PathBuf)> {
    let zsh = folder.join("zsh");
    std::fs::create_dir_all(&zsh)?;
    for name in ZSH_FILES {
        write_if_changed(&zsh.join(name), &zsh_file(name))?;
    }
    let bash = folder.join("bash_env.sh");
    write_if_changed(&bash, &bash_env())?;
    Ok((zsh, bash))
}

/// Written whole, then renamed over the old one, so a shell starting at the
/// same moment never reads half a file.
fn write_if_changed(path: &Path, content: &str) -> std::io::Result<()> {
    if std::fs::read_to_string(path).is_ok_and(|current| current == content) {
        return Ok(());
    }
    let partial = path.with_extension(format!("partial-{}", uuid::Uuid::new_v4()));
    std::fs::write(&partial, content)?;
    std::fs::rename(&partial, path)
}

/// Where the files live: the host's own temporary folder, per user.
#[must_use]
pub(crate) fn default_folder() -> PathBuf {
    std::env::temp_dir().join("lemma-agent-host-shell")
}

/// The variables that keep `bin` first in the agent's shells, to set over the
/// adapter's environment. Empty when the files cannot be written, in which
/// case the process `PATH` is all the run gets, and the log says why.
#[must_use]
pub(crate) fn environment(bin: &Path, folder: &Path) -> BTreeMap<String, String> {
    let mut environment = BTreeMap::new();
    if cfg!(windows) {
        return environment;
    }
    let (zsh, bash) = match write_files(folder) {
        Ok(paths) => paths,
        Err(error) => {
            tracing::warn!(
                %error,
                folder = %folder.display(),
                "could not write the shell startup files; a lemma the person installed may come first in the agent's shells"
            );
            return environment;
        }
    };
    // Empty means `$HOME`, as zsh itself reads it, in the agent's shell.
    let user_zdotdir = std::env::var("ZDOTDIR")
        .ok()
        .filter(|value| Path::new(value) != zsh)
        .unwrap_or_default();
    environment.insert(CLI_BIN.to_owned(), bin.display().to_string());
    environment.insert(USER_ZDOTDIR.to_owned(), user_zdotdir);
    environment.insert("ZDOTDIR".to_owned(), zsh.display().to_string());
    environment.insert("BASH_ENV".to_owned(), bash.display().to_string());
    environment
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    /// A home whose startup files put a person's own `lemma` first, the way
    /// a global install does, in every file zsh and bash read.
    fn home_preferring_its_own_lemma(base: &Path) -> (PathBuf, PathBuf) {
        let home = base.join("home");
        let global = home.join(".local/bin");
        std::fs::create_dir_all(&global).unwrap();
        let prepend = "export PATH=\"$HOME/.local/bin:$PATH\"\n";
        for file in [
            ".zshenv",
            ".zprofile",
            ".zshrc",
            ".zlogin",
            ".bashrc",
            ".bash_profile",
            ".profile",
        ] {
            std::fs::write(home.join(file), prepend).unwrap();
        }
        executable(&global.join("lemma"));
        (home, global)
    }

    fn executable(path: &Path) {
        use std::os::unix::fs::PermissionsExt;
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, "#!/bin/sh\n").unwrap();
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755)).unwrap();
    }

    fn which_lemma(
        shell: &str,
        args: &[&str],
        home: &Path,
        extra: &BTreeMap<String, String>,
        path: &str,
    ) -> String {
        let output = std::process::Command::new(shell)
            .args(args)
            .arg("command -v lemma")
            .env_clear()
            .env("HOME", home)
            .env("PATH", path)
            .envs(extra)
            .output()
            .unwrap();
        String::from_utf8_lossy(&output.stdout).trim().to_owned()
    }

    #[test]
    fn the_runs_cli_wins_over_the_persons_startup_files_in_every_shell() {
        let directory = tempfile::tempdir().unwrap();
        let base = std::fs::canonicalize(directory.path()).unwrap();
        let (home, global) = home_preferring_its_own_lemma(&base);
        let bin = base.join("cli/bin");
        executable(&bin.join("lemma"));
        let path = format!("{}:/usr/bin:/bin", bin.display());
        let shims = environment(&bin, &base.join("shell"));
        let chosen = bin.join("lemma").display().to_string();
        let theirs = global.join("lemma").display().to_string();

        for (shell, args) in [
            ("/bin/zsh", &["-c"][..]),
            ("/bin/zsh", &["-lc"][..]),
            ("/bin/zsh", &["-ilc"][..]),
            ("/bin/bash", &["-c"][..]),
            ("/bin/bash", &["-lc"][..]),
        ] {
            if !Path::new(shell).is_file() {
                continue;
            }
            // Without the files, the person's own copy wins: the bug.
            assert_eq!(
                which_lemma(shell, args, &home, &BTreeMap::new(), &path),
                if shell.ends_with("bash") && args == ["-c"] {
                    chosen.clone()
                } else {
                    theirs.clone()
                },
                "{shell} {args:?} without the startup files"
            );
            assert_eq!(
                which_lemma(shell, args, &home, &shims, &path),
                chosen,
                "{shell} {args:?}"
            );
        }
    }

    /// The person's setup still loads: what their files define is there.
    #[test]
    fn the_persons_own_zsh_files_still_load() {
        let directory = tempfile::tempdir().unwrap();
        let base = std::fs::canonicalize(directory.path()).unwrap();
        let home = base.join("home");
        std::fs::create_dir_all(&home).unwrap();
        std::fs::write(home.join(".zshenv"), "export FROM_ZSHENV=yes\n").unwrap();
        std::fs::write(home.join(".zprofile"), "export FROM_ZPROFILE=yes\n").unwrap();
        let bin = base.join("cli/bin");
        executable(&bin.join("lemma"));
        let shims = environment(&bin, &base.join("shell"));
        if !Path::new("/bin/zsh").is_file() {
            return;
        }
        let output = std::process::Command::new("/bin/zsh")
            .args(["-lc", "echo $FROM_ZSHENV $FROM_ZPROFILE"])
            .env_clear()
            .env("HOME", &home)
            .env("PATH", "/usr/bin:/bin")
            .envs(&shims)
            .output()
            .unwrap();
        assert_eq!(String::from_utf8_lossy(&output.stdout).trim(), "yes yes");
    }

    #[test]
    fn the_files_are_rewritten_only_when_they_change() {
        let directory = tempfile::tempdir().unwrap();
        let folder = directory.path().join("shell");
        let bin = directory.path().join("bin");
        let first = environment(&bin, &folder);
        let written = std::fs::metadata(folder.join("zsh/.zshenv"))
            .unwrap()
            .modified()
            .unwrap();
        assert_eq!(environment(&bin, &folder), first);
        assert_eq!(
            std::fs::metadata(folder.join("zsh/.zshenv"))
                .unwrap()
                .modified()
                .unwrap(),
            written
        );
        assert_eq!(first[CLI_BIN], bin.display().to_string());
        assert_eq!(
            first["BASH_ENV"],
            folder.join("bash_env.sh").display().to_string()
        );
        assert_eq!(first["ZDOTDIR"], folder.join("zsh").display().to_string());
    }
}
