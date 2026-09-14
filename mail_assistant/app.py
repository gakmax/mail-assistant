"""The desktop window: 현황·메일·일정·실행·설정 tabs over the same database the worker writes.

The database is the source of truth; Excel is an export. Everything here reads from
sqlite through its own connection (WAL, so the worker can keep writing).
"""
import json
import os
import queue
import subprocess
import threading
import tkinter as tk
from datetime import date, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, update
from .calendar_sheet import COLORS, WEEKDAYS, next_month, weeks_of
from .core import (FAILED, HANDLED, STATES, Store, account_key, filter_rows, local_text,
                   parse_mail, row_view)
from .dashboard import CATEGORIES, PRIORITIES, describe
from .excel import mailto
from .hub import line_text
from .overview import overview
from .report import remember_secret, report
from .settings import CUSTOM, FIELDS, model_rows, model_value, normalize, read_models
from .style import ACCENT, CALM, DASH_BLUE, DASH_GREEN, DASH_RED, SOON, TODAY_FILL, tk_color
# Size only: webui imports no toolkit at module level, and both windows should
# open at the same size rather than each picking its own.
from .webui import WINDOW_MIN, window_size

ICONS = {'start': '▶', 'stop': '■', 'now': '⟳', 'excel': '▤', 'test': '⇄', 'settings': '⚙',
         'login': '⌨', 'folder': '📁', 'mail': '✉', 'retry': '↻', 'save': '💾', 'update': '⬆'}
LAMP = {'실행 중': '#1a7f37', '중지됨': '#6b7280', '연결됨': '#1a7f37', '로그인 필요': '#b91c1c',
        '확인 실패': '#b45309', '확인 중': '#6b7280', '저장됨': '#1a7f37', '없음': '#b91c1c'}
CARD_COLORS = (ACCENT, DASH_RED, DASH_BLUE, DASH_GREEN)
LIST_COLUMNS = (('received', '수신', 110), ('sender', '발신자', 180), ('subject', '제목', 300),
                ('category', '종류', 90), ('priority', '우선순위', 80), ('state', '상태', 90))
CELL, CELL_HEIGHT = 118, 84


