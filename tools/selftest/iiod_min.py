"""Minimal IIOD client: libiio's network protocol over a plain socket.

Vendored deliberately. A health check you run because you suspect the board
is damaged is the worst possible moment to discover that your libiio version
no longer matches the firmware's, or that a C extension will not build. This
module needs nothing but the standard library.

Protocol notes, all confirmed against a live board (IIOD 0.25). Commands are
CRLF-terminated; every reply opens with a decimal line that is either a byte
count or a NEGATIVE ERRNO. Attribute payloads are NUL-terminated.

    READ  <dev> [INPUT|OUTPUT] <ch> <attr>
    WRITE <dev> [INPUT|OUTPUT] <ch> <attr> <len>   then value + NUL
    OPEN  <dev> <samples> <mask> [CYCLIC]
    READBUF  <dev> <bytes>    -> length, mask echo, then data
    WRITEBUF <dev> <bytes>    -> ACKED TWICE, before and after the payload
    CLOSE <dev>

The channel mask is fixed-width and easy to get wrong: exactly 8 hex
characters per 32 scan channels. "00000003" enables channels 0 and 1; both
"3" and "0000000000000003" fail with -22 EINVAL and no explanation.
"""

from __future__ import annotations

import errno
import socket
import struct
import xml.etree.ElementTree as ET

DEFAULT_HOST = "192.168.2.1"
DEFAULT_PORT = 30431


class IiodError(OSError):
    def __init__(self, code: int, command: str):
        self.code = -code if code < 0 else code
        self.command = command
        name = errno.errorcode.get(self.code, self.code)
        super().__init__(self.code, f"{name} from IIOD: {command}")


def mask_for(channels, total_channels: int) -> str:
    words = max(1, (total_channels + 31) // 32)
    bits = 0
    for c in channels:
        bits |= 1 << c
    return f"{bits:0{words * 8}x}"


class Iiod:
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=15.0):
        self.host, self.port, self.timeout = host, port, timeout
        self._sock = None
        self._f = None
        self._devices = None

    # -- connection ---------------------------------------------------------

    def connect(self):
        if self._sock is None:
            self._sock = socket.create_connection((self.host, self.port), self.timeout)
            self._sock.settimeout(self.timeout)
            self._f = self._sock.makefile("rwb")
        return self

    def close(self):
        for obj in (self._f, self._sock):
            try:
                if obj is not None:
                    obj.close()
            except OSError:
                pass
        self._f = self._sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    # -- framing ------------------------------------------------------------

    def _send(self, command):
        self.connect()
        self._f.write((command + "\r\n").encode())
        self._f.flush()

    def _status(self, command):
        line = self._f.readline()
        if not line:
            raise ConnectionError(f"IIOD closed the connection during: {command}")
        n = int(line.strip())
        if n < 0:
            raise IiodError(n, command)
        return n

    def _read_exactly(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self._f.read(min(65536, n - len(buf)))
            if not chunk:
                raise ConnectionError(f"IIOD sent {len(buf)} of {n} bytes")
            buf += chunk
        return bytes(buf)

    def _text(self, command):
        self._send(command)
        n = self._status(command)
        data = self._read_exactly(n).decode(errors="replace")
        self._f.readline()
        return data.rstrip("\x00").strip()

    # -- attributes ---------------------------------------------------------

    def version(self):
        """VERSION is the one command that answers with a bare line, not a
        length-prefixed payload."""
        self._send("VERSION")
        return self._f.readline().decode(errors="replace").strip()

    def context_xml(self):
        raw = self._text("PRINT")
        start, end = raw.find("<?xml"), raw.rfind("</context>")
        return raw[start:end + len("</context>")] if start >= 0 and end > start else raw

    def read(self, device, channel, attr, output=False):
        return self._text(
            f"READ {device} {'OUTPUT' if output else 'INPUT'} {channel} {attr}")

    def read_device(self, device, attr):
        return self._text(f"READ {device} {attr}")

    def write(self, device, channel, attr, value, output=False):
        payload = f"{value}".encode() + b"\x00"
        cmd = (f"WRITE {device} {'OUTPUT' if output else 'INPUT'} "
               f"{channel} {attr} {len(payload)}")
        self._send(cmd)
        self._f.write(payload)
        self._f.flush()
        self._status(cmd)

    # -- context ------------------------------------------------------------

    def devices(self):
        """{name: (device_id, n_scan_channels)} for every device in the context."""
        if self._devices is None:
            root = ET.fromstring(self.context_xml())
            out = {}
            for dev in root.iter("device"):
                name = dev.get("name") or dev.get("id")
                scan = sum(1 for c in dev.iter("channel")
                           if c.find("scan-element") is not None)
                out[name] = (dev.get("id"), scan)
            self._devices = out
        return self._devices

    def context_attrs(self):
        root = ET.fromstring(self.context_xml())
        return {a.get("name"): a.get("value") for a in root.findall("context-attribute")}

    # -- sample buffers -----------------------------------------------------

    def read_samples(self, device, nsamples, mask, nchannels=2):
        """Capture nsamples per channel. Returns a flat list of int16."""
        self._send(f"OPEN {device} {nsamples} {mask}")
        self._status(f"OPEN {device}")
        try:
            want = nsamples * 2 * nchannels
            cmd = f"READBUF {device} {want}"
            self._send(cmd)
            got = self._status(cmd)
            self._f.readline()                       # mask echo
            data = self._read_exactly(got) if got else b""
        finally:
            self.close_buffer(device)
        return list(struct.unpack(f"<{len(data) // 2}h", data))

    def write_samples(self, device, values, mask, nchannels=2, cyclic=False):
        nsamples = len(values) // nchannels
        data = struct.pack(f"<{len(values)}h", *values)
        opencmd = f"OPEN {device} {nsamples} {mask}" + (" CYCLIC" if cyclic else "")
        self._send(opencmd)
        self._status(opencmd)
        cmd = f"WRITEBUF {device} {len(data)}"
        self._send(cmd)
        self._status(cmd)                            # ack #1, before the payload
        self._f.write(data)
        self._f.flush()
        return self._status(cmd)                     # ack #2, after it

    def close_buffer(self, device):
        try:
            self._send(f"CLOSE {device}")
            self._status(f"CLOSE {device}")
        except (OSError, ValueError):
            pass
