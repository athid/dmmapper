#!/usr/bin/env python3
"""
parse_dungeon.py
-----------------

This utility parses an uncompressed Dungeon Master 1 (PC version) ``DUNGEON.DAT``
file and extracts the layout of each map as a 32×32 grid of tile types.  It
also scans sensor objects to locate floor pressure plates and wall buttons and
captures the starting party position.  The resulting data is written to JSON
files: one per map plus a ``legend.json`` describing tile codes, the starting
position, and lists of pressure plates and buttons.

The script assumes the dungeon file is little‑endian (PC) and uncompressed.
Only those sections relevant for decoding map layouts and sensors are parsed.

Usage:

    python parse_dungeon.py \
        --input /path/to/DUNGEON.DAT \
        --output_dir /path/to/output

The output directory will contain ``level_00.json`` through ``level_NN.json``
for each map, and ``legend.json`` containing a mapping from numeric tile
codes to human‑readable names as well as the starting position and lists of
sensors.
"""

import argparse
import json
import os
import struct
from typing import Dict, List, Tuple


def read_word_le(data: bytes, offset: int) -> int:
    """Read a 16‑bit little‑endian unsigned integer from ``data`` at ``offset``."""
    return struct.unpack_from('<H', data, offset)[0]


def parse_header(data: bytes) -> Dict[str, int]:
    """Parse the dungeon header.

    Returns a dictionary with fields:
        random_seed, map_data_size, num_maps, start_position_word, object_list_size
    """
    random_seed = read_word_le(data, 0x00)
    map_data_size = read_word_le(data, 0x02)
    num_maps = data[0x04]
    # byte at 0x05 is padding
    text_data_size_words = read_word_le(data, 0x06)  # unused here
    start_position_word = read_word_le(data, 0x08)
    object_list_size_words = read_word_le(data, 0x0A)
    # Skip counts of objects; starting at 0x0C there are 16 words for object counts
    return {
        'random_seed': random_seed,
        'map_data_size': map_data_size,
        'num_maps': num_maps,
        'start_position_word': start_position_word,
        'object_list_size_words': object_list_size_words,
    }


def parse_map_definitions(data: bytes, num_maps: int) -> List[dict]:
    """Parse the map definitions section and return a list of map descriptors.

    Each map descriptor is a dictionary with keys:
        offset: relative offset of tile data in the global map data (bytes)
        width: map width in tiles
        height: map height in tiles
        level: level number (0..63)
        creature_graphics_count: number of creature graphics entries
        wall_graphics_count: number of wall decoration graphics entries
        floor_graphics_count: number of floor decoration graphics entries
        door_decoration_count: number of door decoration graphics entries
    """
    map_defs: List[dict] = []
    base_offset = 0x2C
    for i in range(num_maps):
        off = base_offset + i * 16
        # 0x00: 1 word: offset of map data in global map data
        map_data_off = read_word_le(data, off + 0x00)
        # 0x02: 1 word: bit field (unused in DM1)
        # 0x04: 1 word: unused
        # 0x06: 1 byte: map offset x (unused)
        # 0x07: 1 byte: map offset y (unused)
        # 0x08: 1 word: map size and level number
        size_word = read_word_le(data, off + 0x08)
        height_minus_1 = (size_word >> 11) & 0x1F
        width_minus_1 = (size_word >> 6) & 0x1F
        level_num = size_word & 0x3F
        width = width_minus_1 + 1
        height = height_minus_1 + 1
        # 0x0A: 1 word: number of graphics (floor and wall graphics counts)
        graphics_word = read_word_le(data, off + 0x0A)
        floor_graphics_count = (graphics_word >> 8) & 0x0F
        wall_graphics_count = graphics_word & 0x0F
        # 0x0C: 1 word: map difficulty and number of door decorations and creatures
        misc_word = read_word_le(data, off + 0x0C)
        creature_graphics_count = (misc_word >> 4) & 0x0F
        door_decoration_count = misc_word & 0x0F
        # 0x0E: 1 word: door indices (unused here)
        map_defs.append({
            'offset': map_data_off,
            'width': width,
            'height': height,
            'level': level_num,
            'creature_graphics_count': creature_graphics_count,
            'wall_graphics_count': wall_graphics_count,
            'floor_graphics_count': floor_graphics_count,
            'door_decoration_count': door_decoration_count,
        })
    return map_defs


