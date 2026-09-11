# CLAUDE.md

Windows-only tkinter app. Polls a Hiworks POP3 mailbox, sends each mail to the
Codex CLI for analysis, and writes the result into a desktop Excel workbook
over COM. Shipped as an unsigned Inno Setup installer built by GitHub Actions,
with in-app updates from public GitHub Releases.

User-facing behaviour, install steps and troubleshooting live in `README.md` —
read it before changing anything the user sees. This file is only the things
that break silently if you don't know them.

## Commands

```bash
python -m unittest discover -s tests -v    # from the repo root; 102 tests, all platforms
```

```powershell
.\packaging\build.ps1                      # Windows + Inno Setup 6: exe and installer
```

Release: bump `__version__`, commit, `git tag -a vX.Y.Z -m "..."`, push the tag.
CI does the rest.

## Invariants

**The version lives in exactly one place** — `mail_assistant/__init__.py`. The
installer's `AppVersion`, the exe version resource, the release asset name and
the update comparison all derive from it. CI fails the build if the tag and
`__version__` disagree.

**Release tags must be annotated.** `git push --follow-tags` skips lightweight
tags, so `git tag vX.Y.Z` pushes nothing and no release is built. Use
`git tag -a`.

**Never commit a webhook or any secret.** `report.DEFAULT_WEBHOOK` is `''` in
source; CI writes `mail_assistant/_secrets.py` from the `DISCORD_WEBHOOK`
repository secret. The repo is public and secret scanning will revoke a pushed
Discord URL. A source checkout reports nowhere, which is correct.

**`latest.json` is a frozen filename.** The updater reads
`releases/latest/download/latest.json`, a redirect that resolves by exact asset
name. Renaming it strands every installed copy. The installer's own filename is
versioned and free to change.

**`AppId` in `installer.iss` must never change.** It is the identity Windows
uses to recognise an upgrade; a new GUID installs side by side instead.

**Close the mutex before launching the installer.** `main()`'s `finally` calls
`win32api.CloseHandle(mutex)` and *then* `update.launch(...)`. Reversed, Setup's
`AppMutex` check collides and, under `/SUPPRESSMSGBOXES`, fails as exit code 2 —
"user cancelled" — with no visible error.

**Settings saves must merge.** `open_settings().save()` writes
`{**config, **updated}`. The form holds seven fields; `webhook`, `update`,
`update_skip` and `update_checked` are not among them and a plain write deletes
them. `update.remember()` exists for the same reason and is the only thing that
should touch `config.json` from outside the GUI.

**Any new console script needs `use_utf8()`** from `mail_assistant.console` as
the first line of `main()`. Windows gives a redirected stdout the ANSI codepage,
so Korean `print()` raises `UnicodeEncodeError` the moment anyone pipes output to
a file — which is exactly what you ask for when debugging.

**Excel COM is late-bound only** (`Dispatch` / `DispatchEx`). Never introduce
`gencache.EnsureDispatch`: it writes generated modules into the install
directory, which a per-user frozen install cannot rely on.

**The installer stays per-user and unelevated.** `services.codex_command()`
resolves Codex with `shutil.which()` from the user's PATH, and npm's global
prefix lives in the user profile. An elevated install reports
"Codex CLI가 없습니다" with no obvious cause.

**Mail content must not leave the machine except to Codex.** `services.analyze()`
deliberately discards Codex stdout/stderr on failure, `report.py` scrubs
passwords and masks addresses, and crash reports carry only the traceback, time,
PC name and version. Keep new error paths to that standard.

## What can be verified where

Linux runs the whole test suite: every Windows API is faked with
`patch.dict('sys.modules', ...)`, and the update client's pure parts are
importable off Windows (hence `getattr(subprocess, 'DETACHED_PROCESS', 0)`).

The CI Windows legs are the *only* place real pywin32 and real jsonschema 4.x
are imported — the dev box has jsonschema 3.2, a different package layout.
`MailAssistantTools.exe selftest` runs in CI after freezing and is what proves
`win32comext` and the jsonschema metaschema data files survived bundling. Keep
new lazy imports listed there.

Nothing automated covers Excel COM: no runner has Office. The acceptance list in
`README.md` is the only coverage, and `MailAssistantTools.exe demo` is the
cheapest way to exercise it.

## Style

Korean for anything a user reads, English for docstrings and comments.
Docstrings are one line and say *why*, not what. Stdlib only unless there is no
alternative — `report.py` and `update.py` both do HTTP with `urllib` rather than
add a dependency. Background work never disturbs the app: it runs on a daemon
thread and swallows its own exceptions. New UI work reuses
`background(name, work, done)` in `__main__.py`, which returns results through
the existing `events` queue and `poll()` dispatch.
