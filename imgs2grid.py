#!/usr/bin/env python3
# DeepSeek chat reference: https://chat.deepseek.com/a/chat/s/23776899-ace3-4ba9-a8a2-a9d2d83bb21e
"""
Grid Images to PDF Converter with optional filename labels
-----------------------------------------------------------
Takes all images in a directory, groups them into bunches of 9 (3x3 grid),
and places each bunch on a single US Letter page (300 DPI).
Each image is scaled to fit its cell while preserving aspect ratio.
Optionally, the filename (without extension) is printed below each image.
Supports per-edge margins, cell spacing, and optional forced rotation.
Only processes images in the top-level directory (no subdirectories).

Usage:
    python3 script.py [directory_path]

If no directory is given, the INPUT_DIR variable in the script is used.
"""

import os
import sys
from math import ceil
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ========== CONFIGURATION - EDIT THESE VALUES ==========
INPUT_DIR = "/tmp/"                    # Directory containing images (default)
OUTPUT_PDF = "output_grid.pdf"         # Output PDF filename

# Per-edge margins in pixels (150px = 0.5 inches at 300 DPI)
MARGIN_TOP = 200
MARGIN_BOTTOM = 300
MARGIN_LEFT = 200
MARGIN_RIGHT = 200

# Gap between cells in the grid (pixels)
CELL_GAP = 20

# Set to True to rotate any image with width > height to portrait (vertical),
# set to False to leave all images in their original orientation.
ROTATE_TO_VERTICAL = True

# ------------- FILENAME LABEL OPTIONS -------------
SHOW_FILENAME = True           # Set to True to print filename below each image
FONT_SIZE = 30                 # Font size in pixels (at 300 DPI)
FONT_COLOR = "black"           # Any valid PIL color (e.g., "black", "#333333")
FONT_PATH = "/usr/share/fonts/noto/NotoSans-Regular.ttf"               # Path to a .ttf/.otf file, or None for default PIL font
TEXT_MARGIN_BOTTOM = 10        # Extra space below the text (within the cell)
GAP_BETWEEN_IMAGE_AND_TEXT = 5  # Gap between the image and the text (pixels)
EXTRA_GAP_BELOW_TEXT = 10       # Additional gap below the text (increases space to next row)
# ---------------------------------------------------

# Set to True for RAW mode (exact pixels, no final scaling),
# False for normal mode (pages are already at US Letter size)
RAW_MODE = False   # not really used, kept for compatibility
# ===================================================

# Grid dimensions (fixed 3x3)
GRID_COLS = 3
GRID_ROWS = 3

# US Letter size at 300 DPI (fixed, do not change)
PAGE_WIDTH = 2550
PAGE_HEIGHT = 3300

# Supported image extensions
IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp')


def get_images(directory):
    """Return sorted list of image paths in the top-level directory."""
    images = []
    for ext in IMAGE_EXTS:
        # Use glob with case-insensitive matching
        pattern = f"*{ext}"
        for path in Path(directory).glob(pattern):
            if path.is_file():
                images.append(str(path))
        # also check uppercase extensions
        for path in Path(directory).glob(f"*{ext.upper()}"):
            if path.is_file() and str(path) not in images:
                images.append(str(path))
    return sorted(images)


