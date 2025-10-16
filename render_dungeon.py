#!/usr/bin/env python3
"""
render_dungeon.py
------------------

This script renders Dungeon Master levels from JSON map data into PNG images.  It
expects a directory containing one or more `level_XX.json` files (where XX is
the level number) and a `legend.json` that describes the mapping of tile codes
to human‑readable names as well as lists of pressure plates and buttons.  The
script then composes each map into a single image by drawing a base tile image
for every cell and layering overlay icons (pressure plates and buttons) on top.

Tile images and overlays should be supplied as PNG files in the assets
directory.  For each tile category listed in ``legend.json`` (for example
``wall``, ``floor``, ``pit``, ``stairs``, ``door``, ``teleporter``,
``trick_wall``, ``empty``) there must be a corresponding PNG file named
``<category>.png`` in the assets directory.  Overlay icons should be named
``pressure_plate.png`` and ``button.png``.  The base tile images must all
be the same size; the first one loaded defines the tile width and height.

Buttons are rotated to face the correct direction based on the ``direction``
field in the legend: "north" (no rotation), "east" (90° clockwise), "south"
(180°) and "west" (270°).  The rotated icon is centred within the tile.

Usage:

    python render_dungeon.py \
        --levels_dir /path/to/level/jsons \
        --legend /path/to/legend.json \
        --assets_dir /path/to/png/assets \
        --output_dir /path/to/output

The script will create the output directory if it does not already exist and
write one PNG per level, named ``level_XX.png`` where ``XX`` is the level
number padded to two digits.

Note: This script depends on the Pillow library (``PIL``).  Pillow should be
pre‑installed in this environment, but if you run it elsewhere you may need
to ``pip install pillow`` first.
"""

import argparse
import json
import os
from typing import Dict, List, Tuple

from PIL import Image  # type: ignore


def load_base_tiles(asset_dir: str, legend: Dict[str, str]) -> Dict[str, Image.Image]:
    """Load base tile images from the assets directory.

    Parameters
    ----------
    asset_dir : str
        Path to the directory containing PNG assets.
    legend : Dict[str, str]
        Legend mapping from numeric tile codes (as strings) to human readable
        names.  Only entries whose keys are digits are considered.

    Returns
    -------
    Dict[str, Image.Image]
        A dictionary mapping tile names (e.g., ``'floor'``) to opened PIL
        images.  All images are converted to RGBA mode.  If any expected
        asset is missing, a ``FileNotFoundError`` is raised.
    """
    base_tiles: Dict[str, Image.Image] = {}
    for code, name in legend.items():
        # skip non‑numeric keys like 'starting_position', 'pressure_plates', etc.
        if not code.isdigit():
            continue
        filename = f"{name}.png"
        path = os.path.join(asset_dir, filename)
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"Expected base tile asset '{filename}' not found in {asset_dir}"
            )
        img = Image.open(path).convert("RGBA")
        base_tiles[name] = img
    # Ensure all base tiles have the same dimensions
    if base_tiles:
        first_size = next(iter(base_tiles.values())).size
        for name, img in base_tiles.items():
            if img.size != first_size:
                raise ValueError(
                    f"All base tile images must have the same dimensions; '{name}.png'"
                    f" is {img.size}, expected {first_size}"
                )
    return base_tiles


def load_overlays(asset_dir: str) -> Dict[str, Image.Image]:
    """Load overlay icons from the assets directory.

    Parameters
    ----------
    asset_dir : str
        Path to the directory containing PNG assets.

    Returns
    -------
    Dict[str, Image.Image]
        A dictionary containing overlay images for 'pressure_plate' and 'button'.
        Missing overlays are mapped to ``None`` instead of raising an error.
    """
    overlays: Dict[str, Image.Image] = {}
    for name in ["pressure_plate", "button"]:
        filename = f"{name}.png"
        path = os.path.join(asset_dir, filename)
        if os.path.isfile(path):
            overlays[name] = Image.open(path).convert("RGBA")
        else:
            overlays[name] = None
    return overlays


def index_by_level(items: List[dict], level_key: str = "level") -> Dict[int, List[dict]]:
    """Index a list of dictionaries by their level.

    Parameters
    ----------
    items : List[dict]
        List of items (pressure plates or buttons) each containing a 'level'
        key indicating which map it belongs to.
    level_key : str, optional
        Name of the key in each item that stores the level number.  Default
        'level'.

    Returns
    -------
    Dict[int, List[dict]]
        A dictionary mapping level numbers to lists of items.
    """
    indexed: Dict[int, List[dict]] = {}
    for item in items:
        lvl = item.get(level_key)
        if lvl is None:
            continue
        indexed.setdefault(lvl, []).append(item)
    return indexed


