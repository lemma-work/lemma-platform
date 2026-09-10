; Lemma's NSIS hooks.
;
; Two things, each at the one point where it can work.
;
; PREINSTALL stops the previous installation's background services, because
; Windows will not replace a file that is open.
;
; PREUNINSTALL erases the local data when the uninstaller's checkbox asked
; for it, which is not where Tauri would have looked.

; An upgrade installs over a running installation.
;
; Windows will not replace a file that is open, so an installer that runs while
; locald and the Agent Host are up either fails outright or -- because Tauri's
; NSIS template does not stop on a failed file write -- leaves the previous
; binaries in place and reports success. The app then launches as the new
; version against the old daemon, which answers with the same path it always
; did, and until the build stamp in the handshake landed there was nothing that
; could tell the difference.
;
; Killed rather than asked to stop: there is no window here in which to wait,
; and a daemon that is killed comes back when the app is next opened. Its state
; is on disk and survives.

!macro NSIS_HOOK_PREINSTALL
  DetailPrint "Stopping Lemma's background services..."
  ; By image name, which is wider than this installation, for the reason the
  ; uninstall hook gives: narrowing it needs the process path, which NSIS
  ; cannot filter on without a PowerShell round trip that nothing here can
  ; test. Installing is explicit and rare.
  ;
  ; Waited for rather than slept over. `taskkill /F` returns once termination
  ; has been *requested*, and a file whose last handle closes a moment later is
  ; still open when the copy starts -- so a fixed pause was a guess about a
  ; machine we are not on, and being wrong leaves the previous binaries in
  ; place under an installer that reports success.
  ;
  ; taskkill is its own probe: it exits non-zero when there is no such image,
  ; so "both refused" is "both are gone", and a service that respawned between
  ; iterations is killed again rather than missed.
  ;
  ; The template's own registers, borrowed and given back: this hook runs
  ; inside its install section, not beside it.
  Push $0
  Push $1
  Push $2
  StrCpy $1 0
  ${Do}
    nsExec::ExecToLog 'taskkill /F /T /IM lemma-locald.exe'
    Pop $0
    nsExec::ExecToLog 'taskkill /F /T /IM lemma-agent-host.exe'
    Pop $2
    ${If} $0 <> 0
    ${AndIf} $2 <> 0
      ${ExitDo}
    ${EndIf}
    Sleep 250
    IntOp $1 $1 + 1
  ${LoopUntil} $1 >= 40
  ${If} $0 == 0
  ${OrIf} $2 == 0
    ; Ten seconds of `/F` and still answering. Said out loud rather than
    ; aborted: nothing has been written yet, so this install is still
    ; recoverable either way, and the handshake stamp this release adds is what
    ; catches the daemon that survives -- the app refuses to adopt a binary
    ; that is not the one it ships, instead of running the new shell against
    ; the old runtime and reporting the same version on both sides.
    DetailPrint "Lemma is still running. Close it and run this installer again if the new version does not start."
  ${EndIf}
  Pop $2
  Pop $1
  Pop $0
!macroend

; Tauri's uninstaller offers to delete application data, and what it deletes is
; %APPDATA%\${BUNDLEID} and %LOCALAPPDATA%\${BUNDLEID} -- for Lemma,
; work.lemma.desktop. Lemma's data is not there. It is in %LOCALAPPDATA%\Lemma,
; and the bulk of it is not files at all: it is a registered WSL distribution
; whose ext4.vhdx holds every workspace, database and container image, running
; to several gigabytes. So the checkbox deleted nothing anybody had, and
; uninstalling left a registered distribution and a multi-gigabyte disk that
; only `wsl --unregister` from a terminal could remove.
;
;
; locald already knows how to do this properly: `reset --confirm=erase-local-
; lemma` unregisters the guest, reclaims the installation's processes and
; removes its state, and it refuses to touch a directory whose contents are not
; recognisably Lemma's. This runs it, at PREUNINSTALL, because by POSTUNINSTALL
; its binary has already been deleted.


!macro NSIS_HOOK_PREUNINSTALL
  ; `reset` refuses while the daemon's socket is answering -- deliberately, so
  ; it cannot race a live installation -- so the daemon has to go first.
  ;
  ; By image name, which is wider than this installation: a second user
  ; profile or a development root has its own state, its own daemon and its
  ; own guest, and this ends those too. Narrowing it needs the process path,
  ; which NSIS cannot filter on without a PowerShell round trip that nothing
  ; here can test. Uninstalling is rare and explicit, and a daemon that is
  ; killed comes back when its app is next opened.
  nsExec::ExecToLog 'taskkill /F /T /IM lemma-locald.exe'
  Pop $0
  nsExec::ExecToLog 'taskkill /F /T /IM lemma-agent-host.exe'
  Pop $0

  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    DetailPrint "Removing Lemma's local data and private runtime..."
    nsExec::ExecToLog '"$INSTDIR\lemma-locald.exe" reset --confirm=erase-local-lemma'
    Pop $0
    ${If} $0 <> 0
      ; Not fatal. An installation too damaged to reset is still an
      ; installation the user asked to remove, and saying so beats failing the
      ; uninstall over it.
      DetailPrint "Lemma's local data could not be removed automatically (code $0)."
      ; Both entries. The data lives in the second one now -- the runtime
      ; distribution is replaced wholesale by every upgrade, so nothing that
      ; must survive one is kept there -- and naming only the first would tell
      ; somebody following this by hand to delete the disposable half and keep
      ; the several gigabytes they were trying to remove.
      DetailPrint "Run: wsl --list --quiet, then wsl --unregister on each Lemma entry"
      DetailPrint "  (LemmaRuntime, and LemmaRuntimeData which holds the data)"
    ${EndIf}
  ${EndIf}
!macroend
