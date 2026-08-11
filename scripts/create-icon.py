from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def create_icon(output: Path) -> None:
    size = 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 248, 248), radius=50, fill="#12344D")
    draw.rounded_rectangle((38, 53, 218, 203), radius=18, fill="#FFFFFF")
    draw.rectangle((38, 86, 218, 105), fill="#19A38C")
    draw.line((128, 53, 128, 203), fill="#D6E3EA", width=5)
    for index, width in enumerate((5, 9, 4, 7, 4, 10, 5, 8, 4)):
        x = 57 + index * 16
        draw.rectangle((x, 124, x + width, 181), fill="#12344D")
    draw.polygon(((115, 53), (141, 53), (128, 73)), fill="#19A38C")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(
        output,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    create_icon(args.output)


if __name__ == "__main__":
    main()
