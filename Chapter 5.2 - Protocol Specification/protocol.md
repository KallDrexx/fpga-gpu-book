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

All coordinates are signed 16-bit. Drawing commands write the back buffer. The exact pixels (clipping, the line algorithm, the top-left rule, asset allocation) are defined by `host/gpu_model.py`.

**Reserved ranges.** Each later chapter gets a block of 16 opcodes. Until a chapter defines its commands, they are **unknown opcodes** (skipped, `BAD_OP`). The names below are a plan, not a promise: the chapter that builds a feature fixes its opcodes and payloads, and may rename, add or drop commands.

| Range | For | Planned commands |
| --- | --- | --- |
| `10`-`1F` | Tile layers (5.3) | scroll per layer, tile graphics upload, tile map writes |
| `20`-`2F` | Sprite table (5.4) | write sprite entries; move sprites by rewriting their 4 position bytes |
| `30`-`3F` | Compositor (5.5) | layer enable and priority |
| `40`-`4F` | Streamed maps (5.6) | `WRITE_TILE_COLUMN`, `WRITE_TILE_ROW` (64 entries, 128 bytes) |
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

**One exception.** `UPLOAD_ASSET` acts on its header once its first five payload bytes have arrived, and writes data bytes as they arrive. A truncated upload therefore leaves the asset allocated and the bytes that arrived written. Re-send the whole image; with the same id and size it replaces the old one in place.

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

| Byte | Meaning | Value in Chapter 5.2 |
| --- | --- | --- |
| 0 | magic, ASCII `G` | `47` |
| 1 | protocol version | `01` |
| 2, 3 | capabilities, 16-bit | `0F 02` (`gpu_link`), `0F 00` (`uart_gpu`) |
| 4, 5 | screen width, 16-bit | `40 01` (320) |
| 6, 7 | screen height, 16-bit | `F0 00` (240) |
| 8 | error flags (section 9) | `00` after power-up |

So a fresh GPU answers `47 01 0F 02 40 01 F0 00 00`. `GET_INFO` changes no flag.

**Capability bits:**

| Bit | Name | Meaning |
| --- | --- | --- |
| 0 | `DRAW` | `SET_PALETTE`, `CLEAR`, `PLOT`, `FB_CRC`, `FILL_RECT`, `DRAW_LINE` |
| 1 | `FLIP` | `FLIP` |
| 2 | `TRI` | `DRAW_TRI` |
| 3 | `BLIT` | `UPLOAD_ASSET`, `BLIT` |
| 4 | `TILES` | tile layers (5.3) |
| 5 | `SPRITES` | sprite table (5.4) |
| 6 | `COMPOSITOR` | compositor (5.5) |
| 7 | `TILE_STREAM` | streamed maps (5.6) |
| 8 | `DISPLAY_LISTS` | display lists (5.7) |
| 9 | `SPI` | the SPI link is present (`gpu_link`; `uart_gpu` reports 0) |
| 10 | `3D` | Phase 6 |
| 11-15 | | reserved, 0 |

Bits 4 to 10 are planned like the opcode ranges; their chapters may split or rename them.

## 9. Error flags

Four sticky bits in byte 8 of `GET_INFO` (bits 7:4 read 0):

| Bit | Name | Set when |
| --- | --- | --- |
| 0 | `OVERFLOW` | a byte was written into a full FIFO and lost |
| 1 | `BAD_LEN` | a known opcode arrived with a wrong `len` (including `UPLOAD_ASSET` with fewer than 5 payload bytes) |
| 2 | `BAD_OP` | an unknown opcode arrived (reserved ones included) |
| 3 | `TRUNC` | a packet was abandoned by a frame start (section 4) |

- A packet with the right length but arguments the command ignores (a `BLIT` of an unknown id, an `UPLOAD_ASSET` that does not fit) does what its Phase 4 rule says and sets **no** flag.
- **Sticky:** once set, a flag stays set until cleared. Reading does not clear it, nor does a frame or the reset button. Power-up starts all flags at 0.
- **Clearing:** `CLEAR_ERRORS` clears the flags whose bit is set in its mask (bit n is flag n; bits 7:4 are ignored). Mask `0F` clears all. A flag set in the same clock as a `CLEAR_ERRORS` runs stays set.
- `BAD_LEN`, `BAD_OP` and `TRUNC` describe the stream up to the `GET_INFO` that reads them. `OVERFLOW` is live: a byte lost behind the `GET_INFO` may already show.
- Only `OVERFLOW` appears in the status byte. The others need a `GET_INFO`.
- After `OVERFLOW` the stream is damaged: bytes of one or more packets are missing. The next frame start resets the parser. Clear the flag (`CLEAR_ERRORS 01`) and re-send whatever was in flight.
