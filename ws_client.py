"""Minimal stdlib WebSocket client (RFC 6455) for public market streams.

Only what the liquidation collector needs: TLS connect, text frames, ping/pong, close.
No new dependencies; importing this module performs no network I/O.
"""
from __future__ import annotations

import base64
import os
import socket
import ssl
import struct
from urllib.parse import urlparse

MAX_MESSAGE_BYTES = 4 * 1024 * 1024
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketClosed(Exception):
    pass


def _accept_key(key):
    import hashlib
    return base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()


def encode_frame(payload: bytes, opcode=0x1, mask_key=None):
    """Client frame: FIN set, always masked (clients must mask, RFC 6455 §5.3)."""
    mask_key = mask_key if mask_key is not None else os.urandom(4)
    header = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack("!H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack("!Q", n)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return bytes(header) + mask_key + masked


def decode_frame(read_exact):
    """Read one frame via read_exact(n) -> bytes; returns (fin, opcode, payload)."""
    b0, b1 = read_exact(2)
    fin, opcode = bool(b0 & 0x80), b0 & 0x0F
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack("!H", read_exact(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", read_exact(8))[0]
    if n > MAX_MESSAGE_BYTES:
        raise WebSocketClosed("frame too large")
    mask = read_exact(4) if b1 & 0x80 else None
    payload = read_exact(n) if n else b""
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload


class WebSocket:
    def __init__(self, sock):
        self.sock = sock
        self._buf = b""
        self._fragments = []
        self._frag_opcode = None
        self.closed = False

    @classmethod
    def connect(cls, url, timeout=10.0):
        parts = urlparse(url)
        if parts.scheme not in ("wss", "ws"):
            raise ValueError("unsupported websocket scheme")
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "wss" else 80)
        raw = socket.create_connection((host, port), timeout=timeout)
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host) \
            if parts.scheme == "wss" else raw
        key = base64.b64encode(os.urandom(16)).decode()
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        request = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                   "Sec-WebSocket-Version: 13\r\nUser-Agent: crypto-screener-positioning\r\n\r\n")
        sock.sendall(request.encode())
        ws = cls(sock)
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk or len(head) > 16384:
                sock.close()
                raise WebSocketClosed("handshake failed")
            head += chunk
        head, ws._buf = head.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            sock.close()
            raise WebSocketClosed(f"handshake rejected: {lines[0][:80]}")
        headers = {k.strip().lower(): v.strip() for k, _, v in
                   (line.partition(":") for line in lines[1:])}
        if headers.get("sec-websocket-accept") != _accept_key(key):
            sock.close()
            raise WebSocketClosed("bad Sec-WebSocket-Accept")
        return ws

    def settimeout(self, seconds):
        self.sock.settimeout(seconds)

    def _read_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                self.closed = True
                raise WebSocketClosed("connection closed by peer")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send_text(self, text):
        self.sock.sendall(encode_frame(text.encode(), 0x1))

    def recv(self):
        """Next complete text message; raises socket.timeout when idle, WebSocketClosed on close."""
        while True:
            fin, opcode, payload = decode_frame(self._read_exact)
            if opcode == 0x9:  # ping -> pong with same payload
                self.sock.sendall(encode_frame(payload, 0xA))
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                self.closed = True
                try:
                    self.sock.sendall(encode_frame(payload[:2], 0x8))
                except OSError:
                    pass
                raise WebSocketClosed("server closed")
            if opcode in (0x1, 0x2):
                self._frag_opcode, self._fragments = opcode, [payload]
            elif opcode == 0x0:
                self._fragments.append(payload)
            if sum(len(p) for p in self._fragments) > MAX_MESSAGE_BYTES:
                raise WebSocketClosed("message too large")
            if fin:
                data = b"".join(self._fragments)
                self._fragments = []
                return data.decode("utf-8", "replace")

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.sock.sendall(encode_frame(b"\x03\xe8", 0x8))
            except OSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass
