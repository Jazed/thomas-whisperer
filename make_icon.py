#!/usr/bin/env python3
"""Render ThomasWhisperer logo → assets/icon.icns using AppKit."""
import subprocess
from pathlib import Path

from AppKit import (
    NSImage, NSBitmapImageRep, NSColor, NSBezierPath, NSMakeRect,
    NSGraphicsContext,
)
from Foundation import NSMakeSize


def draw_logo(size: int, path: Path) -> None:
    img = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()

    cx = cy = size / 2
    r  = size / 2 * 0.82

    # Clear
    NSColor.clearColor().set()
    NSBezierPath.fillRect_(NSMakeRect(0, 0, size, size))

    # Outer circle — deep purple-blue
    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.18, 0.10, 0.42, 1.0).set()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - r, cy - r, r * 2, r * 2)
    ).fill()

    # Inner highlight — blue overlay
    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.28, 0.38, 0.82, 0.45).set()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - r * 0.62, cy - r * 0.62, r * 1.24, r * 1.24)
    ).fill()

    # Waveform bars
    bar_heights = [0.40, 0.68, 1.00, 0.68, 0.40]
    n     = len(bar_heights)
    bw    = r * 0.15
    gap   = r * 0.12
    total = n * bw + (n - 1) * gap
    x0    = cx - total / 2
    max_h = r * 0.80

    NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 1.0, 1.0, 0.92).set()
    for i, frac in enumerate(bar_heights):
        bh = frac * max_h
        bx = x0 + i * (bw + gap)
        by = cy - bh / 2
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(bx, by, bw, bh), bw / 2, bw / 2
        ).fill()

    img.unlockFocus()

    tiff = img.TIFFRepresentation()
    rep  = NSBitmapImageRep.imageRepWithData_(tiff)
    png  = rep.representationUsingType_properties_(4, None)  # PNG
    png.writeToFile_atomically_(str(path), True)


def main():
    assets  = Path(__file__).parent / "assets"
    assets.mkdir(exist_ok=True)
    iconset = Path("/tmp/tw_icon.iconset")
    iconset.mkdir(exist_ok=True)

    for s in (16, 32, 128, 256, 512):
        draw_logo(s,     iconset / f"icon_{s}x{s}.png")
        draw_logo(s * 2, iconset / f"icon_{s}x{s}@2x.png")

    out = assets / "icon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    import shutil; shutil.rmtree(iconset, ignore_errors=True)
    print(f"  Icon → {out}")


if __name__ == "__main__":
    main()
