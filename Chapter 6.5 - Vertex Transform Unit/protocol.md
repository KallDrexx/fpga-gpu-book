# GPU protocol, version 1

This is the reference that host code, the Python model and the Verilog all follow. Keep it in the repository as `docs/protocol.md`. Later chapters add commands to its tables; the framing and parser rules stay as they are.

## 1. Links and frames

The GPU has two command links. Both feed one parser through one FIFO.

| Link | Framing | Replies |
| --- | --- | --- |
| SPI mode 0, MSB first | A **frame** is one `CS` low period. | Read with read frames (section 7). |
| UART, 8N1 | None: a stream of bytes. | Sent back on `TX`. |

- **One link at a time.** A UART byte that arrives while `CS` is low is dropped.
- **Back-pressure.** `READY` high means the FIFO has room for at least 257 bytes. The master may send one largest packet, 257 bytes, per `READY` check. `uart_cts` is `~READY`.
- **Frames carry whole packets.** The master puts complete packets in a frame and raises `CS` after the last one. A packet split over two frames is lost: the second frame's first byte abandons the first half (section 4).
- A frame whose first byte is `FF` is a **read frame**. None of its bytes reach the parser. It reads status and replies (section 7).

## 2. Packets

```
[opcode][len][payload: len bytes]        2 + len bytes, 2 to 257
```

- `len` counts payload bytes only, 0 to 255.
- **Byte order:** every multi-byte field is little endian, low byte first.
- **Pixel coordinates:** signed 16-bit. The origin is the top-left pixel of the 320x240 screen; x grows right, y grows down. Off-screen values are legal and are clipped.
- **Colors:** an 8-bit palette index. A palette entry is 24-bit RGB, sent R, G, B.
- Commands run **in the order sent**, one at a time. A reply reflects every earlier command and none of the later ones.
- **A packet is always consumed whole.** An unknown opcode, or a known one with the wrong `len`, is read to its end, changes nothing, makes no reply and sets an error flag (section 9). `NOP` accepts any length.

## 3. Opcodes

`len` is the payload length the command requires. "Reply" is the number of reply bytes.

| Op | Name | len | Payload | Reply | Chapter |
| --- | --- | --- | --- | --- | --- |
| `00` | `NOP` | any | ignored | | 4.2 |
| `01` | `SET_PALETTE` | 4 | index, R, G, B | | 4.2 |
| `02` | `CLEAR` | 1 | color | | 4.2 |
| `03` | `PLOT` | 5 | x, y, color; off-screen ignored | | 4.2 |
| `04` | `FB_CRC` | 0 | | 4: zlib CRC-32 of the back buffer (addresses 0 to 76,799, y * 320 + x), low byte first | 4.2 |
| `05` | `FILL_RECT` | 9 | x0, y0, x1, y1, color; corners inclusive, either order | | 4.3 |
| `06` | `DRAW_LINE` | 9 | x0, y0, x1, y1, color; endpoints inclusive | | 4.4 |
| `07` | `FLIP` | 0 | swap buffers in the next vertical blanking; later commands wait | | 4.5 |
| `08` | `DRAW_TRI` | 13 | x0, y0, x1, y1, x2, y2, color; top-left rule | | 4.7 |
| `09` | `UPLOAD_ASSET` | 5 + n | id, w, h, offset (16-bit), n data bytes, n at most 250; w = h = 0 with n = 0 forgets all assets | | 4.8 |
| `0A` | `BLIT` | 6 | id, x, y, flags (bit 0 `FLIP_X`, bit 1 `FLIP_Y`, bit 2 `OPAQUE`); index 0 is transparent unless `OPAQUE` | | 4.8 |
| `0B` | `GET_INFO` | 0 | | 9: section 8 | 5.2 |
| `0C` | `CLEAR_ERRORS` | 1 | mask (section 9) | | 5.2 |
| `0D`-`0F` | spare | | | | |
| `10` | `SET_TILE_LAYER` | 1 | mask: bit 0 shows layer 0, bit 1 shows layer 1 (bits 7:2 ignored); at the next vertical blanking | | 5.3 |
| `11` | `SET_SCROLL` | 5 | layer, x (16-bit), y (16-bit); taken modulo 512; layer above 1: nothing; at the next vertical blanking | | 5.3 |
| `12` | `UPLOAD_TILES` | 2 + n | offset (16-bit), n data bytes, n at most 253; bytes at address 16,384 and up dropped | | 5.3 |
| `13` | `WRITE_TILE_MAP` | 3 + 2k | layer, offset (16-bit, in entries), k 16-bit entries, k at most 126; entries from 4,096 up and layers above 1 dropped | | 5.3 |
| `14`-`1F` | spare (tile layers) | | | | |
| `20` | `SET_SPRITE` | 9 | id (0 to 63), then the 8 bytes of table entry id: x (16-bit), y (16-bit), asset, palette, flags, reserved; id 64 or more: nothing; shadow table (section 11) | | 5.4 |
| `21` | `MOVE_SPRITE` | 5 | id, x (16-bit), y (16-bit): rewrites bytes 0 to 3 of entry id and nothing else; id 64 or more: nothing; shadow table | | 5.4 |
| `22` | `SPRITE_CTRL` | 1 | bit 0: 1 shows the sprites, 0 hides all; other bits ignored; at the next vertical blanking | | 5.4 |
| `23`-`2F` | spare (sprites) | | | | |
| `30` | `SET_PRIORITY` | 1 | byte `{sp[1:0], l1[1:0], l0[1:0], fb[1:0]}`: the layer priority 0 to 3 of the sprites, tile layer 1, tile layer 0 and the framebuffer; shadow (section 12) | | 5.5 |
| `31` | `SET_BACKDROP` | 1 | palette index shown where no source is opaque; shadow | | 5.5 |
| `32` | `FB_ENABLE` | 1 | bit 0: 1 shows the framebuffer, 0 makes it transparent (other bits ignored); shadow | | 5.5 |
| `33`-`3F` | spare (compositor) | | | | |
| `40` | `WRITE_TILE_COLUMN` | 130 | layer, column (0 to 63), 64 map entries (16-bit each, 128 bytes): entry k goes to map row k of that column; layer above 1 or column above 63: nothing; immediate (section 13) | | 5.6 |
| `41` | `WRITE_TILE_ROW` | 130 | layer, row (0 to 63), 64 map entries: entry k goes to map column k of that row; same rules; immediate | | 5.6 |
| `42`-`4F` | spare (streamed maps) | | | | |
| `50` | `DEFINE_LIST` | 1 | id (0 to 7): makes list id empty (length 0); id 8 or more: nothing | | 5.7 |
| `51` | `APPEND_LIST` | 1 + n | id (0 to 7), then n bytes (n at most 254) added to the end of list id, up to 512 bytes in all; the rest is dropped; id 8 or more: nothing; immediate (section 14) | | 5.7 |
| `52` | `RUN_LIST` | 1 | id (0 to 7): run list id now, then the next host command; an empty list or id 8 or more: nothing | | 5.7 |
| `53` | `WRITE_LIST_WORD` | 5 | id, byte offset (16-bit), value (16-bit): writes the two bytes of value, low byte first, at offset and offset + 1 of list id; an offset of 512 or more is dropped, byte by byte; id 8 or more: nothing; the length does not change | | 5.7 |
| `54` | `SET_AUTO_LIST` | 1 | id (0 to 7): run list id at every start of vertical blanking; `FF`: stop; any other value: nothing | | 5.7 |
| `55`-`5F` | spare (display lists) | | | | |
| `60` | `DRAW_TRI_SHADED` | 22 | x0, y0, x1, y1, x2, y2 (signed 16-bit), ramp, z0, z1, z2 (unsigned 16-bit), i0, i1, i2 (8 bits, Q5.3): the pixels of `DRAW_TRI`, each written as color `(ramp & 7) * 32 + shade`, the shade being the interpolated intensity rounded and clamped to 0 to 31; the depth is interpolated and, as `SET_DEPTH` says, tested and stored (sections 15 and 16) | | 6.3 |
| `61` | `SET_DEPTH` | 1 | mode: bit 0 `TEST` (draw a shaded pixel only if its z is less than the stored depth), bit 1 `WRITE` (store the z of every pixel drawn); bits 7:2 ignored; power-up 0; only `DRAW_TRI_SHADED` is affected (section 16) | | 6.4 |
| `62` | `CLEAR_DEPTH` | 2 | z (unsigned 16-bit): every stored depth becomes z; the colors stay | | 6.4 |
| `63` | `CLEAR_ALL` | 3 | color, z (16-bit): `CLEAR` and `CLEAR_DEPTH` in one pass | | 6.4 |
| `64` | `GET_STATS` | 0 | | 12: three 32-bit counters, low byte first: triangle pixels produced, pixels written, clocks spent on triangles (section 16) | 6.4 |
| `65` | `CLEAR_STATS` | 0 | all three counters become 0 | | 6.4 |
| `66`-`6F` | spare (3D) | | | | |
| `70` | `UPLOAD_VERTICES` | 2 + 6k | first slot (16-bit), then k vertices of x, y, z (signed 16-bit), k = 1 to 42: vertex j goes to slot first + j; slots 1,024 and up dropped (section 17) | | 6.5 |
| `71` | `SET_MATRIX` | 24 | m00, m01, m02, m10, m11, m12, m20, m21, m22 (signed 16-bit, Q2.14: 16,384 is 1.0), then tx, ty, tz (signed 16-bit, model units): the transform for later `TRANSFORM`s; power-up: the identity | | 6.5 |
| `72` | `SET_PROJECTION` | 4 | f (signed 16-bit, focal length in pixels), near (unsigned 16-bit, model units); power-up f = 277, near = 1 | | 6.5 |
| `73` | `TRANSFORM` | 4 | first slot (16-bit), count (16-bit): transform vertex slots first to first + count - 1 (clipped at slot 1,023) into the transformed-vertex buffer; later commands wait | | 6.5 |
| `74` | `READ_VERTEX` | 2 | slot (16-bit; 1,024 or more: no reply) | 8: sx, sy (signed 16-bit), depth (unsigned 16-bit), flags, reserved 0 (section 17) | 6.5 |
| `75`-`7F` | spare (3D) | | | | |

