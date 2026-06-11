#!/usr/bin/env python3
# ============================================
# DYNAMIC GRID TO PDF CONVERTER (PER-EDGE MARGINS)
# ============================================
# A script that scales and splits an image into parts for printing.
# Each page can have different margins per edge (top, bottom, left, right).
#
# USAGE:
#   python script.py [image_path]    # optional: override INPUT_IMAGE
#
#   If no image is given, the INPUT_IMAGE variable below is used.
# ============================================

import os
import sys
import argparse
from PIL import Image

# ========== EDIT THESE VALUES (defaults) ==========
INPUT_IMAGE = "image.jpg"      # Path to your image (can be overridden via CLI)
OUTPUT_PDF = "output.pdf"      # Output PDF filename
RAW_MODE = False               # True → exact pixel dimensions, False → scale to US Letter

# Grid layout
C = 2                          # Columns
R = 3                          # Rows

# Per‑edge margins in pixels (150px = 0.5 inches at 300 DPI)
MARGIN_TOP = 50
MARGIN_BOTTOM = 250
MARGIN_LEFT = 150
MARGIN_RIGHT = 150

# Overlap between adjacent tiles (pixels) – set >0 to show extra image in each tile
# The tile size on the page remains unchanged; the image is scaled down slightly
# to fit the extra content, so adjacent tiles will have overlapping areas.
OVERLAP_DELTA = 0
# ===================================================

# US Letter size at 300 DPI (fixed)
PAGE_WIDTH = 2550
PAGE_HEIGHT = 3300

# Derived values
LIVE_WIDTH = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
LIVE_HEIGHT = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
TOTAL_GRID_WIDTH = LIVE_WIDTH * C
TOTAL_GRID_HEIGHT = LIVE_HEIGHT * R
TOTAL_PAGES = C * R


