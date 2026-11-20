# The WebSocket codec (KnobDeck/ws.py) on its own.
import base64
import hashlib
import struct

import pytest

from KnobDeck import ws


def server_frame(opcode, payload, fin=True):
    n = len(payload)
    head = bytes([(0x80 if fin else 0) | opcode])
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack("!H", n)
    else:
        head += bytes([127]) + struct.pack("!Q", n)
    return head + payload


def unmask(frame):
    n, i = frame[1] & 0x7F, 2
    if n == 126:
        n, i = struct.unpack("!H", frame[2:4])[0], 4
    elif n == 127:
        n, i = struct.unpack("!Q", frame[2:10])[0], 10
    assert frame[1] & 0x80, "client frames are masked"
    mask = frame[i : i + 4]
    return frame[0] & 0x0F, bytes(c ^ mask[j % 4] for j, c in enumerate(frame[i + 4 : i + 4 + n]))


@pytest.mark.parametrize("size", [0, 5, 125, 126, 65535, 65536, 70000])
def test_client_frames_are_masked_and_sized(size):
    text = "é" * (size // 2) + "x" * (size % 2)
    op, payload = unmask(ws.text_frame(text))
    assert op == ws.OP_TEXT
    assert payload.decode() == text


def test_decoder_reassembles_split_and_fragmented_messages():
    d = ws.Decoder()
    data = server_frame(ws.OP_TEXT, b"hel", fin=False) + server_frame(ws.OP_CONT, b"lo") + server_frame(ws.OP_PING, b"p")
    data += server_frame(ws.OP_TEXT, b"x" * 70000)
    out = []
    for i in range(0, len(data), 7):  # byte trickle, as a non-blocking socket may deliver it
        out += d.feed(data[i : i + 7])
    assert out == [("text", "hello"), ("ping", b"p"), ("text", "x" * 70000)]
    assert d.feed(server_frame(ws.OP_CLOSE, struct.pack("!H", 1000))) == [("close", struct.pack("!H", 1000))]


def test_decoder_rejects_oversized_frames():
    with pytest.raises(ws.Closed):
        ws.Decoder().feed(bytes([0x81, 127]) + struct.pack("!Q", ws.MAX_MESSAGE + 1))


def test_upgrade_answer():
    key = ws.new_key()
    req = ws.upgrade_request("127.0.0.1", 32150, "/companion", key).decode()
    assert req.startswith("GET /companion HTTP/1.1\r\nHost: 127.0.0.1:32150\r\n")
    assert "Origin" not in req
    accept = base64.b64encode(hashlib.sha1((key + ws._GUID).encode()).digest()).decode()
    good = "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nSec-WebSocket-Accept: {}\r\n\r\n".format(accept).encode()
    assert ws.upgrade_answer(good[:20], key) is None
    assert ws.upgrade_answer(good + b"rest", key) == b"rest"
    with pytest.raises(ws.Refused):
        ws.upgrade_answer(b"HTTP/1.1 403 Forbidden\r\n\r\n", key)
    with pytest.raises(ws.Refused):
        ws.upgrade_answer(good.replace(accept.encode(), b"nope"), key)
