"""gpu_model.py -- packet encoders and a Python model of the GPU (Chapter 4.2,
extended with FILL_RECT in Chapter 4.3, DRAW_LINE in Chapter 4.4, FLIP in
Chapter 4.5, DRAW_TRI in Chapter 4.7, UPLOAD_ASSET / BLIT in Chapter 4.8 and
GET_INFO, CLEAR_ERRORS, error flags and frame starts in Chapter 5.2, the
tile layers in Chapter 5.3, the sprite table in Chapter 5.4, the layer
compositor in Chapter 5.5, streamed map writes in Chapter 5.6, display
lists in Chapter 5.7, shaded triangles in Chapter 6.3, the depth buffer in Chapter 6.4, the vertex transform in Chapter 6.5 and the index buffer, DRAW_MESH and culling in Chapter 6.6).

Copy to host/gpu_model.py, replacing the Chapter 6.6 version. Everything the
earlier versions did still works the same (feed(bytes) alone is the UART
view of the world). This version adds WRITE_TILE_COLUMN and WRITE_TILE_ROW
(0x40, 0x41), capability bit 7 and the builders write_tile_column() and
write_tile_row(); Gpu.line_stream() executes them. Chapter 5.7 adds the display
list commands 0x50-0x54, capability bit 8, the
builders define_list(), append_list(), list_packets(), run_list(),
write_list_word() and set_auto_list(), and Gpu.run_list() / Gpu.auto_run().
Chapter 6.3 adds DRAW_TRI_SHADED (0x60), capability bit 10 (CAPS_SPI is now
0x07FF in 6.3), the builders draw_tri_shaded() and ramp_palette(), and the counters
tri_shaded_count and tri_shaded_drawn.
Chapter 6.4 adds the depth buffer: SET_DEPTH (0x61), CLEAR_DEPTH (0x62), CLEAR_ALL (0x63),
GET_STATS (0x64) and CLEAR_STATS (0x65), capability bit 11 (CAPS_SPI is now 0x0FFF), the
builders set_depth(), clear_depth(), clear_all(), get_stats(), clear_stats() and
parse_stats(), and the attributes depth, d_test, d_wr, stat_cov, stat_wr, stat_clocks. Chapter 6.5 adds the vertex transform: UPLOAD_VERTICES (0x70), SET_MATRIX (0x71), SET_PROJECTION (0x72),
TRANSFORM (0x73) and READ_VERTEX (0x74), capability bit 12 (CAPS_SPI is now 0x1FFF), the builders upload_vertices(),
vertex_packets(), set_matrix(), set_projection(), transform(), read_vertex() and parse_vertex(), and the attributes
vsrc, vdst, matrix, proj_f, proj_near, read_undefined. The arithmetic is host/vertex_model.py. Chapter 6.6 adds UPLOAD_INDICES (0x66), SET_MESH_MODE (0x67), DRAW_MESH (0x68), GET_MESH_STATS (0x69) and CLEAR_MESH_STATS (0x6A),
capability bit 13 (CAPS_SPI is now 0x3FFF), the builders upload_indices(), index_packets(), set_mesh_mode(), draw_mesh(),
get_mesh_stats(), clear_mesh_stats() and parse_mesh_stats(), and the attributes idx, mesh_mode, mesh_culled, mesh_rejected,
mesh_drawn, mesh_undefined. The decisions are host/mesh_model.py. Chapter 6.7 adds UPLOAD_NORMALS (0x6B) and SET_LIGHT (0x6C),
capability bit 14 (CAPS_SPI is now 0x7FFF), the builders upload_normals(), normal_packets() and set_light(), and the attributes nsrc,
vlit, light_en, light_dir, light_amb, lit_undefined; TRANSFORM now also writes byte 7 of every record it produces (host/light_model.py)
when SET_LIGHT has switched lighting on, READ_VERTEX returns it and DRAW_MESH in vertex-intensity mode draws with it. It needs host/raster_model.py from
Chapter 6.3 (the Chapter 4.6 one has no interpolation) next to it. The host scripts use it to
build packets, and sim/py/ref_cmd.py, ref_rect.py, ref_line.py,
ref_flip_sync.py, ref_flip.py, ref_tri_draw.py and ref_blit.py use the same model to make
the testbenches' expected framebuffer, palette, FB_CRC replies and frames, so
simulation, hardware and Python all follow one definition of the protocol.

Packet: [opcode][length][payload: length bytes]

  * length counts payload bytes only (0-255), not the opcode or itself.
  * 16-bit fields are little-endian, low byte first. Coordinates are signed
    (two's complement), so later steps can pass off-screen and negative
    corners.
  * A packet whose opcode is unknown, or whose length is wrong for its
    opcode, is read to its end and ignored. NOP accepts any length.
  * Commands run in order. FB_CRC sees every earlier command and none of
    the later ones.
  * There are two framebuffers (Step 4.5). The front buffer is the one on
    the screen, the back buffer is the one every drawing command and FB_CRC
    use. At power-up both hold the same picture (the test image on the
    board), buffer 0 is the front, and buffer 1 is the back.

Opcodes so far:

  0x00 NOP          any length   payload ignored
  0x01 SET_PALETTE  4            index, R, G, B        entry = {R, G, B}
  0x02 CLEAR        1            color                 every pixel = color
  0x03 PLOT         5            x lo, x hi, y lo, y hi, color
                                 off-screen (x < 0, x >= 320, y < 0, y >= 240)
                                 changes nothing
  0x04 FB_CRC       0            reply: 4 bytes, the CRC-32 of the 76,800
                                 framebuffer bytes in address order
                                 (y*320 + x, from 0), low byte first
  0x05 FILL_RECT    9            x0, y0, x1, y1 (signed 16-bit each), color
                                 (Step 4.3) fills every on-screen pixel with
                                 min(x0,x1) <= x <= max(x0,x1) and
                                 min(y0,y1) <= y <= max(y0,y1): both corners
                                 are inside the rectangle, the corners may
                                 come in either order, and the part off the
                                 screen is clipped away. x0 == x1 is a
                                 one-pixel-wide column, never empty.
  0x06 DRAW_LINE    9            x0, y0, x1, y1 (signed 16-bit each), color
                                 (Step 4.4) writes the on-screen points of
                                 line_points(x0, y0, x1, y1): Bresenham from
                                 (x0, y0) to (x1, y1), both endpoints
                                 included. Points off the screen are
                                 skipped one by one. The direction matters:
                                 DRAW_LINE a b and DRAW_LINE b a can differ
                                 where the line passes exactly half-way
                                 between two pixels (see line_points).
  0x07 FLIP         0            (Step 4.5) swaps the two buffers: the back
                                 buffer, with everything drawn into it so
                                 far, becomes the front buffer and goes on
                                 the screen during vertical blanking,
                                 and the old front buffer becomes the back
                                 buffer, keeping the picture it had. The
                                 GPU runs no command after FLIP until the
                                 swap has happened. There is no reply, and
                                 the model's swap is instant. The palette
                                 is not double-buffered: SET_PALETTE
                                 changes the colors on screen at once.
  0x08 DRAW_TRI     13           x0, y0, x1, y1, x2, y2 (signed 16-bit each),
                                 color
                                 (Step 4.7) writes color to every pixel of
                                 raster_model.tri_pixels(v0, v1, v2, 320, 240):
                                 the on-screen pixels that Chapter 4.6's
                                 edge functions and top-left rule cover.
                                 Either winding order draws the same pixels,
                                 pixels exactly on a shared edge belong to
                                 exactly one of the two triangles, and a
                                 degenerate triangle (zero area) or one
                                 entirely off the screen draws nothing.

  0x60 DRAW_TRI_SHADED 22      (Chapter 6.3) x0, y0, x1, y1, x2, y2 (signed 16-bit
                                 each), ramp, z0, z1, z2 (unsigned 16-bit each),
                                 i0, i1, i2 (8 bits each, Q5.3: 8 = one shade).
                                 Covers exactly the pixels of DRAW_TRI. Each
                                 pixel gets the shade round(i / 8) of the interpolated
                                 intensity, clamped to 0-31, and is written
                                 as color (ramp & 7) * 32 + shade; the
                                 interpolation is raster_model.shade_pixels().
                                 The depth (smaller = nearer) is interpolated; what
                                 happens to it is set by SET_DEPTH (Chapter 6.4):
                                 with the test off (the default) the pixel is
                                 always drawn, with it on the pixel is drawn only
                                 if z < the stored depth, and with the write on
                                 a drawn pixel's z is stored.

  0x61 SET_DEPTH    1            (Chapter 6.4) mode: bit 0 test, bit 1 write,
                                 bits 7:2 ignored. Power-up 0. Only shaded
                                 triangles use it; DRAW_TRI never reads or writes
                                 depth.
  0x62 CLEAR_DEPTH  2            (Chapter 6.4) z (16-bit): every stored depth = z
                                 (65,535 = farthest). The colors stay.
  0x63 CLEAR_ALL    3            (Chapter 6.4) color, z: CLEAR and CLEAR_DEPTH in
                                 one pass.
  0x64 GET_STATS    0            (Chapter 6.4) reply, 12 bytes: three 32-bit
                                 little-endian counters: pixels the rasterizer
                                 produced (all triangles), pixels written to the
                                 color buffer, clocks spent drawing triangles.
                                 The model counts the first two; it cannot know the
                                 third and reports Gpu.stat_clocks.
  0x65 CLEAR_STATS  0            (Chapter 6.4) all three counters := 0.

  0x09 UPLOAD_ASSET 5 + n    id, w, h, offset (16-bit), then n data bytes,
                                 0 <= n <= 250   (Step 4.8)
                                 Stores a w x h image of palette indices,
                                 row by row, top row first, in the 32 KB asset
                                 RAM, under asset number id (0-15). The
                                 payload limit is 255 bytes, so an image goes
                                 up in several packets: each one says which
                                 asset it belongs to (id, w, h) and where its
                                 n bytes go (offset, counted in bytes from
                                 the start of the image). See Gpu.upload()
                                 for allocation, replacing, reset and what
                                 happens when the RAM is full.
  0x0A BLIT         6          id, x, y (signed 16-bit each), flags
                                 (Step 4.8) copies asset id into the back
                                 buffer with its top-left corner at (x, y),
                                 clipped to the screen. flags bit 0 mirrors it
                                 left-right (FLIP_X), bit 1 top-bottom
                                 (FLIP_Y), bit 2 (OPAQUE) draws index 0 too;
                                 otherwise index 0 is transparent and leaves
                                 the pixel alone. Other bits are ignored.
                                 An id that was never uploaded draws nothing.

  0x0B GET_INFO     0          (Chapter 5.2) reply, 9 bytes:
                                 0   0x47 ('G')
                                 1   protocol version, 0x01
                                 2-3 capabilities, 16-bit little endian
                                     (Gpu(caps=...), default CAPS_SPI = 0x7FFF)
                                 4-5 screen width, 320, little endian
                                 6-7 screen height, 240, little endian
                                 8   error flags (see below)
  0x0C CLEAR_ERRORS 1          mask. Clears the error flags whose bit is set
                                 in the mask (bits 4-7 are ignored).

  0x10 SET_TILE_LAYER 1        (Chapter 5.3) mask: bit 0 shows layer 0, bit 1
                                 shows layer 1; a layer whose bit is 0 is
                                 transparent everywhere. Bits 7:2 are ignored.
                                 Takes effect at the next vertical blanking.
  0x11 SET_SCROLL   5          layer, x, y (16-bit each, little endian):
                                 the world position shown at the top-left
                                 corner of the screen, taken modulo 512 (so
                                 -1 is 511). A layer other than 0 or 1 makes
                                 the packet do nothing (no error flag). Takes
                                 effect at the next vertical blanking.
  0x12 UPLOAD_TILES 2 + n      offset (16-bit), then n data bytes (n <= 253):
                                 byte i goes to tile graphics RAM address
                                 offset + i. Addresses 16384 and up are
                                 dropped, byte by byte. Immediate.
  0x13 WRITE_TILE_MAP 3 + 2k   layer, offset (16-bit, in entries), then k
                                 16-bit entries (k <= 126, low byte first):
                                 entry i goes to map entry offset + i of that
                                 layer. Entries 4096 and up are dropped, and
                                 so is everything for a layer other than 0
                                 or 1. A length that is not 3 plus an even
                                 number is a wrong length. Immediate.

  0x40 WRITE_TILE_COLUMN 130   (Chapter 5.6) layer, column, then 64 16-bit
                                 entries (128 bytes, low byte first): entry k
                                 goes to map row k of that column, i.e. map
                                 entry k * 64 + column. A layer above 1 or a
                                 column above 63: nothing happens, no error
                                 flag. Any length other than 130 is a wrong
                                 length. Immediate.
  0x41 WRITE_TILE_ROW   130    layer, row, then 64 entries: entry k goes to map
                                 column k of that row, i.e. map entry
                                 row * 64 + k. Same rules.

  0x20 SET_SPRITE   9          (Chapter 5.4) id, then the 8 bytes of table
                                 entry id: x (16-bit), y (16-bit), asset,
                                 palette, flags, reserved. id 64 or more:
                                 nothing happens, no error flag. Writes the
                                 shadow table.
  0x21 MOVE_SPRITE  5          id, x, y (16-bit each): rewrites bytes 0-3 of
                                 entry id and nothing else. id 64 or more:
                                 nothing happens.
  0x22 SPRITE_CTRL  1          bit 0: 1 shows the sprites, 0 hides all of
                                 them (other bits ignored). Shadow register.

  0x30 SET_PRIORITY 1          (Chapter 5.5) byte {sp[1:0], l1[1:0], l0[1:0],
                                 fb[1:0]}: the layer priority (0-3) of each
                                 source. Default 0xE4 (fb 0, layer 0 1,
                                 layer 1 2, sprites 3). Shadow register.
  0x31 SET_BACKDROP 1          palette index shown where nothing is opaque.
                                 Default 0. Shadow register.
  0x32 FB_ENABLE    1          bit 0: 1 shows the framebuffer, 0 makes it
                                 transparent everywhere (other bits ignored).
                                 Default 1. Shadow register.

  0x50 DEFINE_LIST  1          (Chapter 5.7) id (0-7): empties list id (its
                                 length becomes 0; the bytes stay in the RAM).
                                 id 8 or more: nothing happens, no flag.
  0x51 APPEND_LIST  1 + n      id, then n bytes (n <= 254) of commands: byte i
                                 goes to list RAM address (id, length) and the
                                 length grows by one, up to 512; bytes past
                                 512 are dropped. id 8 or more: nothing
                                 happens. A packet cut by a frame start keeps
                                 the bytes that arrived. Immediate.
  0x52 RUN_LIST     1          id: runs the commands in list id, byte
                                 0 to length - 1, as if the host had sent them
                                 here, then the next host command. A list of
                                 length 0 or an id of 8 or more: nothing.
  0x53 WRITE_LIST_WORD 5       id, offset (16-bit), value (16-bit): writes the
                                 two bytes of value (low byte first) at list
                                 RAM addresses (id, offset) and (id,
                                 offset + 1). An address of 512 or more is
                                 dropped (so offset 511 writes one byte);
                                 id 8 or more: nothing. The length does not
                                 change.
  0x54 SET_AUTO_LIST 1         id (0-7): at every start of vertical blanking
                                 run list id (Gpu.auto_run()); 0xFF: stop. Any
                                 other id: nothing happens.

A list may hold any command except GET_INFO, FB_CRC, CLEAR_ERRORS,
UPLOAD_ASSET, UPLOAD_TILES and the list commands 0x50-0x5F. Such a packet is
skipped whole and sets BAD_OP, as if its opcode were unknown. FLIP is allowed.
A list whose last packet is incomplete (the length ends inside it): the
part that arrived is treated like a frame start would treat it (streamed
commands keep what arrived) and TRUNC is set.

Tile layers (Chapter 5.3). Each layer is a 64 x 64 map of 16-bit entries
(index row * 64 + column): bits 7:0 tile number, bit 8 FLIP_X, bit 9 FLIP_Y,
bits 11:10 PRIORITY (Chapter 5.5: the tile's item priority, 0-3), bits 15:12 reserved (write 0). The 256 tiles of 8 x 8 pixels share one tile
graphics RAM, 64 bytes per tile, address tile * 64 + y * 8 + x, one palette
index per pixel; index 0 is transparent. The world is 512 x 512 pixels and
wraps. The screen shows world pixel ((sx + X) mod 512, (sy + Y) mod 512) at
screen pixel (X, Y) of the 320 x 240 view. Order, back to front: the front
framebuffer, layer 0, layer 1. Scroll and enable are shadow registers that
Gpu.vblank() copies to the live ones, as the hardware does at the start of
vertical blanking. Gpu.screen() is one frame of palette indices.

Sprites (Chapter 5.4). A table of 64 entries of 8 bytes: bytes 0-1 x and 2-3 y
(signed 16-bit, the top-left corner on the 320 x 240 screen), byte 4 asset id
(bits 3:0), byte 5 palette (bits 3:0), byte 6 flags (bit 0 FLIP_X, bit 1
FLIP_Y, bit 2 ENABLE, bits 4:3 PRIORITY: the sprite's item priority in the
compositor, Chapter 5.5), byte 7 reserved. A sprite is 16 x 16 pixels, the first 256 bytes
of its asset, and is drawn only if that asset exists and is 16 x 16 (see
sprite_ok). Colors: index 0 is transparent; any other index i shows palette
entry (i + 16 * palette) mod 256. Per scan line (0-239): the sprites that are
enabled and have y <= line < y + 16 are "on the line", whatever their x; the
16 with the lowest table index are drawn, the others are dropped for that
line. Where drawn sprites overlap, the lowest index is in front, whatever
their PRIORITY. The table and the global enable are shadows that Gpu.vblank()
copies to the live ones, like scroll. Gpu.sprite_lines() lists the sprites
drawn on a line; Gpu.sprite_plane() draws them.

Compositor (Chapter 5.5). Per pixel, the four sources are the front
framebuffer, tile layer 0, tile layer 1 and the sprites. A source is opaque
at a pixel if it is enabled and its index is not 0 (for a sprite: if a sprite
drew the pixel, even with the final index 0). Each opaque pixel has the key
(layer priority, item priority): the layer priority (0-3) comes from
SET_PRIORITY; the item priority is 0 for the framebuffer, the PRIORITY of the
tile under the pixel for a tile layer, and the PRIORITY of the sprite that
drew the pixel for the sprites. The largest key wins, layer priority first,
so item priorities only matter between sources whose layer priorities are
equal; equal keys go to the later of framebuffer, layer 0, layer 1, sprites.
Where nothing is opaque the backdrop index is shown. All of this uses live
values (SET_PRIORITY, SET_BACKDROP and FB_ENABLE are shadows).

Error flags: sticky bits, set by events, cleared only by CLEAR_ERRORS (a new Gpu()
starts them at 0, like power-up):

  bit 0 OVERFLOW  the link dropped a byte because the command FIFO was full.
                  The model cannot predict it: a test sets Gpu.overflow.
  bit 1 BAD_LEN   a known opcode arrived with a wrong length (UPLOAD_ASSET
                  with fewer than 5 payload bytes counts).
  bit 2 BAD_OP    an opcode no chapter has assigned (unknown), reserved
                  ones included until their chapter defines them.
  bit 3 TRUNC     a frame began while a packet was only partly received.

Frames (SPI): the first byte of a frame (CS falling to rising) starts a new
packet, whatever the parser was in the middle of, and so does the first UART
byte after SPI bytes (a link change). feed(data, sof=True) says that data
begins a frame, or is the first UART data after SPI data. A partly received packet is dropped, as if it had
never been sent: nothing it carried is executed, TRUNC is set. The one
exception is UPLOAD_ASSET: its header (first five payload bytes) is acted on
as soon as it is complete, and data bytes are written as they arrive, so a
truncated UPLOAD_ASSET leaves the asset allocated and the bytes that arrived
written (see Gpu.upload, `declared`). feed(data) with sof=False continues the
packet in progress, which is how the UART behaves: it has no frames.

The CRC is the one zlib.crc32 and binascii.crc32 compute: reflected
polynomial 0xEDB88320, initial value 0xFFFFFFFF, final XOR 0xFFFFFFFF.

Opcodes 0x80-0xFF are never assigned, so tests can use them as "unknown";
0xFF is never an opcode, because a frame starting with 0xFF is a read frame.
Opcodes 0x0D-0x7F that no chapter has defined are reserved for later chapters (RESERVED below) and are
"unknown" until then.
"""
import os
import struct
import sys
import zlib

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import raster_model  # noqa: E402  (Chapter 4.6)
import vertex_model  # noqa: E402  (Chapter 6.5)
import mesh_model  # noqa: E402  (Chapter 6.6)
import light_model  # noqa: E402  (Chapter 6.7)

