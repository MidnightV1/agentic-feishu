#!/usr/bin/env python3
"""Image compression subprocess — isolates PIL from main process.

Usage: python3 compress_image.py <input_path> <output_path> [max_dim]

Resizes to fit within max_dim (default 1024) and saves as WebP.
Runs in a subprocess to avoid ld.so dlopen issues when forking.
"""

import sys
from pathlib import Path


def compress(src: str, dst: str, max_dim: int = 1024) -> None:
    from PIL import Image

    img = Image.open(src)

    # Convert RGBA → RGB for WebP compatibility
    if img.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Resize if needed
    w, h = img.size
    if max(w, h) > max_dim:
        ratio = max_dim / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

    # Save as WebP
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, "WEBP", quality=80)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input> <output> [max_dim]", file=sys.stderr)
        sys.exit(1)

    src_path = sys.argv[1]
    dst_path = sys.argv[2]
    max_dimension = int(sys.argv[3]) if len(sys.argv) > 3 else 1024

    compress(src_path, dst_path, max_dimension)
