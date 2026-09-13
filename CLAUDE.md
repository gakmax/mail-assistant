# CLAUDE.md

Windows-only tkinter app with a second, web-based set of screens. Polls a Hiworks POP3 mailbox, sends each mail to the
Codex CLI for analysis, stores everything in sqlite, and writes the result into a
desktop Excel workbook over COM. The window (`app.py`) reads from the database,
not from the workbook: 현황·메일·일정·실행·설정 tabs over `mail.db`. The same database
also backs the NiceGUI screens in `webui.py`, opened with
`MailAssistantTools.exe web` — ten pages on 127.0.0.1, sharing one `Hub`. Shipped as
an unsigned Inno Setup installer built by GitHub Actions, with in-app updates from
public GitHub Releases.

User-facing behaviour, install steps and troubleshooting live in `README.md` —
read it before changing anything the user sees. This file is only the things
that break silently if you don't know them.

## Commands

```bash
python -m unittest discover -s tests -v    # from the repo root; 247 tests, all platforms
```

```powershell
.\packaging\build.ps1                      # Windows + Inno Setup 6: exe and installer
.\packaging\shoot.ps1 -Source -Path /mail  # headless screenshot of one web page
.\packaging\spike.ps1 -Venv                # bundle size and cold start, web only
```

The web screens run off Windows too, which is how they are developed:
`PYTHONPATH=. python packaging/entry_web.py --browser`.

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

**Settings saves must merge.** `App.save_settings()` writes
`{**config, **updated}`. The form holds seven fields; `webhook`, `update`,
`update_skip` and `update_checked` are not among them and a plain write deletes
them. `update.remember()` exists for the same reason and is the only thing that
should touch `config.json` from outside the GUI.

**One list of filter values, in `core.STATES`.** The dropdown in `app.py`, the one
in `webui.py` and the SQL in `core.STATE_SQL` are the same set; a value offered with
no SQL behind it would quietly list everything, so a test holds them equal. `handled`
now carries three values — `''`, `PROGRESS`, `HANDLED` — because the kanban's middle
column *is* the mail's own state rather than a second place to keep it.

**A `<<TreeviewSelect>>` handler must never re-set the selection.** ttk queues the
event for every `selection set`, whether or not the selection actually changed
(`SELECTION_SET` in `ttkTreeview.c` flags a change unconditionally), so a handler
that calls `selection_set` again feeds the event straight back and the window
spins on its own events until Windows marks it 응답 없음. `App.select()`,
`show_mail()` and `paint_list()` each compare against the current selection
before setting it.

**The draft box is the only user-owned text in the window.** `poll()` reopens the
selected mail on every worker report, so `show_mail()` must not refill the draft
while an edit is in progress: it compares the widget against `draft_baseline`
and leaves a dirty draft alone. `flush_draft()` saves it before anything can
replace it — switching mail, `close()`, and the teardown in `poll()` all call it.
Reloading unconditionally silently discarded whatever the user had typed in the
seconds before a poll.

**A credential read that fails is not a credential that is missing.**
`services.read_password()` raises `LookupError` only for winerror 1168; anything
else propagates, and `App.stored_password()` puts it in `self.password_error` so
the lamp reads 확인 실패 instead of 없음. Swallowing it told the user to re-enter
a password that was already stored. `save_settings()` reads the password back
after writing it for the same reason.

**A `.ps1` carrying Korean text needs a UTF-8 BOM.** Windows PowerShell 5.1 reads
a BOM-less script as the ANSI codepage — 949 on a Korean box — and CP949 is
double-byte, so an odd-length run of non-ASCII bytes pairs its last byte with the
character that follows. When that character is the closing quote, the string never
ends and the parser reports a cascade of errors on unrelated lines. `installer.iss`
already carries a BOM for the same reason; `build.ps1` survived only because every
non-ASCII run beside a quote happened to be an even number of bytes. Both now have
one. `[System.Management.Automation.Language.Parser]::ParseFile` checks a script
without running it.

**`webui.py` must keep importing without nicegui.** The shaping helpers sit at
module level and every `from nicegui import ui` is inside the function that needs
it. That is what lets `tests/test_webui.py` cover the screens on Linux — the first
UI coverage this project has ever had, and the reason to keep the split.

**nicegui is the one declared exception to stdlib-only**, recorded in
`requirements.txt` and priced in `UI-PLAN.md` 6절: the bundle went from 37.9MB to
59.6MB. Two things hold that number down and both are easy to lose:

* `Analysis.datas` must be filtered *after* the hook runs. hooks-contrib ships
  `hook-nicegui.py` with a bare `collect_data_files('nicegui')`, so vendored
  JavaScript dropped in a spec-level `collect_all` is added straight back. The
  filter drops ~21MB of element bundles this app never creates.
* `pywebview` is excluded on purpose. `requirements.txt` does not install it, so a
  CI build would not have it: shipping a `web --native` that only works on a dev box
  is worse than not offering it.