W, H = 320, 240
FB_SIZE = W * H

NOP = 0x00
SET_PALETTE = 0x01
CLEAR = 0x02
PLOT = 0x03
FB_CRC = 0x04
FILL_RECT = 0x05
DRAW_LINE = 0x06
FLIP = 0x07
DRAW_TRI = 0x08
UPLOAD_ASSET = 0x09
BLIT = 0x0A
GET_INFO = 0x0B
CLEAR_ERRORS = 0x0C
SET_TILE_LAYER = 0x10
SET_SCROLL = 0x11
UPLOAD_TILES = 0x12
WRITE_TILE_MAP = 0x13
WRITE_TILE_COLUMN = 0x40
WRITE_TILE_ROW = 0x41
DEFINE_LIST = 0x50
APPEND_LIST = 0x51
RUN_LIST = 0x52
WRITE_LIST_WORD = 0x53
SET_AUTO_LIST = 0x54
DRAW_TRI_SHADED = 0x60
SET_DEPTH = 0x61
CLEAR_DEPTH = 0x62
CLEAR_ALL = 0x63
GET_STATS = 0x64
CLEAR_STATS = 0x65
UPLOAD_INDICES = 0x66
SET_MESH_MODE = 0x67
DRAW_MESH = 0x68
GET_MESH_STATS = 0x69
CLEAR_MESH_STATS = 0x6A
UPLOAD_NORMALS = 0x6B
SET_LIGHT = 0x6C
UPLOAD_VERTICES = 0x70
SET_MATRIX = 0x71
SET_PROJECTION = 0x72
TRANSFORM = 0x73
READ_VERTEX = 0x74
SET_SPRITE = 0x20
MOVE_SPRITE = 0x21
SPRITE_CTRL = 0x22
SET_PRIORITY = 0x30
SET_BACKDROP = 0x31
FB_ENABLE = 0x32

# Reserved opcode ranges (Chapter 5.2): range -> what it is for.
RESERVED = {
    (0x14, 0x1F): "tile layers (5.3), not yet used",
    (0x23, 0x2F): "sprite table (5.4), spare",
    (0x33, 0x3F): "compositor (5.5), spare",
    (0x42, 0x4F): "streamed tile map writes (5.6), spare",
    (0x55, 0x5F): "display lists (5.7), spare",
    (0x6D, 0x6F): "3D (Phase 6), spare so far",
    (0x75, 0x7F): "3D (Phase 6, the vertex block of 6.5), spare so far",
}

# Error flags (GET_INFO byte 8, CLEAR_ERRORS mask)
ERR_OVERFLOW, ERR_BAD_LEN, ERR_BAD_OP, ERR_TRUNC = 1, 2, 4, 8
CAPS_UART = 0x000F    # DRAW, FLIP, TRI, BLIT (uart_gpu)
CAPS_TILES = 0x0010   # tile layers (Chapter 5.3)
CAPS_SPRITES = 0x0020  # sprite table (Chapter 5.4)
CAPS_COMPOSITOR = 0x0040  # layer compositor (Chapter 5.5)
CAPS_STREAMING = 0x0080  # column and row writes (Chapter 5.6)
CAPS_LISTS = 0x0100    # display lists (Chapter 5.7)
CAPS_3D = 0x0400      # shaded triangles, DRAW_TRI_SHADED (Chapter 6.3)
CAPS_DEPTH = 0x0800   # depth buffer and statistics (Chapter 6.4); later 3D features take bits 12-15
CAPS_VERTEX = 0x1000  # vertex transform (Chapter 6.5); later 3D features take bits 13-15
CAPS_MESH = 0x2000    # index buffer, DRAW_MESH and culling (Chapter 6.6); later 3D features take bits 14-15
CAPS_LIGHT = 0x4000   # per-vertex normals and lighting (Chapter 6.7); bit 15 is still free
CAPS_SPI = 0x7FFF     # ... plus the tile layers, sprites, compositor, streaming, lists, the SPI link, 3D, the depth buffer, the vertex transform, meshes and lighting (gpu_link)
INFO_MAGIC, PROTOCOL_VERSION = 0x47, 1

FLIP_X, FLIP_Y, OPAQUE = 1, 2, 4      # BLIT flags
ASSET_RAM = 32768                       # bytes of asset RAM
N_ASSETS = 16                           # asset ids 0-15
UPLOAD_HEADER = 5                       # id, w, h, offset lo, offset hi
UPLOAD_CHUNK = 255 - UPLOAD_HEADER      # data bytes that fit in one packet

N_LAYERS = 2
MAP_W = MAP_H = 64                      # tiles per layer row and column
MAP_ENTRIES = MAP_W * MAP_H             # 4096 entries per layer
TILE_BYTES = 256 * 64                   # tile graphics RAM: 256 tiles of 8 x 8 bytes
WORLD = 512                             # pixels per side of a layer's world
FLIP_X_BIT, FLIP_Y_BIT = 0x100, 0x200   # map entry bits
TILE_PRI_SHIFT = 10                     # map entry bits 11:10: the tile's item priority (Chapter 5.5)
TILE_CHUNK = 253                        # data bytes of one UPLOAD_TILES packet
MAP_CHUNK = 126                         # entries of one WRITE_TILE_MAP packet
N_LISTS = 8                             # display lists (Chapter 5.7), ids 0-7
LIST_BYTES = 512                        # bytes in one list's slot of the list RAM
AUTO_OFF = 0xFF                         # SET_AUTO_LIST id that stops the automatic run
LIST_CHUNK = 254                        # data bytes of one APPEND_LIST packet
# opcodes a list may not contain: replies, error flags, uploads and the list commands themselves
LIST_FORBIDDEN = {FB_CRC, UPLOAD_ASSET, GET_INFO, CLEAR_ERRORS, UPLOAD_TILES} | {GET_STATS, UPLOAD_VERTICES, READ_VERTEX, UPLOAD_INDICES, GET_MESH_STATS, UPLOAD_NORMALS} | set(range(0x50, 0x60))
STREAM_LEN = 2 + 2 * MAP_W              # payload of WRITE_TILE_COLUMN / ROW: layer, index, 64 entries

N_SPRITES = 64                          # sprite table entries (Chapter 5.4)
SPRITE_SIZE = 16                        # every sprite is 16 x 16 pixels
SPRITE_BYTES = 8                        # bytes per table entry
MAX_PER_LINE = 16                       # sprites drawn on one scan line
SPRITE_FLIP_X, SPRITE_FLIP_Y, SPRITE_ON = 1, 2, 4   # flags byte
SPRITE_PRI_SHIFT = 3                    # flags bits 4:3 PRIORITY (Chapter 5.5)
DEFAULT_PRIORITY = 0xE4                 # fb 0, layer 0 1, layer 1 2, sprites 3 (SET_PRIORITY byte)
SRC_FB, SRC_L0, SRC_L1, SRC_SP = 0, 1, 2, 3   # the compositor's sources, in tie-break order

# Payload length each opcode requires. None: any length is fine.
LENGTHS = {NOP: None, SET_PALETTE: 4, CLEAR: 1, PLOT: 5, FB_CRC: 0, FILL_RECT: 9, DRAW_LINE: 9, FLIP: 0, DRAW_TRI: 13,
           UPLOAD_ASSET: None, BLIT: 6, GET_INFO: 0, CLEAR_ERRORS: 1, SET_TILE_LAYER: 1, SET_SCROLL: 5,
           UPLOAD_TILES: None, WRITE_TILE_MAP: None, SET_SPRITE: 9, MOVE_SPRITE: 5, SPRITE_CTRL: 1,
           SET_PRIORITY: 1, SET_BACKDROP: 1, FB_ENABLE: 1,
           WRITE_TILE_COLUMN: STREAM_LEN, WRITE_TILE_ROW: STREAM_LEN,
           DEFINE_LIST: 1, APPEND_LIST: None, RUN_LIST: 1, WRITE_LIST_WORD: 5, SET_AUTO_LIST: 1, DRAW_TRI_SHADED: 22,
           SET_DEPTH: 1, CLEAR_DEPTH: 2, CLEAR_ALL: 3, GET_STATS: 0, CLEAR_STATS: 0,
           UPLOAD_VERTICES: None, SET_MATRIX: 24, SET_PROJECTION: 4, TRANSFORM: 4, READ_VERTEX: 2,
           UPLOAD_INDICES: None, UPLOAD_NORMALS: None, SET_LIGHT: 9, SET_MESH_MODE: 1, DRAW_MESH: 4, GET_MESH_STATS: 0, CLEAR_MESH_STATS: 0}  # UPLOAD_ASSET: at least 5, UPLOAD_TILES at least 2, WRITE_TILE_MAP 3 + even, UPLOAD_VERTICES 2 + 6k, UPLOAD_INDICES 2 + 8k (see execute)

# Bytes in the reply to each opcode (only FB_CRC replies).
REPLY_LEN = {FB_CRC: 4, GET_INFO: 9, GET_STATS: 12, READ_VERTEX: 8, GET_MESH_STATS: 12}
VERTEX_BYTES = 6                        # x, y, z as int16
VERTEX_CHUNK = 42                       # vertices in one UPLOAD_VERTICES packet: 2 + 6 * 42 = 254 payload bytes
DEPTH_FAR = 0xFFFF                      # farthest depth: the usual CLEAR_DEPTH value
STAT_MAX = 0xFFFFFFFF                   # the statistics counters saturate here
N_TRIANGLES = mesh_model.N_TRIANGLES    # index buffer entries (Chapter 6.6)
INDEX_CHUNK = mesh_model.ENTRY_CHUNK    # entries in one UPLOAD_INDICES packet: 2 + 8 * 31 = 250 payload bytes


# ---------------------------------------------------------------- packets
def packet(op, payload=b""):
    """Any packet, valid or not: [op][len(payload)][payload]."""
    payload = bytes(payload)
    if not 0 <= op <= 255 or len(payload) > 255:
        raise ValueError("opcode must be a byte and payload at most 255 bytes")
    return bytes([op, len(payload)]) + payload


def nop(payload=b""):
    return packet(NOP, payload)


