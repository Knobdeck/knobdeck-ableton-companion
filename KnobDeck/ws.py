# A minimal WebSocket client codec (RFC 6455) on the standard library: the upgrade request and
# answer, masked text frames out, text / ping / close frames in. It does no I/O itself: hub.py
# feeds it bytes from a non-blocking socket, because a Remote Script runs on Live's main thread
# and must never wait. Live bundles no WebSocket module. No TLS: loopback only.
#
# Python 3.7 compatible (Live 11's Python).

import base64
import hashlib
import os
import struct

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA

MAX_MESSAGE = 1 << 20
MAX_HEAD = 65536


class Closed(Exception):
    """The connection is gone, or the server broke the protocol."""


class Refused(Exception):
    """The server answered the upgrade with something other than 101."""


def new_key():
    return base64.b64encode(os.urandom(16)).decode()


def upgrade_request(host, port, path, key):
    # No Origin header: the hub tells native companions from web pages by it.
    return (
        (
            "GET {path} HTTP/1.1\r\n"
            "Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        .format(path=path, host=host, port=port, key=key)
        .encode()
    )


def upgrade_answer(buf, key):
    """None while the answer's head is incomplete, else the bytes after it. Raises Refused."""
    if b"\r\n\r\n" not in buf:
        if len(buf) > MAX_HEAD:
            raise Refused("handshake answer too long")
        return None
    head, rest = buf.split(b"\r\n\r\n", 1)
    lines = head.decode("latin-1").split("\r\n")
    status = lines[0].split(" ", 2)
    if len(status) < 2 or status[1] != "101":
        raise Refused(lines[0])
    headers = {}
    for line in lines[1:]:
        k, _, v = line.partition(":")
        headers[k.strip().lower()] = v.strip()
    want = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
    if headers.get("sec-websocket-accept") != want:
        raise Refused("bad Sec-WebSocket-Accept")
    return rest


def frame(opcode, payload, mask=None):
    """One masked client frame."""
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack("!H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack("!Q", n)
    mask = mask or os.urandom(4)
    return head + mask + _xor(payload, mask)


def text_frame(text):
    return frame(OP_TEXT, text.encode("utf-8"))


def _xor(data, mask):
    if not data:
        return b""
    # Whole-buffer XOR through int arithmetic: much faster than a per-byte loop in pure Python.
    n = len(data)
    key = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big")


class Decoder:
    """Turns server bytes into messages: ("text", str), ("ping", bytes), ("close", bytes)."""

    def __init__(self):
        self._buf = b""
        self._parts = []

    def feed(self, data):
        self._buf += data
        out = []
        while True:
            f = self._frame()
            if f is None:
                return out
            fin, opcode, payload = f
            if opcode == OP_PING:
                out.append(("ping", payload))
            elif opcode == OP_PONG:
                pass
            elif opcode == OP_CLOSE:
                out.append(("close", payload))
                return out
            elif opcode in (OP_TEXT, OP_BINARY, OP_CONT):
                if opcode != OP_CONT:
                    self._parts = [(opcode, payload)]
                elif not self._parts:
                    raise Closed("continuation without a first frame")
                else:
                    self._parts.append((opcode, payload))
                if sum(len(p) for _, p in self._parts) > MAX_MESSAGE:
                    raise Closed("message too large")
                if fin:
                    first, data = self._parts[0][0], b"".join(p for _, p in self._parts)
                    self._parts = []
                    if first == OP_TEXT:
                        out.append(("text", data.decode("utf-8", "replace")))

    def _frame(self):
        b = self._buf
        if len(b) < 2:
            return None
        fin, opcode = b[0] & 0x80, b[0] & 0x0F
        masked, n = b[1] & 0x80, b[1] & 0x7F
        i = 2
        if n == 126:
            if len(b) < 4:
                return None
            n = struct.unpack("!H", b[2:4])[0]
            i = 4
        elif n == 127:
            if len(b) < 10:
                return None
            n = struct.unpack("!Q", b[2:10])[0]
            i = 10
        if n > MAX_MESSAGE:
            raise Closed("frame too large")
        mask = b""
        if masked:
            if len(b) < i + 4:
                return None
            mask = b[i : i + 4]
            i += 4
        if len(b) < i + n:
            return None
        payload = b[i : i + n]
        if mask:
            payload = _xor(payload, mask)
        self._buf = b[i + n :]
        return bool(fin), opcode, payload