All coordinates are signed 16-bit. Drawing commands write the back buffer. The exact pixels (clipping, the line algorithm, the top-left rule, asset allocation) are defined by `host/gpu_model.py`.

**Reserved ranges.** Each later chapter gets a block of 16 opcodes. Until a chapter defines its commands, they are **unknown opcodes** (skipped, `BAD_OP`). The names below are a plan, not a promise: the chapter that builds a feature fixes its opcodes and payloads, and may rename, add or drop commands.

| Range | For | Planned commands |
| --- | --- | --- |
| `10`-`1F` | Tile layers (5.3): `10` to `13` defined, `14`-`1F` spare | section 10 |
| `20`-`2F` | Sprite table (5.4): `20` to `22` defined, `23`-`2F` spare | section 11 |
| `30`-`3F` | Compositor (5.5): `30` to `32` defined, `33`-`3F` spare | section 12 |
| `40`-`4F` | Streamed maps (5.6): `40` and `41` defined, `42`-`4F` spare | section 13 |
| `50`-`5F` | Display lists (5.7): `50` to `54` defined, `55`-`5F` spare | section 14 |
| `60`-`6F` | 3D (Phase 6): `60` to `65` defined, `66`-`6F` spare | sections 15 and 16 |
| `70`-`7F` | 3D vertex block (6.5): `70` to `74` defined, `75`-`7F` spare | section 17; planned: `DRAW_MESH` (with an index buffer) |
| `80`-`FF` | **Never assigned.** Tests use them as unknown opcodes. `FF` can never be an opcode, because a frame starting with `FF` is a read frame. | |

Every payload must fit in 255 bytes, so larger data (a 128-byte map column, a display list, a vertex buffer) goes in packets of at most 255 bytes.

Rules for adding a command:

1. Take the next free opcode in its range. Never reuse or renumber one.
2. Once a chapter has defined a command, its length rule never changes. A new layout is a new opcode.
3. Set the feature's capability bit (section 8), so a host can ask whether the command exists.
4. The protocol version changes only if framing or an existing command changes incompatibly. Adding commands does not change it.

## 4. Frame starts reset the parser

**The first byte that an SPI frame puts into the FIFO starts a new packet**, whatever the parser was doing. So does **a UART byte that follows SPI bytes** (the link changed). If the parser was in the middle of a packet, that packet is dropped: nothing it carried is executed, no reply is made, and `TRUNC` is set. A complete packet is never affected.

This has the same effect as "raising `CS` resets the parser": a partial packet does nothing while it waits for more bytes, so dropping it when the next byte arrives cannot be told apart from dropping it at the `CS` rise. Frames that put no byte into the FIFO (empty frames and read frames) change nothing, so a UART packet in progress continues past them.

**The exception: streamed commands.** `UPLOAD_ASSET` acts on its header once its first five payload bytes have arrived, and writes data bytes as they arrive. A truncated upload therefore leaves the asset allocated and the bytes that arrived written. Re-send the whole image; with the same id and size it replaces the old one in place. `UPLOAD_TILES`, `WRITE_TILE_MAP`, `WRITE_TILE_COLUMN`, `WRITE_TILE_ROW` and `APPEND_LIST` are streamed the same way (sections 10, 13 and 14): a truncated one leaves the bytes, or whole map entries, that arrived written (for `APPEND_LIST`, appended).

To recover from any lost state: raise `CS`, wait for `READY`, send the next frame. It starts cleanly. Complete commands already in the FIFO still run, and a command that is running (a `CLEAR`, a `FLIP` waiting for vertical blanking) is not interrupted.

## 5. UART

The UART has no frames and the hardware does not resynchronize it: there is no break detector or timeout. A UART host must keep its packets whole. To recover from a lost byte, send one SPI command frame (a `NOP`, `00 00`, is enough), or press the reset button.

## 6. Parser rules

- Every packet takes exactly `2 + len` bytes from the FIFO, whatever the opcode, unless a frame start (section 4) cuts it short. While a display list runs the bytes come from the list instead (section 14) and nothing is taken from the FIFO.
- Every engine state ends after a bounded number of clocks, except two that wait on purpose: `FLIP` waits for vertical blanking, and a reply waits for room in the reply queue (section 7) or the UART.
- A long command always finishes. Nothing on the link aborts a running command, a running list included.

