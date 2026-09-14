# -*- mode: python ; coding: utf-8 -*-
"""onedir, two exes, one shared runtime.

onedir rather than onefile: onefile re-extracts ~30MB into %TEMP% on every
launch, which this app pays at every Windows login, and a self-extracting
stub is the shape unsigned-binary heuristics dislike most. The installer
already gives us a single file to download.

Two Analysis objects feeding one COLLECT so MailAssistant.exe and
MailAssistantTools.exe share _internal/ instead of shipping two copies of
tcl/tk and pywin32.
"""
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct,
                                                 StringTable, VarFileInfo, VarStruct,
                                                 VSVersionInfo)

ROOT = Path(SPECPATH).resolve().parent
ICON = str(ROOT / 'packaging' / 'app.ico')

VERSION = re.search(r'^__version__\s*=\s*"([^"]+)"',
                    (ROOT / 'mail_assistant' / '__init__.py').read_text(encoding='utf-8'),
                    re.M).group(1)
NUMBERS = tuple(int(part) for part in VERSION.split('.')) + (0,) * 4


def version_resource(description, filename):
    """So Explorer's Properties tab and the updater agree on one number."""
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=NUMBERS[:4], prodvers=NUMBERS[:4], mask=0x3F, flags=0x0,
                          OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable('041204B0', [   # 0x0412 ko-KR, 1200 Unicode
                StringStruct('CompanyName', 'Hiworks Mail Assistant'),
                StringStruct('FileDescription', description),
                StringStruct('FileVersion', VERSION),
                StringStruct('InternalName', filename),
                StringStruct('OriginalFilename', filename),
                StringStruct('ProductName', '메일 도우미'),
                StringStruct('ProductVersion', VERSION),
            ])]),
            VarFileInfo([VarStruct('Translation', [0x0412, 1200])]),
        ])


# Every one of these is imported inside a function, and win32com.shell is really
# win32comext wired up at runtime. pyinstaller-hooks-contrib handles that one and
# the jsonschema data files; listing them here is documentation plus insurance.
HIDDEN = [
    'win32api', 'win32event', 'win32cred',
    'win32com', 'win32com.client', 'win32com.shell',
    'win32comext.shell.shell', 'win32comext.shell.shellcon',
    'pythoncom', 'pywintypes',
    'jsonschema', 'jsonschema_specifications', 'referencing',
]

# Kept short on purpose: aggressive excludes are the usual cause of frozen-only
# ImportErrors, and the savings are noise next to tcl/tk. email in particular is
# load-bearing (core.py parses mail with it) and must never be listed.
EXCLUDES = ['numpy', 'pandas', 'matplotlib', 'scipy', 'PIL', 'pytest',
            'pip', 'wheel', 'IPython', 'sqlalchemy', 'setuptools', 'pkg_resources',
            # nicegui.testing pulls selenium in; the shipped build never tests.
            'selenium']
# pywebview is NOT excluded any more: MailAssistant.exe *is* the web screens in a
# pywebview frame, so webview/pythonnet/clr_loader are load-bearing. It costs 4.3MB
# because on Windows it drives WebView2 — the Edge engine already installed — rather
# than embedding a browser the way Electron does.

# The browser fetches these from /vendor. They are data, not importable modules, so
# nothing fails until someone opens the 일정 page — selftest's check_webui catches it.
VENDOR = [(str(ROOT / 'mail_assistant' / 'vendor'), 'mail_assistant/vendor')]

# nicegui vendors the JavaScript for every element it offers and this app creates
# almost none of the big ones. Dropping their asset folders saves about 19MB; their
# Python modules stay, so nicegui still imports. The filtering has to happen on
# Analysis.datas, *after* the hook runs: pyinstaller-hooks-contrib ships
# hook-nicegui.py with a bare collect_data_files('nicegui'), which adds back anything
# a spec-level collect_all dropped.
#
# echart is the one deliberate exception: every chart in webui.py is a ui.echart, so
# its 1.8MB has to ship. selftest's check_webui is what notices if it stops shipping.
DROP_ASSETS = ('nicegui/elements/plotly', 'nicegui/elements/mermaid',
               'nicegui/elements/codemirror',
               'nicegui/elements/json_editor', 'nicegui/elements/scene',
               'nicegui/elements/aggrid', 'nicegui/elements/leaflet',
               'nicegui/elements/xterm', 'nicegui/static/sass.dart.js')


def wanted(entry):
    """Analysis.datas holds (destination, source, typecode), so match on the destination."""
    return not any(piece in str(entry[0]).replace('\\', '/') for piece in DROP_ASSETS)

# Both exes serve the same screens now, so both carry nicegui, the vendored assets
# and uvicorn. nicegui used to be excluded here, when MailAssistant.exe was tkinter:
# leaving that in would have frozen a window that cannot import its own window.
SERVER = ['webview'] + collect_submodules('uvicorn')
gui = Analysis([str(ROOT / 'packaging' / 'entry_gui.py')], pathex=[str(ROOT)],
               datas=VENDOR, hiddenimports=HIDDEN + SERVER,
               excludes=EXCLUDES + ['openpyxl'], noarchive=False)
# uvicorn picks its protocol and loop implementations by name at runtime, and pywebview
# reaches its Edge backend through pythonnet, so neither is reachable by static analysis.
tools = Analysis([str(ROOT / 'packaging' / 'entry_tools.py')], pathex=[str(ROOT)],
                 datas=VENDOR,
                 hiddenimports=HIDDEN + SERVER + ['openpyxl', 'diagnose', 'sample', 'demo'],
                 excludes=EXCLUDES, noarchive=False)

for analysis in (gui, tools):
    analysis.datas = [entry for entry in analysis.datas if wanted(entry)]

gui_pyz, tools_pyz = PYZ(gui.pure), PYZ(tools.pure)

gui_exe = EXE(gui_pyz, gui.scripts, [], exclude_binaries=True, name='MailAssistant',
              console=False, icon=ICON, upx=False, strip=False,
              version=version_resource('메일 도우미', 'MailAssistant.exe'))

tools_exe = EXE(tools_pyz, tools.scripts, [], exclude_binaries=True, name='MailAssistantTools',
                console=True, icon=ICON, upx=False, strip=False,
                version=version_resource('메일 도우미 진단 도구', 'MailAssistantTools.exe'))

coll = COLLECT(gui_exe, tools_exe,
               gui.binaries, gui.datas,
               tools.binaries, tools.datas,
               strip=False, upx=False, name='MailAssistant')
