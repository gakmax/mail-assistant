# CLAUDE.md

Windows-only tkinter app with a second, web-based set of screens. Polls a Hiworks POP3 mailbox, sends each mail to the
Codex CLI for analysis, stores everything in sqlite, and writes the result into a
desktop Excel workbook over COM. The window (`app.py`) reads from the database,
not from the workbook: 현황·메일·일정·실행·설정 tabs over `mail.db`. The same database
also backs the NiceGUI screens in `webui.py`, opened with
`MailAssistantTools.exe web` — nine pages on 127.0.0.1, sharing one `Hub`. Shipped as
an unsigned Inno Setup installer built by GitHub Actions, with in-app updates from
public GitHub Releases.

User-facing behaviour, install steps and troubleshooting live in `README.md` —
read it before changing anything the user sees. This file is only the things
that break silently if you don't know them.

## Commands

```bash
python -m unittest discover -s tests -v    # from the repo root; 372 tests, all platforms
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

**'새 버전이 없습니다' and '물어보지 못했습니다' are different sentences.**
`update.check()` returns None for both, so `Updater.recheck()` tells them apart by
whether `update_checked` moved: `remember()` writes that stamp only after the manifest
was really fetched. A 지금 확인 that silently claimed 최신 버전입니다 behind a proxy
that ate the request is exactly the failure this replaces — the old 설정 화면 said
'건너뛴 버전이 없습니다', which answered a question nobody asked. `recheck()` also
copies the stamp back into the live `config` dict, because `remember()` only touches
the file and 마지막 확인 reads the dict.

**Settings saves must merge.** `App.save_settings()` writes
`{**config, **updated}`. The form holds seven fields; `webhook`, `update`,
`update_skip` and `update_checked` are not among them and a plain write deletes
them. `update.remember()` exists for the same reason and is the only thing that
should touch `config.json` from outside the GUI.

**The Codex model list is read, never shipped.** `settings.read_models()` parses
`%USERPROFILE%\.codex\models_cache.json` — the file the CLI itself writes on every
run — and offers only the entries marked `visibility: list`. Hardcoding slugs was
tried and thrown away: the ones a ChatGPT account may use turn over every few weeks
(every `gpt-5.1-codex*` name was already refused by the API by the time the radio was
written), and a stale default fails *every* analysis with nothing on screen to explain
it. So a missing cache offers 기본값 and 직접 입력 only, `model_rows()` keeps a row for
whatever is saved even after Codex stops listing it, and `field_errors` rejects a model
with a space in it because the value becomes one `--model` argument.

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

**`win32timezone` is imported by pywin32's C code, and only the frozen build
notices.** Any Win32 call that returns a time builds its tzinfo by importing that
module from C, and `CredRead`'s `LastWritten` is one — so `read_password()` raises
`ModuleNotFoundError: No module named 'win32timezone'` in a bundle that did not list
it, which is every poll and every 비밀번호 저장. No static analysis can see the import;
it is in the spec's `HIDDEN` for that reason alone. `selftest` used to read
`win32cred.CRED_TYPE_GENERIC` — a constant, which proved only that the .pyd loaded —
and so passed while the app could not read a password at all. `check_cred()` now
writes, reads back and deletes a throwaway credential instead. A probe that only
imports a module is not a probe of what the app does with it.

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
59.6MB; ECharts adds ~1.8MB and Pretendard 2.06MB on top (the CI build prints the
real number).
Two things hold that number down and both are easy to lose:

* `Analysis.datas` must be filtered *after* the hook runs. hooks-contrib ships
  `hook-nicegui.py` with a bare `collect_data_files('nicegui')`, so vendored
  JavaScript dropped in a spec-level `collect_all` is added straight back. The
  filter drops ~19MB of element bundles this app never creates. **`echart` is the
  one deliberate exception** and must stay out of `DROP_ASSETS` in both specs: every
  chart in `webui.py` is a `ui.echart`, so its 1.8MB ships. `selftest`'s
  `check_echart` fails the CI build if a spec change drops it again.
* `pywebview` used to be excluded for exactly one reason — `requirements.txt` did not
  install it — and that reason was circular, so 0.4.0 put it in requirements instead.
  **`MailAssistant.exe` is now the screens in a pywebview frame**, which makes
  `webview`, `pythonnet` and `clr_loader` load-bearing. It costs 4.3MB because on
  Windows pywebview drives WebView2, the Edge engine already on the machine, rather
  than embedding a browser the way Electron does.

**The browser fetches third-party assets from `/vendor`, never a CDN.** FullCalendar
(MIT, standard views only) and Pretendard (OFL-1.1, one 2.06MB variable woff2) are
vendored in `mail_assistant/vendor/` because this app has to work offline and behind
a proxy, where a CDN reference leaves a blank page and no error. `webui.vendor_path()`
locates them either side of freezing, the spec copies the folder whole, and
`selftest`'s `check_webui` is what notices when one did not arrive. The font's
`@font-face` lives in `THEME` and **`FONT_PRELOAD` must stay in the head**: ECharts
paints its labels into a canvas once and never repaints when a font lands late, so a
merely-declared font gives you charts in 맑은 고딕 and a page in Pretendard. Two grid
paddings exist for the same font — `AXIS_GUTTER`, and `trend_option`'s `left` — because
`containLabel` sizes the axis gutter from ECharts' own guess at label height, which
Pretendard's taller line box overruns.

**Screen styling lives in `webui.THEME`, not in `.style()` calls.** One `<style>`
block, injected once by `shell()`, holds the tokens (`--ink`, `--card`, `--brand`, …)
and the component classes (`.ma-card`, `.ma-kpi`, `.ma-tag`, `.ma-table`, `.ma-lane`,
`.ma-chat`). A page reaches for `.classes()` first and `.style()` only for a value
that is genuinely per-element — a width, a computed tone. The colours themselves still
come from `style.py` through `css_color()`, so the window and the pages cannot drift
apart. Two of those rules exist because nicegui's own defaults fight the page:
`.nicegui-content` is a flex column with `align-items:start`, which shrink-wraps the
header band and stops a long log line wrapping, and every surface that stacks text
(`.ma-card`, `.ma-sunken`, `.ma-note`) therefore declares `display:block`.

**A `q-table` cell slot is coloured by an expression, never by a JSON blob.**
`tag_cell()` builds the `:style` lookup with single quotes throughout, because the
whole expression sits inside a double-quoted Vue attribute and `json.dumps` would
close it on the first key. `'3회 실패'` carries its count, so the 실패 tone is matched
with `props.value.includes('실패')` rather than a table key. There is no longer a mark
for the open row: the mail opens in a dialog over the list, and a highlight that only
moves when the table is rebuilt pointed at the wrong row more often than the right one.

**The 메일 list is sorted by its own header, and that costs three slots.** The page is
sorted and paged in sqlite, so q-table's own `sortable` would only reorder the fifty
rows it is holding; `header_cell()` emits instead, and `$parent.$emit` is the *only*
route a slot template has back to the server — `$parent` is the q-table, whose vnode
props carry the `onSortby` that `table.on('sortby', …)` registered. Two things a
custom header cell must not forget: `:style="props.col.headerStyle"`, because q-table
stops applying the column widths the moment the cell is yours, and that the argument
arrives as the client's *list*, which is what `clicked_key()` unwraps. The third slot
is `body-selection`: Quasar's own checkbox lets the click reach the row, so without
`@click.stop` ticking a box also opens the mail.

**The 메일 detail is a dialog, and the list is repainted when it closes — if it
changed anything.** Rebuilding the table is also what clears q-table's checkboxes, so
a mail opened to be *read* must not cost the user the selection they made; `touched`
is set by the things that change a list column (처리 상태, 다시 분석, 삭제) and nothing
else. Refreshing while the dialog is open is the same work done where nobody can see
it, behind a dialog that covers the rows.

**Deleting mail keeps its `seen` uid.** `Store.delete()` drops the mail row and the
chat hung off it, and deliberately leaves `seen` alone: the mail is still on the POP3
server, and forgetting the uid means the next poll collects and analyses the very mail
the user just threw away. Rows already written to the workbook stay there — Excel does
not read this database.

**'지금 가져오기' opens a `Store` of its own.** It runs through `nicerun.io_bound`,
which is a pool thread, and a sqlite connection cannot cross threads — the page's
`store(directory)` belongs to the event loop's. It also only collects: analysis is the
worker's and takes minutes per mail, which is why `collect_text()` says so out loud
when the collector is stopped. When the worker *is* running the button wakes it rather
than opening a second POP3 session against the same account.

**A Codex answer is rendered by `rich_text()`, never by `ui.markdown`.** The
difference is not the Markdown — it is that `nicegui.elements.markdown` imports
markdown2 *and pygments*, and pygments is 8.7MB that UI-PLAN's 1단계 measurement
listed as removable precisely because nothing imports it. Reaching for `ui.markdown`
quietly welds it into an unsigned installer whose download size is the user's first
friction. `rich_text()` escapes first and then emits only `<p> <br> <ul> <ol> <li>
<b> <code> <pre>`; `ui.chat_message(text_html=True)` hands that to DOMPurify in the
browser, so sanitising is a second lock rather than the only one — which is why
`selftest`'s `check_chat` fails the build if `dompurify.mjs` or `elements/html.js`
stops shipping.

**Never build a `ui.timer` inside a handler that has just called `refresh()`.** The
timer takes its client from whatever slot is current, `refresh()` has deleted that
slot, and the `RuntimeError` it raises kills the rest of the handler — in 상담 that
left `busy` True and the composer disabled with no way back except a reload, and
nothing on screen said so. `scroll()` is `async` and awaits `asyncio.sleep` instead:
the tick it needs before `scroll_to` costs nothing and creates no element.

The same rule covers `ui.notify`, and 업데이트's 지금 확인 is where it was learned:
`refresh()` before `await` deleted the button the handler was running in, so the
`ui.notify` after the await could not resolve `context.client`, and the
`RuntimeError` killed the rest of the handler — leaving '확인하는 중입니다…' on
screen for ever with no error anywhere a user could see. Refresh *last* in a handler,
and say the in-between things with `set_text()` on a label that lives outside the
refreshable. A plain element method (`set_text`, `props`, `disable`, a dialog's
`open`) needs no client and is always safe; anything under `ui.` does.

**Korean never goes into a `strftime` format.** Windows encodes the format string
with the locale codec, so `time.strftime('마지막 확인: %Y…')` raises
`UnicodeEncodeError` on any PC that is not set to Korean — and on CPython 3.11 that
includes the CI runner, where it is a Windows-only test failure the dev box cannot
reproduce. `updater.checked_text()` concatenates the label instead. `local_text()` in
`core.py` is the same rule: every pattern it is given is ASCII.

**The frame's size is `webui.WINDOW`, and both windows use it.** pywebview opens at
800x600 unless told otherwise, which puts 현황's two columns and 메일's six-column table
inside a scrollbar from the first launch. `serve()` passes `window_size(screen_size())`
into `app.native.window_args`, and `app.py` asks tkinter for the same number — importing
it from `webui`, which costs nothing because that module imports no toolkit at module
level. Both clamp `min_size` to the opening size: on a small screen a minimum larger
than the window is what pywebview obeys, and the title bar ends up below the desktop
where it cannot be dragged back.

**`overview()['cards']` holds exactly four, and that is a layout contract.**
`App.paint_overview()` in `app.py` walks a fixed list of four tkinter labels, so a
fifth key added to that dict is drawn nowhere and silently lost. The web page's fifth
card, 분석 실패, is therefore a separate `'failed'` count that `webui.card_rows()`
appends — and appends only when it is non-zero, because a card reading 0 every day is
how a reader learns to stop looking at that corner. `CARD_TONES` is checked against
`card_rows()` and not against `['cards']` for the same reason.

**The 현황 할 일 tally goes through `board()`, never through SQL.** `board_counts()`
calls the same function the kanban renders from, so the two cannot disagree about what
counts as a card — a mail whose analysis produced no `next_action` is not one, and no
`COUNT(*)` on `handled` knows that. The same rule makes `home()`'s `read()` fetch rows
and todos itself instead of calling `snapshot()`: the tally needs the rows the overview
already read, and a second `page()` every five seconds reads the whole mailbox twice.

**The 마감 checklist keeps what the card drops.** `overview.deadlines()` and
`past_due()` filter handled mail out because the 현황 cards count what is *left*;
`due_window()` deliberately keeps it, because a tick that deleted its own row would
move the progress bar with nothing left to point at. So the panel and the `…일 내
마감` card beside it show different numbers on purpose — filter the list to match the
card and the bar reads 0% forever. What bounds the noise is the window: an unhandled
miss never falls off however old, a finished one only lingers while it is inside the
same 7/14/30-day window the toggle is showing. The tick writes the mail's own
`handled` — the field the kanban moves — so the two screens cannot disagree, and a
mail carrying several deadlines moves all of its rows at once.

**The kanban's drag never talks to the server until the drop.** `dragover` fires
once per frame while the pointer travels, so the highlight is a `js_handler` that
only adds a class — give any of `dragover`/`dragleave`/`dragstart`/`dragend` a Python
handler and a single card's travel becomes hundreds of websocket messages, to decorate
a board that is rebuilt from sqlite anyway. Only `DRAG_DROP` calls `emit`. The card's
identity rides in the drag's own `dataTransfer` (`drag_payload`, id last so a mail id
holding a colon survives the split), which is also what lets `drag_drop()` drop a card
returned to its own lane instead of rewriting the state it already has. The chevron
buttons stay: `shoot.ps1` cannot click, so drag is the one thing on this page no test
covers, and it must not be the only way to move a card.

**The 현황 charts are updated, not rebuilt.** `home()` creates its four `ui.echart`
elements once and `paint()` writes new options into them every `REFRESH_SECONDS`; only
the text blocks are `@ui.refreshable`. Wrapping a chart in a refreshable drops the
canvas and replays the entry animation on every tick, which on a 5-second timer reads
as a page that will not sit still.

**A page must not depend on `ui.run_javascript` to draw itself.** The calendar's
first attempt passed its events that way and nothing ever rendered: the call needs a
connected client, and by the time one exists the page has been built and handed over.
The payload rides in `ui.add_body_html` instead, the `<script src>` goes in the
*head* so the library is defined before anything calls it, and the initialiser
retries while Vue mounts the div, then writes `data-state` so a failure is visible
instead of silent.

**The window is the web screens; `app.py` is the fallback, not the default.**
`main()` still owns the crash hooks, the mutex, `update.sweep()` and the installer
launch in its `finally`; what changed is what it opens. `native_ready()` decides:
`--window`, or a `webview` that will not import, takes `open_window()` and the tkinter
`App` exactly as before. Do not delete `app.py` — it is the only thing a PC without a
WebView2 runtime can still show, and nothing in CI ever opens a pywebview frame, so
the fallback is the only tested-by-a-human path on such a machine. Both paths hand
`main()` the same `pending` dict, because the `finally` must not care which ran.

**A windowed build may have no `sys.stdout` at all.** `MailAssistant.exe` is built
`console=False`, where PyInstaller can leave `sys.stdout` and `sys.stderr` as `None` —
and uvicorn's logger and nicegui's own startup line both write to them. The tkinter
window never printed, so this only became load-bearing when the screens became the
window: `route_output()` runs before `update.sweep()` and points both at
`window.log`, truncated per launch so it cannot grow. Remove it and the app dies
before anything is drawn, with a traceback only `entry_gui.py` would catch.

**Both exes carry nicegui now.** The GUI `Analysis` used to list `'nicegui'` in its
excludes, from when `MailAssistant.exe` was tkinter and only the tools exe served
pages. Left in place it would have frozen a window that cannot import its own window —
and nothing but a real Windows build would have said so.

**The update offer is a state machine in `updater.py`, not in a screen.** `Updater`
holds `offer`, `state`, `progress` and `message`, and — the part `main()` depends on —
`installer` and `autostart`. Both windows drive the same object, so the install order
is identical either way: the screen stops the worker, brings the server down with
`app.shutdown()`, and only then does `main()`'s `finally` close the mutex and launch
Setup. Nothing in `updater.py` imports a toolkit, which is what lets the tests cover
every state off Windows.

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

**Every list query needs `rowid` as its last tie-break.** `received` is written by
`now()` when the mail is stored, and a Windows clock ticks about every 15ms, so one
poll cycle gives every mail it collected the same string. `ORDER BY received DESC`
alone then has nothing left to decide with and sqlite may return a different order on
each call — the list reshuffling between two refreshes, and a Windows-only CI failure
that does not reproduce on the dev box. `page()`, `search()` and `unedited_drafts()`
all end `, rowid DESC`, which is the order the mails actually arrived in.

**Schema changes are `ALTER TABLE ADD COLUMN` only.** `Store.migrate()` adds what
is missing to `mail` and nothing else; installed databases hold the only copy of
collected mail. A whole new table is different and allowed: it goes in
`Store.__init__`'s `executescript` as `CREATE TABLE IF NOT EXISTS`, which is how
`log` arrived, and `LogStore` repeats the same statement so whichever of the two
opens the file first is fine. The window opens its own connection on the UI thread — sqlite connections
are not shareable across threads, and WAL is what lets the worker keep writing.

**A version printed from an attribute is a version that will read `?`.**
`selftest`'s `check_native` asks importlib.metadata for pywebview's version, because
pywebview exposes no `__version__` and `getattr(webview, '__version__', '?')` printed a
bare `?` that looks exactly like a broken bundle. That needs the `.dist-info`, which is
why the spec carries `copy_metadata('pywebview')`; nicegui's arrives free with
hooks-contrib's own hook.

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
tests (`tests/test_webui.py`) — including the chart option dicts (`bar_option`,
`recent_option`, `trend_option`) and the `tag_cell` slot templates, which is why those
stay pure and nicegui-free — and `packaging/shoot.ps1` renders a page with headless
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