def create_grid_page(images_chunk, page_num, total_pages):
    """
    Create a single page (PIL Image) with up to 9 images arranged in a 3x3 grid.
    If SHOW_FILENAME is True, each image will have its base name (without extension)
    printed below it, with a configurable gap between the image and the text,
    and an extra configurable gap below the text to increase row separation.
    """
    # Create white canvas
    page = Image.new('RGB', (PAGE_WIDTH, PAGE_HEIGHT), 'white')

    # Calculate live area and cell dimensions
    live_width = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    live_height = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
    cell_width = (live_width - (GRID_COLS - 1) * CELL_GAP) // GRID_COLS
    cell_height = (live_height - (GRID_ROWS - 1) * CELL_GAP) // GRID_ROWS

    # Prepare font if labels are enabled
    font = None
    if SHOW_FILENAME:
        try:
            if FONT_PATH and os.path.isfile(FONT_PATH):
                font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
            else:
                # Use default PIL font (bitmap, may be small)
                font = ImageFont.load_default()
                print(f"  Warning: Using default PIL font; FONT_PATH not set or invalid.")
        except Exception as e:
            print(f"  Warning: Could not load font: {e}. Using default.")
            font = ImageFont.load_default()

    # Process up to 9 images
    for idx, img_path in enumerate(images_chunk):
        row = idx // GRID_COLS
        col = idx % GRID_COLS

        # Compute top-left corner of the cell (including margins and gaps)
        cell_x = MARGIN_LEFT + col * (cell_width + CELL_GAP)
        cell_y = MARGIN_TOP + row * (cell_height + CELL_GAP)

        # Open image and possibly rotate to vertical
        with Image.open(img_path) as img:
            # Convert to RGB if necessary (e.g., PNG with alpha)
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')

            # Rotate if requested and image is landscape
            if ROTATE_TO_VERTICAL and img.width > img.height:
                img = img.rotate(90, expand=True)

            # Determine space needed for text (if labels are enabled)
            text_height = 0
            text_width = 0
            label = ""
            if SHOW_FILENAME:
                label = os.path.splitext(os.path.basename(img_path))[0]
                # Get text dimensions using the font
                draw = ImageDraw.Draw(page)
                try:
                    bbox = draw.textbbox((0, 0), label, font=font)
                    text_width = bbox[2] - bbox[0]
                    text_height = bbox[3] - bbox[1]
                except AttributeError:
                    try:
                        text_width, text_height = draw.textsize(label, font=font)
                    except AttributeError:
                        text_width = len(label) * FONT_SIZE // 2
                        text_height = FONT_SIZE

            # Calculate available height for the image
            if SHOW_FILENAME:
                # Place text at the bottom of the cell, with margins and extra gap below
                text_y = cell_y + cell_height - text_height - TEXT_MARGIN_BOTTOM - EXTRA_GAP_BELOW_TEXT
                # The image must end above the text, leaving GAP_BETWEEN_IMAGE_AND_TEXT
                max_img_height = text_y - GAP_BETWEEN_IMAGE_AND_TEXT - cell_y
                # Ensure we don't get negative
                if max_img_height < 0:
                    max_img_height = 0
            else:
                max_img_height = cell_height

            # Resize image to fit within (cell_width, max_img_height)
            img.thumbnail((cell_width, max_img_height), Image.Resampling.LANCZOS)

            # Center the image horizontally and vertically in the image zone
            x_offset = cell_x + (cell_width - img.width) // 2
            # The image zone is from cell_y to cell_y + max_img_height
            y_offset = cell_y + (max_img_height - img.height) // 2

            # Paste onto page
            page.paste(img, (x_offset, y_offset))

            # If labels are enabled, draw the filename below the image
            if SHOW_FILENAME:
                # The text_y is already computed; we just need to center horizontally
                text_x = cell_x + (cell_width - text_width) // 2
                draw.text((text_x, text_y), label, fill=FONT_COLOR, font=font)

    return page


def print_usage():
    print("Usage: python3 script.py [directory_path]")
    print("If no directory is given, the INPUT_DIR variable in the script is used.")
    print("Options:")
    print("  -h, --help    Show this help message")


def main():
    # Parse command line arguments
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg in ('-h', '--help'):
            print_usage()
            sys.exit(0)
        else:
            input_dir = arg
    else:
        input_dir = INPUT_DIR

    # Check if directory exists
    if not os.path.isdir(input_dir):
        print(f"Error: Directory '{input_dir}' does not exist.")
        sys.exit(1)

    # Get all images
    images = get_images(input_dir)
    total_images = len(images)
    if total_images == 0:
        print(f"Error: No supported images found in '{input_dir}'")
        print(f"Supported formats: {', '.join(IMAGE_EXTS)}")
        sys.exit(1)

    # Calculate number of pages
    images_per_page = GRID_COLS * GRID_ROWS
    total_pages = ceil(total_images / images_per_page)

    print("==============================================")
    print("Grid Images to PDF Converter (3×3 per page)")
    print("==============================================")
    print(f"Found {total_images} image(s) in: {input_dir}")
    print(f"Will generate {total_pages} page(s) (9 images per page)")
    print(f"Margins - Top:{MARGIN_TOP} Bottom:{MARGIN_BOTTOM} Left:{MARGIN_LEFT} Right:{MARGIN_RIGHT}")
    print(f"Cell gap: {CELL_GAP} px")
    print(f"Rotate to vertical: {'YES' if ROTATE_TO_VERTICAL else 'NO'}")
    print(f"Show filenames: {'YES' if SHOW_FILENAME else 'NO'}")
    if SHOW_FILENAME:
        print(f"  Font size: {FONT_SIZE} px, Color: {FONT_COLOR}")
        print(f"  Font path: {FONT_PATH if FONT_PATH else 'default'}")
        print(f"  Gap between image and text: {GAP_BETWEEN_IMAGE_AND_TEXT} px")
        print(f"  Extra gap below text: {EXTRA_GAP_BELOW_TEXT} px")
        print(f"  Text bottom margin: {TEXT_MARGIN_BOTTOM} px")
    print("==============================================")
    print()

    # Process each page
    pages = []
    for page_num in range(total_pages):
        start = page_num * images_per_page
        end = min(start + images_per_page, total_images)
        chunk = images[start:end]
        print(f"--- Page {page_num+1}/{total_pages} ---")
        for img_path in chunk:
            print(f"  Processing: {os.path.basename(img_path)}")
        page_img = create_grid_page(chunk, page_num+1, total_pages)
        pages.append(page_img)
        print(f"  ✓ Page {page_num+1} ready")

    print()
    print("Saving PDF...")
    # Save all pages as a single PDF
    pages[0].save(
        OUTPUT_PDF,
        save_all=True,
        append_images=pages[1:],
        resolution=300.0,
        title="Grid Images PDF"
    )
    print(f"✓ PDF created: {OUTPUT_PDF}")
    print(f"  Total pages: {total_pages}")
    print("==============================================")


if __name__ == "__main__":
    main()
