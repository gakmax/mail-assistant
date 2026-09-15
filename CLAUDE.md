# CLAUDE.md

Windows-only tkinter app with a second, web-based set of screens. Polls a POP3 mailbox (Hiworks is only the default host), sends each mail to the
Codex CLI for analysis, stores everything in sqlite, and writes the result into a
desktop Excel workbook over COM. The window (`app.py`) reads from the database,
not from the workbook: 현황·메일·일정·실행·설정 tabs over `mail.db` — those are the
tkinter fallback's own tab names, and the web screens call the same first page
대시보드. The same database also backs the NiceGUI screens in `webui.py`, opened with
`MailAssistantTools.exe web` — ten pages on 127.0.0.1, sharing one `Hub`. Shipped as
an unsigned Inno Setup installer built by GitHub Actions, with in-app updates from
public GitHub Releases.

User-facing behaviour, install steps and troubleshooting live in `README.md` —
read it before changing anything the user sees. This file is only the things
that break silently if you don't know them.

## Commands

```bash
python -m unittest discover -s tests -v    # from the repo root; 689 tests, all platforms
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
(every `gpt-5.1-codex*` name was already refused by the API by the time the picker was
written), and a stale default fails *every* analysis with nothing on screen to explain
it. So a missing cache offers 기본값 alone, `model_rows()` keeps a row for whatever is
saved even after Codex stops listing it, and `field_errors` still rejects a model with a
space in it because the value becomes one `--model` argument.

**직접 입력 is gone, and 성능·사용량 are a position in Codex's own order.** The free-text
box was the developer's answer to a list that might be missing something, and what it
actually shipped was a box that took any word at all — a name Codex has retired fails
every analysis, quietly, which is the exact failure the cache is read to avoid. What
replaced it is one `ui.select` (a ttk `Combobox`, `state='readonly'`, in the fallback)
whose rows read 'GPT-5.6-Luna · 가볍고 빠름 · 사용량 적음', because the person choosing
here reads mail for a living and has no reason to know what a slug costs. The cache
carries no price and no speed field; it carries `priority`, the order Codex lists them
in, so `model_choices()` sorts by it and `model_traits(index, total)` turns a row's
*place* into the two words — relative, never absolute, whatever is on offer this week.
`GRADE_NOTE` says that out loud under the dropdown rather than letting the words read as
Codex's own answer, a model Codex has stopped listing carries no words at all, and
`webui.COST_TONES` colours only the 사용량 half (a test holds it against `GRADES`) for
the reason 분석 결과 spends its accent on two blocks of four.

**Codex 사용량 comes from the app-server, and it is the one Codex call outside the
slot.** The CLI knows what is left of the account's quota and `codex exec` is the one
place it will not say so: `--ephemeral` writes no rollout (which is the point — a
rollout would put mail bodies on disk under `~/.codex/sessions`), and the exec `--json`
stream carries token counts and no rate limits. So `services.codex_usage()` opens one
short-lived `codex app-server` over stdio, sends `initialize` and
`account/rateLimits/read`, and kills it in a `finally` — a server left running is one
orphan per beat. Three things follow. It does **not** take `codex_slot()`: that
semaphore exists because analysis, 상담 and the briefing share one quota, and this is
the only call that spends none — while also being the thing a reader most wants while a
240-second analysis holds the slot. It is read on `USAGE_SECONDS`, not on the shell's
five-second beat, and `Meter`'s own gate and lock are what keep ten open pages to one
process between them. And a quota nobody has managed to read draws **nothing** — not
0%, not '?' — which is `badge_text()`'s rule for a zero and `recheck()`'s for a check
that never reached GitHub. `usage.snapshot()` reads 'out of messages' from the
backend's own `rateLimitReachedType`/`ordinaryUsageAllowed` and never off 100%: a
percentage is a measurement and the permission is an answer.

**'분석 중' is a column on the mail, not a log line.** `Store.mark_analyzing()` writes
`analyzing` before `services.analyze()` and the worker's `finally` clears it — every
path, including the one that gives up, or a mail reads 분석 중 for ever and
`Store.reset()` then refuses to touch it. A crash cannot run that `finally`, so
`worker.run()` clears the marker at every start. Only one row ever carries it
(`codex_slot` is a `BoundedSemaphore(1)`), which is why `analyzing()` can return one
id. `state_of()` puts it *before* 실패: '2회 실패' of a mail Codex is reading right now
is a week-old fact. `reset()` skips a marked row and returns how many it changed —
clearing a result whose answer is already on its way would be overwritten, so a
button that said '요청했습니다' would be describing something that did not happen;
`reanalyze_text()` is that count turned into a sentence.

**'다시 분석' is a booking, not the analysis.** It clears the result and calls
`Hub.wake()`; the analysis is the worker's, exactly as collecting is. With 수집
stopped there is no worker to wake, and the screen said '요청했습니다' and nothing ever
moved — the same silence `collect_text()` exists to break, which is why
`reanalyze_text(asked, wanted, running)` adds the same kind of sentence rather than
the button being disabled: a request made before 시작 is real and keeps. `REANALYZE_TIP`
says the dependency unconditionally, because the tooltip is built once and the worker
can start or stop underneath it.

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

**The destinations live in `PAGES`; `NAV_GROUPS` holds nothing but paths.**
`nav_rows()` takes the name and icon from the first and the order and grouping from the
second, so a page added to `PAGES` and forgotten in `NAV_GROUPS` is reachable by URL and
by nothing on screen — a test holds the two sets equal, the way `core.STATES` is held
against `STATE_SQL`. They were one horizontal list in the header band until 0.6.0, with
`overflow-x:auto` and `scrollbar-width:none`: nine Korean labels already spent ~670px of
a 1240px band, so anything else put beside them pushed 설정 off the end with no scrollbar
and nothing on screen saying so. That is the arithmetic the sidebar exists for, not
taste.

**A sidebar badge is `nav_counts()`, never a `COUNT(*)`.** It calls `overview()` and
`board_counts()` — the same functions the 대시보드 cards and the kanban tally read — so
the number beside 메일 and the card called 미처리 메일 cannot disagree, and a mail whose
analysis produced no `next_action` is not counted as a 할 일 card by either. `badge_text()`
draws nothing for a zero, for the reason the 분석 실패 card is absent rather than 0.

**Collapsing the sidebar is CSS and localStorage; the server never hears about it.**
`rail_css()` holds the rail's rules once and `THEME` emits them twice — under
`html.ma-rail`, which the ≡ button writes, and inside the `SIDE_BREAK` media query under
`html:not(.ma-wide)`, which is the breakpoint's own answer when nobody has chosen. Three
states and not two: with only a rail class a narrow window could never be opened out
again, and the button would do nothing exactly where the labels are hardest to spare.
`RAIL_TOGGLE` is a `js_handler` for the same reason `dragover` is one — a class name is
not worth a websocket round trip — and `RAIL_BOOT` re-applies the choice from the *head*,
before Vue mounts, or a sidebar left collapsed opens and shuts on every page load. The
rail keeps the badge's **number**: '아이콘만' was never 'and no count', which is what the
8px dot it used to draw amounted to. The name the hidden `.ma-side__label` takes away
comes back as `::after` on the row's own `data-name` — the page's own tooltip, as on the
calendar, rather than a native `title` — and that one line is why `.ma-side` turns
`overflow:visible` in the rail.

**Nothing in `rail_css()` may be `display:none`, and the badge is absolute in both
widths.** The fold is one gesture and the sidebar animates its `width`, so every rule
the rail changes has to be a value the wide sidebar can travel *to*: the labels
collapse by `max-width:0` rather than vanishing on the first frame; the icons are
centred by `padding-left` rather than `justify-content`, which cannot be animated to
and so jumped the icon to the middle of a still-wide row; `.ma-side__group` carries a
pinned `SIDE_GROUP` height because no height animates from `auto`, and rolls up into
its own `border-top` rather than swapping a background; and `.ma-badge` is
`position:absolute` in the wide sidebar too, because a box that is in the row's flow
one frame and on the icon's shoulder the next teleports 150px left while the sidebar
it belongs to is still moving. One curve and one duration for all of it (`SIDE_EASE`),
or it reads as several things happening rather than one. A test holds each of those.

**원문's 복사 copies what is on screen, and `turn()` is what keeps it honest.**
One button for both readings rather than one each: 복사 can only mean the panel that is
showing. Switching readings is a visibility swap with no rebuild, so the tooltip is
moved by `set_text()` inside `turn()` — an element method, which needs no client — and
what goes on the clipboard is the text as it was written, never `body_panel()`'s HTML,
for the reason 상담's 답변 복사 copies `text` and not `rich_text(text)`.

**`.ma-chatwrap` is sized to the frame, and `CHAT_CHROME` is that arithmetic.**
The thread was `min(58vh, 470px)` and stopped ~200px short of the bottom of a window
that opens at `WINDOW`. It is `calc(100vh - CHAT_CHROME)` now — the header band (56px
and its hairline) plus `.ma-page`'s own 20px/56px padding, measured off the real frame
— with `.ma-thread` a flex column and `.ma-chat` the one thing in it that grows. Below
the 900px breakpoint the two cards stack and the height goes back to a number, because
stacked they are taller than any window. Change `.ma-page`'s padding and this constant
is what goes stale; a test holds the two together.

**The 대시보드's 수집 기록 is shut, and its state lives outside the refreshable.**
`run_block` is rebuilt on every `REFRESH_SECONDS` beat, so the open/shut choice cannot
live in `run_strip()` — `home()` owns the dict and hands it in, exactly as `window`
owns the 마감 toggle. The fold itself is `grid-template-rows:0fr → 1fr` and a class
swap in the handler, never a `refresh()`: a height cannot animate from `auto`, and the
rebuild is what the animation would be thrown away by. The chevron is absent when
there is no message and no log, for the reason an empty badge draws nothing.

**The fold button lives on the sidebar, and in the rail it is the mark's own square.**
It sat in the header band until 0.8.2, which put the control a page away from the thing
it moves and spent band width on navigation. `.ma-side__top` holds the brand link and
the button; in the rail there is one square at the top and both want it, so the button
is absolutely positioned over the mark and `.ma-side__top:hover` swaps their opacity —
the collapsed sidebar's only press has to be a press, not a 12px corner. The rotate rule
followed the class, so `rail_css()` names `.ma-side__fold` and a test holds the header
band's old class out of `THEME`. Its tooltip is anchored `center right` by hand rather
than left to Quasar's default, which drops it onto the first nav row the fold just hid.

**`.ma-main` is a block, never a flex column.** `.ma-page` centres itself with
`margin:0 auto`, and an auto cross-axis margin on a *flex item* beats `align-self:stretch`
— the page then shrink-wraps to its content, which on the 대시보드 wrapped the four KPI
cards 3+1 and left a 220px gutter down both sides. Nothing errors and no test sees it;
it simply looks like a window someone forgot to fill. `min-width:0` on the same rule is
the other half: a flex child's default minimum is its content, so without it the 메일
table pushes the sidebar off the screen instead of scrolling.

**`shell()` repaints; it never rebuilds.** One `chrome()` call per `REFRESH_SECONDS` beat
feeds the badges, the 수집 중 chip and the 새 버전 pill together, and the tick then uses
`set_text`/`style`/`classes` only — every page in the app is drawn inside this shell, so a
`@ui.refreshable` here would take the 메일 목록's ticked checkboxes, the search box being
typed into and the open dialog with it. The counts are re-read only when `Hub.revision`
moves *or* the cache is older than one beat, because the worker collects without touching
that integer; and `home()`'s `read()` calls `note_counts()` with the rows it has already
fetched, so the 대시보드 does not read the whole mailbox twice on the same tick.

**A `q-table` cell slot is coloured by an expression, never by a JSON blob.**
`tag_cell()` builds the `:style` lookup with single quotes throughout, because the
whole expression sits inside a double-quoted Vue attribute and `json.dumps` would
close it on the first key. `'3회 실패'` carries its count, so the 실패 tone is matched
with `props.value.includes('실패')` rather than a table key. There is no longer a mark
for the open row: the mail opens in a dialog over the list, and a highlight that only
moves when the table is rebuilt pointed at the wrong row more often than the right one.

**A mail's hover card is `mail_tip()`, and it rides on the row.** One shape for the
브리핑's 먼저 볼 메일 and for the 메일 목록's own ⓘ button, so the same mail cannot
describe itself two ways on one screen. It is built in `listing()` from the row the
list already fetched — `Store.LIST_COLUMNS` carries `result`, so there is no second
query — and the q-table slot reads `props.row.tip.*` in the browser: a hover that asked
the server would be a round trip per pointer. Two consequences. Its `summary` and
`action` are in `list_signature()`, because a re-analysis can rewrite them while
우선순위 and 상태 stay where they were, and a repaint is the only thing that refreshes
a tooltip drawn from a row. And the `ⓘ` carries `@click.stop`, for the reason
`body-selection` does: Quasar lets a cell's click reach the row, which opens the mail.

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

**The 메일 목록 repaints itself every second, and a repaint clears the checkboxes.**
That is the whole difficulty: q-table's ticks live in the element the rebuild
destroys. So `watch()` compares `list_signature()` — the columns the table actually
draws, and nothing else, because a repaint for an invisible change would cost the
user a selection they made — and `rows()` carries the previously ticked ids across
the rebuild by *extending* `table.selected` (the same list object as the `selected`
prop; rebinding it changes nothing). The poll also stops while a dialog is over the
list: rebuilding rows nobody can see costs a query and pulls the ground out from
under the open mail. 실행 runs on the same `LIVE_SECONDS` beat, which is why
`run_summary()` now uses the thread's cached `store()` instead of opening a `Store` —
a fresh one runs `migrate()` and both backfills before it can answer.

**The 대시보드 follows the other screens through `Hub.touch()`, not through sqlite.**
Each page is its own client with its own elements and can tell another page nothing;
the process is all they share. Every screen that writes calls `bump()`, and `home()`'s
`beat()` compares one integer each second, repainting at once when it moved and on the
slow `REFRESH_SECONDS` beat otherwise. Nothing is *lost* without it — every page
re-reads on its own timer and a navigation is a fresh page load — it only decides
whether a deletion lands on the cards in a second or in five. The charts still update
in place; only the text blocks are refreshable.

**The 초안 화면 announces a new draft and never rebuilds itself.** The one thing on
that page is a textarea somebody is in the middle of, and `queue.refresh()` would
replace it with whatever the database last saw — the same edit-eating the window's
`draft_baseline` exists to stop. `watch()` compares the queue's ids and sets a notice
label outside the refreshable; only an empty queue, which has nothing to lose,
refreshes itself.

**The 메모 화면 is that same rule, times the number of cards on it.** Every memo is a
textarea somebody may be inside, so the `REFRESH_SECONDS` tick compares
`note_signature()` — the columns the wall draws, deliberately *including* the text,
because the 메일 detail writes into the same table — and only sets a notice. What does
rebuild is 고정 and 삭제, which move or remove a card and so cannot be done in place;
what is being typed survives them because clicking a button blurs the textarea first
and Quasar flushes a debounced input on blur. **Colour is the exception and must stay
one**: `recolor()` writes the new ground onto the element and swaps the `is-live`
swatch by hand rather than refreshing, because a rebuild there would cost the text for
a change that moves no card. `ui.notify` goes *before* the rebuild in every one of
these handlers, for the reason 업데이트's 지금 확인 learned it.

**A memo is `mail_id`, and that is the whole reason it is not a second 할 일 판.**
The key is `chat.mail_id`'s exactly — a mail's id, or `''` for a free-standing note —
which is what lets the 메일 detail carry the memos written about that mail and the
메모 화면 filter to 메일에 붙임. `Store.delete()` therefore calls `detach_notes()`
rather than deleting them: the analysis, the draft and the chat all came *from* the
mail and go with it, but a memo is the user's own writing and dropping it because they
dropped the mail is the same edit-eating the draft box is guarded against. The 삭제
confirmation says so out loud. Subjects come from `Store.mail_subjects()`, one
`IN (…)` shared with `rooms()`, so a memo on a deleted mail is simply absent from the
map instead of a second answer.

**'새 메모' inserts the row, and `delete_empty_notes()` is the other half.** The row
has to exist before the card can be drawn with the caret in it (`autofocus` on the
textarea, never a `run_method` after a `refresh()`), so changing your mind leaves a
blank. It is swept the next time a wall is rebuilt for some other reason — never while
it is on screen, and never on a timer — and the sweep uses sqlite's *two-argument*
`trim`, because the one-argument form strips spaces only and a memo opened and left
alone holds the newline the caret put there. `note_tally()` is not `note_counts()`:
`build()` already has a local of that name feeding the sidebar cache, and a module
function it shadows is a bug waiting for whoever calls it inside that scope.

**메모 is the one page with no sidebar badge, on purpose.** `nav_counts()`' numbers are
all 'what is left'; every memo is just a memo, so the count would never fall, and a
number that never falls is one a reader stops seeing — the same arithmetic that keeps
the 분석 실패 card absent rather than 0. The 메모 wall also overrides `grid()`'s
`auto-fit` with `auto-fill`: fit collapses the empty tracks, so a 고정 wall holding one
memo drew it the full width of the page above a 메모 wall of ordinary cards.

**The 메일 detail is a dialog, and the list is repainted when it closes — if it
changed anything.** Rebuilding the table is also what clears q-table's checkboxes, so
a mail opened to be *read* must not cost the user the selection they made; `touched`
is set by the things that change a list column (처리 상태, 다시 분석, 삭제) and nothing
else. Refreshing while the dialog is open is the same work done where nobody can see
it, behind a dialog that covers the rows.

**A mail card swept off the 할 일 판 is `todo_hidden`, not a deletion.** The board
grows a card for every analysed mail that produced a `next_action`, and before this
the only way to be finished with one was to mark the mail 완료 — a different sentence.
`board()` skips the flag, so `board_counts()` and the 대시보드 tally drop the same card
at the same moment; the mail itself is untouched, and the only way back is the 메일
detail's 할 일 판에 다시 올리기, which is why that button has to live there.

**A 직접 추가한 일정 rides in the same field as a mail id, and `EVENT_MARK` is why
that is safe.** Every panel that draws an entry — the calendar, 오늘 일정, the 마감
checklist — holds `entry.mail_id`, so a manual event has to look like one. Its key is
`'@' + rowid`, which a mail id (24 hex characters) cannot be, exactly as `ROOM_MARK`
works for a free-standing 상담 room. `overview.event_lines()` turns the rows into the
일정 sheet's own row shape and hands them to the same `collect()`, so 마감/시작, the
clock and the sort are decided once for both kinds — a manually added deadline that
sorted differently from an analysed one would be the same day drawn two ways. Three
things follow and each is a place it breaks quietly: a manual row must not be *linked*
to a mail (`is_event_key` gates that in `today_rows`, `deadline_rows` and the
calendar's `eventClick`, which would otherwise send 메일 화면 looking for an id no mail
can carry); the 마감 tick routes on `event_row_id` and writes `event.handled` instead
of `mail.handled`; and `overview()` folds the finished ones into the same `handled`
set, because every panel below it asks 'is this one done' of one set. The Excel 일정
sheet is written from analysed mail and never sees them, which is what the README says
out loud.

**The 일정 화면 reloads after a manual event; it does not repaint.** The grid is a
FullCalendar built in the browser from a payload baked into the page by
`ui.add_body_html` — there is no server-side handle on it, and `ui.run_javascript`
cannot draw it (see the invariant above). `manual_panel`'s `on_change` is therefore
`ui.navigate.to` of this same path. That is the cost of the payload-in-the-page rule,
not an oversight.

**분석 결과 is blocks, and the accent is spent on two of them.** It was four
`ui.label` pairs in a column and read as one wall of text: the headings were the only
thing dividing 요약 from 다음 행동 and they were 11px grey. `ANALYSIS_PARTS` holds the
order, the icon and the accent; `analysis_blocks()` drops the empty ones, and only
요청사항 (what they asked) and 다음 행동 (what you do) carry a colour — accenting all
four is accenting none, which is the same arithmetic as 시작's hue on the calendar.
`PART_TONES` is checked against `ANALYSIS_PARTS` by a test for the reason `CARD_TONES`
is checked against `card_rows()`. `event_kind()` gives an analysed event the calendar's
own kind, and calls one 확인 필요 when *neither* date parses even if Codex did not say
so — the card is then the only place that can tell the reader why that event is on no
calendar, which is the one thing the calendar itself cannot say.

**A table cell that emits nothing joins its neighbours.** `TextHTML` gave `br p div
tr li` a newline and `td`/`th` nothing at all, so 수주번호 A26090135 beside 공급가액
90,000 reached Codex — and 원문 — as `A2609013590,000`, one run with no way back to
the two figures. Hiworks states 수주·견적·정산 in tables, so this was quietly costing
accuracy on exactly the mail that carries numbers. Rows are collected now and
`table_text()` decides between two shapes: a Markdown grid, whose separator row is
what tells a model which column a cell is in, and — for anything under `TABLE_SHAPE`
— plain lines. That second case is not a fallback but the common one: mail HTML wraps
bodies and signatures in single-cell tables, and a grid drawn round a signature is
noise, which is also why a layout cell keeps its own line breaks while a grid cell is
squashed to one line. A nested table is rendered when its own `</table>` is seen and
written into the cell holding it, so the real table inside a layout wrapper survives.
`parsed` is written once by `Store.analyzed()`, so mail analysed before this keeps the
old run-together body until 다시 분석; `raw` is still there, which is what makes that
possible.

**원문 draws the table back, and never the mail's own HTML.** `body_blocks()` splits
the body into `('text', …)` and `('table', rows)` by finding the Markdown `table_text()`
wrote, and `body_panel()` builds a real `<table>` out of elements — the cells go in
through `ui.label`, so markup a sender wrote cannot reach the DOM and the panel needs
no sanitiser of its own. Rendering the mail's HTML instead would: that is the whole
reason this goes the long way round. A table is claimed only when a header, the rule
under it and a row are all three present, because a sender may well write a line that
begins and ends with a pipe, and drawing a grid round that is the panel inventing a
table nobody sent. The class is `.ma-sheet`, not `.ma-grid` — that one is `display:grid`
and every layout on the site uses it — and not `.ma-table` either, which is the 메일
목록's, with a pointer cursor and a row hover for rows that open something. The mail
decides how many columns it has, so `.ma-sheet__wrap` scrolls sideways on `.ma-scroll`'s
own thumb rather than the dialog doing it.

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

**`chat.mail_id` is the room key, and always was.** A mail's id, or `''` for 일반
상담 — which is why the 상담 thread list needed no migration of what is already
stored. A free-standing 새 대화 is the third kind and the only one that owns a name,
so `chat_room` holds nothing but that; its ids start with `core.ROOM_MARK`, which a
mail id (24 hex characters) cannot. `Store.rooms()` is one grouped query plus one
`IN (…)` for the subjects, never one query per room. `name_room(..., only_if_unnamed)`
puts the guard in the UPDATE rather than in a read-then-write: a second question sent
while the first was still being answered would otherwise rename the room out from
under the title it had just taken. `drop_room()` refuses anything without the marker,
because a mail's thread goes with the mail and that button must not be able to reach
it.

**A 상담 answer belongs to the room it was asked in.** `ask()` captures `state['room']`
before the await, not after: a reply takes tens of seconds and the reader may well
have moved on. It is stored against the captured room either way, and `thread.refresh()`
is skipped — with a notice instead — when the open room has changed, or the answer
would be painted into somebody else's thread. Switching rooms redraws `thread`,
`rooms` and `head` rather than navigating, so a half-typed question survives it.

**A mail's thread has a name before it has a turn.** `Store.rooms()` groups `chat`,
so a thread opened from 메일 is in no list until the first question is stored — and the
head read (제목 없음) for the whole of the first answer. `current()` falls back to the
mail's own subject and puts the shaped row at the top of the list as well, so the thread
being read is not the one row the list does not have.

**`ui.clipboard.write()` is not awaitable.** nicegui 3 returns None from it, and the
`await` that used to sit in front of it raised inside the handler — which killed the
`ui.notify` after it, so 복사 copied and said nothing, with the error only in the log.
The 상담's 답변 복사 copies `text`, never `rich_text(text)`: what is on the clipboard
should be what Codex wrote, not the HTML the bubble was drawn from.

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
800x600 unless told otherwise, which puts 대시보드's two columns and 메일's six-column table
inside a scrollbar from the first launch. `serve()` passes `window_size(screen_size())`
into `app.native.window_args`, and `app.py` asks tkinter for the same number — importing
it from `webui`, which costs nothing because that module imports no toolkit at module
level. Both clamp `min_size` to the opening size: on a small screen a minimum larger
than the window is what pywebview obeys, and the title bar ends up below the desktop
where it cannot be dragged back.

**오늘 일정 reads `overview()['events']`, which is what the calendar draws.** Not a
query of its own: the 대시보드 and the 일정 화면 must not be able to disagree about what
is on today. It keeps 시작 where the 마감 panel below it drops it — the two answer
different questions ('오늘 무엇이 있는가' against '언제까지인가') — and drops a handled
mail exactly as `calendar_events()` does. `day_title()` builds '9월 14일 (월)' by hand
from `WEEKDAYS`: Korean in a `strftime` format is the Windows crash above.

**`overview()['cards']` holds exactly four, and that is a layout contract.**
`App.paint_overview()` in `app.py` walks a fixed list of four tkinter labels, so a
fifth key added to that dict is drawn nowhere and silently lost. The web page's fifth
card, 분석 실패, is therefore a separate `'failed'` count that `webui.card_rows()`
appends — and appends only when it is non-zero, because a card reading 0 every day is
how a reader learns to stop looking at that corner. `CARD_TONES` is checked against
`card_rows()` and not against `['cards']` for the same reason.

**The 대시보드 할 일 tally goes through `board()`, never through SQL.** `board_counts()`
calls the same function the kanban renders from, so the two cannot disagree about what
counts as a card — a mail whose analysis produced no `next_action` is not one, and no
`COUNT(*)` on `handled` knows that. The same rule makes `home()`'s `read()` fetch rows
and todos itself instead of calling `snapshot()`: the tally needs the rows the overview
already read, and a second `page()` every five seconds reads the whole mailbox twice.

**The 마감 checklist keeps what the card drops.** `overview.deadlines()` and
`past_due()` filter handled mail out because the 대시보드 cards count what is *left*;
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

**오늘의 AI 브리핑 is the worker's, once a day, and always behind analysis.**
`worker.run()` builds it at the *end* of a cycle and `overview.briefing_due()` is the
whole trigger: a date stamp in `meta` is what makes it daily — the loop already wakes
every `interval`, so there is no scheduler — and `pending` is what makes it wait.
`services.codex_slot()` is a `BoundedSemaphore(1)` and `briefing()` can hold it for 240
seconds, which at nine in the morning is the queue of mail nobody has read yet. Three
things follow. `BRIEF_HOUR` keeps a briefing written at 03:00 from being what the reader
finds at nine. A failure sets `next_briefing` rather than retrying on the next
180-second cycle. And 다시 만들기 is a *booking* — it writes `briefing_ask:<account>` and
calls `Hub.wake()`, exactly as 다시 분석 does, because the page cannot hold that slot for
four minutes inside a shell that repaints every five; `briefing_text()` says which of
the two sentences applies, and the worker clears the flag *before* the attempt so a
failed request does not re-fire for ever with nobody having asked again.

**A briefing's input is analysed answers, never mail.** `overview.briefing_input()` is
pure and tested, and every field in it came out of a `result` JSON that `analyze()`
already paid for. `BRIEF_OPEN`/`BRIEF_EVENTS`/`BRIEF_DRAFTS`/`BRIEF_TEXT` hold it to
roughly 8-12KB, which is where the answer stops getting sharper; thirty bodies at
`analyze()`'s own 60,000-character ceiling is the 240-second timeout instead. What the
caps cut is reported in the payload's own `truncated`, because a model that saw thirty
of eighty-seven has to be able to say '그 외 57건' rather than write '조용합니다' about
the thirty. `briefing_view()` then re-clips on the way out: the schema constrains shape,
not length, and the card is a card. `plain_title()` moved to `calendar_sheet.py`
beside `MARKERS` when the payload started stripping ■/▶/◆ as well: `overview.py`
cannot import a screen, and two copies of that strip is how the marker comes back.

**The 브리핑 card's frame is built outside the refreshable.** A CSS animation restarts
from zero every time its element is created, so `.ma-beam` inside `brief_block()` would
jump back to the top of its lap on every five-second repaint — the same failure the
charts beside it avoid by being updated in place. `briefing_frame()` is therefore called
once by `home()` and `briefing_body()` is what refreshes inside it. The beam itself is a
conic gradient turned by an `@property`-registered angle and masked down to the 1px
border, which is why there is no extra element and nothing to lay out; without the
`@property` registration the ring simply sits still, which is the fallback and not a
break. One card carries it and a test holds that to one, for the reason 분석 결과 spends
its accent on two blocks of four.

**A stale briefing says which day it is for.** `briefing_view()` keeps `day` beside the
text rather than checking it away, and `stale_text()` is the '2일 전 브리핑입니다' band —
a card that showed yesterday's without saying so is the same silence a 지금 확인 that
claimed 최신 버전입니다 behind a dead proxy was. `briefing_empty()` is the other half:
three reasons (no mail, no collector, before `BRIEF_HOUR`) and never blank space.
`local_text` gives the clock and `day_title()` builds the Korean date by hand, because
Korean in a `strftime` format is the Windows crash above.

**먼저 볼 메일 is the mail's subject; the reason is what the hover is for.** The pin
drew Codex's `reason` until 0.8.2 — four chips, each a sentence about a mail, none of
them saying *which*. `watch_pins()` names it from `mail_tip()` and `briefing_card()` is
the one impure line that joins the two: the ids are not known until the stored JSON has
been read, which is why they cannot be a parameter of `briefing_view()`, and it is one
`Store.mail_cards()` for the whole card rather than a `detail()` per pin per beat. A pin
whose mail has been deleted since is dropped rather than drawn, exactly as a pin on a
manual 일정 is: a chip that opens an empty 메일 화면 is worse than one that is absent.

**The 브리핑's sections are marked by position, never by title.** `BRIEF_MARKS` gives
the first section 우선(red), the second 할 일(brand) and the third 일정(amber), and
`briefing_view()` attaches them *after* the empty ones are dropped — the mark belongs to
the place the reader sees, not to the index in the JSON. By position because Codex names
its own sections: the schema constrains shape, not wording, so a title match would turn
back into four identical blue headings the first time it reworded one. The fourth carries
no colour, for the reason 분석 결과 spends its accent on two blocks of four.

**The 대시보드 charts are updated, not rebuilt.** `home()` creates its four `ui.echart`
elements once and `paint()` writes new options into them every `REFRESH_SECONDS`; only
the text blocks are `@ui.refreshable`. Wrapping a chart in a refreshable drops the
canvas and replays the entry animation on every tick, which on a 5-second timer reads
as a page that will not sit still.

**An entry that knows its time is a timed event.** `calendar_sheet.Entry` carries
`clock` beside the label because the label is built for an Excel cell, which has
neither an icon nor a slot to sit in and so spells everything out. `calendar_events()`
turns that into a real `start` with `allDay: False`: while every event was allDay, 주
stacked a 14:00 웨비나 in the 종일 band and left the 2pm row — the only thing a week
view is for — empty. `event_title()` then takes the clock back off the title, because
the slot and `eventTimeFormat` already say it; `clean` keeps it for the tooltip, which
has no slot. 주 is also the one view with 24 hours behind it, so `datesSet` swaps
`height` to a fixed number there (the grid gets its own scroll, which is what makes
`scrollTime` mean anything) and back to `'auto'` elsewhere — and `scrollToTime` is
called *after* the tick, because `setOption` rebuilds the scroller it scrolls.

**Kind is carried by `KIND_ICONS` and three hues, not by three sizes of glyph.**
마감 is red, 시작 is `LINK` blue and 확인 필요 grey — 시작 used to share `SOON` with
'높음', and beside URGENT's deep red an amber block is the same colour at a glance,
which on the calendar is exactly where the two sit side by side. The web draws a
Material icon of one size (`eventContent` builds nodes, never innerHTML: the title is
whatever the sender wrote); Excel keeps ■/▶/◆, one cast and one width. `legend_text()`
counts its colour offsets off the string rather than striding by a constant.

**The 일정 tooltip is the page's own, and there is exactly one of it.** `info.el.title`
was the desktop's: it waits a second, wraps where it likes, and cannot show the kind's
colour. `showTip()` fills one `div.ma-tip` — one for the page, not one per event,
because a month view mounts and unmounts hundreds — measures it *after* the content is
in so it can be clamped to the viewport, and `hideTip()` is wired to `scroll`
(capturing), `resize`, `datesSet` and `eventClick`, because a tooltip anchored to an
element that has been scrolled away or rebuilt is pointing at nothing. The title comes
from `extendedProps.clean`: `calendar_sheet` puts ■/▶/◆ on the front of a label so an
Excel cell can say 마감·시작·확인 필요 in one colour, and beside `KIND_ICONS` and the
kind spelled out that marker is the same thing said twice. `plain_title()` strips it
everywhere on the web — the tooltip, 오늘 일정, the 마감 checklist and 지난 마감 — each
of which draws the icon instead.

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

**The update check has a beat, and it is the shell's.** `update.check()` was called
in exactly two places — `home()`'s once-only timer and `App.boot()` — so an app left
open, which is what a mail poller is, only ever checked at launch: CHECK_SECONDS could
pass all week with nobody asking, and 지금 확인 was the only way to learn about a
release. `Updater.watch()` is that beat's look, and it is deliberately *not* forced —
`check()`'s own six-hour gate is what decides whether a beat costs a request, so
`WATCH_SECONDS` only decides how soon after the gate opens somebody hears. It lives in
`update.py` because both windows read it. Three things follow. The timer is in
`shell()`, not on the 대시보드, because the shell is the one thing every page has. It
never opens the dialog — that one lands at launch on a page nobody has typed into, while
this one can arrive over a draft, a memo or a half-written 상담 question; the pill and a
`ui.notify` say it instead, and the pill needs no wiring because `chrome()` already reads
the same `offer`. And `watch()` returns None while an offer is on the table or a download
is running, or a beat would re-offer what the reader is already looking at. The tkinter
fallback repeats the same beat with `root.after`, rescheduling *before* the check so a
raise cannot make one beat the last.

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

**`services.codex_json()` is the one `codex exec` this app makes.** Analysis, 상담,
the briefing and 번역 differ in what they send and in what they say when it fails; the
sandbox flags, the `--model` splice, the one slot, the 240-second process timeout and
the rule that stdout is never persisted were four copies of the same twenty-five lines
and are now one. A new caller passes a prefix, a prompt, a payload, a schema and its
own Korean failure sentence — and nothing else, because everything else is the part
that must not drift.

**A body over `BODY_LIMIT` is clipped, and the clip is said in three places.**
60,000자 is what comes back inside the 240-second timeout. What makes clipping safe is
that nothing about it is silent: `CLIP_NOTE` goes on the front of the prompt so a model
that saw half a mail does not write '일정 없음' about the other half, `parsed['clipped']`
carries the *original* length into the database, and the 메일 상세 draws an amber band
(`clipped_note()`) above the analysis with 실행 saying it too. `parsed` keeps the **whole**
body — only what is sent to Codex is cut — because 원문 is drawn from that field and a
reader told 'the analysis saw 60,000 of 72,013자' has to be able to read the rest.

**A failure a retry cannot fix is `services.Unanalyzable`, and the worker puts that
mail down.** A mail whose raw bytes will not parse, and one with neither a body nor a
subject, are the two: the next cycle reads the same bytes. Before this they rose as
plain `RuntimeError`s, which is the branch that reports to Discord, pushes the *global*
`next_analysis` backoff to an hour and `break`s the loop — so one mail that could never
succeed stalled every other mail behind it, for ever, and posted a crash report each
time round (report.py's 30-minute de-duplication does not help: the backoff grows past
it; 6번째 시도 is what arrived). `Unanalyzable` gets its own branch —
`store.failed(id, str(exc), NO_RETRY)`, no report, no backoff, `continue` — and it is
the one failure whose message is stored on the mail, because it is ours rather than
Codex's output. `core.NO_RETRY` is a far-future `retry_at` and `Store.reset()` writing 0
over it is the way back in, which is what makes 다시 분석 the only thing that asks again.
`NO_SUBJECT` is named in core for this check alone: `parse_mail()` writes '(제목 없음)'
where a header was missing, so '제목이 없다' is not the same test as `not subject`.

**분석은 답변 초안을 쓰지 않는다, and a made one goes into `reply_draft`.** The
analysis's prompt now says `reply_draft는 항상 빈 문자열` while still judging
`reply_needed` and `reply_subject` — the review queue and the 검토 전 초안 card stand on
those two, not on the draft, so nothing downstream moved. A draft nobody asked for is a
draft written in a voice nobody chose, and it cost a slot the next mail's analysis
wanted. `Store.set_reply_draft()` writes the answer into the stored result's own
`reply_draft` rather than into `draft_edit`, because `review_queue()` reads that column
as '사람이 손댔다' and a draft nobody has read yet belongs in the queue. It *clears*
`draft_edit`, which is the one place in this app where text a person typed is thrown
away: the screens show `draft_edit or reply_draft`, so leaving an edit would store a
draft nobody could see. That is why the button reads 초안 새로 만들기 when the box is
not empty, and why nothing on a timer may ever call this.

**`DRAFT_TONES` and `DRAFT_WAYS` live in `services.py`, and the value is the
instruction.** The toggles in `webui.draft_maker()`, the comboboxes in `app.py` and the
prompt read one list, the way `core.STATES` and `STATE_SQL` are held together — a test
holds them equal. The first entry of each (기본, 메일에 맞춰) sends an *empty*
instruction rather than the word itself: '기본 말투로 쓰세요' is a constraint a model
invents a meaning for. A 방향 goes as a whole sentence (`DRAFT_WAY_ASKS`) because '거절'
alone reads as what the mail is about rather than as what the reply should do.

**번역 is on demand, and `looks_foreign()` only chooses which tab opens.** The Korean
of a foreign mail is a second Codex run on the same single slot, so it is not in
`analyze()`: doing it for every collected mail doubles what that slot has to get
through before anything is on screen at all. The button is offered on every mail and
drawn `outline` rather than `flat` when `core.looks_foreign()` says the body is
somebody else's language — a letters-only ratio, so a 수주 mail of part numbers and
prices is still Korean, and so is a Korean mail with an English thread quoted under
it. Nothing is *claimed* from that guess: no banner says 한국어가 아닙니다, because it
would be wrong about exactly those two. The answer is kept on the mail (`translated`,
`translated_from`) because it costs a Codex run to make again, and a body over
`TRANSLATE_LIMIT` is refused rather than clipped — half a mail translated is worse
than none, since nothing on screen could say which half. 원문 and 한글 번역 are two
elements whose visibility is swapped, never a `refresh()`: the figures are in the
original, and the reader's place in it is what a rebuild would cost.

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