def parse_maps(
    data: bytes,
    map_defs: List[dict],
    legend_map: Dict[int, str],
) -> Tuple[
    List[dict],                      # level_info_list
    List[List[List[int]]],           # raw_tile_data_list
    List[List[List[bool]]],          # raw_presence_data_list
    List[List[int]],                 # wall decoration lists per map
]:
    """Parse all map tile data and return level info, tile codes, presence flags, and wall decorations.

    Parameters
    ----------
    data : bytes
        The entire dungeon file as a bytes object.
    map_defs : List[dict]
        A list of map definitions as returned by ``parse_map_definitions``.
    legend_map : Dict[int, str]
        Mapping from numeric tile codes (0..7) to human‑readable names.

    Returns
    -------
    (level_info_list, raw_tile_data_list, raw_presence_data_list, wall_decorations)
        level_info_list: list of dicts with keys 'level', 'width', 'height', 'grid' (32×32 list of names) and
        additional fields 'door_orientation', 'stairs_orientation', 'stairs_direction'.  Each of these
        additional fields is a 32×32 grid where valid entries are strings ('horizontal', 'vertical', 'up', 'down')
        or ``None`` for non‑door and non‑stairs tiles.
        raw_tile_data_list: list of 2D arrays (height × width) of numeric tile codes (0..7)
        raw_presence_data_list: list of 2D boolean arrays (height × width) indicating if objects are present on each tile (bit 4 set)
        wall_decorations: list of lists; each inner list contains the wall decoration IDs for that map, in the order defined by the map data
    """
    base_map_data_offset = 0x5250  # Start of global map data for DM1 PC
    level_info_list: List[dict] = []
    raw_tile_data: List[List[List[int]]] = []
    raw_presence_data: List[List[List[bool]]] = []
    wall_decorations_list: List[List[int]] = []
    for map_index, mdef in enumerate(map_defs):
        start = base_map_data_offset + mdef['offset']
        w = mdef['width']
        h = mdef['height']
        # extract tile bytes (column major)
        tiles_bytes = data[start : start + w * h]
        # build raw tile code grid (h rows, w columns) and presence grid
        tile_codes: List[List[int]] = [[0] * w for _ in range(h)]
        presence: List[List[bool]] = [[False] * w for _ in range(h)]
        orient: List[List[str]] = [[None] * w for _ in range(h)]  # 'horizontal'/'vertical' for doors
        stairs_orient: List[List[str]] = [[None] * w for _ in range(h)]  # 'horizontal'/'vertical' for stairs
        stairs_dir: List[List[str]] = [[None] * w for _ in range(h)]  # 'up'/'down' for stairs
        idx = 0
        for x in range(w):
            for y in range(h):
                tile_byte = tiles_bytes[idx]
                tile_code = (tile_byte >> 5) & 0x7
                tile_codes[y][x] = tile_code
                presence[y][x] = (tile_byte & 0x10) != 0  # bit 4 indicates objects on tile
                # Orientation for door tiles (tile_code 4).  Bit 3 of tile_byte indicates orientation: 0 = horizontal (west-east), 1 = vertical (north-south)
                if tile_code == 4:
                    orientation_bit = (tile_byte >> 3) & 1
                    orient[y][x] = 'vertical' if orientation_bit == 1 else 'horizontal'
                # Orientation and direction for stairs (tile_code 3).  Bit 3: orientation (0 horizontal, 1 vertical); Bit 2: direction (0 down, 1 up)
                if tile_code == 3:
                    orientation_bit = (tile_byte >> 3) & 1
                    direction_bit = (tile_byte >> 2) & 1
                    stairs_orient[y][x] = 'vertical' if orientation_bit == 1 else 'horizontal'
                    stairs_dir[y][x] = 'up' if direction_bit == 1 else 'down'
                idx += 1
        raw_tile_data.append(tile_codes)
        raw_presence_data.append(presence)
        # skip creature graphics
        offset = start + w * h
        # Skip creature graphics
        offset += mdef['creature_graphics_count']
        # Parse wall decoration graphics IDs for this map
        wall_graphics_count = mdef['wall_graphics_count']
        wall_decorations: List[int] = []
        if wall_graphics_count > 0:
            wall_decorations = list(data[offset : offset + wall_graphics_count])
        else:
            wall_decorations = []
        offset += wall_graphics_count
        # Skip floor decorations
        offset += mdef['floor_graphics_count']
        # Skip door decoration graphics
        offset += mdef['door_decoration_count']
        # Note: 'offset' is now at end of map's decoration lists, not used further here
        wall_decorations_list.append(wall_decorations)
        # Build 32×32 grid of names (pad beyond width/height with 'wall')
        padded_grid: List[List[str]] = [[legend_map[0]] * 32 for _ in range(32)]
        for y in range(h):
            for x in range(w):
                code = tile_codes[y][x]
                name = legend_map.get(code, legend_map[0])
                padded_grid[y][x] = name
        level_info_list.append({
            'level': mdef['level'],
            'width': w,
            'height': h,
            'grid': padded_grid,
            'door_orientation': [[orient[y][x] if (y < h and x < w) else None for x in range(32)] for y in range(32)],
            'stairs_orientation': [[stairs_orient[y][x] if (y < h and x < w) else None for x in range(32)] for y in range(32)],
            'stairs_direction': [[stairs_dir[y][x] if (y < h and x < w) else None for x in range(32)] for y in range(32)],
        })
    return level_info_list, raw_tile_data, raw_presence_data, wall_decorations_list


