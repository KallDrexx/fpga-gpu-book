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

All coordinates are signed 16-bit. Drawing commands write the back buffer. The exact pixels (clipping, the line algorithm, the top-left rule, asset allocation) are defined by `host/gpu_model.py`.

**Reserved ranges.** Each later chapter gets a block of 16 opcodes. Until a chapter defines its commands, they are **unknown opcodes** (skipped, `BAD_OP`). The names below are a plan, not a promise: the chapter that builds a feature fixes its opcodes and payloads, and may rename, add or drop commands.

| Range | For | Planned commands |
| --- | --- | --- |
| `10`-`1F` | Tile layers (5.3): `10` to `13` defined, `14`-`1F` spare | section 10 |
| `20`-`2F` | Sprite table (5.4): `20` to `22` defined, `23`-`2F` spare | section 11 |
| `30`-`3F` | Compositor (5.5): `30` to `32` defined, `33`-`3F` spare | section 12 |
| `40`-`4F` | Streamed maps (5.6): `40` and `41` defined, `42`-`4F` spare | section 13 |
| `50`-`5F` | Display lists (5.7) | write a list, `RUN_LIST`, `WRITE_LIST_WORD`, run a list every vsync |
| `60`-`6F` | 3D (Phase 6) | `UPLOAD_VERTICES`, `SET_MATRIX`, `DRAW_MESH` (with an index buffer) |
| `70`-`7F` | Future | |
| `80`-`FF` | **Never assigned.** Tests use them as unknown opcodes. `FF` can never be an opcode, because a frame starting with `FF` is a read frame. | |

Every payload must fit in 255 bytes, so larger data (a 128-byte map column, a list, a vertex buffer) goes in packets of at most 255 bytes.

Rules for adding a command:

1. Take the next free opcode in its range. Never reuse or renumber one.
2. Once a chapter has defined a command, its length rule never changes. A new layout is a new opcode.
3. Set the feature's capability bit (section 8), so a host can ask whether the command exists.
4. The protocol version changes only if framing or an existing command changes incompatibly. Adding commands does not change it.

## 4. Frame starts reset the parser

**The first byte that an SPI frame puts into the FIFO starts a new packet**, whatever the parser was doing. So does **a UART byte that follows SPI bytes** (the link changed). If the parser was in the middle of a packet, that packet is dropped: nothing it carried is executed, no reply is made, and `TRUNC` is set. A complete packet is never affected.

This has the same effect as "raising `CS` resets the parser": a partial packet does nothing while it waits for more bytes, so dropping it when the next byte arrives cannot be told apart from dropping it at the `CS` rise. Frames that put no byte into the FIFO (empty frames and read frames) change nothing, so a UART packet in progress continues past them.

**The exception: streamed commands.** `UPLOAD_ASSET` acts on its header once its first five payload bytes have arrived, and writes data bytes as they arrive. A truncated upload therefore leaves the asset allocated and the bytes that arrived written. Re-send the whole image; with the same id and size it replaces the old one in place. `UPLOAD_TILES`, `WRITE_TILE_MAP`, `WRITE_TILE_COLUMN` and `WRITE_TILE_ROW` are streamed the same way (sections 10 and 13): a truncated one leaves the bytes, or whole map entries, that arrived written.

To recover from any lost state: raise `CS`, wait for `READY`, send the next frame. It starts cleanly. Complete commands already in the FIFO still run, and a command that is running (a `CLEAR`, a `FLIP` waiting for vertical blanking) is not interrupted.

## 5. UART

The UART has no frames and the hardware does not resynchronize it: there is no break detector or timeout. A UART host must keep its packets whole. To recover from a lost byte, send one SPI command frame (a `NOP`, `00 00`, is enough), or press the reset button.

## 6. Parser rules

- Every packet takes exactly `2 + len` bytes from the FIFO, whatever the opcode, unless a frame start (section 4) cuts it short.
- Every engine state ends after a bounded number of clocks, except two that wait on purpose: `FLIP` waits for vertical blanking, and a reply waits for room in the reply queue (section 7) or the UART.
- A long command always finishes. Nothing on the link aborts a running command.

## 7. Replies and the MISO bytes

Replies are raw bytes with no header, in command order: 4 bytes for `FB_CRC`, 9 for `GET_INFO`. They go to the link that sent the most recent byte written into the FIFO.

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

| Byte | Meaning | Value in Chapter 5.6 |
| --- | --- | --- |
| 0 | magic, ASCII `G` | `47` |
| 1 | protocol version | `01` |
| 2, 3 | capabilities, 16-bit | `FF 02` (`gpu_link`), `0F 00` (`uart_gpu`) |
| 4, 5 | screen width, 16-bit | `40 01` (320) |
| 6, 7 | screen height, 16-bit | `F0 00` (240) |
| 8 | error flags (section 9) | `00` after power-up |

So a fresh GPU answers `47 01 FF 02 40 01 F0 00 00`. `GET_INFO` changes no flag.

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
| 8 | `DISPLAY_LISTS` | display lists (5.7) |
| 9 | `SPI` | the SPI link is present (`gpu_link`; `uart_gpu` reports 0) |
| 10 | `3D` | Phase 6 |
| 11-15 | | reserved, 0 |

Bits 4 to 7 are defined by Chapters 5.3 to 5.6. Bits 8 to 10 are planned like the opcode ranges; their chapters may split or rename them.

## 9. Error flags

Four sticky bits in byte 8 of `GET_INFO` (bits 7:4 read 0):

| Bit | Name | Set when |
| --- | --- | --- |
| 0 | `OVERFLOW` | a byte was written into a full FIFO and lost |
| 1 | `BAD_LEN` | a known opcode arrived with a wrong `len` (including `UPLOAD_ASSET` with fewer than 5 payload bytes, `UPLOAD_TILES` with fewer than 2, `WRITE_TILE_MAP` with fewer than 3 or an even number, and `WRITE_TILE_COLUMN` or `WRITE_TILE_ROW` with any length other than 130) |
| 2 | `BAD_OP` | an unknown opcode arrived (reserved ones included) |
| 3 | `TRUNC` | a packet was abandoned by a frame start (section 4) |

- A packet with the right length but arguments the command ignores (a `BLIT` of an unknown id, an `UPLOAD_ASSET` that does not fit, a tile command for a layer above 1, a column or row write for a column or row above 63, tile data past the end of its memory, a sprite command for an id of 64 or more) does what its rule says and sets **no** flag.
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