def render_level(
    level_data: dict,
    tile_map: Dict[str, Image.Image],
    overlays: Dict[str, Image.Image],
    plates_by_level: Dict[int, List[dict]],
    buttons_by_level: Dict[int, List[dict]],
    output_dir: str,
) -> str:
    """Render a single map into a PNG image.

    Parameters
    ----------
    level_data : dict
        Parsed JSON data for a single level.  Must contain 'level' (int) and
        'grid' (list of lists of strings).
    tile_map : Dict[str, Image.Image]
        Mapping from tile names (e.g., 'floor') to base tile images.
    overlays : Dict[str, Image.Image]
        Overlay icons mapping, may contain 'pressure_plate' and 'button'.  A
        value of ``None`` means no overlay is available for that type.
    plates_by_level : Dict[int, List[dict]]
        Mapping from level number to list of pressure plate dicts.  Each dict
        should contain keys 'level', 'x', 'y'.
    buttons_by_level : Dict[int, List[dict]]
        Mapping from level number to list of button dicts.  Each dict should
        contain keys 'level', 'x', 'y' and 'direction'.
    output_dir : str
        Directory where the rendered image will be saved.

    Returns
    -------
    str
        Path to the saved PNG file.
    """
    level_num = level_data.get("level")
    if level_num is None:
        raise ValueError("Level data missing 'level' key")
    grid: List[List[str]] = level_data.get("grid")
    if grid is None:
        raise ValueError("Level data missing 'grid' key")
    # Determine tile size from any tile image
    if not tile_map:
        raise ValueError("No base tiles loaded")
    tile_size = next(iter(tile_map.values())).size[0]
    height = len(grid)
    width = len(grid[0]) if height > 0 else 0
    # Orientation grid for doors (optional)
    orientation_grid = level_data.get("door_orientation")
    # Create blank canvas for the map
    img_width = width * tile_size
    img_height = height * tile_size
    canvas = Image.new("RGBA", (img_width, img_height))
    # Draw base layer
    for y in range(height):
        for x in range(width):
            tile_type = grid[y][x]
            # Determine orientation for door tiles
            orientation = None
            if tile_type == 'door' and orientation_grid is not None:
                # orientation_grid is 32x32; indices safe if within bounds
                if y < len(orientation_grid) and x < len(orientation_grid[y]):
                    orientation = orientation_grid[y][x]
            # Get base tile image
            base_img = tile_map.get(tile_type, tile_map.get('wall'))
            if base_img is None:
                raise ValueError(f"No base image found for tile type '{tile_type}'")
            # Rotate door tile if orientation indicates vertical
            if tile_type == 'door' and orientation == 'vertical':
                rotated_img = base_img.rotate(-90, expand=False)
                canvas.paste(rotated_img, (x * tile_size, y * tile_size))
            else:
                canvas.paste(base_img, (x * tile_size, y * tile_size))
    # Overlay pressure plates
    plates = plates_by_level.get(level_num, [])
    plate_icon = overlays.get("pressure_plate")
    if plate_icon:
        for plate in plates:
            x, y = plate.get("x"), plate.get("y")
            if x is None or y is None:
                continue
            # Centre overlay on tile
            dx = x * tile_size + (tile_size - plate_icon.width) // 2
            dy = y * tile_size + (tile_size - plate_icon.height) // 2
            canvas.alpha_composite(plate_icon, (dx, dy))
    # Overlay buttons
    button_icon = overlays.get("button")
    if button_icon:
        buttons = buttons_by_level.get(level_num, [])
        for button in buttons:
            x, y = button.get("x"), button.get("y")
            direction = button.get("direction", "north")
            if x is None or y is None:
                continue
            # Compute rotation based on direction
            angle = 0
            direction_lower = str(direction).lower()
            if direction_lower == "east":
                angle = -90  # pillow rotates counterclockwise; -90 is 90° clockwise
            elif direction_lower == "south":
                angle = 180
            elif direction_lower == "west":
                angle = 90  # rotate 90° counterclockwise
            else:
                angle = 0
            rotated = button_icon.rotate(angle, expand=True)
            dx = x * tile_size + (tile_size - rotated.width) // 2
            dy = y * tile_size + (tile_size - rotated.height) // 2
            canvas.alpha_composite(rotated, (dx, dy))
    # Prepare output path
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"level_{level_num:02d}.png")
    canvas.save(output_path)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Render Dungeon Master levels to PNG images.")
    parser.add_argument(
        "--levels_dir", required=True, help="Directory containing level_XX.json files."
    )
    parser.add_argument(
        "--legend", required=True, help="Path to legend.json file with tile mappings and overlays."
    )
    parser.add_argument(
        "--assets_dir", required=True, help="Directory containing PNG assets for tiles and overlays."
    )
    parser.add_argument(
        "--output_dir", required=True, help="Directory to write rendered PNG maps to."
    )
    args = parser.parse_args()

    # Load legend
    with open(args.legend, "r", encoding="utf-8") as f:
        legend_data = json.load(f)
    # Extract tile name mapping: numeric keys only
    tile_mapping = {k: v for k, v in legend_data.items() if k.isdigit()}
    # Load base tile images
    base_tiles = load_base_tiles(args.assets_dir, tile_mapping)
    # Load overlay icons
    overlay_icons = load_overlays(args.assets_dir)
    # Build per‑level lookup for plates and buttons
    plates_by_level = index_by_level(legend_data.get("pressure_plates", []))
    buttons_by_level = index_by_level(legend_data.get("buttons", []))
    # Render each level JSON file
    level_files = [
        f
        for f in os.listdir(args.levels_dir)
        if f.lower().endswith(".json") and f.startswith("level_")
    ]
    if not level_files:
        raise FileNotFoundError(
            f"No level JSON files found in directory {args.levels_dir}"
        )
    # Sort files by level number extracted from filename
    level_files.sort()
    for filename in level_files:
        level_path = os.path.join(args.levels_dir, filename)
        with open(level_path, "r", encoding="utf-8") as lf:
            level_data = json.load(lf)
        saved_path = render_level(
            level_data,
            tile_map=base_tiles,
            overlays=overlay_icons,
            plates_by_level=plates_by_level,
            buttons_by_level=buttons_by_level,
            output_dir=args.output_dir,
        )
        print(f"Rendered level {level_data.get('level')} -> {saved_path}")


if __name__ == "__main__":
    main()