"""Rebuild packaging/app.ico from packaging/app.png.

The .ico is a binary blob in the repository, so keep it reproducible: edit or
replace app.png, run this, commit both. Full bleed on purpose -- a margin costs
pixels at 16px, where the calendar's smile is the first thing to turn to mush.
"""
from pathlib import Path

from PIL import Image

SIZES = (16, 24, 32, 48, 64, 128, 256)
HERE = Path(__file__).resolve().parent


def main():
    source = Image.open(HERE / 'app.png').convert('RGBA')
    if source.width != source.height:
        raise SystemExit(f'app.png는 정사각형이어야 합니다: {source.size}')
    frames = [source.resize((size, size), Image.LANCZOS) for size in SIZES]
    frames[-1].save(HERE / 'app.ico', format='ICO',
                    sizes=[(size, size) for size in SIZES], append_images=frames[:-1])
    print('app.ico', sorted(Image.open(HERE / 'app.ico').info['sizes']))


if __name__ == '__main__':
    main()