def map_objects_to_tiles(
    raw_tile_data: List[List[List[int]]],
    raw_presence_data: List[List[List[bool]]],
    map_defs: List[dict],
    object_ids: List[int],
    object_next_lookup: Dict[int, Tuple[int, int]],
    sensors_offset: int,
    sensor_count: int,
    wall_decorations_list: List[List[int]],
    sensors_entry_size: int = 8,
    data: bytes = b"",
) -> Tuple[List[dict], List[dict], List[dict]]:
    """Map sensors to tiles and classify pressure plates, buttons, and fountains.

    Parameters
    ----------
    raw_tile_data : List[List[List[int]]]
        List of raw tile code grids (height × width) for each map.
    map_defs : List[dict]
        Map definitions list.
    object_ids : List[int]
        List of object ID entries from the 'List of object IDs of first objects on tiles'.
        Each entry corresponds to a tile that has the bit 4 set in its tile byte.
    object_next_lookup : Dict[int, Tuple[int, int]]
        Mapping from category code (0..15) to (list_offset, entry_size).  Used to look up
        the next object ID in the appropriate object list.  The value is a tuple
        (list_offset, entry_size).
    sensors_offset : int
        Byte offset in the file where the sensors list begins.
    sensor_count : int
        Number of sensors in the list (calculated from object counts or known constant).
    sensors_entry_size : int
        Size in bytes of each sensor entry (default 8 bytes).

    Returns
    -------
    (pressure_plates, buttons, fountains)
        pressure_plates: list of dicts with keys 'level', 'x', 'y', 'type'
        buttons: list of dicts with keys 'level', 'x', 'y', 'direction', 'type'
        fountains: list of dicts with keys 'level', 'x', 'y', 'direction'
    """
    pressure_plates: List[dict] = []
    buttons: List[dict] = []
    fountains: List[dict] = []
    # Build mapping of tile coordinates that have objects (bit 4 set)
    tiles_with_objects: List[Tuple[int, int, int]] = []  # (level_idx, x, y)
    # Iterate through maps in order
    for level_idx, (mdef, presence_grid) in enumerate(zip(map_defs, raw_presence_data)):
        h = mdef['height']
        w = mdef['width']
        for x in range(w):
            for y in range(h):
                if presence_grid[y][x]:
                    tiles_with_objects.append((level_idx, x, y))
    # Validate that we have enough tiles for object IDs
    if len(tiles_with_objects) < len(object_ids):
        raise ValueError(
            f"Not enough tiles with objects: found {len(tiles_with_objects)}, expected {len(object_ids)}"
        )
    # Map sensors to coordinates
    for idx, obj_id in enumerate(object_ids):
        if obj_id == 0xFFFF:
            continue
        # Get tile coordinate for this object list entry
        if idx >= len(tiles_with_objects):
            break
        level_idx, x, y = tiles_with_objects[idx]
        # Decode object ID fields
        obj_pos = (obj_id >> 14) & 0x3  # position on tile
        obj_cat = (obj_id >> 10) & 0xF
        obj_num = obj_id & 0x3FF
        # Follow object chain
        current_id = obj_id
        while current_id != 0xFFFE and current_id != 0xFFFF:
            pos_bits = (current_id >> 14) & 0x3
            category = (current_id >> 10) & 0xF
            number = current_id & 0x3FF
            if category == 3:  # Sensor
                # Read sensor data
                if number >= sensor_count:
                    break
                sensor_offset = sensors_offset + number * sensors_entry_size
                # sensor type bits are bits 6-0 of word at sensor_offset+2?
                # According to spec, sensor structure is:
                # 0x0: Next object ID (word)
                # 0x2: word: Bits 15-7: data, Bits 6-0: type
                sensor_type_word = read_word_le(data, sensor_offset + 2)
                sensor_type = sensor_type_word & 0x7F  # 7 bits
                # Determine tile type (floor/wall) from raw_tile_data (code): 0=wall,1=floor,2=pit,...
                tile_code = raw_tile_data[level_idx][y][x]
                tile_name_code = tile_code
                # Floor tile types: 1=floor, 2=pit, 3=stairs, 5=teleporter? 6 trick wall? We'll treat floor as
                # any tile that is not wall (0) or door (4).  Buttons only valid on walls (tile code 0).
                is_wall = (tile_code == 0)
                # Pressure plates: floor sensors type in {1,2,3,4,7}
                # Buttons: wall sensors type in {1,2,3,4}
                if not is_wall and sensor_type in {1, 2, 3, 4, 7}:
                    pressure_plates.append({
                        'level': map_defs[level_idx]['level'],
                        'x': x,
                        'y': y,
                        'type': sensor_type,
                    })
                elif is_wall and sensor_type in {1, 2, 3, 4}:
                    # Determine direction from object position bits
                    direction_map = {0: 'north', 1: 'east', 2: 'south', 3: 'west'}
                    direction = direction_map.get(pos_bits, 'north')
                    buttons.append({
                        'level': map_defs[level_idx]['level'],
                        'x': x,
                        'y': y,
                        'direction': direction,
                        'type': sensor_type,
                    })
                # Decoration sensors: wall sensor type 0 can display a decoration.
                # If sensor_type == 0, check decoration ordinal and map to fountain if applicable.
                if sensor_type == 0 and is_wall:
                    # Read third word of sensor (offset +4) to get decoration ordinal bits 15-12
                    dec_word = read_word_le(data, sensor_offset + 4)
                    decoration_ordinal = (dec_word >> 12) & 0xF
                    # decoration_ordinal == 0 means no decoration; ordinal 1 corresponds to index 0 in wall_decorations list
                    if decoration_ordinal > 0:
                        # Get wall decorations list for current map (level_idx)
                        wall_decorations = wall_decorations_list[level_idx] if level_idx < len(wall_decorations_list) else []
                        index = decoration_ordinal - 1
                        if index < len(wall_decorations):
                            decoration_id = wall_decorations[index]
                            # Decoration ID 35 corresponds to 'Fountain' according to DM1 graphics list
                            if decoration_id == 35:
                                direction_map = {0: 'north', 1: 'east', 2: 'south', 3: 'west'}
                                direction = direction_map.get(pos_bits, 'north')
                                fountains.append({
                                    'level': map_defs[level_idx]['level'],
                                    'x': x,
                                    'y': y,
                                    'direction': direction,
                                })
            # Find next object ID
            # Determine which list to index based on category
            if category not in object_next_lookup:
                break
            list_offset, entry_size = object_next_lookup[category]
            obj_offset = list_offset + number * entry_size
            next_id = read_word_le(data, obj_offset)
            if next_id == 0xFFFF or next_id == 0xFFFE:
                break
            current_id = next_id
    return pressure_plates, buttons, fountains