**The browser fetches third-party assets from `/vendor`, never a CDN.** FullCalendar
is vendored in `mail_assistant/vendor/` (MIT, standard views only) because this app
has to work offline and behind a proxy, where a CDN reference leaves a blank page
and no error. `webui.vendor_path()` locates it either side of freezing, the spec
copies it, and `selftest`'s `check_webui` is what notices when it did not.

**A page must not depend on `ui.run_javascript` to draw itself.** The calendar's
first attempt passed its events that way and nothing ever rendered: the call needs a
connected client, and by the time one exists the page has been built and handed over.
The payload rides in `ui.add_body_html` instead, the `<script src>` goes in the
*head* so the library is defined before anything calls it, and the initialiser
retries while Vue mounts the div, then writes `data-state` so a failure is visible
instead of silent.

**The window lives in `app.py`, the worker in `hub.py`, the entry point in
`__main__.py`.** `main()` owns the crash hooks, the mutex, `update.sweep()` and the
installer launch in its `finally`; it builds the `Hub` and hands it to `App`.
Everything visible belongs to `App`, which exposes `boot`, `close`, `start`,
`pending_installer`, `pending_autostart` and `offer` because the entry point needs
them after `mainloop()` returns.

**The `Hub` owns the worker; a screen only watches it.** `Hub.start()` returns
False when a worker is already running, which is what stops a second screen from
starting a second poller, and `App` no longer holds the thread or the stop/wake
events. Screens call `subscribe()` for a queue of messages, and `publish` is the
`notify` the worker was always given. The sentinel `None` is sent *after*
`running()` has already gone False, so a screen reacting to it is never told the
worker is still alive.

**Every thread that logs must release its `LogStore`.** `Hub.store()` keeps one
connection per thread in a `threading.local`, because sqlite connections are not
shareable; `Hub.release()` closes the calling thread's, `work()` calls it in its
`finally` and `close()` calls it for the screen. Skip it and Windows holds
`mail.db` locked for the life of the process — the tests catch this only when they
run on Windows, where `TemporaryDirectory` cleanup fails with WinError 32.

**One Codex process at a time.** `services.codex_slot()` guards a
`BoundedSemaphore(1)`, because analysis, the login check and anything added later
share one account and one usage quota. `analyze()` waits by default; the indicator
asks with `check_login(timeout=LAMP_WAIT)` and reports 확인 중 rather than queueing
behind a 240-second analysis. `CodexBusy` subclasses `RuntimeError`, so catch it
*before* the `RuntimeError` branch or 'busy' is reported as '로그인 필요'.

**Schema changes are `ALTER TABLE ADD COLUMN` only.** `Store.migrate()` adds what
is missing to `mail` and nothing else; installed databases hold the only copy of
collected mail. A whole new table is different and allowed: it goes in
`Store.__init__`'s `executescript` as `CREATE TABLE IF NOT EXISTS`, which is how
`log` arrived, and `LogStore` repeats the same statement so whichever of the two
opens the file first is fine. The window opens its own connection on the UI thread — sqlite connections
are not shareable across threads, and WAL is what lets the worker keep writing.

**Any new console script needs `use_utf8()`** from `mail_assistant.console` as
the first line of `main()`. Windows gives a redirected stdout the ANSI codepage,
so Korean `print()` raises `UnicodeEncodeError` the moment anyone pipes output to
a file — which is exactly what you ask for when debugging.

**`Range.Value` has three shapes and only one is a tuple of rows.** COM returns a
bare scalar for a single-cell range and `None` for a single *empty* cell, so
`excel.first_column()` normalises all three. Reading the column directly worked
until a sheet held exactly one data row, and then raised `'NoneType' object is not
iterable` — or silently iterated the characters of a one-cell id. `append_missing()`
and `mail_rows()` both go through it. `MailAssistantTools.exe demo`, run twice, is
what exercises the append-to-existing path this lives on.

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
cheapest way to exercise it. Run it *twice*: the second run is the one that appends
to an existing sheet, which is where `first_column()` earns its keep.

The web screens are covered two ways. Their shaping is plain functions with unit
tests (`tests/test_webui.py`), and `packaging/shoot.ps1` renders a page with headless
Edge so layout can be looked at without a person at the machine — which is how the
squeezed table columns, the centred 원문 panel and the overflowing trend bars were
all found. What no test covers is clicking: typing in the search box, moving a kanban
card, sending a chat turn.

## Style

Korean for anything a user reads, English for docstrings and comments. A `.ps1`
carrying Korean needs a UTF-8 BOM (see the invariant above) — that applies to
throwaway scripts too, which is a mistake easy to repeat.
Docstrings are one line and say *why*, not what. Stdlib only unless there is no
alternative — `report.py` and `update.py` both do HTTP with `urllib` rather than
add a dependency. Background work never disturbs the app: it runs on a daemon
thread and swallows its own exceptions. New tkinter work reuses
`App.background(name, work, done)` in `app.py`, which returns results through the
`Hub` subscription and `poll()` dispatch; new web work puts its blocking calls
through `nicegui.run.io_bound` so the event loop keeps serving. Screen arithmetic belongs in
`overview.py` and the other pure modules (`rules`, `calendar_sheet`, `dashboard`)
so the tests can cover it off Windows — `app.py` itself is not importable there.