def set_palette(index, rgb):
    """rgb is 0xRRGGBB. Sent as index, R, G, B."""
    return packet(SET_PALETTE, bytes([index & 255, (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255]))


def clear(color):
    return packet(CLEAR, bytes([color & 255]))


def plot(x, y, color):
    """x, y are signed 16-bit (-32768..32767), sent low byte first."""
    return packet(PLOT, struct.pack("<hhB", x, y, color & 255))


def fb_crc():
    return packet(FB_CRC)


def fill_rect(x0, y0, x1, y1, color):
    """Corners are signed 16-bit and inclusive, in either order."""
    return packet(FILL_RECT, struct.pack("<hhhhB", x0, y0, x1, y1, color & 255))


def flip():
    """Swap the front and back buffers at the next vertical blanking."""
    return packet(FLIP)


def draw_tri(x0, y0, x1, y1, x2, y2, color):
    """Corners are signed 16-bit, in either winding order."""
    return packet(DRAW_TRI, struct.pack("<hhhhhhB", x0, y0, x1, y1, x2, y2, color & 255))


def draw_tri_shaded(v0, v1, v2, ramp):
    """A shaded triangle. Each vertex is (x, y, z, i): signed 16-bit x and y, unsigned 16-bit depth z, intensity i
    (0-255, Q5.3, so 8 is one shade). ramp 0-7 selects palette entries ramp * 32 .. ramp * 32 + 31."""
    (x0, y0, z0, i0), (x1, y1, z1, i1), (x2, y2, z2, i2) = v0, v1, v2
    return packet(DRAW_TRI_SHADED, struct.pack("<hhhhhhBHHHBBB", x0, y0, x1, y1, x2, y2, ramp & 255, z0, z1, z2, i0, i1, i2))


def set_depth(test=False, write=False):
    """SET_DEPTH: test = draw only nearer pixels, write = store the depth of drawn pixels."""
    return packet(SET_DEPTH, bytes([(1 if test else 0) | (2 if write else 0)]))


def clear_depth(z=DEPTH_FAR):
    return packet(CLEAR_DEPTH, struct.pack("<H", z))


def clear_all(color, z=DEPTH_FAR):
    return packet(CLEAR_ALL, struct.pack("<BH", color & 255, z))


def get_stats():
    return packet(GET_STATS)


def clear_stats():
    return packet(CLEAR_STATS)


def vertex_len_ok(n):
    """True if n is a legal UPLOAD_VERTICES payload length: 2 + 6k with k >= 1."""
    return n >= 2 + VERTEX_BYTES and (n - 2) % VERTEX_BYTES == 0


def upload_vertices(first, verts):
    """UPLOAD_VERTICES: verts[0] goes to vertex slot `first`, the next to first + 1 and so on. Up to 42 vertices (x, y, z)."""
    assert 1 <= len(verts) <= VERTEX_CHUNK
    return packet(UPLOAD_VERTICES, struct.pack("<H", first) + b"".join(vertex_model.pack_vertex(v) for v in verts))


def vertex_packets(first, verts):
    """UPLOAD_VERTICES packets for any number of vertices, in order."""
    return [upload_vertices(first + i, verts[i:i + VERTEX_CHUNK]) for i in range(0, len(verts), VERTEX_CHUNK)]


def set_matrix(m):
    """SET_MATRIX: 12 ints m00 m01 m02 m10 m11 m12 m20 m21 m22 (Q2.14) then tx ty tz (int16 model units)."""
    return packet(SET_MATRIX, vertex_model.pack_matrix(m))


def set_projection(f=vertex_model.DEFAULT_F, near=vertex_model.DEFAULT_NEAR):
    """SET_PROJECTION: focal length f (int16, pixels) and near (u16, model units)."""
    return packet(SET_PROJECTION, struct.pack("<hH", f, near))


def transform(first, count):
    """TRANSFORM: transform vertex slots first .. first + count - 1 (clipped to 1024) into the transformed-vertex buffer."""
    return packet(TRANSFORM, struct.pack("<HH", first, count))


def read_vertex(index):
    """READ_VERTEX: 8 reply bytes, the transformed vertex `index` (parse_vertex)."""
    return packet(READ_VERTEX, struct.pack("<H", index))


def upload_normals(first, normals):
    """UPLOAD_NORMALS (Chapter 6.7): normals[0] goes to slot `first`, the next to first + 1 and so on. Each is (nx, ny, nz) in
    Q2.14 (16384 = 1.0), up to 42 per packet. Slot k belongs to vertex k."""
    assert 1 <= len(normals) <= VERTEX_CHUNK
    return packet(UPLOAD_NORMALS, struct.pack("<H", first) + b"".join(struct.pack("<hhh", *n) for n in normals))


def normal_packets(first, normals):
    """UPLOAD_NORMALS packets for any number of normals, in order."""
    return [upload_normals(first + i, normals[i:i + VERTEX_CHUNK]) for i in range(0, len(normals), VERTEX_CHUNK)]


def set_light(direction, ambient=0, enable=True):
    """SET_LIGHT (Chapter 6.7): the light direction in CAMERA space (towards the light, Q2.14 each), the ambient level (Q2.14),
    and whether TRANSFORM lights (flags bit 0). 9 payload bytes: flags, lx, ly, lz, ambient."""
    return packet(SET_LIGHT, struct.pack("<Bhhhh", 1 if enable else 0, *direction, ambient))


def index_len_ok(n):
    """True if n is a legal UPLOAD_INDICES payload length: 2 + 8k with k >= 1."""
    return n >= 2 + mesh_model.ENTRY_BYTES and (n - 2) % mesh_model.ENTRY_BYTES == 0


def upload_indices(first, entries):
    """UPLOAD_INDICES: entries[0] goes to index slot `first`, the next to first + 1 and so on. Each entry is 8 bytes
    (mesh_model.pack_entry); up to 31 entries."""
    assert 1 <= len(entries) <= INDEX_CHUNK and all(len(e) == 8 for e in entries)
    return packet(UPLOAD_INDICES, struct.pack("<H", first) + b"".join(entries))


def index_packets(first, entries):
    """UPLOAD_INDICES packets for any number of entries, in order."""
    return [upload_indices(first + i, entries[i:i + INDEX_CHUNK]) for i in range(0, len(entries), INDEX_CHUNK)]


def set_mesh_mode(cull=mesh_model.CULL_NONE, vertex_i=False):
    """SET_MESH_MODE: cull 0 none, 1 back faces, 2 front faces, 3 everything; vertex_i takes the intensity from the vertex records."""
    return packet(SET_MESH_MODE, bytes([cull | (mesh_model.MODE_VERTEX_I if vertex_i else 0)]))


def draw_mesh(first, count):
    """DRAW_MESH: draw index entries first .. first + count - 1 (clipped at entry 511)."""
    return packet(DRAW_MESH, struct.pack("<HH", first, count))


def get_mesh_stats():
    return packet(GET_MESH_STATS)


def clear_mesh_stats():
    return packet(CLEAR_MESH_STATS)


def parse_mesh_stats(reply):
    """The 12 reply bytes of GET_MESH_STATS -> (culled, rejected, drawn)."""
    return struct.unpack("<III", bytes(reply))


def parse_vertex(reply):
    """The 8 reply bytes of READ_VERTEX -> (sx, sy, depth, flags)."""
    return vertex_model.unpack_result(reply)


def parse_stats(reply):
    """The 12 reply bytes of GET_STATS -> (pixels, written, clocks)."""
    return struct.unpack("<III", bytes(reply))


def ramp_palette(ramp, dark, light):
    """The 32 SET_PALETTE packets of palette ramp number ramp: shade s is dark + (light - dark) * s / 31 per channel,
    rounded half up. dark and light are (r, g, b)."""
    out = []
    for s in range(32):
        rgb = tuple((d * (31 - s) + l * s + 15) // 31 for d, l in zip(dark, light))
        out.append(set_palette(ramp * 32 + s, (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]))
    return out


def tri_covered(x0, y0, x1, y1, x2, y2):
    """The pixels DRAW_TRI writes, as (x, y) in raster order (Chapter 4.6)."""
    return raster_model.tri_pixels((x0, y0), (x1, y1), (x2, y2), W, H)


def upload_asset(asset_id, w, h, data=b"", offset=0):
    """One UPLOAD_ASSET packet: n = len(data) <= 250 bytes of asset_id's
    w x h image, starting offset bytes into it."""
    data = bytes(data)
    if len(data) > UPLOAD_CHUNK:
        raise ValueError("a packet carries at most 250 data bytes: use upload_packets()")
    return packet(UPLOAD_ASSET, bytes([asset_id & 255, w & 255, h & 255]) + struct.pack("<H", offset) + data)


def upload_packets(asset_id, w, h, data):
    """A whole image as a list of packets: the first (offset 0) allocates the
    asset and carries the first 250 bytes, the rest append at their offsets."""
    data = bytes(data)
    if len(data) != w * h:
        raise ValueError("data must be w * h bytes")
    return [upload_asset(asset_id, w, h, data[o:o + UPLOAD_CHUNK], o) for o in range(0, len(data), UPLOAD_CHUNK)]


def asset_reset():
    """w = h = 0: forget every asset and free the whole asset RAM (its bytes are not erased)."""
    return upload_asset(0, 0, 0)


def blit(asset_id, x, y, flags=0):
    """x, y are signed 16-bit: the screen position of the sprite's top-left corner."""
    return packet(BLIT, struct.pack("<BhhB", asset_id & 255, x, y, flags & 255))


def blit_points(w, h, x, y, flags):
    """Every sprite pixel of a BLIT, on screen or not, as (X, Y, source index),
    where source index = row * w + column in the stored image. Sprite pixel
    (sx, sy) lands at X = x + sx, Y = y + sy; FLIP_X reads column w - 1 - sx
    instead, FLIP_Y row h - 1 - sy."""
    for sy in range(h):
        row = (h - 1 - sy) if flags & FLIP_Y else sy
        for sx in range(w):
            col = (w - 1 - sx) if flags & FLIP_X else sx
            yield x + sx, y + sy, row * w + col


def clip_rect(x0, y0, x1, y1):
    """The on-screen part of FILL_RECT's rectangle, as inclusive
    (xa, ya, xb, yb) with xa <= xb and ya <= yb, or None if nothing of it is
    on the screen."""
    xa, xb = min(x0, x1), max(x0, x1)
    ya, yb = min(y0, y1), max(y0, y1)
    if xb < 0 or xa > W - 1 or yb < 0 or ya > H - 1:
        return None
    return max(xa, 0), max(ya, 0), min(xb, W - 1), min(yb, H - 1)


def draw_line(x0, y0, x1, y1, color):
    """Endpoints are signed 16-bit and both drawn; (x0, y0) is the start."""
    return packet(DRAW_LINE, struct.pack("<hhhhB", x0, y0, x1, y1, color & 255))


def line_points(x0, y0, x1, y1):
    """Every point of DRAW_LINE's line, in drawing order, on screen or not.

    Bresenham's algorithm in its all-octant form, with integer adds and
    compares only. dx >= 0 and dy <= 0 are the distances (dy negated), sx and
    sy the directions. err is the error of the diagonal neighbour: for
    E(u, v) = DX*v - DY*u, with u and v the steps taken so far in x and y and
    DX = dx, DY = -dy, err = E(u + 1, v + 1). Each clock:

        e2 = 2*err
        step x  if e2 >= dy      (the diagonal is at least as close to the
                                  true line as the pure y step)
        step y  if e2 <= dx      (the diagonal is at least as close as the
                                  pure x step)

    Both tests use the same e2, so a clock may step x, y or both. A tie
    (two candidates exactly as far from the line) goes to the diagonal: the
    minor coordinate rounds half-way values toward (x1, y1). That is why the
    result depends on which endpoint is first. The line has
    max(|x1-x0|, |y1-y0|) + 1 points and ends exactly on (x1, y1).
    """
    dx, sx = abs(x1 - x0), (1 if x0 < x1 else -1)
    dy, sy = -abs(y1 - y0), (1 if y0 < y1 else -1)
    err = dx + dy
    x, y = x0, y0
    while True:
        yield x, y
        if x == x1 and y == y1:
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def line_points_major(x0, y0, x1, y1):
    """The same points by the other common formulation: step the major axis
    every clock and keep the decision variable
    D = 2*dmin*(k + 1) - dmaj*(2*m + 1) after k major and m minor steps,
    which is 2*dmaj times (the true minor coordinate at the next major step
    minus m + 1/2). Step the minor axis too when D >= 0, so a half-way value
    again rounds toward (x1, y1). The self-check below shows the two forms
    agree pixel for pixel."""
    ax, ay = abs(x1 - x0), abs(y1 - y0)
    sx, sy = (1 if x1 > x0 else -1), (1 if y1 > y0 else -1)
    steep = ay > ax
    dmaj, dmin = (ay, ax) if steep else (ax, ay)
    d = 2 * dmin - dmaj
    x, y = x0, y0
    pts = [(x, y)]
    for _ in range(dmaj):
        if d >= 0:
            if steep:
                x += sx
            else:
                y += sy
            d -= 2 * dmaj
        d += 2 * dmin
        if steep:
            y += sy
        else:
            x += sx
        pts.append((x, y))
    return pts


def set_tile_layer(mask):
    """Bit 0 shows layer 0, bit 1 shows layer 1 (from the next vertical blanking)."""
    return packet(SET_TILE_LAYER, bytes([mask & 255]))


def set_scroll(layer, x, y):
    """World position at the screen's top-left corner, modulo 512. x and y are
    sent as 16-bit values (negative ones wrap: -1 is 511)."""
    return packet(SET_SCROLL, struct.pack("<BHH", layer & 255, x & 0xFFFF, y & 0xFFFF))


def upload_tiles(offset, data):
    """One UPLOAD_TILES packet: n = len(data) <= 253 bytes for tile graphics RAM address offset..."""
    data = bytes(data)
    if len(data) > TILE_CHUNK:
        raise ValueError("a packet carries at most 253 tile bytes: use tile_packets()")
    return packet(UPLOAD_TILES, struct.pack("<H", offset) + data)


def tile_packets(data, offset=0):
    """All of data (tile graphics RAM bytes from offset on) as UPLOAD_TILES packets."""
    data = bytes(data)
    return [upload_tiles(offset + o, data[o:o + TILE_CHUNK]) for o in range(0, len(data), TILE_CHUNK)]


def map_entry(tile, flip_x=False, flip_y=False, pri=0):
    """A map entry; pri (0-3) is the tile's item priority, bits 11:10."""
    return (tile & 255) | (FLIP_X_BIT if flip_x else 0) | (FLIP_Y_BIT if flip_y else 0) | (pri & 3) << TILE_PRI_SHIFT


def write_tile_map(layer, offset, entries):
    """One WRITE_TILE_MAP packet: k = len(entries) <= 126 entries starting at entry offset."""
    entries = list(entries)
    if len(entries) > MAP_CHUNK:
        raise ValueError("a packet carries at most 126 map entries: use map_packets()")
    return packet(WRITE_TILE_MAP, struct.pack("<BH", layer & 255, offset) + struct.pack("<%dH" % len(entries), *entries))


def map_packets(layer, entries, offset=0):
    """All of entries as WRITE_TILE_MAP packets."""
    entries = list(entries)
    return [write_tile_map(layer, offset + o, entries[o:o + MAP_CHUNK]) for o in range(0, len(entries), MAP_CHUNK)]


def write_tile_column(layer, column, entries):
    """WRITE_TILE_COLUMN: 64 entries, entry k for map row k of map column `column` (0-63)."""
    entries = list(entries)
    if len(entries) != MAP_H:
        raise ValueError("a column write carries exactly 64 entries")
    return packet(WRITE_TILE_COLUMN, struct.pack("<BB", layer & 255, column & 255) + struct.pack("<64H", *entries))


def write_tile_row(layer, row, entries):
    """WRITE_TILE_ROW: 64 entries, entry k for map column k of map row `row` (0-63)."""
    entries = list(entries)
    if len(entries) != MAP_W:
        raise ValueError("a row write carries exactly 64 entries")
    return packet(WRITE_TILE_ROW, struct.pack("<BB", layer & 255, row & 255) + struct.pack("<64H", *entries))


def layer_pixel_pri(tiles, tmap, sx, sy, x, y):
    """(index, pri) of the tile layer at screen pixel (x, y): index 0 = transparent,
    pri = the PRIORITY (map entry bits 11:10) of the tile covering the pixel."""
    wx, wy = (x + sx) % WORLD, (y + sy) % WORLD
    e = tmap[(wy >> 3) * MAP_W + (wx >> 3)]
    px, py = wx & 7, wy & 7
    if e & FLIP_X_BIT:
        px = 7 - px
    if e & FLIP_Y_BIT:
        py = 7 - py
    return tiles[(e & 255) * 64 + py * 8 + px], (e >> TILE_PRI_SHIFT) & 3


def layer_pixel(tiles, tmap, sx, sy, x, y):
    """The palette index layer shows at screen pixel (x, y), 0 = transparent.
    tiles is the 16,384-byte tile graphics RAM, tmap the layer's 4,096 entries."""
    return layer_pixel_pri(tiles, tmap, sx, sy, x, y)[0]


def compose_pixel(sources, priority=DEFAULT_PRIORITY, backdrop=0):
    """The compositor for one pixel (Chapter 5.5). sources is four (opaque, item, index)
    tuples in the order framebuffer, layer 0, layer 1, sprites. Returns the palette index.
    The key of an opaque source is (layer priority from the SET_PRIORITY byte, item priority);
    the largest key wins and, on equal keys, the later source. Nothing opaque: the backdrop."""
    best = None
    for s, (opaque, item, index) in enumerate(sources):
        if not opaque:
            continue
        key = ((priority >> (2 * s)) & 3, item, s)           # s last: ties go to the later source
        if best is None or key > best[0]:
            best = (key, index)
    return backdrop if best is None else best[1]


def sprite_entry(x, y, asset, palette=0, flags=SPRITE_ON, reserved=0):
    """The 8 bytes of a table entry."""
    return struct.pack("<hhBBBB", x, y, asset & 255, palette & 255, flags & 255, reserved & 255)


def set_sprite(sprite_id, x, y, asset, palette=0, flags=SPRITE_ON):
    """SET_SPRITE: all 8 bytes of entry sprite_id. flags defaults to ENABLE alone."""
    return packet(SET_SPRITE, bytes([sprite_id & 255]) + sprite_entry(x, y, asset, palette, flags))


def move_sprite(sprite_id, x, y):
    """MOVE_SPRITE: rewrites bytes 0-3 (x and y) of entry sprite_id."""
    return packet(MOVE_SPRITE, struct.pack("<Bhh", sprite_id & 255, x, y))


def sprite_ctrl(enable):
    return packet(SPRITE_CTRL, bytes([1 if enable else 0]))


def sprite_pri(p):
    """The PRIORITY field (0-3) as flags bits 4:3: set_sprite(..., flags=SPRITE_ON | sprite_pri(2))."""
    return (p & 3) << SPRITE_PRI_SHIFT


def priority_byte(fb=0, l0=1, l1=2, sp=3):
    """The SET_PRIORITY byte: a layer priority (0-3) for each source. Defaults are the power-up order."""
    return (fb & 3) | (l0 & 3) << 2 | (l1 & 3) << 4 | (sp & 3) << 6


def set_priority(fb=0, l0=1, l1=2, sp=3):
    return packet(SET_PRIORITY, bytes([priority_byte(fb, l0, l1, sp)]))


def set_backdrop(index):
    return packet(SET_BACKDROP, bytes([index & 255]))


def fb_enable(enable):
    return packet(FB_ENABLE, bytes([1 if enable else 0]))


def define_list(list_id):
    """DEFINE_LIST: empty list list_id (length 0)."""
    return packet(DEFINE_LIST, bytes([list_id & 255]))


def append_list(list_id, data):
    """One APPEND_LIST packet: up to 254 bytes of commands added to the end of list list_id."""
    data = bytes(data)
    if len(data) > LIST_CHUNK:
        raise ValueError("a packet appends at most 254 bytes: use list_packets()")
    return packet(APPEND_LIST, bytes([list_id & 255]) + data)


def list_packets(list_id, data):
    """DEFINE_LIST, then all of data (packets joined) as APPEND_LIST packets."""
    data = bytes(data)
    return [define_list(list_id)] + [append_list(list_id, data[o:o + LIST_CHUNK]) for o in range(0, len(data), LIST_CHUNK)]


def run_list(list_id):
    return packet(RUN_LIST, bytes([list_id & 255]))


def write_list_word(list_id, offset, value):
    """WRITE_LIST_WORD: the 16-bit value (low byte first) at byte offset of list list_id."""
    return packet(WRITE_LIST_WORD, struct.pack("<BHH", list_id & 255, offset & 0xFFFF, value & 0xFFFF))


def set_auto_list(list_id=AUTO_OFF):
    """SET_AUTO_LIST: run list list_id at every vertical blanking; AUTO_OFF (0xFF) stops."""
    return packet(SET_AUTO_LIST, bytes([list_id & 255]))


def get_info():
    return packet(GET_INFO)


def clear_errors(mask=0x0F):
    """Clear the error flags whose bit is set in mask (default: all four)."""
    return packet(CLEAR_ERRORS, bytes([mask & 255]))


def parse_info(reply):
    """The 9 reply bytes of GET_INFO -> dict (magic, version, caps, width, height, errors)."""
    r = bytes(reply)
    if len(r) != 9:
        raise ValueError("GET_INFO replies with 9 bytes")
    return {"magic": r[0], "version": r[1], "caps": r[2] | (r[3] << 8),
            "width": r[4] | (r[5] << 8), "height": r[6] | (r[7] << 8), "errors": r[8]}


def parse_crc_reply(reply):
    """The 4 reply bytes of FB_CRC -> int."""
    return struct.unpack("<I", bytes(reply))[0]


# ---------------------------------------------------------------- CRC-32
def crc32(data):
    """CRC-32 as zlib computes it (and as FB_CRC must)."""
    return zlib.crc32(bytes(data)) & 0xFFFFFFFF


def crc32_bitwise(data):
    """The same CRC, one bit at a time, the way the hardware does it."""
    crc = 0xFFFFFFFF
    for b in bytes(data):
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


# ---------------------------------------------------------------- the model
class Gpu:
    """Byte-stream model of the command processor.

    fb is 76,800 palette indices (address y*320 + x), pal 256 ints 0xRRGGBB.
    Both buffers start as copies of fb. Drawing commands and FB_CRC use the
    back buffer, which is also what the attribute `fb` returns, so code
    written before Step 4.5 keeps working. `front_fb` is the buffer on the
    screen, and `flips` counts FLIPs.
    feed() takes any slice of the stream (packets may be split anywhere) and
    returns the reply bytes those packets produce.
    """

    def __init__(self, fb=None, pal=None, caps=CAPS_SPI):
        first = bytearray(fb) if fb is not None else bytearray(FB_SIZE)
        self.pal = list(pal) if pal is not None else [0] * 256
        if len(first) != FB_SIZE or len(self.pal) != 256:
            raise ValueError("fb must be 76,800 bytes and pal 256 entries")
        self.bufs = [bytearray(first), bytearray(first)]
        self.front = 0                   # index of the buffer on the screen
        self.flips = 0                   # FLIPs executed so far
        self._pkt = bytearray()          # the packet being received
        self.caps = caps                 # capability bits GET_INFO reports
        self.errors = 0                  # sticky error flags 1-3 (ERR_*), see CLEAR_ERRORS
        self.overflow = False            # ERR_OVERFLOW: tests set it, the model cannot know
        self.ignored = 0                 # packets skipped (unknown or bad length)
        self.rect_pixels = 0             # pixels FILL_RECT has written so far
        self.line_points = 0             # points DRAW_LINE has stepped through
        self.line_pixels = 0             # ... and the on-screen ones it wrote
        self.tri_count = 0               # DRAW_TRIs executed
        self.tri_drawn = 0               # pixels DRAW_TRI has written so far
        self.tri_shaded_count = 0        # DRAW_TRI_SHADEDs executed (Chapter 6.3)
        self.tri_shaded_drawn = 0        # pixels they have written so far
        self.depth = [0] * FB_SIZE       # the depth buffer (Chapter 6.4); the hardware starts at 0 too, hosts clear it first
        self.d_test = 0                  # SET_DEPTH bit 0
        self.d_wr = 0                    # SET_DEPTH bit 1
        self.depth_rejected = 0          # shaded pixels the depth test refused
        self.stat_cov = 0                # GET_STATS: pixels the rasterizer produced
        self.stat_wr = 0                 # ... pixels written to the color buffer
        self.stat_clocks = 0             # ... clocks: the model cannot know, tests set it
        self.vsrc = [None] * vertex_model.N_VERTICES    # the vertex buffer (Chapter 6.5): (x, y, z) or None = never uploaded
        self.vdst = [None] * vertex_model.N_VERTICES    # the transformed-vertex buffer: (sx, sy, depth, flags) or None = undefined
        self.matrix = vertex_model.identity()           # SET_MATRIX: power-up identity
        self.proj_f = vertex_model.DEFAULT_F            # SET_PROJECTION power-up values
        self.proj_near = vertex_model.DEFAULT_NEAR
        self.nsrc = [None] * vertex_model.N_VERTICES    # the normal buffer (Chapter 6.7): (nx, ny, nz) Q2.14 or None = never uploaded
        self.vlit = [0] * vertex_model.N_VERTICES       # byte 7 of each transformed record: the intensity, 0 unless lighting was on; None = undefined
        self.light_en = 0                               # SET_LIGHT flags bit 0: TRANSFORM also lights
        self.light_dir = (0, 0, 0)                      # SET_LIGHT direction in camera space (Q2.14): power-up zero
        self.light_amb = 0                              # SET_LIGHT ambient (Q2.14)
        self.lit_undefined = 0                          # lit vertices whose normal nothing defined: tests keep this 0
        self.idx = [None] * N_TRIANGLES  # the index buffer (Chapter 6.6): (i0, i1, i2, ramp, intensity) or None = never uploaded
        self.mesh_mode = 0               # SET_MESH_MODE: bits 1:0 cull mode, bit 2 intensity from the vertex records
        self.mesh_culled = 0             # GET_MESH_STATS counters
        self.mesh_rejected = 0
        self.mesh_drawn = 0
        self.mesh_undefined = 0          # DRAW_MESH triangles that used an entry or vertex nothing defined: tests keep this 0
        self.read_undefined = 0          # READ_VERTEXes of an entry no TRANSFORM of an uploaded vertex defined: tests keep this 0
        self.ram = bytearray(ASSET_RAM)  # the asset RAM (Step 4.8)
        self.ram_written = bytearray(ASSET_RAM)   # 1 where an upload has written since allocation
        self.assets = {}                 # id -> (base, w, h); a missing id was never uploaded
        self.next_free = 0               # first unallocated byte of the asset RAM
        self.upload_rejected = 0         # valid-looking uploads refused (RAM full, size mismatch ...)
        self.blit_count = 0              # BLITs of an uploaded asset
        self.blit_drawn = 0              # pixels BLIT has written so far
        self.blit_unwritten = 0          # source bytes read that no upload had written: tests must keep this 0
        self.tiles = bytearray(TILE_BYTES)                # tile graphics RAM (Chapter 5.3)
        self.tmap = [[0] * MAP_ENTRIES for _ in range(N_LAYERS)]
        self.scroll = [[0, 0] for _ in range(N_LAYERS)]   # shadow: SET_SCROLL writes here
        self.enable = 0                                   # shadow: SET_TILE_LAYER writes here
        self.live_scroll = [[0, 0] for _ in range(N_LAYERS)]   # what the screen uses; vblank() copies the shadows
        self.live_enable = 0
        self.sprites = bytearray(N_SPRITES * SPRITE_BYTES)   # shadow sprite table (Chapter 5.4)
        self.sp_enable = 0                                    # shadow global enable
        self.live_sprites = bytearray(N_SPRITES * SPRITE_BYTES)
        self.live_sp_enable = 0
        self.priority = DEFAULT_PRIORITY                      # shadow compositor settings (Chapter 5.5)
        self.backdrop = 0
        self.fb_on = 1
        self.live_priority = DEFAULT_PRIORITY
        self.live_backdrop = 0
        self.live_fb_on = 1
        self.lists = [bytearray(LIST_BYTES) for _ in range(N_LISTS)]   # the list RAM (Chapter 5.7)
        self.list_len = [0] * N_LISTS
        self.auto = None                 # the list SET_AUTO_LIST runs at every vblank, or None
        self.list_runs = 0               # list runs so far (RUN_LIST and auto_run)

    @property
    def fb(self):
        """The back buffer: where drawing commands and FB_CRC look."""
        return self.bufs[1 - self.front]

    @property
    def front_fb(self):
        """The buffer on the screen."""
        return self.bufs[self.front]

    def feed(self, data, sof=False):
        """Run bytes through the parser. sof=True: the first of them begins an
        SPI frame (or is the first UART byte after SPI bytes), so a packet
        still in progress is dropped first (TRUNC)."""
        out = bytearray()
        for i, b in enumerate(bytes(data)):
            if sof and i == 0:
                self.abandon()
            self._pkt.append(b)
            if len(self._pkt) >= 2 and len(self._pkt) == 2 + self._pkt[1]:
                out += self.execute(self._pkt[0], bytes(self._pkt[2:]))
                self._pkt.clear()
        return bytes(out)

    def vblank(self):
        """The start of vertical blanking: scroll and enable take effect."""
        self.live_scroll = [list(s) for s in self.scroll]
        self.live_enable = self.enable
        self.live_sprites = bytearray(self.sprites)
        self.live_sp_enable = self.sp_enable
        self.live_priority = self.priority
        self.live_backdrop = self.backdrop
        self.live_fb_on = self.fb_on

    def sprite_ok(self, asset):
        """True if an asset can be a sprite image: uploaded, and 16 x 16."""
        a = self.assets.get(asset)
        return a is not None and a[1] == SPRITE_SIZE and a[2] == SPRITE_SIZE

    def sprite_lines(self, line):
        """Table indexes of the sprites drawn on scan line `line` (live table), lowest first:
        the first MAX_PER_LINE enabled entries with y <= line < y + 16."""
        if not self.live_sp_enable:
            return []
        out = []
        for i in range(N_SPRITES):
            x, y, asset, pal, flags, _ = struct.unpack("<hhBBBB", self.live_sprites[i * 8:i * 8 + 8])
            if flags & SPRITE_ON and y <= line < y + SPRITE_SIZE:
                out.append(i)
                if len(out) == MAX_PER_LINE:
                    break
        return out

    def sprite_plane(self):
        """The sprites as a plane of 76,800 entries (y * 320 + x): None where no sprite drew the
        pixel, else (index, item priority). Drawn line by line, back to front (the highest drawn index first),
        so the lowest index ends in front. The item priority is the PRIORITY field of the sprite that won the pixel."""
        plane = [None] * FB_SIZE
        if not self.live_sp_enable:
            return plane
        for line in range(H):
            for i in reversed(self.sprite_lines(line)):
                x, y, asset, pal, flags, _ = struct.unpack("<hhBBBB", self.live_sprites[i * 8:i * 8 + 8])
                asset &= 15
                if not self.sprite_ok(asset):
                    continue
                base = self.assets[asset][0]
                item = (flags >> SPRITE_PRI_SHIFT) & 3
                ry = line - y
                if flags & SPRITE_FLIP_Y:
                    ry = SPRITE_SIZE - 1 - ry
                for px in range(SPRITE_SIZE):
                    sx = x + px
                    if not 0 <= sx < W:
                        continue
                    cx = SPRITE_SIZE - 1 - px if flags & SPRITE_FLIP_X else px
                    c = self.ram[base + ry * SPRITE_SIZE + cx]
                    if c:
                        plane[line * W + sx] = ((c + 16 * (pal & 15)) & 255, item)
        return plane

    def screen(self):
        """One frame as 76,800 palette indices (y * 320 + x): at every pixel compose_pixel() of the
        front framebuffer, the two enabled tile layers and the sprites, with the live compositor
        settings. Index 0 is transparent in every source."""
        fb = self.front_fb
        sp = self.sprite_plane()
        planes = []
        for layer in range(N_LAYERS):
            if self.live_enable >> layer & 1:
                sx, sy = self.live_scroll[layer]
                planes.append([layer_pixel_pri(self.tiles, self.tmap[layer], sx, sy, x, y)
                               for y in range(H) for x in range(W)])
            else:
                planes.append(None)
        out = bytearray(FB_SIZE)
        for n in range(FB_SIZE):
            srcs = [(self.live_fb_on and fb[n] != 0, 0, fb[n])]
            for p in planes:
                if p is None:
                    srcs.append((False, 0, 0))
                else:
                    srcs.append((p[n][0] != 0, p[n][1], p[n][0]))
            srcs.append((sp[n] is not None, sp[n][1] if sp[n] else 0, sp[n][0] if sp[n] else 0))
            out[n] = compose_pixel(srcs, self.live_priority, self.live_backdrop)
        return bytes(out)

    def run_list(self, list_id):
        """Run list list_id: its bytes, 0 to length - 1, go through the parser as if the host had sent them.
        A forbidden opcode (LIST_FORBIDDEN) makes the packet skipped whole with BAD_OP; a packet the length
        cuts short is treated like a frame start would treat it (TRUNC)."""
        if list_id >= N_LISTS or self.list_len[list_id] == 0:
            return
        self.list_runs += 1
        data = bytes(self.lists[list_id][:self.list_len[list_id]])
        saved = bytes(self._pkt)
        self._pkt.clear()
        i = 0
        while i < len(data):
            op = data[i]
            ln = data[i + 1] if i + 1 < len(data) else None
            if ln is None or i + 2 + ln > len(data):          # the list ends inside this packet
                part = data[i:]
                if op in LIST_FORBIDDEN:
                    self.errors |= ERR_TRUNC
                else:
                    self._pkt = bytearray(part)
                    self.abandon()
                break
            payload = data[i + 2:i + 2 + ln]
            if op in LIST_FORBIDDEN:
                self.ignored += 1
                self.errors |= ERR_BAD_OP
            else:
                self.execute(op, payload)
            i += 2 + ln
        self._pkt = bytearray(saved)

    def auto_run(self):
        """What the GPU does at the start of every vertical blanking when SET_AUTO_LIST is on."""
        if self.auto is not None:
            self.run_list(self.auto)

    def list_stream(self, payload):
        """APPEND_LIST (payload: id, then data): bytes go to the end of the list, up to 512."""
        if len(payload) < 1 or payload[0] >= N_LISTS:
            return
        lid = payload[0]
        for b in payload[1:]:
            if self.list_len[lid] < LIST_BYTES:
                self.lists[lid][self.list_len[lid]] = b
                self.list_len[lid] += 1

    def abandon(self):
        """A frame starts while a packet is partly received: drop it, set TRUNC.
        UPLOAD_ASSET already acted on its header (the 5 payload bytes after
        id, w, h, offset) and wrote the data bytes that arrived."""
        p = bytes(self._pkt)
        if p:
            self.errors |= ERR_TRUNC
            if p[0] == UPLOAD_ASSET and len(p) >= 2 + UPLOAD_HEADER and p[1] > UPLOAD_HEADER:
                self.upload(p[2:], declared=p[1] - UPLOAD_HEADER)
            elif p[0] == UPLOAD_TILES and len(p) >= 2 and p[1] >= 2:
                self.tile_stream(p[2:])           # the bytes that arrived are written
            elif p[0] == WRITE_TILE_MAP and len(p) >= 2 and p[1] >= 3 and p[1] % 2 == 1:
                self.map_stream(p[2:])            # the whole entries that arrived are written
            elif p[0] in (WRITE_TILE_COLUMN, WRITE_TILE_ROW) and len(p) >= 2 and p[1] == STREAM_LEN:
                self.line_stream(p[0], p[2:])     # a right-length packet streams: whole entries that arrived are written
            elif p[0] == UPLOAD_VERTICES and len(p) >= 2 and vertex_len_ok(p[1]):
                self.vertex_stream(p[2:])         # the whole vertices that arrived are written
            elif p[0] == UPLOAD_NORMALS and len(p) >= 2 and vertex_len_ok(p[1]):
                self.normal_stream(p[2:])         # the whole normals that arrived are written
            elif p[0] == UPLOAD_INDICES and len(p) >= 2 and index_len_ok(p[1]):
                self.index_stream(p[2:])          # the whole entries that arrived are written
            elif p[0] == APPEND_LIST and len(p) >= 2 and p[1] >= 1:
                self.list_stream(p[2:])           # the bytes that arrived are appended
            self._pkt.clear()

    def error_byte(self):
        """GET_INFO's byte 8."""
        return (self.errors | (ERR_OVERFLOW if self.overflow else 0)) & 0x0F

    def execute(self, op, payload):
        """Run one complete packet; return its reply bytes."""
        if op == UPLOAD_ASSET and len(payload) < UPLOAD_HEADER:
            self.ignored += 1
            self.errors |= ERR_BAD_LEN
            return b""
        if op not in LENGTHS:
            self.ignored += 1
            self.errors |= ERR_BAD_OP
            return b""
        if op == UPLOAD_VERTICES and not vertex_len_ok(len(payload)):
            self.ignored += 1
            self.errors |= ERR_BAD_LEN
            return b""
        if op == UPLOAD_NORMALS and not vertex_len_ok(len(payload)):
            self.ignored += 1
            self.errors |= ERR_BAD_LEN
            return b""
        if op == UPLOAD_INDICES and not index_len_ok(len(payload)):
            self.ignored += 1
            self.errors |= ERR_BAD_LEN
            return b""
        if (op == APPEND_LIST and len(payload) < 1) or (op == UPLOAD_TILES and len(payload) < 2) or (op == WRITE_TILE_MAP and (len(payload) < 3 or len(payload) % 2 == 0)):
            self.ignored += 1
            self.errors |= ERR_BAD_LEN
            return b""
        if LENGTHS[op] is not None and len(payload) != LENGTHS[op]:
            self.ignored += 1
            self.errors |= ERR_BAD_LEN
            return b""
        if op == GET_INFO:
            return bytes([INFO_MAGIC, PROTOCOL_VERSION, self.caps & 255, self.caps >> 8, W & 255, W >> 8, H & 255, H >> 8,
                          self.error_byte()])
        if op == GET_STATS:
            return struct.pack("<III", self.stat_cov, self.stat_wr, self.stat_clocks)
        if op == GET_MESH_STATS:
            return struct.pack("<III", self.mesh_culled, self.mesh_rejected, self.mesh_drawn)
        if op == READ_VERTEX:
            i = struct.unpack("<H", payload)[0]
            if i >= vertex_model.N_VERTICES:
                return b""                                    # no such vertex: no reply, no flag
            if self.vdst[i] is None:
                self.read_undefined += 1
                return bytes(8)
            if self.vlit[i] is None:
                self.read_undefined += 1
                return bytes(8)
            sx, sy, d, flags = self.vdst[i]
            return struct.pack("<hhHBB", sx, sy, d, flags, self.vlit[i])
        if op == CLEAR_ERRORS:
            self.errors &= ~payload[0] & 0x0E
            if payload[0] & ERR_OVERFLOW:
                self.overflow = False
            return b""
        if op == SET_PALETTE:
            i, r, g, b = payload
            self.pal[i] = (r << 16) | (g << 8) | b
        elif op == CLEAR:
            self.fb[:] = bytes([payload[0]]) * FB_SIZE
        elif op == PLOT:
            x, y, c = struct.unpack("<hhB", payload)
            if 0 <= x < W and 0 <= y < H:
                self.fb[y * W + x] = c
        elif op == FILL_RECT:
            x0, y0, x1, y1, c = struct.unpack("<hhhhB", payload)
            r = clip_rect(x0, y0, x1, y1)
            if r is not None:
                xa, ya, xb, yb = r
                row = bytes([c]) * (xb - xa + 1)
                for y in range(ya, yb + 1):
                    self.fb[y * W + xa: y * W + xb + 1] = row
                self.rect_pixels += len(row) * (yb - ya + 1)
        elif op == DRAW_LINE:
            x0, y0, x1, y1, c = struct.unpack("<hhhhB", payload)
            for x, y in line_points(x0, y0, x1, y1):
                self.line_points += 1
                if 0 <= x < W and 0 <= y < H:
                    self.fb[y * W + x] = c
                    self.line_pixels += 1
        elif op == DRAW_TRI:
            x0, y0, x1, y1, x2, y2, c = struct.unpack("<hhhhhhB", payload)
            self.tri_count += 1
            for x, y in tri_covered(x0, y0, x1, y1, x2, y2):
                self.fb[y * W + x] = c
                self.tri_drawn += 1
                self.stat_cov = min(self.stat_cov + 1, STAT_MAX)
                self.stat_wr = min(self.stat_wr + 1, STAT_MAX)
        elif op == DRAW_TRI_SHADED:
            x0, y0, x1, y1, x2, y2, ramp, z0, z1, z2, i0, i1, i2 = struct.unpack("<hhhhhhBHHHBBB", payload)
            self.tri_shaded_count += 1
            for x, y, z, i in raster_model.shade_pixels((x0, y0, z0, i0), (x1, y1, z1, i1), (x2, y2, z2, i2), W, H):
                a = y * W + x
                self.stat_cov = min(self.stat_cov + 1, STAT_MAX)
                if self.d_test and not z < self.depth[a]:          # equal depth fails: smaller = nearer
                    self.depth_rejected += 1
                    continue
                self.fb[a] = ((ramp & 7) << 5) | i
                if self.d_wr:
                    self.depth[a] = z
                self.tri_shaded_drawn += 1
                self.stat_wr = min(self.stat_wr + 1, STAT_MAX)
        elif op == UPLOAD_INDICES:
            self.index_stream(payload)
        elif op == SET_MESH_MODE:
            self.mesh_mode = payload[0] & 7
        elif op == CLEAR_MESH_STATS:
            self.mesh_culled = self.mesh_rejected = self.mesh_drawn = 0
        elif op == DRAW_MESH:
            first, count = struct.unpack("<HH", payload)
            for t in range(first, min(first + count, N_TRIANGLES)):
                self.draw_triangle(t)
        elif op == SET_DEPTH:
            self.d_test = payload[0] & 1
            self.d_wr = (payload[0] >> 1) & 1
        elif op == CLEAR_DEPTH:
            self.depth = [struct.unpack("<H", payload)[0]] * FB_SIZE
        elif op == CLEAR_ALL:
            self.fb[:] = bytes([payload[0]]) * FB_SIZE
            self.depth = [payload[1] | (payload[2] << 8)] * FB_SIZE
        elif op == CLEAR_STATS:
            self.stat_cov = self.stat_wr = self.stat_clocks = 0
        elif op == UPLOAD_VERTICES:
            self.vertex_stream(payload)
        elif op == SET_MATRIX:
            self.matrix = struct.unpack("<12h", payload)
        elif op == UPLOAD_NORMALS:
            self.normal_stream(payload)
        elif op == SET_LIGHT:
            flags, lx, ly, lz, amb = struct.unpack("<Bhhhh", payload)
            self.light_en = flags & 1
            self.light_dir = (lx, ly, lz)
            self.light_amb = amb
        elif op == SET_PROJECTION:
            self.proj_f, self.proj_near = struct.unpack("<hH", payload)
        elif op == TRANSFORM:
            first, count = struct.unpack("<HH", payload)
            for i in range(first, min(first + count, vertex_model.N_VERTICES)):
                v = self.vsrc[i]
                self.vdst[i] = None if v is None else vertex_model.transform(v, self.matrix, self.proj_f, self.proj_near)
                if v is None:
                    self.vlit[i] = None
                elif not self.light_en:
                    self.vlit[i] = 0                              # byte 7 stays 0: Chapter 6.5's records
                elif self.nsrc[i] is None:
                    self.vlit[i] = None
                    self.lit_undefined += 1
                else:
                    self.vlit[i] = light_model.vertex_intensity(self.nsrc[i], self.matrix, self.light_dir, self.light_amb)
        elif op == SET_TILE_LAYER:
            self.enable = payload[0] & 3
        elif op == SET_SCROLL:
            layer, x, y = struct.unpack("<BHH", payload)
            if layer < N_LAYERS:
                self.scroll[layer] = [x % WORLD, y % WORLD]
        elif op == SET_SPRITE:
            if payload[0] < N_SPRITES:
                self.sprites[payload[0] * 8: payload[0] * 8 + 8] = payload[1:9]
        elif op == MOVE_SPRITE:
            if payload[0] < N_SPRITES:
                self.sprites[payload[0] * 8: payload[0] * 8 + 4] = payload[1:5]
        elif op == SPRITE_CTRL:
            self.sp_enable = payload[0] & 1
        elif op == SET_PRIORITY:
            self.priority = payload[0]
        elif op == SET_BACKDROP:
            self.backdrop = payload[0]
        elif op == FB_ENABLE:
            self.fb_on = payload[0] & 1
        elif op == UPLOAD_TILES:
            self.tile_stream(payload)
        elif op == WRITE_TILE_MAP:
            self.map_stream(payload)
        elif op in (WRITE_TILE_COLUMN, WRITE_TILE_ROW):
            self.line_stream(op, payload)
        elif op == UPLOAD_ASSET:
            self.upload(payload)
        elif op == DEFINE_LIST:
            if payload[0] < N_LISTS:
                self.list_len[payload[0]] = 0
        elif op == APPEND_LIST:
            self.list_stream(payload)
        elif op == RUN_LIST:
            self.run_list(payload[0])
        elif op == WRITE_LIST_WORD:
            lid, off, val = struct.unpack("<BHH", payload)
            if lid < N_LISTS:
                for k, b in enumerate((val & 255, val >> 8)):
                    if off + k < LIST_BYTES:
                        self.lists[lid][off + k] = b
        elif op == SET_AUTO_LIST:
            if payload[0] == AUTO_OFF:
                self.auto = None
            elif payload[0] < N_LISTS:
                self.auto = payload[0]
        elif op == BLIT:
            self.blit(payload)
        elif op == FLIP:
            self.front = 1 - self.front
            self.flips += 1
        elif op == FB_CRC:
            return struct.pack("<I", self.crc())
        return b""

    def upload(self, payload, declared=None):
        """UPLOAD_ASSET (Step 4.8), payload at least 5 bytes: id, w, h,
        offset (16-bit), then n data bytes.

        * w = h = 0: reset. Every asset is forgotten and the RAM is empty
          again (its bytes are not erased). Only with n = 0; otherwise ignored.
        * Otherwise the packet is ignored (and changes nothing) unless
          id < 16, w and h are both 1-255, and offset + n <= w * h.
        * offset = 0 allocates. If asset id exists and already holds w * h
          bytes, it keeps its place (the image is replaced in place, its w
          and h may change). Otherwise the asset is placed at the first free
          byte, which then moves up by w * h; if that does not fit in 32,768
          bytes the packet is refused, and the table and RAM stay as they
          were. The space of an asset that is replaced by one of another size
          is not given back until the next reset.
        * offset > 0 continues an upload: ignored unless asset id exists and
          its w and h are the ones in this packet.
        * The n data bytes are written at base + offset.
        """
        a, w, h = payload[0], payload[1], payload[2]
        off = payload[3] | (payload[4] << 8)
        data = payload[5:]
        n = len(data) if declared is None else declared   # declared: n the packet announced, when it was cut short
        if declared is not None and w == 0 and h == 0:
            return                                          # a reset needs the whole (empty) packet
        if w == 0 and h == 0:
            if n == 0:
                self.assets.clear()
                self.next_free = 0
            return
        if w == 0 or h == 0 or a >= N_ASSETS or off + n > w * h:
            return
        if off == 0:
            old = self.assets.get(a)
            if old is not None and old[1] * old[2] == w * h:
                base = old[0]                       # same size: replace in place
            elif self.next_free + w * h <= ASSET_RAM:
                base = self.next_free
                self.next_free += w * h
                self.ram_written[base:base + w * h] = bytes(w * h)
            else:
                self.upload_rejected += 1
                return
            self.assets[a] = (base, w, h)
        else:
            cur = self.assets.get(a)
            if cur is None or cur[1] != w or cur[2] != h:
                self.upload_rejected += 1
                return
            base = cur[0]
        self.ram[base + off: base + off + len(data)] = data
        self.ram_written[base + off: base + off + len(data)] = b"\x01" * len(data)

    def index_stream(self, payload):
        """UPLOAD_INDICES (payload of 2 + 8k bytes, or the part that arrived): first slot (16-bit), then 8-byte entries.
        Only whole entries are written; slots from 512 up are dropped (no wrap)."""
        if len(payload) < 2:
            return
        first = payload[0] | (payload[1] << 8)
        data = payload[2:]
        for k in range(len(data) // mesh_model.ENTRY_BYTES):
            if first + k < N_TRIANGLES:
                self.idx[first + k] = struct.unpack("<HHHBB", data[8 * k:8 * k + 8])

    def draw_triangle(self, t):
        """One DRAW_MESH step: index entry t -> rejected, culled, or DRAW_TRI_SHADED with the entry's ramp and intensity."""
        e = self.idx[t]
        if e is None:
            self.mesh_undefined += 1
            self.mesh_rejected += 1
            return
        i0, i1, i2, ramp, inten = e
        recs = [self.vdst[i] if i < vertex_model.N_VERTICES else None for i in (i0, i1, i2)]
        if any(i < vertex_model.N_VERTICES and r is None for i, r in zip((i0, i1, i2), recs)):
            self.mesh_undefined += 1
        v = mesh_model.verdict(recs, (i0, i1, i2), self.mesh_mode)
        if v == mesh_model.REJECTED:
            self.mesh_rejected = min(self.mesh_rejected + 1, STAT_MAX)
            return
        if v == mesh_model.CULLED:
            self.mesh_culled = min(self.mesh_culled + 1, STAT_MAX)
            return
        self.mesh_drawn = min(self.mesh_drawn + 1, STAT_MAX)
        # the intensity of a record is byte 7: 0 unless lighting was on at its TRANSFORM (Chapter 6.7)
        if self.mesh_mode & mesh_model.MODE_VERTEX_I:
            ints = [self.vlit[i] for i in (i0, i1, i2)]
            if None in ints:
                self.mesh_undefined += 1
                ints = [0 if x is None else x for x in ints]
        else:
            ints = [inten] * 3
        payload = struct.pack("<hhhhhhBHHHBBB", recs[0][0], recs[0][1], recs[1][0], recs[1][1], recs[2][0], recs[2][1], ramp,
                              recs[0][2], recs[1][2], recs[2][2], *ints)
        self.execute(DRAW_TRI_SHADED, payload)

    def normal_stream(self, payload):
        """UPLOAD_NORMALS (payload of 2 + 6k bytes, or the part that arrived): first slot (16-bit), then 6-byte normals
        (nx, ny, nz, Q2.14). Only whole normals are written; slots from 1024 up are dropped (no wrap)."""
        if len(payload) < 2:
            return
        first = payload[0] | (payload[1] << 8)
        data = payload[2:]
        for k in range(len(data) // VERTEX_BYTES):
            if first + k < vertex_model.N_VERTICES:
                self.nsrc[first + k] = struct.unpack("<hhh", data[VERTEX_BYTES * k:VERTEX_BYTES * k + VERTEX_BYTES])

    def vertex_stream(self, payload):
        """UPLOAD_VERTICES (payload of 2 + 6k bytes, or the part that arrived): first slot (16-bit), then 6-byte vertices.
        Only whole vertices are written; slots from 1024 up are dropped (no wrap)."""
        if len(payload) < 2:
            return
        first = payload[0] | (payload[1] << 8)
        data = payload[2:]
        for k in range(len(data) // VERTEX_BYTES):
            if first + k < vertex_model.N_VERTICES:
                self.vsrc[first + k] = struct.unpack("<hhh", data[VERTEX_BYTES * k:VERTEX_BYTES * k + VERTEX_BYTES])

    def tile_stream(self, payload):
        """UPLOAD_TILES: offset (16-bit), then data bytes; bytes past address 16383 are dropped."""
        if len(payload) < 2:
            return
        off = payload[0] | (payload[1] << 8)
        for i, b in enumerate(payload[2:]):
            if off + i < TILE_BYTES:
                self.tiles[off + i] = b

    def map_stream(self, payload):
        """WRITE_TILE_MAP: layer, offset (16-bit), then 16-bit entries; a trailing odd byte
        is ignored, entries past 4095 and layers above 1 are dropped."""
        if len(payload) < 3:
            return
        layer, off = payload[0], payload[1] | (payload[2] << 8)
        data = payload[3:]
        for i in range(len(data) // 2):
            if layer < N_LAYERS and off + i < MAP_ENTRIES:
                self.tmap[layer][off + i] = data[2 * i] | (data[2 * i + 1] << 8)

    def line_stream(self, op, payload):
        """WRITE_TILE_COLUMN / WRITE_TILE_ROW (payload of 130 bytes, or the part that arrived):
        layer, index, then 64 entries. A layer above 1 or an index above 63 writes nothing."""
        if len(payload) < 2:
            return
        layer, idx = payload[0], payload[1]
        if layer >= N_LAYERS or idx >= MAP_W:
            return
        data = payload[2:]
        for k in range(min(len(data) // 2, MAP_W)):
            e = data[2 * k] | (data[2 * k + 1] << 8)
            self.tmap[layer][k * MAP_W + idx if op == WRITE_TILE_COLUMN else idx * MAP_W + k] = e

    def blit(self, payload):
        """BLIT (Step 4.8): see the opcode table at the top of this file."""
        a, x, y, flags = struct.unpack("<BhhB", payload)
        if a not in self.assets:
            return
        base, w, h = self.assets[a]
        self.blit_count += 1
        for X, Y, i in blit_points(w, h, x, y, flags):
            if 0 <= X < W and 0 <= Y < H:
                if not self.ram_written[base + i]:
                    self.blit_unwritten += 1
                c = self.ram[base + i]
                if c != 0 or flags & OPAQUE:
                    self.fb[Y * W + X] = c
                    self.blit_drawn += 1

    def crc(self):
        return crc32(self.fb)


if __name__ == "__main__":
    # Self-check: the bitwise CRC matches zlib, and the check value of the
    # standard "123456789" test string is 0xCBF43926.
    assert crc32(b"123456789") == crc32_bitwise(b"123456789") == 0xCBF43926
    g = Gpu()
    assert g.feed(fb_crc()) == struct.pack("<I", zlib.crc32(bytes(FB_SIZE)))
    # FILL_RECT: inclusive corners in either order, clipped to the screen.
    assert clip_rect(5, 7, 5, 7) == (5, 7, 5, 7)              # one pixel
    assert clip_rect(30, 20, 10, 40) == (10, 20, 30, 40)       # inverted x
    assert clip_rect(-32768, -32768, 32767, 32767) == (0, 0, W - 1, H - 1)
    assert clip_rect(320, 0, 400, 10) is None and clip_rect(-5, 0, -1, 10) is None
    g.feed(fill_rect(319, 239, -100, 0, 9))
    assert g.fb[0] == 9 and g.fb[FB_SIZE - 1] == 9 and g.rect_pixels == FB_SIZE
    # DRAW_LINE: endpoints included, ties toward the end point, so the
    # direction matters, and the two formulations agree everywhere.
    assert list(line_points(5, 7, 5, 7)) == [(5, 7)]
    assert list(line_points(0, 0, 4, 2)) == [(0, 0), (1, 1), (2, 1), (3, 2), (4, 2)]
    assert list(line_points(4, 2, 0, 0)) == [(4, 2), (3, 1), (2, 1), (1, 0), (0, 0)]
    for x1 in range(-24, 25):
        for y1 in range(-24, 25):
            p = list(line_points(3, -2, x1, y1))
            assert p == line_points_major(3, -2, x1, y1), (x1, y1)
            assert p[-1] == (x1, y1) and len(p) == max(abs(x1 - 3), abs(y1 + 2)) + 1
    assert len(list(line_points(-32768, 32767, 32767, -32768))) == 65536
    g.feed(clear(0) + draw_line(-10, 120, 400, 120, 5))       # clipped point by point
    assert g.fb.count(5) == W and g.line_points == 411 and g.line_pixels == W
    # FLIP: drawing goes to the back buffer, FB_CRC covers the back buffer,
    # and a flip puts the drawn picture on the screen while the old one stays
    # behind the drawing. FLIP with a payload is ignored.
    g = Gpu()
    blank = zlib.crc32(bytes(FB_SIZE)) & 0xFFFFFFFF
    g.feed(clear(7) + draw_line(0, 0, 9, 0, 5))
    assert g.front_fb.count(0) == FB_SIZE and g.fb.count(7) == FB_SIZE - 10 and g.flips == 0
    drawn = g.crc()
    assert drawn != blank
    g.feed(flip())
    assert g.flips == 1 and g.front_fb.count(7) == FB_SIZE - 10 and g.fb.count(0) == FB_SIZE
    assert g.feed(fb_crc()) == struct.pack("<I", blank)        # FB_CRC: the new back buffer
    g.feed(packet(FLIP, b"\x00") + packet(FLIP, b"\x00\x00"))   # wrong length: ignored
    assert g.flips == 1 and g.ignored == 2
    g.feed(flip())
    assert g.flips == 2 and g.crc() == drawn and g.front_fb.count(0) == FB_SIZE
    # DRAW_TRI: Chapter 4.6's worked triangle covers 10 pixels, either winding,
    # corners in any order; zero area and off-screen draw nothing; a quad split
    # along a diagonal covers every pixel once; corners may be at the int16 limits.
    g = Gpu()
    g.feed(draw_tri(0, 0, 0, 4, 4, 0, 9))
    assert g.tri_count == 1 and g.tri_drawn == 10 and g.fb.count(9) == 10
    assert g.fb[0] == 9 and g.fb[3] == 9 and g.fb[4] == 0 and g.fb[4 * W] == 0
    g.feed(clear(0) + draw_tri(0, 0, 4, 0, 0, 4, 9))              # clockwise
    assert g.fb.count(9) == 10 and g.tri_drawn == 20
    g.feed(clear(0) + draw_tri(5, 5, 5, 5, 5, 5, 1) + draw_tri(0, 0, 9, 9, 3, 3, 1)
           + draw_tri(-50, -50, -1, -1, -1, -50, 1) + draw_tri(320, 0, 400, 0, 320, 50, 1))
    assert g.fb.count(1) == 0 and g.tri_count == 6 and g.tri_drawn == 20
    g.feed(draw_tri(0, 0, 0, 4, 4, 0, 5) + draw_tri(4, 0, 0, 4, 4, 4, 6))
    assert g.fb.count(5) + g.fb.count(6) == 16 and g.fb.count(6) == 6   # a 4x4 square, drawn once
    g.feed(clear(0) + draw_tri(-32768, -32768, 32767, -32768, 32767, 32767, 1)
           + draw_tri(-32768, -32768, 32767, 32767, -32768, 32767, 2))
    assert g.fb.count(1) + g.fb.count(2) == FB_SIZE and g.fb.count(1) > 0 and g.fb.count(2) > 0
    g.feed(packet(DRAW_TRI, bytes(12)) + packet(DRAW_TRI, bytes(14)))        # wrong length: ignored
    assert g.ignored == 2 and g.tri_count == 10
    # UPLOAD_ASSET and BLIT: a 3 x 2 image, transparent index 0, four flips,
    # clipping, several packets, replacing in place, a full RAM, and reset.
    g = Gpu()
    img = bytes([1, 0, 2,
                 3, 4, 0])
    g.feed(b"".join(upload_packets(5, 3, 2, img)) + blit(5, 10, 20, 0))
    px = lambda gg, x, y: gg.fb[y * W + x]
    assert [px(g, 10 + i, 20) for i in range(3)] == [1, 0, 2] and [px(g, 10 + i, 21) for i in range(3)] == [3, 4, 0]
    g = Gpu()
    g.feed(b"".join(upload_packets(5, 3, 2, img)) + blit(5, 10, 20, FLIP_X))
    assert [px(g, 10 + i, 20) for i in range(3)] == [2, 0, 1] and [px(g, 10 + i, 21) for i in range(3)] == [0, 4, 3]
    g = Gpu()
    g.feed(b"".join(upload_packets(5, 3, 2, img)) + blit(5, 10, 20, FLIP_Y))
    assert [px(g, 10 + i, 20) for i in range(3)] == [3, 4, 0] and [px(g, 10 + i, 21) for i in range(3)] == [1, 0, 2]
    g = Gpu()
    g.feed(b"".join(upload_packets(5, 3, 2, img)) + blit(5, 10, 20, FLIP_X | FLIP_Y | OPAQUE))
    assert [px(g, 10 + i, 21) for i in range(3)] == [2, 0, 1] and [px(g, 10 + i, 20) for i in range(3)] == [0, 4, 3]
    assert g.blit_drawn == 6 and g.blit_unwritten == 0
    g = Gpu()
    g.feed(b"".join(upload_packets(5, 3, 2, img)) + blit(5, -1, -1) + blit(5, 319, 239) + blit(5, 400, 0) + blit(5, -32768, 32767))
    assert px(g, 0, 0) == 4 and g.fb.count(0) == FB_SIZE - 2 and px(g, 319, 239) == 1 and g.blit_count == 4
    # a 300-byte image goes up in two packets (250 + 50), in either order after the allocating one
    big = bytes(range(1, 101)) * 3
    pk = upload_packets(1, 100, 3, big)
    assert len(pk) == 2 and len(pk[0]) == 2 + 255 and pk[1][2:7] == bytes([1, 100, 3, 250, 0])
    g = Gpu()
    g.feed(upload_asset(1, 100, 3, b"") + pk[1] + pk[0] + blit(1, 0, 0))
    assert bytes(g.fb[0:100]) == big[:100] and bytes(g.fb[2 * W: 2 * W + 100]) == big[200:300] and g.blit_unwritten == 0
    # malformed uploads change nothing; BLIT of an unknown id draws nothing
    g = Gpu()
    g.feed(packet(UPLOAD_ASSET, bytes(4)) + upload_asset(16, 2, 2, bytes(4)) + upload_asset(0, 0, 3, b"") + upload_asset(0, 3, 0, b"")
           + upload_asset(0, 2, 2, bytes(5)) + upload_asset(0, 2, 2, b"\x01", 4) + upload_asset(0, 2, 2, b"\x01", 1)
           + packet(UPLOAD_ASSET, bytes([0, 0, 0, 0, 0, 7])) + blit(0, 0, 0, OPAQUE) + packet(BLIT, bytes(5)) + packet(BLIT, bytes(7)))
    assert g.assets == {} and g.next_free == 0 and g.blit_count == 0 and g.fb.count(0) == FB_SIZE and g.ignored == 3
    # replacing: same size keeps its place (even as a different shape), another size moves to new space
    g = Gpu()
    g.feed(b"".join(upload_packets(2, 4, 4, bytes([1] * 16)) + upload_packets(3, 2, 2, bytes([2] * 4))))
    assert g.assets == {2: (0, 4, 4), 3: (16, 2, 2)} and g.next_free == 20
    g.feed(b"".join(upload_packets(2, 8, 2, bytes([5] * 16))))
    assert g.assets[2] == (0, 8, 2) and g.next_free == 20
    g.feed(b"".join(upload_packets(2, 3, 3, bytes([6] * 9))))
    assert g.assets[2] == (20, 3, 3) and g.next_free == 29 and g.assets[3] == (16, 2, 2)
    g.feed(upload_asset(3, 4, 4, b"\x09", 5))                      # w, h do not match asset 3: refused
    assert g.upload_rejected == 1 and g.ram[16:20] == bytes([2] * 4)
    # a full RAM: 128 x 255 + 16 x 8 fill it exactly, one more byte is refused and nothing changes
    g = Gpu()
    g.feed(upload_asset(7, 128, 255, b"\x03") + upload_asset(8, 16, 8, b"\x04") + upload_asset(9, 1, 1, b"\x05"))
    assert g.assets[7] == (0, 128, 255) and g.assets[8] == (32640, 16, 8) and 9 not in g.assets
    assert g.next_free == ASSET_RAM and g.upload_rejected == 1
    g.feed(upload_asset(7, 255, 128, b"\x03") + upload_asset(8, 1, 2, b"\x01"))   # same size: in place; smaller: needs new space
    assert g.assets[7] == (0, 255, 128) and g.assets[8] == (32640, 16, 8) and g.upload_rejected == 2
    g.feed(asset_reset() + upload_asset(9, 1, 1, b"\x05"))
    assert 7 not in g.assets and g.assets == {9: (0, 1, 1)} and g.next_free == 1
    # Chapter 5.2: GET_INFO, error flags, CLEAR_ERRORS, frame starts.
    g = Gpu()
    r = g.feed(get_info())
    assert r == bytes([0x47, 1, 0xFF, 0x7F, 0x40, 0x01, 0xF0, 0x00, 0x00]) and parse_info(r)["caps"] == CAPS_SPI
    assert Gpu(caps=CAPS_UART).feed(get_info())[2:4] == bytes([0x0F, 0x00])
    g.feed(packet(0x7E, b"\x01") + packet(PLOT, b"\x00") + packet(0xC0) + packet(UPLOAD_ASSET, bytes(3)))
    assert g.errors == ERR_BAD_LEN | ERR_BAD_OP and g.feed(get_info())[8] == 0x06 and g.ignored == 4
    g.feed(clear_errors(ERR_BAD_LEN))
    assert g.feed(get_info())[8] == 0x04
    g.feed(clear_errors(0xF0) + packet(CLEAR_ERRORS) + packet(CLEAR_ERRORS, bytes(2)))   # no bits / wrong length
    assert g.feed(get_info())[8] == 0x06
    g.overflow = True
    assert g.feed(get_info())[8] == 0x07 and g.feed(clear_errors(ERR_OVERFLOW) + get_info())[8] == 0x06
    g.feed(clear_errors())
    assert g.feed(get_info())[8] == 0
    # a frame start drops a partial packet and sets TRUNC; the next packet is whole
    g = Gpu()
    g.feed(plot(1, 1, 7)[:5])                       # cut inside the payload
    assert g.feed(fb_crc(), sof=True) == struct.pack("<I", crc32(bytes(FB_SIZE))) and g.fb[W + 1] == 0
    assert g.errors == ERR_TRUNC
    g.feed(clear_errors())
    g.feed(plot(1, 1, 7)[:1])                       # opcode only
    g.feed(plot(2, 2, 7), sof=True)
    assert g.errors == ERR_TRUNC and g.fb[2 * W + 2] == 7 and g.fb[W + 1] == 0
    g.feed(clear_errors() + plot(3, 3, 1), sof=True)  # a complete packet does not set TRUNC
    assert g.errors == 0 and g.fb[3 * W + 3] == 1
    # truncated UPLOAD_ASSET: the header took effect, the data that arrived is written
    g = Gpu()
    full = upload_asset(4, 3, 2, bytes([1, 2, 3, 4, 5, 6]))
    g.feed(full[:2 + 5 + 2])                        # header and 2 of 6 data bytes
    g.feed(nop(), sof=True)
    assert g.assets == {4: (0, 3, 2)} and bytes(g.ram[:3]) == bytes([1, 2, 0]) and g.errors == ERR_TRUNC
    g.feed(full[:2 + 4])                            # header incomplete: nothing happens
    g.feed(nop(), sof=True)
    assert g.assets == {4: (0, 3, 2)}
    g = Gpu()
    g.feed(upload_asset(1, 2, 2, bytes(4))[:7])     # cut right after the header: the asset exists
    g.feed(asset_reset(), sof=True)                 # a whole reset in the next frame
    assert g.assets == {} and g.next_free == 0
    # Chapter 5.3: tile layers.
    g = Gpu()
    assert g.feed(get_info())[2:4] == bytes([0xFF, 0x7F])
    tiles = bytearray(TILE_BYTES)
    for t in range(1, 4):                                  # tile t: all pixels nonzero and distinct
        for i in range(64):
            tiles[t * 64 + i] = 1 + (t * 64 + i) % 255
    g.feed(b"".join(tile_packets(tiles[:4 * 64])))
    assert bytes(g.tiles[:256]) == bytes(tiles[:256]) and len(tile_packets(tiles)) == 65
    assert all(len(p) <= 257 for p in tile_packets(tiles)) and len(map_packets(0, [0] * 4096)) == 33
    ents = [map_entry(1), map_entry(2, True), map_entry(3, False, True), map_entry(1, True, True)]
    g.feed(b"".join(map_packets(0, ents)))
    assert g.tmap[0][:4] == ents and g.tmap[1][:4] == [0, 0, 0, 0]
    assert map_entry(5, True, True) == 0x305 and write_tile_map(1, 0x123, [0x0201]) == bytes([0x13, 5, 1, 0x23, 0x01, 0x01, 0x02])
    # nothing is visible until enabled and latched
    assert g.screen() == bytes(FB_SIZE)
    g.feed(set_tile_layer(1))
    assert g.screen() == bytes(FB_SIZE)                    # a shadow register: needs vblank()
    g.vblank()
    s = g.screen()
    assert s[0] == tiles[64] and s[8] == tiles[2 * 64 + 7] and s[16] == tiles[3 * 64 + 56] and s[24] == tiles[64 + 63]
    # scroll: world position of the top-left, modulo 512; -1 wraps to 511
    g.feed(set_scroll(0, 8, 0) + set_scroll(1, 5, 5))
    assert g.screen() == s                                 # not yet
    g.vblank()
    assert g.screen()[0] == tiles[2 * 64 + 7]              # tile column 1 starts at x = 0, flipped in X
    assert layer_pixel(tiles, [1] * 4096, 0, 0, 0, 0) == tiles[64] and layer_pixel(tiles, [1] * 4096, 511, 511, 1, 1) == tiles[64]
    assert struct.unpack("<BHH", set_scroll(0, -1, 513)[2:]) == (0, 0xFFFF, 513)
    g.feed(set_scroll(0, -1, 513))
    assert g.scroll[0] == [511, 1]
    # layer 1 is in front, index 0 shows what is behind, layer 5 does nothing and sets no flag
    g.feed(write_tile_map(1, 0, [map_entry(2)]) + set_tile_layer(3) + set_scroll(0, 0, 0) + set_scroll(1, 0, 0))
    g.tmap[0][0] = map_entry(1)
    g.tiles[2 * 64 + 9] = 0
    g.vblank()
    s = g.screen()
    assert s[0] == tiles[2 * 64] and s[1] == tiles[2 * 64 + 1]
    g.errors = 0
    g.feed(set_scroll(2, 7, 7) + write_tile_map(2, 0, [1]) + set_tile_layer(0xFC))
    g.vblank()
    assert g.errors == 0 and g.scroll == [[0, 0], [0, 0]] and g.tmap[0][0] == map_entry(1) and g.live_enable == 0
    # dropped bytes and entries past the end; wrong lengths
    g.feed(upload_tiles(16380, bytes(range(1, 9))) + write_tile_map(1, 4094, [1, 2, 3, 4]))
    assert bytes(g.tiles[16380:]) == bytes([1, 2, 3, 4]) and g.tiles[0] == 0 and g.tmap[1][4094:] == [1, 2] and g.tmap[1][0] == map_entry(2)
    g.feed(packet(UPLOAD_TILES, b"\x00") + packet(WRITE_TILE_MAP, bytes(4)) + packet(WRITE_TILE_MAP, bytes(2)) + packet(SET_SCROLL, bytes(4))
           + packet(SET_TILE_LAYER, bytes(2)))
    assert g.errors == ERR_BAD_LEN and g.ignored == 5 and g.feed(get_info())[8] == ERR_BAD_LEN
    g.feed(packet(UPLOAD_TILES, bytes(2)) + packet(WRITE_TILE_MAP, bytes(3)))          # empty data is legal
    assert g.ignored == 5
    # a frame start cuts a packet: the data that arrived stays written
    g = Gpu()
    g.feed(upload_tiles(100, bytes([9, 8, 7, 6]))[:2 + 2 + 3])
    g.feed(nop(), sof=True)
    assert bytes(g.tiles[100:104]) == bytes([9, 8, 7, 0]) and g.errors == ERR_TRUNC
    g.feed(write_tile_map(0, 10, [0x111, 0x222, 0x333])[:2 + 3 + 3 + 1], sof=True)    # two whole entries and half of the third
    g.feed(nop(), sof=True)
    assert g.tmap[0][10:13] == [0x111, 0x222, 0] and g.tmap[0][13] == 0
    # Chapter 5.4: sprites.
    g = Gpu()
    img = bytes(1 + (i % 15) for i in range(256))                     # 16 x 16, no transparent pixel
    g.feed(b"".join(upload_packets(0, 16, 16, img)) + b"".join(upload_packets(1, 8, 8, bytes(64))))
    assert sprite_entry(-2, 300, 1, 2, 5) == bytes([0xFE, 0xFF, 0x2C, 0x01, 1, 2, 5, 0])
    assert set_sprite(3, 1, 2, 4) == bytes([0x20, 9, 3, 1, 0, 2, 0, 4, 0, SPRITE_ON, 0])
    assert move_sprite(7, -1, 5) == bytes([0x21, 5, 7, 0xFF, 0xFF, 5, 0]) and sprite_ctrl(True) == bytes([0x22, 1, 1])
    g.feed(set_sprite(0, 10, 20, 0) + sprite_ctrl(True))
    assert g.screen() == bytes(FB_SIZE)                              # shadows: nothing until vblank()
    g.vblank()
    s = g.screen()
    assert s[20 * W + 10] == 1 and s[35 * W + 25] == img[255] and s[20 * W + 9] == 0 and s[36 * W + 10] == 0
    g.feed(move_sprite(0, 8, 21))                                    # only x and y change
    assert g.sprites[:8] == sprite_entry(8, 21, 0) and g.screen() == s
    g.vblank()
    s = g.screen()
    assert s[21 * W + 8] == 1 and s[20 * W + 10] == 0
    g.feed(set_sprite(1, 12, 21, 0, 1, SPRITE_ON | SPRITE_FLIP_X))   # behind sprite 0; palette 1; mirrored
    g.vblank()
    s = g.screen()
    assert s[21 * W + 8] == 1 and s[21 * W + 27] == img[0] + 16 and s[21 * W + 24] == img[3] + 16 and s[21 * W + 12] == img[4]
    g.feed(set_sprite(2, -8, -4, 0, 0, SPRITE_ON | SPRITE_FLIP_Y) + set_sprite(3, 316, 230, 0) + set_sprite(4, 0, 0, 1) + set_sprite(70, 0, 0, 0))
    g.vblank()
    s = g.screen()
    assert s[0] == img[(15 - 4) * 16 + 8] and s[7 * W + 7] == img[(15 - 11) * 16 + 15] and s[8] == 0
    assert s[230 * W + 316] == img[0] and s[239 * W + 319] == img[9 * 16 + 3]
    assert g.errors == 0                                             # asset 1 is 8 x 8: sprite 4 draws nothing, no flag
    # 16 per line: 20 sprites on one line, the first 16 survive; off-screen ones count too
    g = Gpu()
    g.feed(b"".join(upload_packets(0, 16, 16, img)) + sprite_ctrl(True))
    for i in range(20):
        g.feed(set_sprite(i, -200 if i == 5 else i * 16, 100, 0))
    g.vblank()
    assert g.sprite_lines(100) == list(range(16)) and g.sprite_lines(99) == [] and g.sprite_lines(115) == list(range(16))
    assert g.sprite_lines(116) == []
    # Chapter 5.5: the compositor.
    assert set_priority() == bytes([0x30, 1, 0xE4]) and priority_byte(3, 2, 1, 0) == 0x1B
    assert set_backdrop(7) == bytes([0x31, 1, 7]) and fb_enable(False) == bytes([0x32, 1, 0])
    assert map_entry(5, True, False, 2) == 0x905 and sprite_pri(2) == 0x10
    opq = lambda item, idx: (True, item, idx)
    none = (False, 0, 0)
    assert compose_pixel([opq(0, 1), opq(0, 2), none, none]) == 2               # layer 0 over the framebuffer
    assert compose_pixel([opq(0, 1), opq(0, 2), opq(0, 3), opq(0, 4)]) == 4     # default order: sprites on top
    assert compose_pixel([opq(3, 1), opq(3, 2), opq(3, 3), opq(0, 4)]) == 4     # distinct layer priorities: items ignored
    assert compose_pixel([opq(0, 1), opq(0, 2), opq(0, 3), opq(0, 4)], priority_byte(3, 0, 1, 2)) == 1
    assert compose_pixel([opq(0, 1), opq(0, 2), opq(0, 3), opq(0, 4)], 0) == 4   # all equal: the later source
    assert compose_pixel([opq(0, 1), opq(0, 2), opq(1, 3), opq(0, 4)], priority_byte(0, 0, 0, 0)) == 3    # equal layer priority: the item decides
    assert compose_pixel([opq(0, 1), opq(0, 2), opq(1, 3), opq(1, 4)], priority_byte(0, 0, 0, 0)) == 4    # equal items: the later source
    assert compose_pixel([none] * 4, backdrop=9) == 9
    g = Gpu()
    g.feed(b"".join(upload_packets(0, 16, 16, img)) + write_tile_map(0, 0, [map_entry(1, pri=1)])
           + upload_tiles(64, bytes([7] * 64)) + set_tile_layer(1) + sprite_ctrl(True) + set_sprite(0, 0, 0, 0, 0, SPRITE_ON)
           + set_priority(fb=0, l0=2, l1=1, sp=2))
    g.vblank()
    assert g.screen()[0] == 7 and g.screen()[8 * W] == img[8 * 16]               # equal layer priority: the tile (item 1) hides the sprite (item 0) on its 8 x 8
    g.feed(set_sprite(0, 0, 0, 0, 0, SPRITE_ON | sprite_pri(1)))
    g.vblank()
    assert g.screen()[0] == img[0]                                               # equal items: the sprite is the later source
    g.feed(set_priority(sp=0, l0=3) + set_backdrop(200) + fb_enable(False))
    assert g.screen()[0] == img[0]                                               # shadows
    g.vblank()
    assert g.screen()[0] == 7 and g.screen()[100 * W + 100] == 200 and g.live_fb_on == 0
    assert g.feed(packet(0x33, b"") + packet(SET_PRIORITY, b"")) == b"" and g.errors == ERR_BAD_OP | ERR_BAD_LEN
    # Chapter 5.6: WRITE_TILE_COLUMN and WRITE_TILE_ROW.
    g = Gpu()
    col = [0x100 + k for k in range(64)]
    g.feed(write_tile_column(1, 5, col) + write_tile_row(0, 9, col))
    assert len(write_tile_column(0, 0, col)) == 132 and write_tile_column(0, 63, col)[:4] == bytes([0x40, 130, 0, 63])
    assert [g.tmap[1][k * 64 + 5] for k in range(64)] == col and g.tmap[1][6] == 0       # a column: stride 64
    assert g.tmap[0][9 * 64:10 * 64] == col and g.tmap[0][8 * 64 + 5] == 0                # a row: 64 in a line
    g.feed(write_tile_column(2, 0, col) + write_tile_row(0, 64, col) + write_tile_column(0, 255, col))
    assert g.errors == 0 and not any(g.tmap[0][:9 * 64]) and g.tmap[1][0] == 0            # out of range: nothing, no flag
    assert g.feed(packet(WRITE_TILE_COLUMN, bytes(129)) + packet(WRITE_TILE_ROW, bytes(131)) + packet(WRITE_TILE_ROW, b"")) == b""
    assert g.errors == ERR_BAD_LEN and g.ignored == 3 and g.feed(packet(0x42, b"")) == b"" and g.errors == ERR_BAD_LEN | ERR_BAD_OP
    g.feed(write_tile_row(1, 3, [7] * 64)[:2 + 2 + 5])
    g.feed(get_info(), sof=True)
    assert g.tmap[1][3 * 64:3 * 64 + 2] == [7, 7] and g.tmap[1][3 * 64 + 2] == 0 and g.errors & ERR_TRUNC   # a cut packet keeps whole entries
    assert Gpu().feed(get_info())[2:4] == bytes([0xFF, 0x7F]) and CAPS_STREAMING == 0x80
    # Chapter 5.7: display lists.
    g = Gpu()
    body = fill_rect(0, 0, 9, 9, 5) + plot(20, 20, 7)
    g.feed(b"".join(list_packets(2, body)))
    assert g.list_len[2] == len(body) and bytes(g.lists[2][:len(body)]) == body and g.list_len[0] == 0
    g.feed(run_list(2))
    assert g.fb[0] == 5 and g.fb[20 * W + 20] == 7 and g.list_runs == 1 and g.errors == 0
    g.feed(write_list_word(2, len(body) - 3, 0x0909))                 # the PLOT's x and y low bytes are at -5 and -3
    g.feed(write_list_word(2, 2 + 8, 0x0063))                         # FILL_RECT color byte (offset 10) and the next byte
    assert g.lists[2][10] == 0x63 and g.lists[2][11] == 0x00
    g.feed(write_list_word(2, 511, 0xAABB) + write_list_word(8, 0, 1) + write_list_word(2, 512, 1) + run_list(9) + run_list(5) + define_list(8)
           + append_list(8, b"\x00") + set_auto_list(8))
    assert g.lists[2][511] == 0xBB and g.errors == 0 and bytes(g.lists[3]) == bytes(512)       # no spill into list 3, no flags
    g.feed(define_list(2) + append_list(2, bytes(300)[:254]) + append_list(2, bytes(254)) + append_list(2, bytes(254)))
    assert g.list_len[2] == 512
    g.feed(define_list(4) + append_list(4, nop() + plot(1, 1, 1)[:3]))        # ends inside a PLOT
    g.feed(run_list(4))
    assert g.errors == ERR_TRUNC and g.list_len[4] == 5
    g.feed(clear_errors())
    bad = get_info() + fb_crc() + packet(UPLOAD_ASSET, bytes(5)) + clear_errors(1) + run_list(4) + packet(UPLOAD_TILES, bytes(2)) + write_list_word(0, 0, 0) + plot(3, 3, 9)
    g.feed(define_list(5) + append_list(5, bad))
    assert g.feed(run_list(5)) == b"" and g.errors == ERR_BAD_OP and g.fb[3 * W + 3] == 9    # all skipped, the PLOT still ran
    g.feed(clear_errors() + define_list(6) + append_list(6, flip() + packet(0x77, b"\x01")))
    g.feed(run_list(6))
    assert g.flips == 1 and g.errors == ERR_BAD_OP                                                  # FLIP is allowed; an unknown opcode is BAD_OP
    g.feed(set_auto_list(6))
    assert g.auto == 6
    g.feed(set_auto_list(200))
    assert g.auto == 6
    g.auto_run()
    assert g.flips == 2
    g.feed(set_auto_list(AUTO_OFF))
    g.auto_run()
    assert g.auto is None and g.flips == 2
    g.feed(append_list(0, b""))                                                  # APPEND_LIST with only an id: legal, appends nothing
    assert g.list_len[0] == 0
    g.feed(packet(APPEND_LIST, b""))
    assert g.errors & ERR_BAD_LEN
    g.feed(append_list(1, bytes([0x00, 0x00]))[:2 + 1 + 1], sof=False)
    g.feed(get_info(), sof=True)
    assert g.list_len[1] == 1 and g.errors & ERR_TRUNC                      # a cut APPEND keeps the bytes that arrived
    # Chapter 6.3: DRAW_TRI_SHADED.
    pk = draw_tri_shaded((10, 10, 100, 124), (10, 30, 60000, 248), (30, 10, 31000, 0), 2)
    assert len(pk) == 24 and pk[:2] == bytes([0x60, 22]) and pk[14] == 2 and pk[2:4] == bytes([10, 0])
    g = Gpu()
    g.feed(clear(9) + pk)
    assert g.tri_shaded_count == 1 and g.tri_shaded_drawn == len(raster_model.tri_pixels((10, 10), (10, 30), (30, 10), W, H))
    assert g.fb[10 * W + 10] == 2 * 32 + 16 and g.fb[29 * W + 10] == 2 * 32 + 30 and g.fb[10 * W + 30] == 9
    assert g.errors == 0
    g.feed(packet(DRAW_TRI_SHADED, bytes(21)) + packet(DRAW_TRI_SHADED, bytes(23)) + packet(0x7E, bytes(22)))
    assert g.errors == ERR_BAD_LEN | ERR_BAD_OP and g.tri_shaded_count == 1
    flat = draw_tri_shaded((5, 5, 7, 80), (5, 25, 7, 80), (25, 5, 7, 80), 8 + 3)         # ramp bits 7:3 are ignored
    g.feed(flat)
    assert g.fb[5 * W + 5] == 3 * 32 + 10
    assert [len(p) for p in ramp_palette(1, (0, 0, 0), (255, 255, 255))] == [6] * 32
    assert ramp_palette(1, (0, 0, 0), (255, 255, 255))[31] == set_palette(63, 0xFFFFFF)
    g = Gpu()                                                       # a list may hold shaded triangles
    g.feed(b"".join(list_packets(0, pk)) + run_list(0))
    assert g.tri_shaded_count == 1 and g.errors == 0
    # Chapter 6.4: the depth buffer.
    g = Gpu()
    A = [(10, 10, 100, 248), (60, 10, 100, 248), (10, 60, 100, 248)]
    B = [(10, 10, 100, 0), (60, 10, 100, 0), (10, 60, 100, 0)]            # same depths
    g.feed(clear_all(1) + set_depth(True, True) + draw_tri_shaded(*A, 0) + draw_tri_shaded(*B, 1))
    assert g.fb[11 * W + 11] == 31 and g.depth[11 * W + 11] == 100        # equal depth fails: the first stays
    assert g.errors == 0 and g.depth_rejected == g.tri_shaded_drawn
    g.feed(set_depth(False, False) + draw_tri_shaded(*B, 1))              # test off: painter's algorithm
    assert g.fb[11 * W + 11] == 32 + 0 and g.depth[11 * W + 11] == 100
    g.feed(clear_depth(5) + set_depth(True, False) + draw_tri_shaded(*A, 2))
    assert g.fb[11 * W + 11] == 32 and g.depth[11 * W + 11] == 5          # z 100 is not < 5; write was off
    g.feed(clear_depth() + draw_tri_shaded(*A, 2))
    assert g.fb[11 * W + 11] == 64 + 31 and g.depth[11 * W + 11] == 65535  # drawn, depth not written (test only)
    g.feed(set_depth(False, True) + draw_tri_shaded(*B, 3) + draw_tri(0, 0, 3, 0, 0, 3, 7))
    assert g.depth[11 * W + 11] == 100 and g.depth[1] == 65535 and g.fb[1] == 7        # DRAW_TRI never touches depth
    assert [len(x) for x in (set_depth(), clear_depth(), clear_all(0), get_stats(), clear_stats())] == [3, 4, 5, 2, 2]
    g = Gpu()
    g.feed(draw_tri(0, 0, 9, 0, 0, 9, 3) + draw_tri_shaded(*A, 0))
    st = parse_stats(g.feed(get_stats()))
    assert st == (g.tri_drawn + g.tri_shaded_drawn, g.tri_drawn + g.tri_shaded_drawn, 0) and g.feed(get_stats()) == struct.pack("<III", *st)
    g.feed(clear_stats())
    assert parse_stats(g.feed(get_stats())) == (0, 0, 0)
    g.feed(packet(SET_DEPTH, b"") + packet(CLEAR_DEPTH, bytes(3)) + packet(CLEAR_ALL, bytes(2)) + packet(GET_STATS, bytes(1)) + packet(0x7E, b""))
    assert g.errors == ERR_BAD_LEN | ERR_BAD_OP
    g = Gpu()
    g.feed(b"".join(list_packets(0, set_depth(True, True) + clear_all(0) + draw_tri_shaded(*A, 0) + clear_stats() + get_stats())) + run_list(0))
    assert g.errors == ERR_BAD_OP and g.tri_shaded_count == 1 and g.d_test == 1 and g.stat_cov == 0   # GET_STATS is not allowed in a list
    # Chapter 6.5: the vertex transform.
    g = Gpu()
    g.feed(b"".join(vertex_packets(0, [(0, 0, 100), (50, -20, 300), (0, 0, 1)])) + transform(0, 3))
    assert parse_vertex(g.feed(read_vertex(0))) == (160, 120, 65535 - 655, 0)       # the power-up matrix, f = 277, near = 1
    assert parse_vertex(g.feed(read_vertex(1)))[:2] == (160 + round(277 * 50 / 300), 120 + round(277 * 20 / 300))
    assert g.feed(read_vertex(2))[6] == 1 and g.feed(read_vertex(1024)) == b"" and g.feed(read_vertex(65535)) == b""
    assert g.errors == 0 and g.read_undefined == 0
    g.feed(set_matrix((0x4000, 0, 0, 0, 0x4000, 0, 0, 0, 0x4000, 0, 0, 50)) + set_projection(100, 10) + transform(0, 1))
    assert parse_vertex(g.feed(read_vertex(0))) == (160, 120, 65535 - round(65535 * 10 / 150), 0)
    g.feed(packet(UPLOAD_VERTICES, bytes(7)) + packet(UPLOAD_VERTICES, bytes(1)) + packet(UPLOAD_VERTICES, b"")
           + packet(SET_MATRIX, bytes(23)) + packet(SET_PROJECTION, bytes(5)) + packet(TRANSFORM, bytes(3)) + packet(READ_VERTEX, bytes(1)))
    assert g.errors == ERR_BAD_LEN and g.ignored == 7
    g.feed(clear_errors() + upload_vertices(1022, [(1, 2, 3)] * 4) + transform(1023, 100) + transform(1024, 5))
    assert g.vsrc[1022] == (1, 2, 3) and g.vsrc[0] == (0, 0, 100) and g.vdst[1023] is not None and g.vdst[1022] is None and g.errors == 0
    g.feed(read_vertex(1022))
    assert g.read_undefined == 1
    g = Gpu()
    g.feed(b"".join(list_packets(1, set_matrix(vertex_model.identity()) + transform(0, 1) + read_vertex(0)
                                 + upload_vertices(0, [(1, 1, 1)]))) + run_list(1))
    assert g.errors == ERR_BAD_OP and g.ignored == 2 and g.vsrc[0] is None          # READ_VERTEX and UPLOAD_VERTICES are not allowed in a list
    g = Gpu()
    pk = upload_vertices(5, [(1, 2, 3), (4, 5, 6), (7, 8, 9)])
    g.feed(pk[:2 + 2 + 6 + 6 + 3])
    g.feed(plot(1, 1, 1), sof=True)                                                   # a frame start cuts the packet: whole vertices kept
    assert g.errors == ERR_TRUNC and g.vsrc[5:8] == [(1, 2, 3), (4, 5, 6), None]
    # Chapter 6.6: the index buffer, DRAW_MESH, culling
    g = Gpu()
    ents = mesh_model.cube_entries()
    g.feed(b"".join(vertex_packets(0, mesh_model.CUBE_VERTS)) + b"".join(index_packets(0, ents)))
    assert g.idx[0] is not None and g.idx[11] is not None and g.idx[12] is None and g.errors == 0
    g.feed(set_matrix(mesh_model.rot_matrix(0, 0, 0, 0, 0, 300)) + transform(0, 8) + set_mesh_mode(mesh_model.CULL_BACK) + draw_mesh(0, 12))
    assert parse_mesh_stats(g.feed(get_mesh_stats())) == (10, 0, 2) and g.tri_shaded_count == 2 and g.mesh_undefined == 0
    g.feed(clear_mesh_stats() + set_mesh_mode(mesh_model.CULL_FRONT) + draw_mesh(0, 12))
    assert parse_mesh_stats(g.feed(get_mesh_stats())) == (2, 0, 10)
    g.feed(clear_mesh_stats() + set_mesh_mode(mesh_model.CULL_BOTH) + draw_mesh(10, 2) + draw_mesh(512, 4) + draw_mesh(0, 0))
    assert parse_mesh_stats(g.feed(get_mesh_stats())) == (2, 0, 0) and g.errors == 0
    g.feed(clear_mesh_stats() + set_mesh_mode(0) + set_matrix(mesh_model.rot_matrix(0, 0, 0, 0, 0, 40)) + set_projection(277, 8) + transform(0, 8) + draw_mesh(0, 12))
    st = parse_mesh_stats(g.feed(get_mesh_stats()))
    assert st[0] == 0 and st[1] > 0 and sum(st) == 12                            # the cube pokes through the near plane
    g.feed(packet(UPLOAD_INDICES, bytes(9)) + packet(UPLOAD_INDICES, bytes(11)) + packet(UPLOAD_INDICES, b"") + packet(SET_MESH_MODE, b"")
           + packet(SET_MESH_MODE, bytes(2)) + packet(DRAW_MESH, bytes(3)) + packet(DRAW_MESH, bytes(5)) + packet(GET_MESH_STATS, bytes(1)) + packet(CLEAR_MESH_STATS, bytes(1)))
    assert g.errors == ERR_BAD_LEN and g.ignored == 9
    g = Gpu()
    g.feed(b"".join(list_packets(1, set_mesh_mode(1) + clear_mesh_stats() + draw_mesh(0, 1) + get_mesh_stats() + upload_indices(0, [bytes(8)]))) + run_list(1))
    assert g.errors == ERR_BAD_OP and g.ignored == 2 and g.idx[0] is None and g.mesh_mode == 1   # GET_MESH_STATS and UPLOAD_INDICES are not allowed in a list
    g = Gpu()
    pk = upload_indices(509, [mesh_model.pack_entry(1, 2, 3, 4, 5)] * 5)
    g.feed(pk)
    assert g.idx[508] is None and g.idx[509] is not None and g.idx[511] is not None and g.errors == 0       # entries past 511 are dropped
    pk = upload_indices(5, [mesh_model.pack_entry(1, 2, 3, 4, 5), mesh_model.pack_entry(2, 3, 4, 5, 6), mesh_model.pack_entry(3, 4, 5, 6, 7)])
    g.feed(pk[:2 + 2 + 8 + 8 + 3])
    g.feed(plot(1, 1, 1), sof=True)                                                   # a frame start cuts the packet: whole entries kept
    assert g.errors == ERR_TRUNC and g.idx[5:8] == [(1, 2, 3, 4, 5), (2, 3, 4, 5, 6), None]
    # Chapter 6.7: normals, SET_LIGHT, lighting in TRANSFORM
    g = Gpu()
    g.feed(upload_vertices(0, [(0, 0, 100), (10, 0, 100), (0, 10, 100)]) + upload_normals(0, [(0, 0, -16384), (0, 0, 16384), (16384, 0, 0)])
           + set_matrix(vertex_model.identity()) + transform(0, 3))
    assert g.errors == 0 and g.vlit[:3] == [0, 0, 0] and g.nsrc[3] is None                    # lighting is off at power-up: byte 7 stays 0
    assert g.feed(read_vertex(0))[7] == 0
    g.feed(set_light((0, 0, -16384), 4096) + transform(0, 3))
    assert g.vlit[:3] == [248 + 62, 62, 62] or g.vlit[:3] == [255, 62, 62], g.vlit[:3]        # facing the light: 248 + ambient, clamped to 255
    assert g.feed(read_vertex(0))[7] == 255 and g.feed(read_vertex(1))[7] == 62
    g.feed(set_light((0, 0, -16384), 0, enable=False) + transform(0, 3))
    assert g.vlit[:3] == [0, 0, 0]
    g.feed(set_light((0, 0, -16384), 0) + transform(0, 4))
    assert g.vlit[3] is None and g.lit_undefined == 0                                         # vertex 3 was never uploaded: no record, no intensity
    g.feed(upload_vertices(3, [(0, 0, 50)]) + transform(3, 1))
    assert g.vlit[3] is None and g.lit_undefined == 1 and g.vdst[3] is not None               # a vertex without a normal: record defined, byte 7 not
    g.feed(packet(UPLOAD_NORMALS, bytes(7)) + packet(UPLOAD_NORMALS, b"") + packet(SET_LIGHT, bytes(8)) + packet(SET_LIGHT, bytes(10)))
    assert g.errors == ERR_BAD_LEN and g.ignored == 4
    g.feed(clear_errors() + b"".join(list_packets(3, set_light((1, 2, 3), 4, True) + upload_normals(0, [(1, 1, 1)]) + transform(0, 1))) + run_list(3))
    assert g.errors == ERR_BAD_OP and g.ignored == 5 and g.light_dir == (1, 2, 3) and g.nsrc[0] == (0, 0, -16384)     # UPLOAD_NORMALS is not allowed in a list
    pk = upload_normals(5, [(1, 2, 3), (4, 5, 6), (7, 8, 9)])
    g.feed(pk[:2 + 2 + 6 + 6 + 3])
    g.feed(plot(1, 1, 1), sof=True)
    assert g.nsrc[5:8] == [(1, 2, 3), (4, 5, 6), None]
    g = Gpu()
    g.feed(upload_normals(1022, [(1, 1, 1)] * 4) + upload_normals(65535, [(2, 2, 2)]))        # slots past 1023 are dropped, no flag
    assert g.nsrc[1022] == (1, 1, 1) and g.nsrc[1023] == (1, 1, 1) and g.errors == 0
    print("gpu_model self-check ok")
