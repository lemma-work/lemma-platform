//! The second distribution, which holds everything an upgrade must not
//! throw away.

use super::*;

/// What to collect from a guest that has just failed.
///
/// Held in `guest-diagnostics.sh` rather than inline, because both ends need
/// the same text and neither can import the other: Windows runs it through
/// `wsl.exe`, and macOS has no exec channel at all, so `lemma-guestd` compiles
/// the same file in and runs it from inside the guest. One file, included
/// twice, is the only arrangement where the two cannot drift.
///
/// This used to be `journalctl --lines 300`, and on Windows it collected
/// nothing at all, ever. The guest ships `/etc/wsl.conf` with
/// `systemd=false` -- deliberately, because `lemma-runtime-init` starts
/// containerd itself -- so there is no journal to read. Every Windows failure
/// wrote the literal text "-- No entries --" into `logs/guest.log` and that
/// was the whole record. A start that failed because the guest reported an
/// unreachable address left nothing on the machine to say so.
///
/// So: the logs the guest actually writes, and the two pieces of state that
/// explain most of what goes wrong here -- what addresses the guest has, and
/// which containers are up. Bounded per file, and `2>&1` throughout, because a
/// collector that fails halfway is still worth what it printed first.
/// The gate between an upgrade and somebody's work.
///
/// `--unregister` deletes a distribution's ext4.vhdx and everything in it, and
/// the upgrade path calls it deliberately. What makes that safe is only that
/// the data is already somewhere else, so this asks -- and the answer comes
/// from the holder itself, not from having just run the migration and assumed
/// it worked, and not from a file on the Windows side that could be left over
/// from an installation that no longer exists.
#[cfg(any(windows, test))]
pub(crate) fn refuse_replacement_without_holder(holder_ready: bool) -> io::Result<()> {
    if holder_ready {
        return Ok(());
    }
    Err(io::Error::other(
        "Lemma will not replace its private runtime until your workspaces and \
         databases have moved out of it. Start Lemma again to retry; nothing \
         has been changed.",
    ))
}

/// Publish the data distribution's storage into the shared namespace.
///
/// `/mnt/wsl` is a tmpfs every distribution in the WSL VM sees, and a bind
/// made into it from one is usable from the others. This is how Lemma's data
/// reaches the runtime distribution without a second disk and without asking
/// for administrator rights.
///
/// Idempotent, and run on every start rather than once at import: the mount
/// belongs to the VM, so `wsl --shutdown` or a reboot removes it while leaving
/// the distribution registered.
#[cfg(any(windows, test))]
pub(crate) const PUBLISH_DATA_SHARE: &str = "\
set -eu
mkdir -p /data /mnt/wsl/lemma-data
if ! /usr/bin/mountpoint -q /mnt/wsl/lemma-data; then
  /usr/bin/mount --bind /data /mnt/wsl/lemma-data
fi
";

