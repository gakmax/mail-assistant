"""Spike entry point for the web screens (UI-PLAN.md 1단계).

A console build on purpose: the spike exists to print the URL and let a failure be
read, and the measurement in spike.ps1 needs stdout. The wiring itself lives in
mail_assistant/webmain.py, which `MailAssistantTools.exe web` also uses.
"""
import sys


def main(argv):
    from mail_assistant.webmain import main as serve
    return serve(argv)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
