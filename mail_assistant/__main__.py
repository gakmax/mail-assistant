import json
import os
import queue
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from . import __version__, update
from .report import install_hooks, remember_secret, report
from .settings import DEFAULTS, FIELDS, normalize

ICONS = {'start': '▶', 'stop': '■', 'now': '⟳', 'excel': '▤', 'test': '⇄', 'settings': '⚙',
         'login': '⌨', 'folder': '📁', 'update': '⬆'}
DOT = {'실행 중': '#1a7f37', '중지됨': '#6b7280', '연결됨': '#1a7f37',
       '로그인 필요': '#b91c1c', '확인 실패': '#b45309', '확인 중': '#6b7280',
       '저장됨': '#1a7f37', '없음': '#b91c1c'}


def main():
    install_hooks()
    if sys.platform != 'win32':
        raise SystemExit('메일 도우미 실행은 Microsoft Excel이 설치된 Windows에서 지원합니다.')
    import win32api
    import win32event
    from win32com.shell import shell, shellcon
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from .services import check_connection, codex_command, codex_environment, login_state, read_password, save_password
    from .worker import run

    mutex = win32event.CreateMutex(None, False, 'Local\\HiworksMailAssistant')
    if win32api.GetLastError() == 183:
        messagebox.showinfo('메일 도우미', '이미 실행 중입니다. 작업 표시줄에서 메일 도우미를 확인하세요.')
        win32api.CloseHandle(mutex)
        return
    directory = Path(os.environ['LOCALAPPDATA']) / 'HiworksMailAssistant'
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / 'config.json'
    update.sweep(directory / 'update')
    desktop = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0))
    config = dict(DEFAULTS, workbook=str(desktop / '메일 업무관리.xlsx'))
    if config_path.exists():
        try:
            config.update(json.loads(config_path.read_text(encoding='utf-8')))
        except (ValueError, OSError) as exc:
            report('설정 파일 읽기 실패', exc, str(config_path))
            messagebox.showwarning('설정 확인', '설정 파일을 읽지 못했습니다. 다시 설정하세요.')

    root = tk.Tk()
    root.title(f'메일 도우미 {__version__}')
    root.geometry('760x620')
    root.minsize(680, 560)
    style = ttk.Style()
    style.configure('Action.TButton', font=('맑은 고딕', 10), padding=(10, 8))
    style.configure('Dot.TLabel', font=('맑은 고딕', 12))
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill='both', expand=True)

    head = ttk.Frame(frame)
    head.pack(fill='x')
    ttk.Label(head, text='메일 업무 도우미', font=('맑은 고딕', 18, 'bold')).pack(side='left')
    settings_button = ttk.Button(head, text=f"{ICONS['settings']} 설정")
    settings_button.pack(side='right')
    update_button = ttk.Button(head, text=f"{ICONS['update']} 업데이트")
    ttk.Label(frame, text='하이웍스 메일을 정리하고 엑셀에 반영합니다. 답변은 초안으로만 저장합니다.',
              wraplength=700).pack(anchor='w', pady=(4, 14))

    card = ttk.LabelFrame(frame, text=' 상태 ', padding=14)
    card.pack(fill='x')
    card.columnconfigure(1, weight=1)
    lamps = {}

    def lamp(row, title, initial):
        ttk.Label(card, text=title, width=14).grid(row=row, column=0, sticky='w', pady=3)
        dot = ttk.Label(card, text='●', style='Dot.TLabel', foreground=DOT.get(initial, '#6b7280'))
        dot.grid(row=row, column=1, sticky='w')
        text = tk.StringVar(value=initial)
        ttk.Label(card, textvariable=text).grid(row=row, column=2, sticky='w', padx=(6, 0))
        lamps[title] = (dot, text)

    lamp(0, '실행', '중지됨')
    lamp(1, 'Codex 로그인', '확인 중')
    lamp(2, '메일 비밀번호', '확인 중')
    lamp(3, '마지막 확인', '—')

    def set_lamp(title, value, color=None):
        dot, text = lamps[title]
        text.set(value)
        dot.configure(foreground=DOT.get(color or value, '#6b7280'))

    buttons = ttk.Frame(frame)
    buttons.pack(fill='x', pady=14)
    events = queue.Queue()
    stop = threading.Event()
    wake = threading.Event()
    thread = None
    busy = set()
    offer = None
    pending_installer = None
    pending_autostart = False
    cancel = threading.Event()
    booted = False

    def log(message):
        view.configure(state='normal')
        view.insert('end', f"{datetime.now().strftime('%H:%M:%S')}  {message}\n")
        if int(view.index('end-1c').split('.')[0]) > 500:
            view.delete('1.0', '100.0')
        view.see('end')
        view.configure(state='disabled')

    def background(name, work, done):
        """Run a check off the UI thread; results come back through the event queue."""
        if name in busy:
            return
        busy.add(name)

        def task():
            try:
                events.put(('done', name, done, work()))
            except Exception as exc:
                events.put(('done', name, done, exc))
        threading.Thread(target=task, daemon=True).start()

    def refresh_codex():
        set_lamp('Codex 로그인', '확인 중')
        background('codex', login_state, show_codex)

    def show_codex(result):
        if isinstance(result, Exception):
            set_lamp('Codex 로그인', '확인 실패')
            log(f'Codex 상태 확인 실패: {result}')
            return
        state, detail = result
        set_lamp('Codex 로그인', state)
        if state != '연결됨':
            log(detail)

    def refresh_password():
        try:
            read_password(config['email'])
            set_lamp('메일 비밀번호', '저장됨')
        except Exception:
            set_lamp('메일 비밀번호', '없음')

    def stored_password():
        try:
            return read_password(config['email'])
        except Exception:
            return None

    def test_connection(password=None, source=config):
        password = password or stored_password()
        if not password:
            messagebox.showwarning('연결 테스트', '메일 전용 비밀번호가 없습니다. 설정에서 입력하세요.')
            return
        log('연결 테스트 중…')
        background('test', lambda: check_connection(source, password), show_test)

    def show_test(result):
        if isinstance(result, Exception):
            log(f'연결 테스트 실패: {type(result).__name__}: {result}')
            messagebox.showerror('연결 테스트', f'실패: {result}\n\n계정·메일 전용 비밀번호·POP3 설정·허용 IP를 확인하세요.')
            return
        log(result)
        messagebox.showinfo('연결 테스트', result)

    def open_settings():
        dialog = tk.Toplevel(root)
        dialog.title('설정')
        dialog.transient(root)
        dialog.grab_set()
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill='both', expand=True)
        fields = {}
        for index, (key, label) in enumerate(FIELDS):
            ttk.Label(body, text=label).grid(row=index, column=0, sticky='w', pady=5, padx=(0, 12))
            variable = tk.StringVar(value='' if key == 'password' else str(config.get(key, '')))
            entry = ttk.Entry(body, textvariable=variable, width=52, show='*' if key == 'password' else '')
            entry.grid(row=index, column=1, sticky='ew', pady=5)
            fields[key] = variable
        body.columnconfigure(1, weight=1)

        def choose():
            path = filedialog.asksaveasfilename(parent=dialog, defaultextension='.xlsx',
                                                filetypes=[('Excel', '*.xlsx')], initialfile='메일 업무관리.xlsx')
            if path:
                fields['workbook'].set(path)

        ttk.Button(body, text='찾기', command=choose).grid(row=5, column=2, padx=(6, 0))
        ttk.Label(body, text='비밀번호는 Windows 자격 증명에 저장됩니다. 저장 후에는 빈칸으로 두어도 됩니다.\n'
                             '메일 본문은 분석을 위해 로그인한 Codex 계정으로 전송됩니다.',
                  wraplength=520).grid(row=len(FIELDS), column=0, columnspan=3, sticky='w', pady=(12, 0))

        def collect():
            return normalize({key: variable.get() for key, variable in fields.items()})

        def test():
            try:
                candidate = collect()
            except ValueError as exc:
                messagebox.showerror('설정 확인', str(exc), parent=dialog)
                return
            password = fields['password'].get() or None
            test_connection(password, candidate)

        def save():
            try:
                updated = collect()
            except ValueError as exc:
                messagebox.showerror('설정 확인', str(exc), parent=dialog)
                return
            if fields['password'].get():
                try:
                    remember_secret(fields['password'].get())
                    save_password(updated['email'], fields['password'].get())
                except Exception as exc:
                    messagebox.showerror('설정 확인', f'비밀번호 저장 실패: {exc}', parent=dialog)
                    return
            temp = config_path.with_suffix('.tmp')
            # Merge: webhook and the update keys are not on this form and must survive.
            temp.write_text(json.dumps({**config, **updated}, ensure_ascii=False, indent=2),
                            encoding='utf-8')
            temp.replace(config_path)
            config.update(updated)
            log('설정을 저장했습니다.')
            refresh_password()
            update_buttons()
            dialog.destroy()

        row = ttk.Frame(body)
        row.grid(row=len(FIELDS) + 1, column=0, columnspan=3, sticky='e', pady=(16, 0))
        ttk.Button(row, text=f"{ICONS['test']} 연결 테스트", command=test).pack(side='left', padx=6)
        ttk.Button(row, text='저장', command=save).pack(side='left', padx=6)
        ttk.Button(row, text='취소', command=dialog.destroy).pack(side='left')
        dialog.bind('<Escape>', lambda event: dialog.destroy())

    def running():
        return bool(thread and thread.is_alive())

    def start():
        nonlocal thread
        if running():
            return
        try:
            normalize({key: config.get(key, '') for key, _ in FIELDS if key != 'password'})
        except ValueError as exc:
            messagebox.showerror('설정 확인', f'{exc}\n\n설정을 먼저 입력하세요.')
            open_settings()
            return
        if not stored_password():
            messagebox.showerror('설정 확인', '메일 전용 비밀번호가 저장되어 있지 않습니다. 설정에서 입력하세요.')
            open_settings()
            return
        stop.clear()
        wake.clear()

        def task():
            try:
                run(dict(config), directory, stop, events.put, wake)
            except Exception as exc:
                report('작업 스레드 중단', exc)
                events.put('실행 오류: 데이터 폴더 접근 권한과 설치 상태를 확인하세요.')
            finally:
                events.put(None)
        thread = threading.Thread(target=task, daemon=False)
        thread.start()
        log('시작했습니다. 창을 최소화해 두어도 계속 실행됩니다.')
        set_lamp('실행', '실행 중')
        update_buttons()

    def halt():
        if not running():
            return
        stop.set()
        wake.set()
        log('중지 요청됨. 현재 메일·분석·엑셀 작업이 끝나면 멈춥니다.')
        update_buttons()

    def check_now():
        if not running():
            return
        wake.set()
        log('지금 확인을 요청했습니다.')

    def login():
        try:
            subprocess.Popen(codex_command() + ['login'], env=codex_environment(),
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
            log('Codex 로그인 창을 열었습니다. 로그인 후 상태를 다시 확인하세요.')
        except Exception as exc:
            report('Codex 로그인 실행 실패', exc)
            messagebox.showerror('Codex 로그인', str(exc))

    def open_excel():
        path = config.get('workbook', '')
        if path and Path(path).exists():
            os.startfile(path)
        else:
            messagebox.showinfo('엑셀 열기', '시작 후 첫 엑셀 반영이 완료되면 파일이 생성됩니다.')

    controls = [
        ('start', '시작', start), ('stop', '중지', halt), ('now', '지금 확인', check_now),
        ('excel', '엑셀 열기', open_excel), ('test', '연결 테스트', lambda: test_connection()),
        ('login', 'Codex 로그인', login),
    ]
    widgets = {}
    for key, label, command in controls:
        button = ttk.Button(buttons, text=f'{ICONS[key]} {label}', command=command, style='Action.TButton')
        button.pack(side='left', padx=(0, 8))
        widgets[key] = button
    settings_button.configure(command=open_settings)

    def update_buttons():
        active = running()
        widgets['start'].configure(state='disabled' if active else 'normal')
        widgets['stop'].configure(state='normal' if active else 'disabled')
        widgets['now'].configure(state='normal' if active else 'disabled')
        widgets['test'].configure(state='disabled' if active else 'normal')
        settings_button.configure(state='disabled' if active else 'normal')

    ttk.Label(frame, text='기록').pack(anchor='w')
    log_frame = ttk.Frame(frame)
    log_frame.pack(fill='both', expand=True, pady=(4, 0))
    view = tk.Text(log_frame, height=10, wrap='word', font=('맑은 고딕', 9),
                   state='disabled', background='#fbfbfd', relief='solid', borderwidth=1)
    scroll = ttk.Scrollbar(log_frame, command=view.yview)
    view.configure(yscrollcommand=scroll.set)
    view.pack(side='left', fill='both', expand=True)
    scroll.pack(side='right', fill='y')
    footer = ttk.Frame(frame)
    footer.pack(fill='x', pady=(8, 0))
    ttk.Button(footer, text=f"{ICONS['folder']} 데이터 폴더", style='Action.TButton',
               command=lambda: os.startfile(directory)).pack(side='left')
    ttk.Button(footer, text='Codex 상태 다시 확인', style='Action.TButton',
               command=refresh_codex).pack(side='left', padx=8)

    def release_autostart():
        """Let the worker start whether or not the update check has answered yet."""
        nonlocal booted
        if booted:
            return
        booted = True
        if '--autostart' in sys.argv and config_path.exists():
            start()

    def boot():
        background('update', lambda: update.check(config, config_path), show_offer)
        # A slow or hanging check must never hold up work at login.
        root.after(20000, release_autostart)

    def show_offer(result):
        nonlocal offer
        release_autostart()
        if isinstance(result, Exception):
            log(f'업데이트 확인 실패: {type(result).__name__}: {result}')
            return
        if not result:
            return
        offer = result
        update_button.pack(side='right', padx=(0, 8))
        open_offer(60 if '--autostart' in sys.argv else 0)

    def open_offer(countdown=0):
        """Deliberately not modal: a dialog at login must not park the assistant."""
        if offer is None:
            return
        dialog = tk.Toplevel(root)
        dialog.title('메일 도우미 업데이트')
        dialog.transient(root)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=f"새 버전 {offer['version']}이(가) 나왔습니다.",
                  font=('맑은 고딕', 14, 'bold')).pack(anchor='w')
        detail = f'현재 {__version__}'
        if offer['size']:
            detail += f" · 내려받기 약 {update.describe(offer['size'])}"
        ttk.Label(body, text=detail, foreground='#6b7280').pack(anchor='w', pady=(2, 12))
        ttk.Label(body, text='변경 내용').pack(anchor='w')
        notes = tk.Text(body, height=8, width=58, wrap='word', font=('맑은 고딕', 9),
                        background='#fbfbfd', relief='solid', borderwidth=1)
        notes.insert('1.0', offer['notes'])
        notes.configure(state='disabled')
        notes.pack(fill='both', expand=True, pady=(4, 12))
        ttk.Label(body, wraplength=520, text=(
            '업데이트하려면 도우미를 잠시 멈춰야 합니다. 현재 메일·분석·엑셀 작업이 끝난 뒤\n'
            '설치하며 몇 분 걸릴 수 있습니다. 설정과 메일 기록은 그대로 유지됩니다.'
            if running() else
            '업데이트하면 도우미가 잠시 종료되었다가 자동으로 다시 시작합니다.\n'
            '설정과 메일 기록은 그대로 유지됩니다.')).pack(anchor='w')
        timer = tk.StringVar()
        if countdown:
            ttk.Label(body, textvariable=timer, foreground='#6b7280').pack(anchor='w', pady=(8, 0))

        def later():
            dialog.destroy()
            log('업데이트를 나중에 합니다. 오른쪽 위 업데이트 버튼으로 언제든 설치할 수 있습니다.')

        def skip():
            update.remember(config_path, {'update_skip': offer['version']})
            config['update_skip'] = offer['version']
            dialog.destroy()
            update_button.pack_forget()
            log(f"{offer['version']} 버전은 건너뜁니다. 다음 버전이 나오면 다시 알려 드립니다.")

        def accept():
            dialog.destroy()
            begin_update()

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

    def begin_update():
        cancel.clear()
        # A plain dict, not the event queue: a 25MB download would enqueue a
        # hundred progress items and tie the log pump to the chunk size.
        progress = {'done': 0, 'total': int(offer.get('size') or 0)}
        window = tk.Toplevel(root)
        window.title('업데이트')
        window.transient(root)
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
        stop_button = ttk.Button(body, text='취소', command=cancel.set)
        stop_button.pack(anchor='e', pady=(12, 0))
        window.protocol('WM_DELETE_WINDOW', cancel.set)
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
            nonlocal closing, pending_installer, pending_autostart
            state['downloading'] = False
            bar.stop()
            if isinstance(result, Exception):
                window.destroy()
                fail_update(result)
                return
            pending_installer = result
            pending_autostart = running()
            stop_button.configure(state='disabled')
            bar.configure(mode='indeterminate')
            bar.start(15)
            phase.set('현재 메일·분석·엑셀 작업이 끝나기를 기다리는 중입니다. 몇 분 걸릴 수 있습니다…'
                      if running() else '설치를 시작합니다. 잠시 후 메일 도우미가 다시 열립니다…')
            # poll() destroys the root once the worker is gone, then main()'s
            # finally releases the mutex and starts the installer.
            closing = True
            halt()

        background('update-download',
                   lambda: update.download(offer, directory / 'update' / offer['version'],
                                           progress, cancel),
                   finished)
        tick()

    def fail_update(exc):
        if isinstance(exc, update.Cancelled):
            log('업데이트를 취소했습니다. 지금 버전을 계속 사용합니다.')
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
        log(f'업데이트 실패: {type(exc).__name__}: {exc}')
        report('업데이트 실패', exc, offer and offer.get('version'))
        messagebox.showerror('업데이트', text)

    update_button.configure(command=lambda: open_offer())

    closing = False

    def close():
        nonlocal closing
        closing = True
        cancel.set()
        halt()
        if not running():
            root.destroy()

    def poll():
        try:
            while True:
                value = events.get_nowait()
                if isinstance(value, tuple) and value and value[0] == 'done':
                    _, name, handler, result = value
                    busy.discard(name)
                    handler(result)
                elif value is None:
                    set_lamp('실행', '중지됨')
                    log('중지됨.')
                    update_buttons()
                else:
                    log(value)
                    set_lamp('마지막 확인', datetime.now().strftime('%H:%M:%S'), '실행 중')
        except queue.Empty:
            pass
        if closing and not running():
            root.destroy()
            return
        root.after(300, poll)

    update_buttons()
    refresh_password()
    refresh_codex()
    log('설정을 확인하고 시작을 누르세요. 첫 연결에서는 기존 메일을 제외합니다.')
    def on_widget_error(kind, value, trace):
        # A windowed build has no console: without this the error leaves no trace.
        report('화면 조작 오류', value)
        messagebox.showerror('메일 도우미', f'화면 처리 중 오류가 발생했습니다.\n{type(value).__name__}: {value}')

    root.report_callback_exception = on_widget_error
    root.protocol('WM_DELETE_WINDOW', close)
    root.after(300, poll)
    root.after(600, boot)
    try:
        root.mainloop()
    finally:
        # Close the mutex first. The last handle going away destroys the kernel
        # object even though we are still alive, so Setup's AppMutex check cannot
        # collide with us and abort with exit code 2.
        win32api.CloseHandle(mutex)
        if pending_installer is not None:
            try:
                update.launch(pending_installer, directory / 'update' / 'install.log',
                              offer['silent'], pending_autostart)
            except Exception as exc:
                report('설치 프로그램 실행 실패', exc)
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(
                        None, '설치 프로그램을 실행하지 못했습니다.\n'
                              '지금 버전은 그대로 사용할 수 있습니다.', '메일 도우미 업데이트', 0x10)
                except Exception:
                    pass


if __name__ == '__main__':
    main()