def main():
    parser = argparse.ArgumentParser(description="Parse Dungeon Master dungeon and output JSON maps and legend.")
    parser.add_argument(
        '--input', required=True, help="Path to DUNGEON.DAT file (uncompressed)."
    )
    parser.add_argument(
        '--output_dir', required=True, help="Directory to write JSON files to."
    )
    args = parser.parse_args()

    with open(args.input, 'rb') as f:
        data = f.read()

    header = parse_header(data)
    num_maps = header['num_maps']
    map_defs = parse_map_definitions(data, num_maps)
    # Legend mapping from numeric tile codes to names
    legend_map = {
        0: 'wall',
        1: 'floor',
        2: 'pit',
        3: 'stairs',
        4: 'door',
        5: 'teleporter',
        6: 'trick_wall',
        7: 'empty',
    }
    # Parse maps: this also returns the list of wall decorations for each map
    level_info_list, raw_tile_data, raw_presence_data, wall_decorations_list = parse_maps(data, map_defs, legend_map)
    # Prepare output directory
    os.makedirs(args.output_dir, exist_ok=True)
    # Write each level to JSON
    for info in level_info_list:
        level_idx = info['level']
        out_path = os.path.join(args.output_dir, f"level_{level_idx:02d}.json")
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(info, f, indent=2)
    # Parse object IDs list of first objects on tiles
    obj_list_offset = 0x043E
    object_list_size_words = header['object_list_size_words']
    object_ids: List[int] = []
    for i in range(object_list_size_words):
        obj_id = read_word_le(data, obj_list_offset + i * 2)
        if obj_id != 0xFFFF:
            object_ids.append(obj_id)
    # Build lookup for next object ID (list_offset, entry_size) by category
    object_next_lookup = {
        0: (0x1F06, 4),  # Doors: 4 bytes per entry
        1: (0x21AE, 6),  # Teleporters: 6 bytes per entry
        2: (0x25E0, 4),  # Texts: 4 bytes per entry
        3: (0x27D4, 8),  # Sensors: 8 bytes per entry
        4: (0x3D34, 16),  # Creatures: 16 bytes per entry
        5: (0x4894, 4),  # Weapons: 4 bytes per entry
        6: (0x4A40, 4),  # Clothes: 4 bytes per entry
        7: (0x4C24, 4),  # Scrolls: 4 bytes per entry
        8: (0x4CB0, 4),  # Potions: 4 bytes per entry
        9: (0x4D90, 8),  # Containers: 8 bytes per entry
        10: (0x4DF0, 4),  # Misc: 4 bytes per entry
        # Projectiles and clouds lists are empty in DM1
    }
    # Number of sensors = number of sensors in sensors list (684 sensors)
    sensors_offset = 0x27D4
    sensor_count = 684
    pressure_plates, buttons, fountains = map_objects_to_tiles(
        raw_tile_data,
        raw_presence_data,
        map_defs,
        object_ids,
        object_next_lookup,
        sensors_offset,
        sensor_count,
        wall_decorations_list,
        data=data,
    )
    # Decode starting position
    start_word = header['start_position_word']
    start_direction_bits = (start_word >> 10) & 0x3
    direction_map = {0: 'north', 1: 'east', 2: 'south', 3: 'west'}
    start_dir = direction_map.get(start_direction_bits, 'north')
    start_y = (start_word >> 5) & 0x1F
    start_x = start_word & 0x1F
    starting_position = {
        'map': 0,  # Always map 0
        'x': start_x,
        'y': start_y,
        'direction': start_dir,
    }
    # Prepare legend JSON
    legend_json = legend_map.copy()
    legend_json.update({
        'starting_position': starting_position,
        'pressure_plates': pressure_plates,
        'buttons': buttons,
        'fountains': fountains,
    })
    with open(os.path.join(args.output_dir, 'legend.json'), 'w', encoding='utf-8') as f:
        json.dump(legend_json, f, indent=2)
    print(f"Parsed {len(level_info_list)} levels. Output written to {args.output_dir}.")


if __name__ == '__main__':
    main()