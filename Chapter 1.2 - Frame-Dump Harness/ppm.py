"""ppm.py -- read and write PPM images as numpy arrays (Chapter 1.2).

An image is a numpy array of shape (height, width, 3), dtype uint16, plus a
maxval (the value that means "full brightness"). The Go Board uses 3 bits per
channel, so its frames have maxval 7; later boards use 255.

Only numpy is needed. Shared by compare.py and every ref_<name>.py.
"""
import numpy as np


def _tokens(data, count, pos):
    """Read `count` whitespace-separated header tokens, skipping # comments."""
    out = []
    while len(out) < count:
        while pos < len(data) and data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            while pos < len(data) and data[pos:pos + 1] not in (b"\n", b"\r"):
                pos += 1
            continue
        start = pos
        while pos < len(data) and not data[pos:pos + 1].isspace():
            pos += 1
        if start == pos:
            raise ValueError("truncated PPM header")
        out.append(data[start:pos])
    return out, pos


def read_ppm(path):
    """Return (image, maxval). Accepts binary P6 and ASCII P3."""
    with open(path, "rb") as f:
        data = f.read()
    (magic, w, h, maxval), pos = _tokens(data, 4, 0)
    w, h, maxval = int(w), int(h), int(maxval)
    if magic == b"P6":
        pos += 1  # exactly one whitespace byte after maxval
        dtype = np.uint8 if maxval < 256 else np.dtype(">u2")
        need = w * h * 3
        pix = np.frombuffer(data, dtype=dtype, count=need, offset=pos)
    elif magic == b"P3":
        pix = np.array(data[pos:].split()[: w * h * 3], dtype=np.int64)
    else:
        raise ValueError(f"{path}: not a P6 or P3 PPM (magic {magic!r})")
    if pix.size != w * h * 3:
        raise ValueError(f"{path}: expected {w * h * 3} samples, found {pix.size}")
    return pix.reshape(h, w, 3).astype(np.uint16), maxval


def write_ppm(path, img, maxval):
    """Write an (h, w, 3) array as binary P6 with the given maxval (<= 255)."""
    img = np.asarray(img)
    if img.ndim != 3 or img.shape[2] != 3:
        raise ValueError("image must have shape (height, width, 3)")
    if maxval > 255 or img.min() < 0 or img.max() > maxval:
        raise ValueError(f"pixel values must be in 0..{maxval} (maxval <= 255)")
    h, w, _ = img.shape
    with open(path, "wb") as f:
        f.write(f"P6\n{w} {h}\n{maxval}\n".encode())
        f.write(img.astype(np.uint8).tobytes())


def blank(width, height):
    """A black image to draw into: img[y, x] = (r, g, b)."""
    return np.zeros((height, width, 3), dtype=np.uint16)