## 7. Replies and the MISO bytes

Replies are raw bytes with no header, in command order: 4 bytes for `FB_CRC`, 9 for `GET_INFO`, 12 for `GET_STATS`, 8 for `READ_VERTEX`. They go to the link that sent the most recent byte written into the FIFO.

- **UART:** sent on `TX` as they are produced.
- **SPI:** replies wait in a 16-byte queue and are read with read frames. Every frame returns on `MISO`:

| MISO byte | Command frame | Read frame (first MOSI byte `FF`) |
| --- | --- | --- |
| 0 | status | status |
| 1 | count: reply bytes queued when `CS` fell (0 to 16) | count |
| 2, 3, ... | `00` | the first `count` reply bytes, oldest first, then `00` |

A reply queue that stays full holds the engine; the FIFO then fills and `READY` falls. Read frames need no `READY`, so the host can always empty the queue.

**Status byte:**

| Bit | Name | Meaning |
| --- | --- | --- |
| 7:6 | | `01`, so a stuck `MISO` is noticed |
| 5 | | reserved, 0 |
| 4 | `OVERFLOW` | the same flag as `OVERFLOW` in `GET_INFO` (section 9) |
| 3 | `VBLANK` | the `VSYNC` pin |
| 2 | `REPLY` | at least one reply byte is queued |
| 1 | | reserved, 0 |
| 0 | `READY` | the `READY` pin |

A healthy idle GPU shows `0x41`.

## 8. GET_INFO

`GET_INFO` (`0B`, length 0) replies with **9 bytes**:

| Byte | Meaning | Value in Chapter 6.5 |
| --- | --- | --- |
| 0 | magic, ASCII `G` | `47` |
| 1 | protocol version | `01` |
| 2, 3 | capabilities, 16-bit | `FF 1F` (`gpu_link`), `0F 00` (`uart_gpu`) |
| 4, 5 | screen width, 16-bit | `40 01` (320) |
| 6, 7 | screen height, 16-bit | `F0 00` (240) |
| 8 | error flags (section 9) | `00` after power-up |

So a fresh GPU answers `47 01 FF 1F 40 01 F0 00 00`. `GET_INFO` changes no flag.

**Capability bits:**

| Bit | Name | Meaning |
| --- | --- | --- |
| 0 | `DRAW` | `SET_PALETTE`, `CLEAR`, `PLOT`, `FB_CRC`, `FILL_RECT`, `DRAW_LINE` |
| 1 | `FLIP` | `FLIP` |
| 2 | `TRI` | `DRAW_TRI` |
| 3 | `BLIT` | `UPLOAD_ASSET`, `BLIT` |
| 4 | `TILES` | `SET_TILE_LAYER`, `SET_SCROLL`, `UPLOAD_TILES`, `WRITE_TILE_MAP`, and the two tile layers in the video (5.3) |
| 5 | `SPRITES` | `SET_SPRITE`, `MOVE_SPRITE`, `SPRITE_CTRL`, and the 64 sprites in the video (5.4) |
| 6 | `COMPOSITOR` | `SET_PRIORITY`, `SET_BACKDROP`, `FB_ENABLE`, and the priority mix of the four sources in the video (5.5) |
| 7 | `TILE_STREAM` | `WRITE_TILE_COLUMN`, `WRITE_TILE_ROW` (5.6) |
| 8 | `DISPLAY_LISTS` | `DEFINE_LIST`, `APPEND_LIST`, `RUN_LIST`, `WRITE_LIST_WORD`, `SET_AUTO_LIST`, and the list RAM (5.7) |
| 9 | `SPI` | the SPI link is present (`gpu_link`; `uart_gpu` reports 0) |
| 10 | `3D` | shaded triangles: `DRAW_TRI_SHADED` (6.3, section 15) |
| 11 | `DEPTH` | the depth buffer and the statistics counters: `SET_DEPTH`, `CLEAR_DEPTH`, `CLEAR_ALL`, `GET_STATS`, `CLEAR_STATS` (6.4, section 16) |
| 12 | `VERTEX` | the vertex buffer, the transformed-vertex buffer and the transform pipeline: `UPLOAD_VERTICES`, `SET_MATRIX`, `SET_PROJECTION`, `TRANSFORM`, `READ_VERTEX` (6.5, section 17) |
| 13-15 | | reserved, 0; later Phase 6 features that a host must be able to detect take these |

Bits 4 to 8 are defined by Chapters 5.3 to 5.7, bit 10 by Chapter 6.3, bit 11 by Chapter 6.4 and bit 12 by Chapter 6.5. `uart_gpu`, which has no video side, still reports `0F 00` even though its command engine would run `DRAW_TRI_SHADED` and the depth commands.

## 9. Error flags

Four sticky bits in byte 8 of `GET_INFO` (bits 7:4 read 0):

| Bit | Name | Set when |
| --- | --- | --- |
| 0 | `OVERFLOW` | a byte was written into a full FIFO and lost |
| 1 | `BAD_LEN` | a known opcode arrived with a wrong `len` (including `UPLOAD_ASSET` with fewer than 5 payload bytes, `UPLOAD_TILES` with fewer than 2, `WRITE_TILE_MAP` with fewer than 3 or an even number, `WRITE_TILE_COLUMN` or `WRITE_TILE_ROW` with any length other than 130, `APPEND_LIST` with no payload, the other list commands with any length but their own, section 14, `DRAW_TRI_SHADED` with any length but 22, the depth commands with any length but their own, `UPLOAD_VERTICES` with a length that is not 2 + 6k for some k of 1 or more, and the other vertex commands with any length but their own, section 17) |
| 2 | `BAD_OP` | an unknown opcode arrived (reserved ones included), or a command a display list may not hold was met in a list (section 14) |
| 3 | `TRUNC` | a packet was abandoned by a frame start (section 4), or a display list ended inside a packet (section 14) |

- A packet with the right length but arguments the command ignores (a `BLIT` of an unknown id, an `UPLOAD_ASSET` that does not fit, a tile command for a layer above 1, a column or row write for a column or row above 63, tile data past the end of its memory, a sprite command for an id of 64 or more, a list command for an id of 8 or more, list bytes past 512, `RUN_LIST` of an empty list) does what its rule says and sets **no** flag.
- **Sticky:** once set, a flag stays set until cleared. Reading does not clear it, nor does a frame or the reset button. Power-up starts all flags at 0.
- **Clearing:** `CLEAR_ERRORS` clears the flags whose bit is set in its mask (bit n is flag n; bits 7:4 are ignored). Mask `0F` clears all. A flag set in the same clock as a `CLEAR_ERRORS` runs stays set.
- `BAD_LEN`, `BAD_OP` and `TRUNC` describe the stream up to the `GET_INFO` that reads them. `OVERFLOW` is live: a byte lost behind the `GET_INFO` may already show.
- Only `OVERFLOW` appears in the status byte. The others need a `GET_INFO`.
- After `OVERFLOW` the stream is damaged: bytes of one or more packets are missing. The next frame start resets the parser. Clear the flag (`CLEAR_ERRORS 01`) and re-send whatever was in flight.

## 10. Tile layers

Two background layers are drawn by the video hardware while the screen is scanned out. They use no framebuffer memory and no drawing commands. The host fills three small memories and sets a scroll position.

