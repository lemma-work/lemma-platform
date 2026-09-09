; Lemma's NSIS hooks.
;
; One thing, done at the one point where it can work.
;
; Tauri's uninstaller offers to delete application data, and what it deletes is
; %APPDATA%\${BUNDLEID} and %LOCALAPPDATA%\${BUNDLEID} -- for Lemma,
; work.lemma.desktop. Lemma's data is not there. It is in %LOCALAPPDATA%\Lemma,
; and the bulk of it is not files at all: it is a registered WSL distribution
; whose ext4.vhdx holds every workspace, database and container image, running
; to several gigabytes. So the checkbox deleted nothing anybody had, and
; uninstalling left a registered distribution and a multi-gigabyte disk that
; only `wsl --unregister` from a terminal could remove.
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
