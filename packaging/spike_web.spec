# -*- mode: python ; coding: utf-8 -*-
"""Throwaway spec for the NiceGUI spike (UI-PLAN.md 1단계).

Separate from mail_assistant.spec on purpose: the shipped installer keeps its
identity while the spike answers one question — what does nicegui cost in the
bundle. Delete this file if the transition is abandoned.

DROP is the interesting part. nicegui vendors the JavaScript for every element it
offers and this app creates none of the big ones, so their asset folders come out.
The Python modules stay, so nicegui still imports; only an element we never build
would be missing its script.

The filtering has to happen on Analysis.datas, *after* the hook has run.
pyinstaller-hooks-contrib ships hook-nicegui.py with a bare
`collect_data_files('nicegui')`, so anything dropped from a spec-level collect_all
is simply added back.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
ICON = str(ROOT / 'packaging' / 'app.ico')

# Measured on nicegui 3.16: plotly 4.6M, mermaid 3.6M, codemirror 2.1M, echart 1.8M,
# json_editor 1.4M, scene 1.2M, aggrid 1.2M, leaflet 440K, xterm 408K, sass 3.0M.
# echart stays: the shipped pages draw every chart with it, so the spike has to
# measure a bundle that carries it too.
DROP = ('nicegui/elements/plotly', 'nicegui/elements/mermaid', 'nicegui/elements/codemirror',
        'nicegui/elements/json_editor', 'nicegui/elements/scene',
        'nicegui/elements/aggrid', 'nicegui/elements/leaflet', 'nicegui/elements/xterm',
        'nicegui/static/sass.dart.js')

EXCLUDES = ['numpy', 'pandas', 'matplotlib', 'scipy', 'PIL', 'pytest', 'pip', 'wheel',
            'IPython', 'sqlalchemy', 'setuptools', 'pkg_resources', 'selenium', 'openpyxl',
            'tkinter']


def wanted(entry):
    """Analysis.datas holds (destination, source, typecode), so match on the destination."""
    return not any(piece in str(entry[0]).replace('\\', '/') for piece in DROP)


spike = Analysis([str(ROOT / 'packaging' / 'entry_web.py')], pathex=[str(ROOT)],
                 # The browser fetches these from /vendor; they are not importable modules.
                 datas=[(str(ROOT / 'mail_assistant' / 'vendor'), 'mail_assistant/vendor')],
                 # uvicorn picks its protocol and loop implementations by name at runtime.
                 hiddenimports=collect_submodules('uvicorn'),
                 excludes=EXCLUDES, noarchive=False)

kept = [entry for entry in spike.datas if wanted(entry)]
print(f'[spike] nicegui 자산 {len(spike.datas) - len(kept)}개 제외, {len(kept)}개 유지')
spike.datas = kept

pyz = PYZ(spike.pure)

exe = EXE(pyz, spike.scripts, [], exclude_binaries=True, name='MailAssistantWeb',
          console=True, icon=ICON, upx=False, strip=False)

coll = COLLECT(exe, spike.binaries, spike.datas, strip=False, upx=False,
               name='MailAssistantWeb')