**Memories.**

| Memory | Size | Written by |
| --- | --- | --- |
| Tile graphics | 256 tiles of 8 x 8 pixels, one palette index per pixel, 16,384 bytes: address = tile * 64 + y * 8 + x | `UPLOAD_TILES` |
| Tile map, layer 0 and layer 1 | 64 x 64 entries of 16 bits: entry number = row * 64 + column | `WRITE_TILE_MAP`, `WRITE_TILE_COLUMN`, `WRITE_TILE_ROW` |

Both layers share the one tile graphics RAM and the one palette (the framebuffer's: `SET_PALETTE`).

**Map entry** (16 bits, sent low byte first):

| Bits | Name | Meaning |
| --- | --- | --- |
| 7:0 | `TILE` | tile number 0 to 255 |
| 8 | `FLIP_X` | draw the tile mirrored left to right |
| 9 | `FLIP_Y` | draw the tile mirrored top to bottom |
| 11:10 | `PRIORITY` | the tile's item priority 0 to 3 (section 12); write 0 if unused |
| 15:12 | | reserved: write 0 |

**The picture.** A layer's world is its map drawn at 8 x 8 pixels per tile: 512 x 512 pixels, wrapping at every edge. The screen shows a 320 x 240 view of it. `SET_SCROLL` gives the world position of the view's top-left corner. Screen pixel (X, Y) shows world pixel ((sx + X) mod 512, (sy + Y) mod 512). The 320 x 240 view is shown at 2 x 2 on the 640 x 480 display, like the framebuffer. The scroll values are 16-bit on the wire and taken modulo 512, so -1 means 511.

**Transparency.** A tile pixel with palette index 0 is transparent, so what lies behind shows. A layer that is not enabled is transparent everywhere. Which source is in front of which is decided by the compositor (section 12); at power-up the order is, back to front, the front framebuffer, layer 0, layer 1.

**When commands take effect.**

- `SET_SCROLL` and `SET_TILE_LAYER` change shadow registers. Once per frame, during vertical blanking, the GPU copies them to the registers the video uses, so a frame is drawn with one scroll value for all its lines. Several writes in one frame: the last one counts. A change made at any time before blanking shows in the next frame, never in the one in progress. At power-up both layers are disabled and both scrolls are 0.
- `UPLOAD_TILES`, `WRITE_TILE_MAP`, `WRITE_TILE_COLUMN`, `WRITE_TILE_ROW` and `SET_PALETTE` write the memories at once. A write during the visible part of a frame can show in the lines still to be drawn in that frame. Only a write made during vertical blanking is certain to be absent from the frame just finished and complete in the next one.

**Streamed data.** `UPLOAD_TILES` and `WRITE_TILE_MAP` act on their data bytes as they arrive, like `UPLOAD_ASSET`: a packet cut short by a frame start (section 4) leaves written the data bytes (for the map, the whole entries) that arrived. A wrong length (below 2 for `UPLOAD_TILES`; below 3, or an even number, for `WRITE_TILE_MAP`) is a wrong length for all of its bytes: nothing is written and `BAD_LEN` is set. A packet with the right length whose arguments the command ignores (a layer above 1, bytes or entries past the end of their memory) sets no flag.

## 11. Sprites

Up to 64 sprites are drawn by the video hardware, in front of everything else unless the compositor (section 12) says otherwise. Like the tile layers they use no framebuffer memory: the host writes a small table and the hardware draws the sprites while it scans the screen.

**The table.** 64 entries of 8 bytes, entry number 0 to 63. The 16-bit fields are little endian.

| Byte | Field | Meaning |
| --- | --- | --- |
| 0-1 | `X` | signed 16-bit: screen x of the sprite's left column, in the 320 x 240 view |
| 2-3 | `Y` | signed 16-bit: screen y of the sprite's top row |
| 4 | `ASSET` | bits 3:0: the asset (Chapter 4.8) that holds the image; bits 7:4 are ignored (write 0) |
| 5 | `PALETTE` | bits 3:0: palette offset (below); bits 7:4 are ignored (write 0) |
| 6 | `FLAGS` | bit 0 `FLIP_X`, bit 1 `FLIP_Y`, bit 2 `ENABLE`, bits 4:3 `PRIORITY` (the sprite's item priority 0 to 3, section 12), bits 7:5 reserved (write 0) |
| 7 | | reserved (write 0) |

Moving a sprite rewrites bytes 0 to 3 only: `MOVE_SPRITE` is exactly that. All entries are zero at power-up, so every sprite is disabled (`ENABLE` is 0).

**The image.** A sprite is **16 x 16** pixels: the first 256 bytes of its asset, row by row, top row first, pixel (`px`, `py`) at byte `py * 16 + px`. The asset must exist and be exactly 16 x 16 (uploaded with `UPLOAD_ASSET` as `id, 16, 16`). A sprite that names an asset that was never uploaded, or one of another size, is **not drawn**. It still counts toward the limit of 16 per line (below). No error flag is set. The check uses the assets as they are when the line is built (during the two display lines before it is shown).

**Colors.** A pixel whose image byte is 0 is transparent. Any other byte `i` is drawn as palette index `(i + 16 * PALETTE) mod 256`: the sum wraps at 256, and a sum of 0 is drawn as index 0 (an opaque pixel of palette entry 0; transparency is decided by the image byte, not the sum). The palette is the framebuffer's.

**Flips.** `FLIP_X` mirrors the image left to right (the pixel drawn at column `px` is image column `15 - px`), `FLIP_Y` top to bottom.

**Per scan line.** Screen lines are numbered 0 to 239. Sprite *n* is **on line `L`** if it is enabled and `Y <= L < Y + 16`. Its `X` and its asset do not matter: a sprite completely off the left or right edge, or one whose asset cannot be drawn, is still on the line. Of the sprites on a line, the **16 with the lowest entry numbers** are drawn; the others are **dropped for that line**, completely. A sprite can be dropped on some of its lines and drawn on others. Nothing happens to the dropped ones and no flag is set.

**Overlap.** Where drawn sprites overlap, the one with the **lowest entry number is in front**, whatever their `PRIORITY` fields. A transparent pixel shows what is behind it. The pixel that results is then mixed with the other sources by the compositor, using the `PRIORITY` of the sprite that won it.

**Order.** At power-up, back to front: the front framebuffer, tile layer 0, tile layer 1, the sprites; section 12 says how to change that. Sprites are clipped to the 320 x 240 view on all four sides, pixel by pixel; a sprite with `X` or `Y` below zero shows its right or lower part.

**When commands take effect.**

- `SET_SPRITE`, `MOVE_SPRITE` and `SPRITE_CTRL` change **shadow** copies of the table and of the global enable. At the start of every vertical blanking the GPU copies them to the live ones the video uses. A frame is drawn from one table, so a sprite does not appear half moved. Several writes to one entry in a frame: the last one counts. A command executed before the copy shows in the next frame; one executed after it (later in blanking) shows one frame after that.
- The copy is complete within 2 microseconds of the start of blanking. A sprite command executed during the copy may show in either frame, and a `SET_SPRITE` or `MOVE_SPRITE` that the copy cuts through can show for that one frame with some of its bytes old and some new. **Hosts must not have sprite commands executing in the first 5 microseconds of vertical blanking**: send them during the visible part of the frame, or at least 5 microseconds after `VSYNC` rises.
- Asset writes (`UPLOAD_ASSET`) are immediate and not buffered. Change an asset while no enabled sprite uses it, or accept that lines built after the change use the new image.
- At power-up all sprites are disabled and the global enable is 0.

## 12. The compositor

Four **sources** produce a palette index for every pixel of the 320 x 240 view: the front framebuffer (`FB`), tile layer 0 (`L0`), tile layer 1 (`L1`) and the sprites (`SP`). The compositor picks one index per pixel and the palette turns it into a color.

**Opaque.** A source is **opaque** at a pixel if it is enabled and its index is not 0. The framebuffer is a source like the others: its index 0 is transparent. A disabled source is transparent everywhere. For the sprites, a pixel is opaque if a sprite drew it (section 11: a drawn pixel whose final index is 0 is still opaque). If no source is opaque, the pixel shows the **backdrop**: palette entry `BACKDROP` (`SET_BACKDROP`, 0 at power-up).

**Key.** An opaque pixel has a key made of two numbers, the **layer priority** and the **item priority**, compared in that order:

| Source | Layer priority | Item priority |
| --- | --- | --- |
| `FB` | `SET_PRIORITY` bits 1:0 | always 0 |
| `L0` | `SET_PRIORITY` bits 3:2 | `PRIORITY` of the tile under the pixel (map entry bits 11:10) |
| `L1` | `SET_PRIORITY` bits 5:4 | the same |
| `SP` | `SET_PRIORITY` bits 7:6 | `PRIORITY` of the sprite that drew the pixel (flags bits 4:3) |

The pixel shows the opaque source with the **largest key**: the higher layer priority wins, and between sources of equal layer priority the higher item priority. **Equal keys**: the later source in the order `FB`, `L0`, `L1`, `SP` wins. Item priorities therefore matter only between sources that share a layer priority.

**Power-up.** `SET_PRIORITY` is `E4` (`FB` 0, `L0` 1, `L1` 2, `SP` 3), the framebuffer is enabled and the backdrop is 0. The four layer priorities differ, so the item priorities are ignored whatever the map and the table contain, and the order is that of Chapters 5.3 and 5.4 (framebuffer, layer 0, layer 1, sprites). The picture looks the same as before because the framebuffer's index 0 now shows the backdrop, palette entry 0.

**Which enable.** `SET_TILE_LAYER` enables the tile layers (5.3), `SPRITE_CTRL` the sprites (5.4) and `FB_ENABLE` the framebuffer (default 1). They are independent, and each is only about transparency: a disabled source takes no part in the mix.

**Commands.** `SET_PRIORITY`, `SET_BACKDROP` and `FB_ENABLE` each need `len` 1; any other length is skipped and sets `BAD_LEN`. Every byte value is legal: `SET_PRIORITY` uses all 8 bits, `SET_BACKDROP` the whole byte, `FB_ENABLE` bit 0. They set no flag. `33` to `3F` stay unknown opcodes (`BAD_OP`).

**When commands take effect.** The three commands change shadow registers, copied to the live ones at the start of every vertical blanking, like `SET_SCROLL`: a frame is mixed with one set of values. Several writes in a frame: the last counts. The item priority bits are part of the map and of the sprite table: the first is written immediately with `WRITE_TILE_MAP` (section 10), the second is shadowed with the sprite table (section 11).

**Latency.** The compositor adds one pipeline stage after the sources. The video output has the same syncs and `de` timing relative to each other, delayed by a few more pixel clocks than in Chapter 5.4 (the exact number is not part of the protocol).

## 13. Streamed maps

A map is 64 x 64 entries but a level can be far larger. The host treats the map as a **window**, or ring, onto the level and rewrites the column or row that has just left the screen with the one that is about to enter. Two commands write a whole column or a whole row of a layer's map in one packet:

| Op | Name | len | Payload |
| --- | --- | --- | --- |
| `40` | `WRITE_TILE_COLUMN` | 130 | layer, column, 64 entries |
| `41` | `WRITE_TILE_ROW` | 130 | layer, row, 64 entries |

- **Payload.** Byte 0 is the layer, byte 1 the column (or row) number, bytes 2 to 129 are 64 map entries of 16 bits, low byte first, in the format of section 10 (including the priority bits).
- **Column.** Entry k is written to map row k of the column: map entry number `k * 64 + column`, for k = 0 to 63. The 64 entries are 64 map entries 64 apart.
- **Row.** Entry k is written to map column k of the row: map entry number `row * 64 + k`. The 64 entries are consecutive map entries.
- **Length.** Exactly 130, so every packet carries exactly 64 entries. Any other length is skipped whole, nothing is written, and `BAD_LEN` is set.
- **Arguments that name nothing.** A layer above 1, or a column or row above 63, makes the packet do nothing and set **no** flag, like a tile command for a layer that does not exist. The number is **not** wrapped: 64 does not mean 0. The host does its own modulo 64.
- **Immediate and streamed.** The entries are written to the map as they arrive, like `WRITE_TILE_MAP`: not shadowed, and a packet cut short by a frame start (section 4) leaves written the whole entries that arrived (when its layer and number were valid) and sets `TRUNC`. The length is known from the second byte, so a wrong length never writes anything.
- **No other state.** The two commands use the same write port as `WRITE_TILE_MAP` and change no register. `SET_SCROLL` is still the only way to move the view.
- **Capability bit 7** (`TILE_STREAM`).

**Using them.** Screen pixel (X, Y) of a layer shows map entry `(((sy + Y) mod 512) >> 3) * 64 + (((sx + X) mod 512) >> 3)`. If the host keeps level column `c` in map column `c mod 64` and level row `r` in map row `r mod 64`, and sends the scroll in level pixels, the map is a ring that shows the level correctly wherever the view's columns and rows are loaded. A 320 x 240 view touches at most 41 columns and 31 rows. Because the writes take effect at once and the scroll only at the next vertical blanking, the writes for a new view are sent during the frame before and must not touch that frame's view: **the view on screen and the next view must fit in 64 columns and 64 rows together.** A step of up to 23 tiles (184 pixels) in X and 33 tiles (264 pixels) in Y always does. Write new columns before new rows, and take the entries of a new row from the columns the map holds after the column writes.

## 14. Display lists

A **display list** is a sequence of ordinary commands stored in the GPU. The host writes it once; afterwards one 3-byte `RUN_LIST` repeats it, or the GPU runs it by itself at every vertical blanking, and the host changes single words in it with 7-byte patches. A heads-up display that is redrawn every frame then costs the host only the bytes of the digits that changed.

**List RAM.** Eight lists, ids 0 to 7, each in its own slot of **512 bytes** (4 KB in all). A list has a **length**, 0 to 512 bytes, 0 at power-up. Only bytes 0 to length - 1 are the list. The bytes behind the length keep whatever was written there (an earlier, longer list, or a patch) and are never run.

**Commands** (table in section 3). An id of 8 or more makes any of them do nothing and set no flag.

- `DEFINE_LIST` sets the length of list `id` to 0. The bytes stay in the RAM.
- `APPEND_LIST` writes its data bytes at the end of list `id`: byte *i* goes to offset *length*, and the length grows by 1 for each byte, up to 512. Bytes that would pass 512 are dropped and set no flag. A list longer than one packet is built with one `DEFINE_LIST` and several `APPEND_LIST`s. A packet with only the id appends nothing and is legal; one with no payload is `BAD_LEN`. Like the other data commands it is **streamed**: bytes are appended as they arrive, and a packet cut by a frame start (section 4) keeps the bytes that arrived and sets `TRUNC`.
- `RUN_LIST` runs the bytes of list `id` as commands (below), then the host's next command. An empty list does nothing.
- `WRITE_LIST_WORD` writes the **word** `value` (16 bits, low byte at the lower address) at byte `offset` and `offset + 1` of list `id`. The offset is a byte offset and need not be even. Offsets of 512 or more are dropped byte by byte, so offset 511 writes one byte and never touches the next list. The offset may lie behind the list's length: the bytes are written and the length does not change. The two bytes are written one after the other while no list is running, so no run ever sees half a patch.
- `SET_AUTO_LIST` with an id from 0 to 7 turns the **automatic run** on for that list (or changes which list it runs); `FF` turns it off. Any other value does nothing. At power-up it is off. A reset (the board's reset button) turns it off and stops a running list.

**What a list may hold.** Any command that has no reply and is not a list command: drawing (`DRAW_TRI_SHADED` too: 24 bytes a triangle, so a list holds about 20), the depth commands `SET_DEPTH`, `CLEAR_DEPTH`, `CLEAR_ALL` and `CLEAR_STATS`, the vertex commands `SET_MATRIX`, `SET_PROJECTION` and `TRANSFORM` (a frame list can transform its own mesh), `FLIP`, `NOP`, `BLIT`, the tile, sprite and compositor commands, the column and row writes, `WRITE_TILE_MAP`, `SET_PALETTE` and so on. A list may **not** hold:

| Not allowed | Why |
| --- | --- |
| `GET_INFO`, `FB_CRC`, `GET_STATS`, `READ_VERTEX` | they make replies, and a list has no link to send them to |
| `CLEAR_ERRORS` | a list must not hide the flags a run sets |
| `UPLOAD_ASSET`, `UPLOAD_TILES`, `UPLOAD_VERTICES` | long streamed uploads belong to the host, and a list that changes an asset while it draws with it is a bug |
| every opcode from `50` to `5F` | no nesting: a list cannot run a list, change one, or switch the automatic run (this is what keeps every run finite) |

Such a packet is **skipped whole** (its length is read like any other) and sets `BAD_OP`: inside a list these opcodes are unknown. The check is made when the packet is run, not when the list is written, so the host can write them (and see the flag when the list runs). The other rules of the parser hold inside a list: a known opcode with a wrong length is skipped and sets `BAD_LEN`; an unknown opcode is skipped and sets `BAD_OP`.

**How a list runs.** A running list is a second source of bytes in front of the same parser. While it runs, the parser takes its bytes from the list, from offset 0 to length - 1, and takes **nothing from the FIFO**: the host's bytes wait, in order, behind the list. (They are not lost; at 257 free bytes `READY` falls as usual if the host keeps sending.) A run is never interrupted. It ends after the last byte, and then the parser reads the FIFO again. The **length is read when the run starts**.

A list normally ends on a packet boundary. If its length ends **inside** a packet, the part that arrived is treated as a frame start would treat it: the packet is dropped (streamed commands keep what arrived), `TRUNC` is set, and the parser starts clean on the host's next byte. The frame-start rule of section 4 does not apply to list bytes, which carry no start-of-frame marker.

**`FLIP` in a list** is an ordinary `FLIP`. It waits for its swap, and the rest of the list, and everything behind the list, waits with it. This is what lets a list be a whole frame: the commands that draw it, and a `FLIP` at the end.

**Automatic run.** At every start of vertical blanking at which `SET_AUTO_LIST` is on, a run of the automatic list becomes **due**.

- A due run **starts** when the parser is between packets and **the FIFO is empty**: the host's waiting commands go first, then the list. When nothing holds it up, it starts within **100 µs** (a few clocks in the reference).
- At most one run is due at a time. A blanking that arrives while a run is due adds nothing; one that arrives while a list is running (or waiting in its `FLIP`) makes the next run due, to start once that list has ended.
- A `SET_AUTO_LIST` that executes after a blanking has started does not make that blanking's run due. If it turns the automatic run on, the first run is due at the next blanking. If it turns it off, a run that is due is cancelled.
- A `RUN_LIST` from the host runs when it is reached, even while a run is due.

**A list that is a frame.** A list that redraws the whole back buffer and ends in `FLIP` is self-contained: every picture it puts on the screen is complete, whichever buffer it was drawn in, so a heads-up display that is part of it is on every displayed frame. (A display drawn once into the back buffer is on every second frame only.) Run automatically, such a list runs once per frame: its `FLIP` ends at the swap in the next blanking, where the next run falls due. It must draw for longer than vertical blanking (1.44 ms) before its `FLIP`: then the `FLIP` is requested outside blanking, and every legal design swaps at the next blanking wherever in the blanking its swap point is. (A `FLIP` requested inside blanking may swap in that blanking or in the next one, depending on the design.)

**The host's commands during an automatic loop.** With such a list running automatically, the engine is busy all frame (drawing, then waiting in `FLIP`). Host commands wait in the FIFO and run in the gap after each swap, in vertical blanking, just before the next run.

- **Patches:** a `WRITE_LIST_WORD` sent during the visible part of frame *N* runs in the blanking after frame *N*, is drawn by the run that starts there, and is on the screen in frame *N* + 2. Do not send patches in blanking or in the first lines after it: a design that swaps late in blanking and starts the run up to 100 µs later can take such a patch before the run, and it shows one frame earlier.
- **Immediate writes** (`WRITE_TILE_MAP`, column and row writes, `SET_PALETTE`, uploads) are fine: made in blanking, they are absent from the frame that just ended and complete in the next.
- **Shadowed settings** (`SET_SCROLL`, `SET_TILE_LAYER`, the sprite commands, the compositor commands) must not be sent: they would run while the shadows are copied, so the frame they reach is undefined, and section 11 forbids sprite commands in the first 5 microseconds of blanking. Nor should the host draw or `FLIP`: the list owns the framebuffer.
- Everything a host sends in a frame waits in the FIFO until the next swap; past about 250 bytes `READY` falls until then. A host that keeps the engine busy after the swap delays the run; if the run then misses the next blanking, the previous picture stays up one more frame and the missed blanking adds no run.

**Driving the frame with `RUN_LIST`.** A host that also changes shadowed settings every frame sends, once per frame and **after `VSYNC` falls**, that frame's commands followed by `RUN_LIST` of a list that ends in `FLIP`. The previous `FLIP` has finished in the blanking, so the batch runs at once, in the visible part of the frame; the shadowed settings are copied at the next blanking, and the list's `FLIP` swaps at the same blanking. Everything sent in frame *N* is on the screen in frame *N* + 1. The batch and the list's drawing must finish before the blanking, and nothing more should be sent in that frame: commands behind the `RUN_LIST` wait for its `FLIP` and run in blanking.

**Capability bit 8** (`DISPLAY_LISTS`). The list RAM is 4 KB and the lists use no other memory.

## 15. Shaded triangles

`DRAW_TRI_SHADED` (`60`, length 22) draws a triangle whose pixels are shaded by interpolating a per-corner **intensity** across the triangle. It is the first command of the 3D block (capability bit 10).

| Payload bytes | Field |
| --- | --- |
| 0-11 | x0, y0, x1, y1, x2, y2: signed 16-bit corners, as in `DRAW_TRI` |
| 12 | ramp: bits 2:0 select the palette ramp, bits 7:3 are ignored |
| 13-18 | z0, z1, z2: unsigned 16-bit depths (0 to 65,535) |
| 19-21 | i0, i1, i2: 8-bit intensities, Q5.3 (8 = one shade, 0 to 31.875) |

- **Pixels.** Exactly those of `DRAW_TRI` for the same corners (Chapters 4.6 and 4.7): either winding, integer sample points, the top-left rule, nothing for zero area, the part on the screen only.
- **Color.** A pixel is written as `(ramp & 7) * 32 + shade`, where `shade` is the intensity interpolated at the pixel, rounded half up to a whole shade and clamped to 0 to 31. **Ramp** r is the 32 palette entries `32 r` to `32 r + 31`: the host fills them with `SET_PALETTE` (shade 0 dark, shade 31 bright), so 256 colors give 8 ramps of 32 shades. `ramp_palette()` in `host/gpu_model.py` builds the 32 packets of one ramp.
- **Interpolation.** After swapping corners 1 and 2 when the area is negative (z and i travel with their corner), each attribute A is a plane through the three corners. Its gradients are `dA/dx = ((y0-y2)(A1-A0) + (y1-y0)(A2-A0)) / area2` and `dA/dy = ((x2-x0)(A1-A0) + (x0-x1)(A2-A0)) / area2`, each rounded half up to 16 fraction bits (never saturated); starting from corner 0, the value at the corner (xmin, ymin) of the clamped bounding box is `A0 + dA/dx (xmin-x0) + dA/dy (ymin-y0)` with those rounded gradients; pixel (x, y) has that plus `dA/dx (x-xmin) + dA/dy (y-ymin)`, rounded to the output format (z: 16 bits, intensity: Q5.3 to a whole shade), half up, clamped. `raster_model.shade_pixels()` is the definition; the hardware adds the gradients step by step and gets the same integers, bit for bit.
- **Depth.** Smaller z is nearer (0 nearest, 65,535 farthest). z is interpolated for every pixel. What happens to it is the business of the depth buffer (section 16): with the depth test off, which is the power-up state, triangles overwrite each other in drawing order and z is thrown away.
- **Errors.** A length other than 22 is skipped whole and sets `BAD_LEN`. There is no argument that makes a length-22 packet an error.
- **Engine.** Like `DRAW_TRI`: the engine fetches no byte while the triangle is drawn, a reset does not abandon it, and the draw goes to the back buffer. Time: about 116 clocks of setup (the divisions) plus one clock for each pixel of the clamped bounding box in the reference design (with the depth buffer, a few clocks more to drain the depth pipeline); any design may differ, and the tests allow a generous setup.
- **Display lists.** Allowed (a list may hold it, section 14); a patch with `WRITE_LIST_WORD` can change a corner's intensity.

**Capability bit 10** (`3D`). Set by Chapter 6.3: `DRAW_TRI_SHADED` exists. As in Phase 5, each later feature a host must detect gets its own bit (11 to 15).

## 16. The depth buffer and the statistics counters

Chapter 6.4 adds a **depth buffer** and a set of **counters**. Capability bit 11 (`DEPTH`) says all five commands exist.

**The buffer.** 320 x 240 unsigned 16-bit depths, address `y * 320 + x` like the framebuffer. There is **one** depth buffer, not one per color buffer: `FLIP` does not swap it and does not touch it, and neither does `CLEAR` or any 2D command. Its contents at power-up are not defined by this protocol (the reference starts at 0); clear it before the first drawing that tests.

**Modes.** `SET_DEPTH` (`61`, length 1) sets two flags, both 0 at power-up; neither a frame start (section 4) nor the board's reset changes them:

| Bit | Name | Effect on a shaded pixel |
| --- | --- | --- |
| 0 | `TEST` | the pixel is drawn only if its z is **less than** the stored depth; with the bit 0 it is always drawn |
| 1 | `WRITE` | a pixel that is drawn stores its z in the depth buffer |

Bits 7:2 are ignored and there is no argument error. The four combinations are all useful: `TEST`+`WRITE` is normal depth buffering, `TEST` alone draws without changing the depth (a decal), `WRITE` alone sets the depth without testing (a backdrop), none is the painter's algorithm of Chapter 6.3.

**The rule.** A pixel passes the test when `z < stored depth`. **Equal depths fail**: of two pixels with the same depth, the one drawn first stays. A refused pixel changes neither the color nor the depth. The test and the store are one step: the next pixel, whether in the same triangle or in the next command, sees the stored depth of the previous one.

**Which commands.** Only `DRAW_TRI_SHADED` tests and writes depth. `DRAW_TRI`, `PLOT`, `FILL_RECT`, `DRAW_LINE`, `BLIT` and the clears ignore it: a flat triangle always draws and leaves the depth as it was.

**Clearing.** `CLEAR_DEPTH` (`62`, length 2) sets every stored depth to z; `CLEAR_ALL` (`63`, length 3) does that and also sets every pixel of the **back** buffer to the color, as `CLEAR` does. 65,535 is the usual z, because nothing can be farther. Both take one clock per pixel in the reference (76,800 clocks, 1.5 ms at 50 MHz); `CLEAR_ALL` costs no more than `CLEAR` alone. The plain `CLEAR` (`02`) does not touch the depth.

**Statistics.** Three 32-bit counters, 0 at power-up, count what the **triangle** commands (`DRAW_TRI` and `DRAW_TRI_SHADED`) do:

| Counter | Counts |
| --- | --- |
| `pixels` | pixels the rasterizer produced: covered pixels of the clamped bounding box, whether or not the depth test then refused them |
| `written` | pixels written into the color buffer: `pixels` minus those the test refused |
| `clocks` | clocks the command engine spent drawing triangles, from the clock after a triangle starts until the last pixel has been written; setup included, the clocks of other commands not |

`GET_STATS` (`64`, length 0) replies with the three counters, low byte first, in that order (12 bytes); it changes nothing. `CLEAR_STATS` (`65`, length 0) sets all three to 0. The counters **saturate** at 4,294,967,295. `pixels` and `written` are exactly what the model computes; `clocks` depends on the design (setup and pipeline depth), so tests check only that it is at least `pixels` (a rasterizer produces at most one pixel per clock) and within a generous bound. `pixels / clocks` is the throughput in pixels per clock.

**Errors.** A wrong length for any of the five is skipped whole and sets `BAD_LEN`. `66`-`6F` are still unknown (`BAD_OP`).

**Display lists.** `SET_DEPTH`, `CLEAR_DEPTH`, `CLEAR_ALL` and `CLEAR_STATS` may be in a list (a frame list typically starts with `CLEAR_ALL` and `SET_DEPTH`). `GET_STATS` may not: it makes a reply, so it is skipped with `BAD_OP` like `GET_INFO` and `FB_CRC`.

**Engine.** Like the other drawing commands: the engine fetches no byte while a triangle or a clear runs, a reset does not abandon a triangle, and drawing goes to the back buffer. The depth buffer is the engine's own; no port of `fb_scanout` or `gpu_link` changes.

**Capability bit 11** (`DEPTH`). Set by Chapter 6.4. Bit 12 is taken by Chapter 6.5 (section 17); bits 13 to 15 are still free.

## 17. The vertex transform

Chapter 6.5 adds two buffers and a pipeline between them: a **vertex buffer** that the host fills once, a **transformed-vertex buffer** that `TRANSFORM` fills from it, a 3 x 4 matrix and two projection parameters. Capability bit 12 (`VERTEX`) says all five commands exist. Chapter 6.6 draws triangles from the transformed-vertex buffer.

**The buffers.** Both have **1,024 slots**, numbered 0 to 1,023. A vertex-buffer slot holds a model-space vertex: x, y, z, signed 16-bit each (6 bytes). A transformed-vertex slot holds a **record** of 8 bytes, the same bytes `READ_VERTEX` replies with:

| Byte | Field |
| --- | --- |
| 0, 1 | `sx`, signed 16-bit: screen x in pixels (0 is the left column of the 320-pixel screen) |
| 2, 3 | `sy`, signed 16-bit: screen y (0 is the top row, y grows down) |
| 4, 5 | `depth`, unsigned 16-bit: the z that `DRAW_TRI_SHADED` takes and the depth buffer compares; smaller is nearer |
| 6 | flags: bit 0 `NEAR`, bit 1 `CLAMP`, bits 7:2 0 |
| 7 | reserved, 0 (a later chapter may define it; hosts must ignore it) |

Neither buffer is touched by anything but its own commands (`FLIP`, the clears and every drawing command leave them alone). Their contents at power-up are not defined by this protocol, and a transformed slot is defined only after a `TRANSFORM` has covered it **from a vertex slot that an upload has written**: do not read anything else.

**`UPLOAD_VERTICES`** (`70`, length 2 + 6k, k = 1 to 42): the first two bytes are a slot number (16-bit); then come k vertices, vertex j going to slot first + j. A slot of 1,024 or more is dropped (no wrap, no flag). A length that is not 2 + 6k with k of at least 1 is skipped whole with `BAD_LEN`. Like the tile uploads the command is **streamed**: each vertex is written when its sixth byte arrives, so a packet cut by a frame start (section 4) keeps its whole vertices and sets `TRUNC`. The transformed-vertex buffer does not change: a vertex is transformed when `TRANSFORM` says so.

**`SET_MATRIX`** (`71`, length 24). Twelve signed 16-bit numbers, row by row: `m00 m01 m02`, `m10 m11 m12`, `m20 m21 m22`, then the translation `tx ty tz`. The nine matrix entries are **Q2.14** (16,384 = 1.0, range -2.0 to 1.99994); the translation is in model units, an integer. The matrix is used by later `TRANSFORM`s; changing it changes nothing already transformed. At power-up it is the identity (diagonal 16,384, translation 0). The matrix is general: a rotation times a uniform scale of less than 2 is the intended use, but any 12 numbers are legal and cannot overflow (see below).

**`SET_PROJECTION`** (`72`, length 4): `f`, the focal length in pixels (signed 16-bit), and `near`, the near-plane distance in model units (unsigned 16-bit). Power-up: f = 277 (a 60 degree horizontal field of view on 320 pixels: 160 / tan 30 degrees), near = 1. The screen centre is fixed at (160, 120).

**`TRANSFORM`** (`73`, length 4): `first` and `count`, 16-bit each. Every slot from `first` to `first + count - 1` is transformed, using the matrix and projection at the time the command runs. Slots from 1,024 up are not (the range is clipped), `count` 0 or `first` of 1,024 or more does nothing, and none of these is an error. The engine fetches nothing while it runs and a reset does not abandon it. The reference takes about 165 clocks per vertex (3.4 ms for 1,024 at 50 MHz); the protocol promises only that the commands behind it wait.

**`READ_VERTEX`** (`74`, length 2): a slot number (16-bit). Replies with the 8-byte record. A slot of 1,024 or more makes **no reply** and sets no flag. The command changes nothing.

**The arithmetic** (exact; `host/vertex_model.py` is the specification). For a vertex (x, y, z):

1. *Camera space.* For each row i: `c_i = round_half_up((m_i0 x + m_i1 y + m_i2 z + t_i * 16384) / 256)`: an exact sum, half an LSB added, an arithmetic shift. The result is a signed number with **6 fraction bits** (64 = one model unit), 25 bits wide. It cannot saturate: with every entry at its limit |c_i| is below 2^24 / 64 = 262,144 units. Camera space has x to the right, y **up** and z **forward** (larger z is farther).
2. *Near test.* The vertex is **NEAR** (flag bit 0) when `z_c <= near * 64`, that is z_c is at most `near` units. The test is on z, nothing else. The position fields of a NEAR record are **undefined** (the reference writes zeros); a host must not use them. A vertex that is not NEAR has `z_c > near`, so the divisions below never meet a divisor of 0 or less.
3. *Projection.* `qx = round_half_up(f * x_c / z_c)`, `qy = round_half_up(f * y_c / z_c)`, `qd = round_half_up(near * 64 * 65535 / z_c)`, each a signed 17-bit number (saturating, which only matters for far off-screen vertices). Then `sx = 160 + qx`, `sy = 120 - qy`, `depth = 65535 - qd`. `sx` and `sy` are **clamped to the signed 16-bit range**; `CLAMP` (flag bit 1) is set when either was clamped (such a vertex is not where the geometry puts it, and a triangle that uses it is distorted).
4. *Depth.* `depth = 65535 * (1 - near / z_c)`, rounded: 0 at the near plane, rising towards 65,535 as z_c grows. Equal steps of 1 / z are equal steps of depth, which is what the rasterizer's linear interpolation across a triangle needs (Chapter 6.3). There is no far plane: depth 65,535 is reached only at infinity.

Worked example: the power-up state, vertex (0, 0, 100): camera (0, 0, 6,400 in Q.6), not NEAR, qx = qy = 0, qd = round(1 * 64 * 65535 / 6400) = 655, so sx = 160, sy = 120, depth = 64,880.

**Accuracy.** In the ranges stated in `ref_vertex.py` the result is within 1 pixel of a floating-point computation (for the matrix as stored: f up to 1,024, z_c of 64 units or more, a position within 1,024 pixels of the centre; for a true rotation before its rounding to Q2.14: vertices within 512 units, z_c of 128 or more, f up to 400, within 512 pixels of the centre). Outside them, a z_c of a few units magnifies the rounding of the camera coordinates to 1/64 unit.

**Errors.** A wrong length is skipped whole with `BAD_LEN`; there is no argument that makes a right-length command an error.

**Display lists.** `SET_MATRIX`, `SET_PROJECTION` and `TRANSFORM` may be in a list. `UPLOAD_VERTICES` (a long upload) and `READ_VERTEX` (a reply) may not: skipped whole with `BAD_OP`, like `UPLOAD_TILES` and `GET_STATS`.

**Capability bit 12** (`VERTEX`). Set by Chapter 6.5. Bits 13 to 15 are still free.