def main():
    # ----- Parse optional CLI argument for input image -----
    parser = argparse.ArgumentParser(
        description="Convert an image to a grid PDF with per‑edge margins.",
        epilog="All other settings (grid, margins, raw mode) are configured at the top of the script."
    )
    parser.add_argument(
        "input_image",
        nargs="?",
        help="Path to input image (overrides INPUT_IMAGE variable if provided)"
    )
    args = parser.parse_args()

    # Determine which image to use
    if args.input_image:
        input_path = args.input_image
    else:
        input_path = INPUT_IMAGE

    if not input_path:
        print("Error: No input image specified. Set INPUT_IMAGE in the script or provide it as an argument.", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(input_path):
        print(f"Error: Input file '{input_path}' not found.", file=sys.stderr)
        sys.exit(1)

    # ----- Load image and get dimensions -----
    try:
        img = Image.open(input_path)
    except Exception as e:
        print(f"Error opening image: {e}", file=sys.stderr)
        sys.exit(1)

    orig_width, orig_height = img.size
    print("==============================================")
    print("Dynamic Grid to PDF Converter (Per-Edge Margins)")
    print("==============================================")
    print(f"Original dimensions: {orig_width}×{orig_height}")

    # Auto‑rotate if width > height
    if orig_width > orig_height:
        print("Width > Height: Rotating image 90 degrees clockwise...")
        img = img.transpose(Image.ROTATE_90)
        print(f"Image rotated. New dimensions: {img.width}×{img.height}")
    else:
        print("No rotation needed (Height >= Width)")

    print(f"Mode: {'RAW (exact pixels)' if RAW_MODE else 'NORMAL (scaled to fit US Letter)'}")
    print(f"Grid: {C}×{R} ({TOTAL_PAGES} pages)")
    print(f"Margins - Top:{MARGIN_TOP} Bottom:{MARGIN_BOTTOM} Left:{MARGIN_LEFT} Right:{MARGIN_RIGHT}")
    print(f"Live area: {LIVE_WIDTH}×{LIVE_HEIGHT} px")
    print(f"Total grid: {TOTAL_GRID_WIDTH}×{TOTAL_GRID_HEIGHT} px")
    print(f"Overlap delta: {OVERLAP_DELTA} px")
    print("==============================================")

    # ----- Step 1: Resize image to fill total grid (preserve aspect, allow upscale) -----
    print(f"\nStep 1/3: Resizing image to {TOTAL_GRID_WIDTH}×{TOTAL_GRID_HEIGHT}...")
    target_w, target_h = TOTAL_GRID_WIDTH, TOTAL_GRID_HEIGHT
    scale = min(target_w / img.width, target_h / img.height)
    new_w = int(round(img.width * scale))
    new_h = int(round(img.height * scale))
    img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    # Create white canvas and paste centered
    canvas = Image.new("RGB", (target_w, target_h), "white")
    x_offset = (target_w - new_w) // 2
    y_offset = (target_h - new_h) // 2
    canvas.paste(img_resized, (x_offset, y_offset))

    # ----- Step 2: Split into tiles with overlap (crop larger, scale down) -----
    print(f"Step 2/3: Splitting into {C}×{R} parts with overlap delta={OVERLAP_DELTA}...")
    tiles = []

    for row in range(R):
        for col in range(C):
            # Original tile boundaries (no overlap)
            left = col * LIVE_WIDTH
            top = row * LIVE_HEIGHT
            right = left + LIVE_WIDTH
            bottom = top + LIVE_HEIGHT

            # Expand crop region by delta, clamped to canvas edges
            x1 = max(0, left - OVERLAP_DELTA)
            y1 = max(0, top - OVERLAP_DELTA)
            x2 = min(TOTAL_GRID_WIDTH, right + OVERLAP_DELTA)
            y2 = min(TOTAL_GRID_HEIGHT, bottom + OVERLAP_DELTA)

            # Crop the larger region
            large_crop = canvas.crop((x1, y1, x2, y2))

            # Resize it back to the original tile size (LIVE_WIDTH x LIVE_HEIGHT)
            # This scales the image down, making the tile show a wider view
            tile = large_crop.resize((LIVE_WIDTH, LIVE_HEIGHT), Image.Resampling.LANCZOS)
            tiles.append(tile)

    # ----- Step 3: Place each tile onto a US Letter page with margins -----
    print("Step 3/3: Placing each tile onto US Letter pages with per‑edge margins...")
    pages = []
    for i, tile in enumerate(tiles, start=1):
        page = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), "white")
        page.paste(tile, (MARGIN_LEFT, MARGIN_TOP))
        pages.append(page)
        print(f"  Processed page {i} of {TOTAL_PAGES}")

    # ----- Save as PDF with appropriate scaling -----
    resolution = 72.0 if RAW_MODE else 300.0

    print(f"\nSaving PDF with resolution={int(resolution)} DPI...")
    pages[0].save(
        OUTPUT_PDF,
        save_all=True,
        append_images=pages[1:],
        resolution=resolution
    )

    # ----- Final output & instructions -----
    print("\n==============================================")
    print(f"Done! PDF created: {OUTPUT_PDF}")
    print(f"Pages: {TOTAL_PAGES}")
    if RAW_MODE:
        print("This PDF uses EXACT pixel dimensions (no scaling).")
    else:
        print("This PDF is scaled to US Letter paper (8.5×11 inches).")
    print(f"Margins - Top:{MARGIN_TOP} Bottom:{MARGIN_BOTTOM} Left:{MARGIN_LEFT} Right:{MARGIN_RIGHT}")
    if OVERLAP_DELTA > 0:
        print(f"Overlap delta: {OVERLAP_DELTA} px (tiles show extra content; overlap when assembling and trim excess)")
    print("\nPRINTING INSTRUCTIONS:")
    print("  - Print all pages (actual size, no scaling)")
    print("  - Cut off the white margins")
    if OVERLAP_DELTA > 0:
        print("  - When assembling, overlap the edges to align the image, then trim the overlapping parts")
    print(f"  - Tape/glue pages together in a {C}×{R} grid")
    print("==============================================")


if __name__ == "__main__":
    main()