class App:
    def __init__(self, root, directory, config_path, config, services, hub, autostart=False):
        self.root, self.directory, self.config_path, self.config = root, directory, config_path, config
        self.services = services          # lazily imported win32-dependent helpers
        self.hub = hub                    # owns the worker thread and the run log
        self.autostart = autostart
        self.events = hub.subscribe()
        self.busy = set()
        self.rows, self.views, self.data = [], [], None
        self.selected = None
        self.offer = None
        self.booted = False
        self.cancel = threading.Event()
        self.pending_installer = None
        self.pending_autostart = False
        self.sort = ('received', True)
        self.month = date.today().replace(day=1)
        self.db = None
        self.closing = False
        self.password_error = None
        self.draft_source = None
        self.draft_baseline = ''
        self.build()
        self.refresh()
        self.check_password()
        self.check_codex()
        self.replay()
        self.log('설정을 확인하고 시작을 누르세요. 첫 연결에서는 기존 메일을 제외합니다.')
        self.root.after(300, self.poll)

    # ------------------------------------------------------------------ data

    def store(self):
        """One connection, owned by the UI thread. WAL lets the worker write meanwhile."""
        if self.db is None:
            self.db = Store(self.directory / 'mail.db')
        return self.db

    def account(self):
        try:
            return account_key(self.config)
        except (KeyError, AttributeError):
            return ''

    def refresh(self):
        account = self.account()
        try:
            self.rows = list(self.store().page(account)) if account else []
        except Exception as exc:
            report('메일 목록 읽기 실패', exc)
            self.log(f'목록을 읽지 못했습니다: {type(exc).__name__}: {exc}')
            self.rows = []
        self.views = [row_view(row) for row in self.rows]
        self.data = overview(self.rows, date.today())
        self.refresh_stamps()
        self.paint_overview()
        self.paint_list()
        self.paint_month()

    def row_of(self, ident):
        return next((row for row in self.rows if row['id'] == ident), None)

    # ------------------------------------------------------------------ layout

    def build(self):
        self.root.title(f'메일 도우미 {__version__}')
        width, height = window_size((self.root.winfo_screenwidth(),
                                     self.root.winfo_screenheight()))
        self.root.geometry(f'{width}x{height}')
        self.root.minsize(min(WINDOW_MIN[0], width), min(WINDOW_MIN[1], height))
        style = ttk.Style()
        style.configure('Action.TButton', font=('맑은 고딕', 10), padding=(10, 7))
        style.configure('Card.TLabel', font=('맑은 고딕', 9), foreground=tk_color(CALM))
        style.configure('Value.TLabel', font=('맑은 고딕', 22, 'bold'))
        style.configure('Section.TLabel', font=('맑은 고딕', 11, 'bold'))
        style.configure('Dot.TLabel', font=('맑은 고딕', 12))
        head = ttk.Frame(self.root, padding=(14, 12, 14, 0))
        head.pack(fill='x')
        ttk.Label(head, text='메일 업무 도우미', font=('맑은 고딕', 15, 'bold')).pack(side='left')
        ttk.Label(head, text='하이웍스 메일을 정리하고 엑셀에 반영합니다. 답변은 초안으로만 저장합니다.',
                  style='Card.TLabel').pack(side='left', padx=(12, 0), pady=(6, 0))
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill='both', expand=True, padx=12, pady=12)
        for title, builder in (('현황', self.build_overview), ('메일', self.build_mail),
                               ('일정', self.build_calendar), ('실행', self.build_run),
                               ('설정', self.build_settings)):
            frame = ttk.Frame(self.tabs, padding=14)
            self.tabs.add(frame, text=f'  {title}  ')
            builder(frame)

    # 현황 -------------------------------------------------------------

    def build_overview(self, parent):
        cards = ttk.Frame(parent)
        cards.pack(fill='x')
        self.card_values = {}
        for index in range(4):
            cards.columnconfigure(index, weight=1)
        for index, name in enumerate(('미처리 메일', '긴급·높음', '7일 내 마감', '검토 전 초안')):
            box = ttk.Frame(cards, relief='solid', borderwidth=1, padding=12)
            box.grid(row=0, column=index, sticky='ew', padx=(0 if index == 0 else 8, 0))
            ttk.Label(box, text=name, style='Card.TLabel').pack(anchor='w')
            value = ttk.Label(box, text='—', style='Value.TLabel',
                              foreground=tk_color(CARD_COLORS[index]))
            value.pack(anchor='w')
            self.card_values[name] = value
        panels = ttk.Frame(parent)
        panels.pack(fill='both', expand=True, pady=(14, 0))
        panels.columnconfigure(0, weight=1)
        panels.columnconfigure(1, weight=1)
        panels.rowconfigure(0, weight=1)
        left = ttk.Frame(panels)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
        ttk.Label(left, text='메일 종류', style='Section.TLabel').pack(anchor='w')
        self.category_bars = tk.Canvas(left, height=150, highlightthickness=0, background='white')
        self.category_bars.pack(fill='x', pady=(4, 12))
        ttk.Label(left, text='우선순위', style='Section.TLabel').pack(anchor='w')
        self.priority_bars = tk.Canvas(left, height=104, highlightthickness=0, background='white')
        self.priority_bars.pack(fill='x', pady=(4, 12))
        ttk.Label(left, text='최근 7일 수신', style='Section.TLabel').pack(anchor='w')
        self.recent_bars = tk.Canvas(left, height=170, highlightthickness=0, background='white')
        self.recent_bars.pack(fill='x', pady=(4, 0))
        right = ttk.Frame(panels)
        right.grid(row=0, column=1, sticky='nsew')
        ttk.Label(right, text='마감 임박 · 지난 마감', style='Section.TLabel').pack(anchor='w')
        self.due = ttk.Treeview(right, columns=('date', 'title', 'left'), show='headings', height=10)
        for column, title, width in (('date', '마감', 90), ('title', '일정', 240), ('left', '남음', 80)):
            self.due.heading(column, text=title)
            self.due.column(column, width=width, anchor='w')
        self.due.tag_configure('지남', foreground=tk_color(DASH_RED))
        self.due.pack(fill='both', expand=True, pady=(4, 0))
        self.due.bind('<Double-1>', lambda event: self.open_due())
        self.summary = ttk.Label(right, text='', style='Card.TLabel', wraplength=380)
        self.summary.pack(anchor='w', pady=(10, 0))

    def bars(self, canvas, pairs, color):
        canvas.delete('all')
        if not pairs:
            return
        top = max(value for _, value in pairs) or 1
        for index, (name, value) in enumerate(pairs):
            y = 6 + index * 24
            canvas.create_text(4, y + 7, text=name, anchor='w', font=('맑은 고딕', 9))
            canvas.create_text(104, y + 7, text=str(value), anchor='e', font=('맑은 고딕', 9, 'bold'))
            if value:
                canvas.create_rectangle(112, y, 112 + int(160 * value / top), y + 14,
                                        fill=tk_color(color), width=0)

    def paint_overview(self):
        if not self.data:
            return
        for name, label in self.card_values.items():
            label.configure(text=str(self.data['cards'].get(name, 0)))
        self.bars(self.category_bars, [(name, self.data['categories'][name]) for name in CATEGORIES], ACCENT)
        self.bars(self.priority_bars, [(name, self.data['priorities'][name]) for name, _ in PRIORITIES], DASH_RED)
        self.bars(self.recent_bars, [(day[5:], count) for day, count in self.data['recent']], DASH_BLUE)
        self.due.delete(*self.due.get_children())
        today = date.today()
        missed = self.data['past_due']
        # past_due() picks the nearest misses; reversed so the column still reads by date.
        # The index keeps the iid unique: one mail can hold two deadlines on one day.
        for index, (day, entry) in enumerate(missed[::-1] + self.data['upcoming']):
            self.due.insert('', 'end', iid=f'{index}:{entry.mail_id}',
                            tags=('지남',) if day < today else (),
                            values=(day.isoformat(), entry.label, describe(day, today)))
        self.summary.configure(text=f"수집 {self.data['total']}건 · 분석 대기 {self.data['waiting']}건"
                                    f" · 처리 완료 {len(self.data['handled'])}건"
                                    + (f" · 지난 마감 {len(missed)}건" if missed else ''))

    def open_due(self):
        selection = self.due.selection()
        if selection:
            self.show_mail(selection[0].split(':', 1)[1])

    # 메일 -------------------------------------------------------------

    def build_mail(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(fill='x')
        ttk.Label(bar, text='검색').pack(side='left')
        self.query = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.query, width=28)
        entry.pack(side='left', padx=(6, 12))
        entry.bind('<KeyRelease>', lambda event: self.paint_list())
        ttk.Label(bar, text='상태').pack(side='left')
        self.state = tk.StringVar(value='')
        picker = ttk.Combobox(bar, textvariable=self.state, values=STATES, width=10, state='readonly')
        picker.pack(side='left', padx=6)
        picker.bind('<<ComboboxSelected>>', lambda event: self.paint_list())
        ttk.Button(bar, text=f"{ICONS['now']} 새로 고침", command=self.refresh).pack(side='right')
        split = ttk.Panedwindow(parent, orient='vertical')
        split.pack(fill='both', expand=True, pady=(10, 0))
        top = ttk.Frame(split)
        self.list = ttk.Treeview(top, columns=[key for key, _, _ in LIST_COLUMNS],
                                 show='headings', height=12)
        for key, title, width in LIST_COLUMNS:
            self.list.heading(key, text=title, command=lambda column=key: self.sort_by(column))
            self.list.column(key, width=width, anchor='w')
        scroll = ttk.Scrollbar(top, command=self.list.yview)
        self.list.configure(yscrollcommand=scroll.set)
        self.list.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.list.bind('<<TreeviewSelect>>', lambda event: self.select())
        self.list.tag_configure(HANDLED, foreground=tk_color(CALM))
        self.list.tag_configure(FAILED, foreground=tk_color(SOON))
        self.list.tag_configure('긴급', foreground=tk_color(DASH_RED))
        split.add(top, weight=3)
        detail = ttk.Frame(split, padding=(0, 10, 0, 0))
        self.detail_title = ttk.Label(detail, text='메일을 선택하세요', style='Section.TLabel', wraplength=820)
        self.detail_title.pack(anchor='w')
        self.detail_meta = ttk.Label(detail, text='', style='Card.TLabel', wraplength=820)
        self.detail_meta.pack(anchor='w', pady=(2, 8))
        self.detail_body = tk.Text(detail, height=6, wrap='word', font=('맑은 고딕', 9),
                                   state='disabled', background='#fbfbfd', relief='solid', borderwidth=1)
        self.detail_body.pack(fill='both', expand=True)
        ttk.Label(detail, text='답변 초안 (다른 메일로 이동하거나 창을 닫으면 자동 저장됩니다)',
                  style='Card.TLabel').pack(anchor='w', pady=(8, 2))
        self.draft = tk.Text(detail, height=5, wrap='word', font=('맑은 고딕', 9),
                             relief='solid', borderwidth=1)
        self.draft.pack(fill='both', expand=True)
        actions = ttk.Frame(detail)
        actions.pack(fill='x', pady=(8, 0))
        self.handle_button = ttk.Button(actions, text='처리 완료로 표시', style='Action.TButton',
                                        command=self.toggle_handled)
        self.handle_button.pack(side='left')
        for text, command in ((f"{ICONS['save']} 초안 저장", self.save_draft),
                              (f"{ICONS['mail']} 답장 열기", self.open_reply),
                              ('원문 보기', self.show_source),
                              (f"{ICONS['retry']} 다시 분석", self.reanalyze)):
            ttk.Button(actions, text=text, style='Action.TButton', command=command).pack(side='left', padx=(8, 0))
        split.add(detail, weight=2)

    def sort_by(self, column):
        key, reverse = self.sort
        self.sort = (column, not reverse if key == column else False)
        self.paint_list()

    def paint_list(self):
        if not hasattr(self, 'list'):
            return
        key, reverse = self.sort
        shown = filter_rows(self.views, self.query.get(), self.state.get())
        shown.sort(key=lambda view: view.get(key) or '', reverse=reverse)
        self.list.delete(*self.list.get_children())
        for view in shown:
            tags = [view['state']] if view['state'] == HANDLED else []
            if view['state'].endswith(FAILED):
                tags.append(FAILED)
            if view['priority'] == '긴급':
                tags.append('긴급')
            self.list.insert('', 'end', iid=view['id'], tags=tags,
                             values=tuple(view[name] for name, _, _ in LIST_COLUMNS))
        if (self.selected and self.list.exists(self.selected)
                and self.list.selection() != (self.selected,)):
            self.list.selection_set(self.selected)

    def select(self):
        # ttk queues <<TreeviewSelect>> for every `selection set`, changed or not.
        # Without this guard show_mail's selection_set feeds the event straight
        # back and the window spins on its own events forever.
        selection = self.list.selection()
        if selection and selection[0] != self.selected:
            self.show_mail(selection[0])

    def show_mail(self, ident, focus=True):
        if self.draft_source not in (None, ident) and self.flush_draft():
            self.log('편집 중이던 답변 초안을 저장했습니다.')
            self.refresh()
        row = self.row_of(ident)
        if row is None:
            return
        self.selected = ident
        if focus:
            self.tabs.select(1)
        if self.list.exists(ident) and self.list.selection() != (ident,):
            self.list.selection_set(ident)
            self.list.see(ident)
        result = json.loads(row['result']) if row['result'] else {}
        self.detail_title.configure(text=row['subject'] or '(제목 없음)')
        pieces = [row['sender'], local_text(row['received'], '%Y-%m-%d %H:%M')]
        if result:
            pieces += [result.get('category', ''), result.get('priority', '')]
        if row['handled'] == HANDLED:
            pieces.append(HANDLED)
        self.detail_meta.configure(text='  ·  '.join(piece for piece in pieces if piece))
        lines = []
        if result:
            lines.append(f"요약: {result.get('summary', '')}")
            if result.get('requests'):
                lines.append(f"요청사항: {result['requests']}")
            lines.append(f"우선순위 근거: {result.get('priority_reason', '')}")
            lines.append(f"다음 행동: {result.get('next_action', '')}")
            for event in result.get('events', []):
                mark = ' (확인 필요)' if event.get('needs_review') else ''
                lines.append(f"일정: {event.get('title', '')} 시작 {event.get('start') or '—'} "
                             f"마감 {event.get('deadline') or '—'}{mark}")
        else:
            lines.append('아직 분석되지 않았습니다.' + (f" 오류: {row['error']}" if row['error'] else ''))
        self.fill(self.detail_body, '\n'.join(lines))
        if not (self.draft_source == ident and self.draft_dirty()):
            # poll() reopens the selected mail on every worker report; an edit must survive that.
            self.load_draft(ident, row['draft_edit'] or result.get('reply_draft', ''))
        self.handle_button.configure(text='미처리로 되돌리기' if row['handled'] == HANDLED else '처리 완료로 표시')

    def fill(self, widget, text):
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', text)
        widget.configure(state='disabled')

    def toggle_handled(self):
        row = self.row_of(self.selected)
        if row is None:
            return
        self.store().set_handled(row['id'], '' if row['handled'] == HANDLED else HANDLED)
        self.refresh()
        self.show_mail(row['id'])

    def load_draft(self, ident, text):
        self.draft.delete('1.0', 'end')
        self.draft.insert('1.0', text)
        self.draft_source = ident
        self.draft_baseline = self.draft.get('1.0', 'end-1c')

    def draft_dirty(self):
        return (self.draft_source is not None
                and self.draft.get('1.0', 'end-1c') != self.draft_baseline)

    def flush_draft(self):
        """Save an edit before anything can replace it. Returns the mail id, or None."""
        if not self.draft_dirty():
            return None
        ident, text = self.draft_source, self.draft.get('1.0', 'end-1c')
        try:
            self.store().set_draft(ident, text)
        except Exception as exc:
            report('답변 초안 저장 실패', exc)
            self.log(f'답변 초안을 저장하지 못했습니다: {type(exc).__name__}: {exc}')
            return None
        self.draft_baseline = text
        return ident

    def save_draft(self):
        if not self.draft_dirty():
            self.log('답변 초안에 변경된 내용이 없습니다.')
            return
        if self.flush_draft():
            self.log('답변 초안을 저장했습니다.')
            self.refresh()

    def open_reply(self):
        row = self.row_of(self.selected)
        if row is None:
            return
        result = json.loads(row['result']) if row['result'] else {}
        draft = self.draft.get('1.0', 'end-1c') or result.get('reply_draft', '')
        link = mailto(row['sender'], result.get('reply_subject') or f"Re: {row['subject']}", draft)
        if not link:
            messagebox.showinfo('답장 열기', '발신자 주소를 찾지 못했습니다.')
            return
        os.startfile(link)

    def show_source(self):
        row = self.selected and self.store().detail(self.selected)
        if not row:
            return
        try:
            parsed = parse_mail(row['raw'])
        except Exception as exc:
            messagebox.showerror('원문 보기', f'본문을 읽지 못했습니다: {exc}')
            return
        window = tk.Toplevel(self.root)
        window.title(parsed['subject'] or '원문')
        window.geometry('720x560')
        text = tk.Text(window, wrap='word', font=('맑은 고딕', 9))
        text.pack(fill='both', expand=True)
        header = (f"보낸사람: {parsed['sender']}\n날짜: {parsed['date']}\n"
                  f"첨부: {', '.join(parsed['attachments']) or '없음'}\n\n")
        text.insert('1.0', header + parsed['body'])
        text.configure(state='disabled')

    def reanalyze(self):
        row = self.row_of(self.selected)
        if row is None:
            return
        again = bool(row['result'])
        if again and not messagebox.askyesno('다시 분석', '기존 분석 결과를 지우고 다시 분석합니다. 계속할까요?'):
            return
        self.store().reset([row['id']], reanalyze=again)
        self.hub.wake()
        self.log('다시 분석하도록 요청했습니다.' + ('' if self.running() else ' 시작을 누르면 처리됩니다.'))
        self.refresh()

    # 일정 -------------------------------------------------------------

    def build_calendar(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(fill='x')
        self.month_label = ttk.Label(bar, text='', style='Section.TLabel')
        self.month_label.pack(side='left')
        ttk.Button(bar, text='다음 ▶', command=lambda: self.shift_month(1)).pack(side='right')
        ttk.Button(bar, text='이번 달', command=lambda: self.shift_month(0)).pack(side='right', padx=6)
        ttk.Button(bar, text='◀ 이전', command=lambda: self.shift_month(-1)).pack(side='right')
        self.grid = tk.Canvas(parent, height=CELL_HEIGHT * 6 + 26, highlightthickness=0, background='white')
        self.grid.pack(fill='both', expand=True, pady=(10, 0))
        self.grid.bind('<Double-1>', self.click_day)

    def shift_month(self, step):
        if step == 0:
            self.month = date.today().replace(day=1)
        elif step > 0:
            self.month = next_month(self.month)
        else:
            self.month = (self.month.replace(day=1) - timedelta(days=1)).replace(day=1)
        self.paint_month()

    def paint_month(self):
        if not hasattr(self, 'grid') or not self.data:
            return
        canvas, events, today = self.grid, self.data['events'], date.today()
        canvas.delete('all')
        self.month_label.configure(text=f'{self.month.year}년 {self.month.month}월')
        for index, name in enumerate(WEEKDAYS):
            canvas.create_text(index * CELL + CELL / 2, 12, text=name, font=('맑은 고딕', 9, 'bold'),
                               fill=tk_color(CALM) if index >= 5 else 'black')
        for week, days in enumerate(weeks_of(self.month.year, self.month.month)):
            for index, day in enumerate(days):
                x, y = index * CELL, 26 + week * CELL_HEIGHT
                outside = day.month != self.month.month
                canvas.create_rectangle(x, y, x + CELL, y + CELL_HEIGHT, outline='#e0e2e8',
                                        fill=tk_color(TODAY_FILL) if day == today else 'white')
                if outside:
                    continue
                canvas.create_text(x + 6, y + 12, text=str(day.day), anchor='w',
                                   font=('맑은 고딕', 9, 'bold' if day == today else 'normal'),
                                   fill=tk_color(CALM) if index >= 5 else 'black')
                for line, entry in enumerate(events.get(day, [])[:3]):
                    canvas.create_text(x + 6, y + 30 + line * 16, anchor='w', width=CELL - 12,
                                       text=entry.label[:16], font=('맑은 고딕', 8),
                                       fill=tk_color(COLORS[entry.kind]),
                                       tags=('day', f"open:{entry.mail_id}"))
                extra = len(events.get(day, [])) - 3
                if extra > 0:
                    canvas.create_text(x + 6, y + 78, anchor='w', text=f'…외 {extra}건',
                                       font=('맑은 고딕', 8), fill=tk_color(CALM))

    def click_day(self, event):
        for tag in self.grid.gettags(self.grid.find_withtag('current')):
            if tag.startswith('open:'):
                self.show_mail(tag.split(':', 1)[1])

    # 실행 -------------------------------------------------------------

    def build_run(self, parent):
        card = ttk.LabelFrame(parent, text=' 상태 ', padding=14)
        card.pack(fill='x')
        card.columnconfigure(2, weight=1)
        self.lamps = {}
        for row, (title, initial) in enumerate((('실행', '중지됨'), ('Codex 로그인', '확인 중'),
                                                ('메일 비밀번호', '확인 중'), ('마지막 확인', '—'),
                                                ('마지막 반영', '—'))):
            ttk.Label(card, text=title, width=14).grid(row=row, column=0, sticky='w', pady=3)
            dot = ttk.Label(card, text='●', style='Dot.TLabel', foreground=LAMP.get(initial, '#6b7280'))
            dot.grid(row=row, column=1, sticky='w')
            text = tk.StringVar(value=initial)
            ttk.Label(card, textvariable=text).grid(row=row, column=2, sticky='w', padx=(6, 0))
            self.lamps[title] = (dot, text)
        buttons = ttk.Frame(parent)
        buttons.pack(fill='x', pady=12)
        self.buttons = {}
        for key, label, command in (('start', '시작', self.start), ('stop', '중지', self.halt),
                                    ('now', '지금 확인', self.check_now), ('excel', '엑셀 열기', self.open_excel),
                                    ('test', '연결 테스트', self.test_connection),
                                    ('login', 'Codex 로그인', self.codex_login)):
            button = ttk.Button(buttons, text=f'{ICONS[key]} {label}', command=command, style='Action.TButton')
            button.pack(side='left', padx=(0, 8))
            self.buttons[key] = button
        ttk.Label(parent, text='기록').pack(anchor='w')
        holder = ttk.Frame(parent)
        holder.pack(fill='both', expand=True, pady=(4, 0))
        self.view = tk.Text(holder, height=12, wrap='word', font=('맑은 고딕', 9), state='disabled',
                            background='#fbfbfd', relief='solid', borderwidth=1)
        scroll = ttk.Scrollbar(holder, command=self.view.yview)
        self.view.configure(yscrollcommand=scroll.set)
        self.view.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        footer = ttk.Frame(parent)
        footer.pack(fill='x', pady=(8, 0))
        ttk.Button(footer, text=f"{ICONS['folder']} 데이터 폴더", style='Action.TButton',
                   command=lambda: os.startfile(self.directory)).pack(side='left')
        ttk.Button(footer, text='Codex 상태 다시 확인', style='Action.TButton',
                   command=self.check_codex).pack(side='left', padx=8)
        self.update_button = ttk.Button(footer, text=f"{ICONS['update']} 업데이트", style='Action.TButton',
                                        command=lambda: self.open_offer())
        self.update_buttons()

    # 설정 -------------------------------------------------------------

    def build_settings(self, parent):
        self.fields = {}
        self.field_rows = {}
        for index, (key, label) in enumerate(FIELDS):
            ttk.Label(parent, text=label).grid(row=index, column=0, pady=5, padx=(0, 12),
                                               sticky='nw' if key == 'model' else 'w')
            variable = tk.StringVar(value='' if key == 'password' else str(self.config.get(key, '')))
            self.fields[key] = variable
            self.field_rows[key] = index
            if key == 'model':
                self.build_models(parent, index, variable)
                continue
            entry = ttk.Entry(parent, textvariable=variable, width=56, show='*' if key == 'password' else '')
            entry.grid(row=index, column=1, sticky='ew', pady=5)
        parent.columnconfigure(1, weight=1)
        ttk.Button(parent, text='찾기', command=self.pick_workbook).grid(
            row=self.field_rows['workbook'], column=2, padx=(6, 0))
        ttk.Label(parent, text='비밀번호는 Windows 자격 증명에 저장됩니다. 저장 후에는 빈칸으로 두어도 됩니다.\n'
                               '메일 본문은 분석을 위해 로그인한 Codex 계정으로 전송됩니다.',
                  wraplength=620).grid(row=len(FIELDS), column=0, columnspan=3, sticky='w', pady=(12, 0))
        row = ttk.Frame(parent)
        row.grid(row=len(FIELDS) + 1, column=0, columnspan=3, sticky='w', pady=(16, 0))
        ttk.Button(row, text=f"{ICONS['save']} 저장", style='Action.TButton',
                   command=self.save_settings).pack(side='left')
        ttk.Button(row, text=f"{ICONS['test']} 연결 테스트", style='Action.TButton',
                   command=self.test_typed).pack(side='left', padx=8)

    def build_models(self, parent, index, variable):
        """Radio buttons over the models Codex itself says this account can use.

        `variable` stays the one the rest of the form reads, so typed() and
        save_settings() never learn that this field stopped being an entry box.
        """
        box = ttk.Frame(parent)
        box.grid(row=index, column=1, sticky='ew', pady=5)
        self.model_choice = tk.StringVar(value=variable.get())
        self.model_typed = tk.StringVar(value='')

        def apply(*_):
            variable.set(model_value(self.model_choice.get(), self.model_typed.get()))
            entry.configure(state='normal' if self.model_choice.get() == CUSTOM else 'disabled')

        for value, label, _ in model_rows(read_models(), variable.get()):
            ttk.Radiobutton(box, text=label, value=value, variable=self.model_choice,
                            command=apply).pack(anchor='w')
        entry = ttk.Entry(box, textvariable=self.model_typed, width=36)
        entry.pack(anchor='w', pady=(3, 0))
        self.model_typed.trace_add('write', apply)
        apply()

    def pick_workbook(self):
        path = filedialog.asksaveasfilename(defaultextension='.xlsx', filetypes=[('Excel', '*.xlsx')],
                                            initialfile='메일 업무관리.xlsx')
        if path:
            self.fields['workbook'].set(path)

    def typed(self):
        return normalize({key: variable.get() for key, variable in self.fields.items()})

    def save_settings(self):
        try:
            updated = self.typed()
        except ValueError as exc:
            messagebox.showerror('설정 확인', str(exc))
            return
        password = self.fields['password'].get()
        if password:
            remember_secret(password)      # keep it out of crash reports from here on
            try:
                self.services['save_password'](updated['email'], password)
                # Read it straight back: a credential store that drops the write
                # must not look like a successful save and fail hours later.
                stored = self.services['read_password'](updated['email'])
            except Exception as exc:
                messagebox.showerror('설정 확인', '비밀번호를 Windows 자격 증명에 저장하지 못했습니다.\n'
                                                 f'{type(exc).__name__}: {exc}')
                return
            if stored != password:
                self.log('경고: 자격 증명에 저장된 비밀번호가 입력과 다릅니다. 다시 입력해 보세요.')
            self.fields['password'].set('')
        temp = self.config_path.with_suffix('.tmp')
        # Merge: webhook and the update keys are not on this form and must survive.
        temp.write_text(json.dumps({**self.config, **updated}, ensure_ascii=False, indent=2),
                        encoding='utf-8')
        temp.replace(self.config_path)
        self.config.update(updated)
        self.log('설정을 저장했습니다.')
        self.check_password()
        self.refresh()

    # ------------------------------------------------------------------ worker and checks

    def log(self, message):
        """Through the hub: persisted, and every screen watching it sees the line."""
        self.hub.log(message)

    def show_line(self, text):
        self.view.configure(state='normal')
        self.view.insert('end', text + '\n')
        if int(self.view.index('end-1c').split('.')[0]) > 500:
            self.view.delete('1.0', '100.0')
        self.view.see('end')
        self.view.configure(state='disabled')

    def replay(self):
        """The log outlives the process now, so show what happened before this run."""
        lines = self.hub.recent()
        if not lines:
            return
        for at, text in lines:
            self.show_line(line_text(at, text))
        self.show_line(f'── 이전 기록 {len(lines)}줄 ──')

    def refresh_stamps(self):
        """From the database, not the local clock: a reopened screen must still be right."""
        if not hasattr(self, 'lamps'):
            return
        account = self.account()
        for title, key in (('마지막 확인', 'last_fetch:'), ('마지막 반영', 'last_export:')):
            stamp = self.store().get_meta(key + account) if account else None
            self.set_lamp(title, local_text(stamp, '%m-%d %H:%M:%S') if stamp else '—',
                          '실행 중' if stamp else None)

    def set_lamp(self, title, value, color=None):
        dot, text = self.lamps[title]
        text.set(value)
        dot.configure(foreground=LAMP.get(color or value, '#6b7280'))

    def background(self, name, work, done):
        if name in self.busy:
            return
        self.busy.add(name)

        def task():
            try:
                self.events.put(('done', name, done, work()))
            except Exception as exc:
                self.events.put(('done', name, done, exc))
        threading.Thread(target=task, daemon=True).start()

    def check_codex(self):
        self.set_lamp('Codex 로그인', '확인 중')
        self.background('codex', self.services['login_state'], self.show_codex)

    def show_codex(self, result):
        if isinstance(result, Exception):
            self.set_lamp('Codex 로그인', '확인 실패')
            self.log(f'Codex 상태 확인 실패: {result}')
            return
        state, detail = result
        self.set_lamp('Codex 로그인', state)
        if state != '연결됨':
            self.log(detail)

    def stored_password(self):
        """None when nothing is stored; self.password_error holds any other failure."""
        self.password_error = None
        try:
            return self.services['read_password'](self.config['email'])
        except LookupError:
            return None
        except Exception as exc:
            self.password_error = f'{type(exc).__name__}: {exc}'
            return None

    def password_message(self, absent):
        """'없음' and '읽지 못함' are different problems and need different messages."""
        if self.password_error:
            return f'메일 전용 비밀번호를 읽지 못했습니다.\n{self.password_error}'
        return absent

    def check_password(self):
        stored = self.stored_password()
        if self.password_error:
            self.set_lamp('메일 비밀번호', '확인 실패')
            self.log(f'Windows 자격 증명을 읽지 못했습니다: {self.password_error}')
            return
        self.set_lamp('메일 비밀번호', '저장됨' if stored else '없음')

    def test_connection(self, password=None, source=None):
        password = password or self.stored_password()
        if not password:
            messagebox.showwarning('연결 테스트',
                                   self.password_message('메일 전용 비밀번호가 없습니다. 설정에서 입력하세요.'))
            return
        self.log('연결 테스트 중…')
        self.background('test', lambda: self.services['check_connection'](source or self.config, password),
                        self.show_test)

    def test_typed(self):
        try:
            candidate = self.typed()
        except ValueError as exc:
            messagebox.showerror('설정 확인', str(exc))
            return
        self.test_connection(self.fields['password'].get() or None, candidate)

    def show_test(self, result):
        if isinstance(result, Exception):
            self.log(f'연결 테스트 실패: {type(result).__name__}: {result}')
            messagebox.showerror('연결 테스트', f'실패: {result}\n\n계정·메일 전용 비밀번호·POP3 설정·허용 IP를 확인하세요.')
            return
        self.log(result)
        messagebox.showinfo('연결 테스트', result)

    def running(self):
        return self.hub.running()

    def update_buttons(self):
        active = self.running()
        for key, state in (('start', not active), ('stop', active), ('now', active), ('test', not active)):
            self.buttons[key].configure(state='normal' if state else 'disabled')

    def start(self):
        if self.running():
            return
        try:
            normalize({key: self.config.get(key, '') for key, _ in FIELDS if key != 'password'})
        except ValueError as exc:
            messagebox.showerror('설정 확인', f'{exc}\n\n설정 탭에서 입력하세요.')
            self.tabs.select(4)
            return
        if not self.stored_password():
            messagebox.showerror('설정 확인', self.password_message(
                '메일 전용 비밀번호가 저장되어 있지 않습니다. 설정 탭에서 입력하세요.'))
            self.tabs.select(4)
            return
        if not self.hub.start(dict(self.config)):
            return
        self.log('시작했습니다. 창을 최소화해 두어도 계속 실행됩니다.')
        self.set_lamp('실행', '실행 중')
        self.update_buttons()

    def halt(self):
        if not self.hub.halt():
            return
        self.log('중지 요청됨. 현재 메일·분석·엑셀 작업이 끝나면 멈춥니다.')
        self.update_buttons()

    def check_now(self):
        if self.hub.wake():
            self.log('지금 확인을 요청했습니다.')

    def codex_login(self):
        try:
            subprocess.Popen(self.services['codex_command']() + ['login'],
                             env=self.services['codex_environment'](),
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
            self.log('Codex 로그인 창을 열었습니다. 로그인 후 상태를 다시 확인하세요.')
        except Exception as exc:
            report('Codex 로그인 실행 실패', exc)
            messagebox.showerror('Codex 로그인', str(exc))

    def open_excel(self):
        path = self.config.get('workbook', '')
        if path and Path(path).exists():
            os.startfile(path)
        else:
            messagebox.showinfo('엑셀 열기', '시작 후 첫 엑셀 반영이 완료되면 파일이 생성됩니다.')

    # ------------------------------------------------------------------ updates

    def boot(self):
        self.background('update', lambda: update.check(self.config, self.config_path), self.show_offer)
        # A slow or hanging check must never hold up work at login.
        self.root.after(20000, self.release_autostart)

    def release_autostart(self):
        """Let the worker start whether or not the update check has answered yet."""
        if self.booted:
            return
        self.booted = True
        if self.autostart and self.config_path.exists():
            self.start()

    def show_offer(self, result):
        self.release_autostart()
        if isinstance(result, Exception):
            self.log(f'업데이트 확인 실패: {type(result).__name__}: {result}')
            return
        if not result:
            return
        self.offer = result
        self.update_button.pack(side='left', padx=8)
        self.open_offer(60 if self.autostart else 0)

    def open_offer(self, countdown=0):
        """Deliberately not modal: a dialog at login must not park the assistant."""
        if self.offer is None:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('메일 도우미 업데이트')
        dialog.transient(self.root)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=f"새 버전 {self.offer['version']}이(가) 나왔습니다.",
                  font=('맑은 고딕', 14, 'bold')).pack(anchor='w')
        detail = f'현재 {__version__}'
        if self.offer['size']:
            detail += f" · 내려받기 약 {update.describe(self.offer['size'])}"
        ttk.Label(body, text=detail, foreground='#6b7280').pack(anchor='w', pady=(2, 12))
        ttk.Label(body, text='변경 내용').pack(anchor='w')
        notes = tk.Text(body, height=8, width=58, wrap='word', font=('맑은 고딕', 9),
                        background='#fbfbfd', relief='solid', borderwidth=1)
        notes.insert('1.0', self.offer['notes'])
        notes.configure(state='disabled')
        notes.pack(fill='both', expand=True, pady=(4, 12))
        ttk.Label(body, wraplength=520, text=(
            '업데이트하려면 도우미를 잠시 멈춰야 합니다. 현재 메일·분석·엑셀 작업이 끝난 뒤\n'
            '설치하며 몇 분 걸릴 수 있습니다. 설정과 메일 기록은 그대로 유지됩니다.'
            if self.running() else
            '업데이트하면 도우미가 잠시 종료되었다가 자동으로 다시 시작합니다.\n'
            '설정과 메일 기록은 그대로 유지됩니다.')).pack(anchor='w')
        timer = tk.StringVar()
        if countdown:
            ttk.Label(body, textvariable=timer, foreground='#6b7280').pack(anchor='w', pady=(8, 0))

        def later():
            dialog.destroy()
            self.log('업데이트를 나중에 합니다. 실행 탭의 업데이트 버튼으로 언제든 설치할 수 있습니다.')

        def skip():
            update.remember(self.config_path, {'update_skip': self.offer['version']})
            self.config['update_skip'] = self.offer['version']
            dialog.destroy()
            self.update_button.pack_forget()
            self.log(f"{self.offer['version']} 버전은 건너뜁니다. 다음 버전이 나오면 다시 알려 드립니다.")

        def accept():
            dialog.destroy()
            self.begin_update()

        row = ttk.Frame(body)
        row.pack(fill='x', pady=(16, 0))
        ttk.Button(row, text='지금 업데이트', command=accept).pack(side='left')
        ttk.Button(row, text='나중에', command=later).pack(side='left', padx=8)
        ttk.Button(row, text='이 버전 건너뛰기', command=skip).pack(side='left')
        dialog.protocol('WM_DELETE_WINDOW', later)
        dialog.bind('<Escape>', lambda event: later())

        def tick(left):
            if not dialog.winfo_exists():
                return
            if left <= 0:
                later()
                return
            timer.set(f'{left}초 안에 선택하지 않으면 이번에는 넘어갑니다.')
            dialog.after(1000, tick, left - 1)

        if countdown:
            tick(countdown)

    def begin_update(self):
        self.cancel.clear()
        # A plain dict, not the event queue: a 25MB download would enqueue a
        # hundred progress items and tie the log pump to the chunk size.
        progress = {'done': 0, 'total': int(self.offer.get('size') or 0)}
        window = tk.Toplevel(self.root)
        window.title('업데이트')
        window.transient(self.root)
        window.resizable(False, False)
        body = ttk.Frame(window, padding=20)
        body.pack(fill='both', expand=True)
        phase = tk.StringVar(value='설치 파일을 내려받는 중입니다…')
        ttk.Label(body, textvariable=phase, width=54).pack(anchor='w')
        bar = ttk.Progressbar(body, length=420,
                              mode='determinate' if progress['total'] else 'indeterminate')
        bar.pack(fill='x', pady=10)
        if not progress['total']:
            bar.start(15)
        ttk.Label(body, foreground='#6b7280', wraplength=420,
                  text='창을 닫지 마세요. 취소해도 지금 버전은 그대로 사용할 수 있습니다.').pack(anchor='w')
        stop_button = ttk.Button(body, text='취소', command=self.cancel.set)
        stop_button.pack(anchor='e', pady=(12, 0))
        window.protocol('WM_DELETE_WINDOW', self.cancel.set)
        state = {'downloading': True}

        def tick():
            if not window.winfo_exists():
                return
            if state['downloading'] and progress['total']:
                bar.configure(maximum=progress['total'], value=progress['done'])
                phase.set('설치 파일을 내려받는 중입니다…  '
                          f"{update.describe(progress['done'])} / {update.describe(progress['total'])}")
            window.after(200, tick)

        def finished(result):
            state['downloading'] = False
            bar.stop()
            if isinstance(result, Exception):
                window.destroy()
                self.fail_update(result)
                return
            self.pending_installer = result
            self.pending_autostart = self.running()
            stop_button.configure(state='disabled')
            bar.configure(mode='indeterminate')
            bar.start(15)
            phase.set('현재 메일·분석·엑셀 작업이 끝나기를 기다리는 중입니다. 몇 분 걸릴 수 있습니다…'
                      if self.running() else '설치를 시작합니다. 잠시 후 메일 도우미가 다시 열립니다…')
            # poll() destroys the root once the worker is gone, then main()'s
            # finally releases the mutex and starts the installer.
            self.closing = True
            self.halt()

        self.background('update-download',
                        lambda: update.download(self.offer, self.directory / 'update' / self.offer['version'],
                                                progress, self.cancel),
                        finished)
        tick()

    def fail_update(self, exc):
        if isinstance(exc, update.Cancelled):
            self.log('업데이트를 취소했습니다. 지금 버전을 계속 사용합니다.')
            return
        if isinstance(exc, update.BadDigest):
            text = ('내려받은 설치 파일이 손상되었습니다. 설치를 중단했습니다.\n'
                    '잠시 후 다시 시도하세요. 지금 버전은 그대로 사용할 수 있습니다.')
        elif isinstance(exc, update.NoSpace):
            text = (f'디스크 공간이 부족해 업데이트를 내려받지 못했습니다.\n'
                    f'약 {update.describe(exc.needed)}의 여유 공간이 필요합니다.')
        else:
            text = (f'업데이트를 내려받지 못했습니다.\n{type(exc).__name__}: {exc}\n\n'
                    '지금 버전은 그대로 사용할 수 있습니다.')
        self.log(f'업데이트 실패: {type(exc).__name__}: {exc}')
        report('업데이트 실패', exc, self.offer and self.offer.get('version'))
        messagebox.showerror('업데이트', text)

    def poll(self):
        changed = False
        try:
            while True:
                value = self.events.get_nowait()
                if isinstance(value, tuple) and value and value[0] == 'done':
                    _, name, handler, result = value
                    self.busy.discard(name)
                    handler(result)
                elif value is None:
                    self.set_lamp('실행', '중지됨')
                    self.show_line(line_text('', '중지됨.'))
                    self.update_buttons()
                else:
                    self.show_line(line_text('', value))
                    changed = True
        except queue.Empty:
            pass
        if changed:
            self.refresh()
            if self.selected:
                self.show_mail(self.selected, focus=False)   # never steal the current tab
        if self.closing and not self.running():
            self.flush_draft()
            self.hub.close()
            self.root.destroy()
            return
        self.root.after(300, self.poll)

    def close(self):
        self.closing = True
        self.flush_draft()
        self.cancel.set()
        self.halt()
        if not self.running():
            if self.db is not None:
                self.db.db.close()
            self.hub.close()
            self.root.destroy()
