"""Generate PWA icon PNG files without external dependencies.

Creates simple flame-at-a-glance style PNGs with a dark background.
Uses the built-in zlib and struct modules to write valid PNGs.
"""

import struct
import zlib
from pathlib import Path

ICON_DIR = Path(__file__).resolve().parent / ".." / "static" / "icons"


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Build a PNG chunk with CRC."""
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def render_flame_png(size: int, path: Path) -> None:
    """Render a dark square with a simple flame shape and save to path."""
    # Simple procedural flame drawn as antialiased pixels.
    pixels = bytearray()

    for y in range(size):
        row = bytearray()
        for x in range(size):
            # Normalized coordinates
            nx = (x + 0.5) / size - 0.5  # -0.5..0.5
            ny = (y + 0.5) / size - 0.5  # -0.5..0.5

            # Flame core: teardrop shape defined by two overlapping circles
            # Outer flame (warm white/gold)
            outer = _flame_intensity(nx, ny)
            # Inner bright core
            inner = _core_intensity(nx, ny)

            # Background dark
            r, g, b = 10, 10, 10
            if outer > 0:
                # White-ish flame
                r = int(250 * outer)
                g = int(200 * outer)
                b = int(80 * outer)
            if inner > 0:
                # Bright core
                r = int(255 * inner + 250 * (1 - inner) * (0.4 + 0.4 * outer))
                g = int(255 * inner + 200 * (1 - inner) * (0.4 + 0.4 * outer))
                b = min(255, int(200 * inner + 80 * (1 - inner)))

            row.extend((r, g, b))
        pixels.extend(b"\x00")  # filter byte: None
        pixels.extend(row)

    # Build PNG
    raw = bytes(pixels)
    compressed = zlib.compress(raw, 9)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)

    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", ihdr)
    png += _chunk(b"IDAT", compressed)
    png += _chunk(b"IEND", b"")

    path.write_bytes(png)


def _flame_intensity(nx: float, ny: float) -> float:
    """Flame body intensity for normalized coords."""
    # Teardrop: a main ellipse body + smaller top flare
    body = _circle(nx, ny * 1.3, 0.0, 0.15, 0.32, 0.42)
    flare = _circle(nx * 0.7, ny + 0.15, 0.0, 0.28, 0.18, 0.18)
    return max(body, flare)


def _core_intensity(nx: float, ny: float) -> float:
    """Bright center core."""
    return _circle(nx * 0.8, ny + 0.05, 0.0, 0.12, 0.15, 0.15)


def _circle(nx: float, ny: float, cx: float, cy: float, rx: float, ry: float) -> float:
    """Normalized signed distance to an ellipse; >0 inside."""
    dx = (nx - cx) / rx
    dy = (ny - cy) / ry
    d = dx * dx + dy * dy
    if d >= 1:
        return 0.0
    # Smooth edge
    return 1.0 - min(1.0, 1.0 - d) * 1.0


def main() -> None:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    render_flame_png(192, ICON_DIR / "icon-192.png")
    render_flame_png(512, ICON_DIR / "icon-512.png")
    print("Icons generated in", ICON_DIR)


if __name__ == "__main__":
    main()
