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
        --output_dir /path/to/outputo

The output directory will contain ``level_00.json`` through ``level_NN.json``
for each map, and ``legend.json`` containing a mapping from numeric tile
codes to human‑readable names as well as the starting position and lists of
sensors.
"""