/// Move an existing installation's data out of the runtime distribution.
///
/// Runs inside whichever distribution holds the data today. Everything before
/// the holder existed lives on that distribution's own disk at these four
/// paths; the holder's marker is written last, so an interrupted copy is
/// retried rather than mistaken for a finished one -- and the replacement that
/// deletes the old distribution refuses until that marker exists.
///
/// `cp -a` rather than a move: the source distribution is about to be deleted
/// anyway on the upgrade path, and on the path where it is not, leaving the
/// old copy in place costs disk and keeps a way back.
///
/// The container store is deliberately not among them, and this is not an
/// oversight. `lemma-bind-data` discards a store whose recorded metadata
/// generation is not the current one, and the old layout recorded none at all
/// -- it predates that file -- so the store is rebuilt on the first start
/// after the move whatever we do. Copying it first would move several
/// gigabytes, measured at 3.6 GB on the machine this was written against, to
/// delete them a second later. Images are re-pullable. What is not is named
/// volumes and workspaces, and those come across: the generation reset clears
/// nerdctl's container definitions and leaves `volumes` alone.
#[cfg(any(windows, test))]
pub(crate) const MIGRATE_DATA_INTO_HOLDER: &str = "\
set -eu
share=/mnt/wsl/lemma-data
if [ ! -d \"$share\" ]; then
  echo 'lemma-data: needs-repair: the data holder is not published' >&2
  exit 1
fi
if [ -f \"$share/.lemma-data-holder\" ]; then
  exit 0
fi
mkdir -p \"$share/lemma\" \"$share/containerd\" \"$share/nerdctl\" \"$share/cni/net.d\"
copy_tree() {
  source=$1
  target=$2
  if [ -d \"$source\" ] && [ -n \"$(ls -A \"$source\" 2>/dev/null)\" ]; then
    cp -a \"$source/.\" \"$target/\"
  fi
}
copy_tree /var/lib/lemma \"$share/lemma\"
copy_tree /var/lib/nerdctl \"$share/nerdctl\"
copy_tree /etc/cni/net.d \"$share/cni/net.d\"
chmod 0700 \"$share/lemma\"
touch \"$share/.lemma-data-holder\"
";

impl ManagedRuntime {
    /// Where Lemma's data lives on Windows, and why it is a second guest.
    ///
    /// The runtime distribution is replaced wholesale by every upgrade, so
    /// nothing that must survive one can live inside it -- and until this
    /// existed, everything did: `/var/lib/lemma`, the container store and the
    /// volumes were all on that distribution's own ext4.vhdx. An upgrade could
    /// therefore only refuse, and it did, telling people to reset the runtime
    /// and lose every workspace, database and pod. That refusal was correct
    /// and useless: it left Windows users pinned to whichever release they
    /// installed first.
    ///
    /// macOS attaches a second disk. WSL cannot: `wsl --mount --vhd` needs
    /// administrator rights, and Lemma does not ask for them. What WSL does
    /// give is `/mnt/wsl`, a tmpfs shared by every distribution in the same
    /// VM, and a bind published into it from one distribution is readable and
    /// writable from another. So the data gets a distribution of its own,
    /// whose ext4.vhdx no upgrade touches, and it publishes itself there.
    ///
    /// Measured on a Windows machine before any of this was written: importing
    /// the second distribution from the rootfs already on disk takes 1.9s; the
    /// share is readable from the runtime distribution immediately; it stays
    /// readable and writable after the data distribution goes idle and is
    /// reported Stopped, and even after an explicit `--terminate`, because the
    /// mount belongs to the VM rather than to the distribution's processes.
    /// It does not survive `wsl --shutdown`, which is why publishing runs on
    /// every start rather than once at import.
    #[cfg(windows)]
    pub(crate) fn data_distribution(&self) -> String {
        format!("{}Data", self.wsl_distribution())
    }

    /// Import the data distribution if it is absent, and publish its share.
    ///
    /// Publishing is idempotent and unconditional: the share lives in the WSL
    /// VM, and anything that stops the VM -- `wsl --shutdown`, a reboot --
    /// takes it with it while leaving the distribution registered.
    #[cfg(windows)]
    pub(crate) fn ensure_data_distribution(&self, rootfs: &Path) -> io::Result<()> {
        let distribution = self.data_distribution();
        if !self.guest_is_registered(&distribution) {
            if !rootfs.is_file() {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    format!("private WSL rootfs is missing: {}", rootfs.display()),
                ));
            }
            let install = self.config.local_root.join("runtime/wsl-data");
            fs::create_dir_all(&install)?;
            self.wsl(
                &[
                    "--import",
                    &distribution,
                    &install.to_string_lossy(),
                    &rootfs.to_string_lossy(),
                    "--version",
                    "2",
                ],
                None,
            )?;
        }
        self.wsl(
            &[
                "--distribution",
                &distribution,
                "--user",
                "root",
                "--exec",
                "/bin/sh",
                "-c",
                PUBLISH_DATA_SHARE,
            ],
            None,
        )?;
        Ok(())
    }

    /// Move an existing installation's data out of the runtime distribution.
    ///
    /// Runs in whichever distribution currently holds the data, before
    /// anything replaces it, and does nothing at all once the holder says it
    /// is ready. A fresh install runs it too and copies nothing: what it is
    /// really doing there is recording that the holder is now the home, so the
    /// first upgrade afterwards knows it may replace the runtime.
    ///
    /// The distribution is terminated first, and only when there is really
    /// something to move. On a warm start the container store is several
    /// gigabytes with live overlay mounts stacked on it -- 3.6 GB on the
    /// machine this was written against -- and copying that from underneath a
    /// running containerd copies a moving target. Terminating costs nothing
    /// here: the next command in the start sequence is the init that brings
    /// the distribution back up.
    #[cfg(windows)]
    pub(crate) fn migrate_data_into_holder(&self) -> io::Result<()> {
        if self.data_holder_is_ready()? {
            return Ok(());
        }
        let _ = self.wsl_allowing_failure(&["--terminate", self.wsl_distribution()], None);
        self.wsl(
            &[
                "--distribution",
                self.wsl_distribution(),
                "--user",
                "root",
                "--exec",
                "/bin/sh",
                "-c",
                MIGRATE_DATA_INTO_HOLDER,
            ],
            None,
        )?;
        Ok(())
    }

    /// Replace the runtime distribution with this release's rootfs.
    ///
    /// The gate is not a formality. `--unregister` deletes an ext4.vhdx and
    /// everything in it, so this refuses to run until the holder itself says
    /// the data is out -- asked of the holder, not inferred from a file on the
    /// Windows side that could be stale, or from having just run the migration
    /// and assumed it worked.
    #[cfg(windows)]
    pub(crate) fn replace_runtime_distribution(
        &self,
        install: &Path,
        rootfs: &Path,
    ) -> io::Result<()> {
        refuse_replacement_without_holder(self.data_holder_is_ready()?)?;
        let _ = self.wsl_allowing_failure(&["--terminate", self.wsl_distribution()], None);
        self.wsl(&["--unregister", self.wsl_distribution()], None)?;
        self.wsl(
            &[
                "--import",
                self.wsl_distribution(),
                &install.to_string_lossy(),
                &rootfs.to_string_lossy(),
                "--version",
                "2",
            ],
            None,
        )?;
        fs::write(self.guest_release_marker(), rootfs_stamp(rootfs)?)?;
        Ok(())
    }

    /// Ask the holder whether it holds the data.
    #[cfg(windows)]
    pub(crate) fn data_holder_is_ready(&self) -> io::Result<bool> {
        let output = self.wsl_allowing_failure(
            &[
                "--distribution",
                &self.data_distribution(),
                "--user",
                "root",
                "--exec",
                "/bin/sh",
                "-c",
                "test -f /data/.lemma-data-holder",
            ],
            None,
        )?;
        Ok(output.status.success())
    }

    /// Remove the private distributions, and everything inside them.
    ///
    /// Both of them, and that is the whole point now: the data moved out of
    /// the runtime distribution into a holder of its own so that upgrades stop
    /// destroying it, and a wipe that removed only the runtime would leave
    /// every workspace, database and volume registered and full while the
    /// dialog above it said "Everything Lemma keeps on this PC is deleted".
    /// That exact sentence was false once before, for the same reason.
    ///
    /// The only lifecycle verbs used to be `--import` and `--terminate`, so a
    /// corrupt guest could not be rebuilt from inside the app and uninstalling
    /// Lemma left a registered distribution and a multi-gigabyte ext4.vhdx that
    /// only `wsl --unregister` from a terminal could remove.
    ///
    /// This destroys guest state. Callers must have asked first.
    #[cfg(windows)]
    pub fn unregister_windows_guest(&self) -> io::Result<()> {
        let output = match self.wsl_allowing_failure(&["--list", "--quiet"], None) {
            Ok(output) => output,
            Err(error)
                if error.kind() == io::ErrorKind::NotFound
                    && !self.config.local_root.join("runtime/wsl").exists() =>
            {
                return Ok(())
            }
            Err(error) => return Err(error),
        };
        // An unavailable WSL service is not evidence that the distribution is
        // absent. Preserve its registration and cleanup records on ambiguity.
        if registered_guest(
            output.status.success(),
            &output.stdout,
            self.wsl_distribution(),
        )? {
            let _ = self.wsl_allowing_failure(&["--terminate", self.wsl_distribution()], None);
            self.wsl(&["--unregister", self.wsl_distribution()], None)?;
        }
        let data = self.data_distribution();
        if registered_guest(output.status.success(), &output.stdout, &data)? {
            let _ = self.wsl_allowing_failure(&["--terminate", &data], None);
            self.wsl(&["--unregister", &data], None)?;
        }
        let _ = fs::remove_file(self.guest_release_marker());
        Ok(())
    }

    /// Whether WSL lists a distribution by this exact name.
    ///
    /// Deliberately not `?`. `wsl --list --quiet` exits non-zero when there are
    /// no distributions at all -- which is exactly the state
    /// prepare_windows_host engineers with `--install --no-distribution` -- so
    /// treating that as fatal aborted the very first start before the import
    /// could ever run, permanently.
    ///
    /// A false "absent" is safe here, and that is not an assumption: `--import`
    /// refuses a name that already exists rather than replacing it, so the
    /// mistake surfaces as an error from the next command and never as a
    /// distribution that was overwritten.
    #[cfg(windows)]
    pub(crate) fn guest_is_registered(&self, name: &str) -> bool {
        self.wsl_allowing_failure(&["--list", "--quiet"], None)
            .map(|output| {
                decode_wsl_output(&output.stdout)
                    .lines()
                    .any(|line| line.trim() == name)
            })
            .unwrap_or(false)
    }
}
