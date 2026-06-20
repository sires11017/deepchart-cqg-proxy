"""
ipc_mitm.py — Monitors the WebSocket IPC between Deepchart.exe and VolumetricaBridge.exe.

Two modes:
  Mode 1 (listener): Connects as an additional WS client to the bridge and logs
    whatever the bridge broadcasts (bridge→client direction only).
  Mode 2 (MITM): Pre-occupies port 10050, lets bridge shift to next port,
    then forwards Deepchart↔Bridge through us with full bidirectional logging.
"""
import asyncio
import hashlib
import logging
import os
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"ipc_mitm_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE)],
    force=True,
)
log = logging.getLogger("ipc-mitm")

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
BRIDGE_EXE = os.path.join(ROOT_DIR, "patched_run/bridge/VolumetricaBridge.exe")
BRIDGE_DIR = os.path.join(ROOT_DIR, "patched_run/bridge")

PROXY_PORT = 19876  # MITM port — outside bridge's default range (10050-10500)
BRIDGE_DEFAULT_PORT = 10050
# Bridge uses PortFinder from 10050-10500 but starts at 10050, so only first ~10 ports matter
SCAN_RANGE = range(BRIDGE_DEFAULT_PORT, BRIDGE_DEFAULT_PORT + 10)

# ─── utilities ───────────────────────────────────────────────────────────────

def hexdump(data: bytes, max_len=128) -> str:
    d = data[:max_len]
    return " ".join(f"{b:02x}" for b in d) + (" ..." if len(data) > max_len else "")


def try_decode_protobuf(data: bytes) -> str:
    parts = []
    i = 0
    while i < len(data):
        key = data[i]
        field_num = key >> 3
        wire_type = key & 7
        i += 1
        if wire_type == 0:  # varint
            val = 0; shift = 0
            while i < len(data):
                b = data[i]; i += 1
                val |= (b & 0x7F) << shift; shift += 7
                if not (b & 0x80): break
            parts.append(f"f{field_num}(varint)={val}")
        elif wire_type == 2:  # length-delimited
            length = 0; shift = 0
            while i < len(data):
                b = data[i]; i += 1
                length |= (b & 0x7F) << shift; shift += 7
                if not (b & 0x80): break
            sub = data[i:i+length]; i += length
            text = "".join(c if 32 <= ord(c) < 127 else "." for c in sub.decode("utf-8", errors="replace"))
            parts.append(f"f{field_num}(len={length})={text[:80]}")
        elif wire_type == 5:  # 32-bit
            val = struct.unpack("<i", data[i:i+4])[0] if i+4 <= len(data) else 0
            parts.append(f"f{field_num}(i32)={val}"); i += 4
        elif wire_type == 1:  # 64-bit
            val = struct.unpack("<q", data[i:i+8])[0] if i+8 <= len(data) else 0
            parts.append(f"f{field_num}(i64)={val}"); i += 8
        else:
            parts.append(f"f{field_num}(wt={wire_type})")
        if len(parts) > 30:
            parts.append("..."); break
    return " | ".join(parts) if parts else "(empty)"


def find_bridge_port() -> int | None:
    return _find_bridge_port_sync()


# ─── WebSocket frame helpers (for client-mode listener) ──────────────────────

def make_ws_key() -> str:
    import base64, os
    return base64.b64encode(os.urandom(16)).decode()


def create_ws_handshake(host: str, port: int, path: str = "/") -> bytes:
    key = make_ws_key()
    return (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    ).encode()


def parse_ws_frame(data: bytes) -> list[tuple[int, bytes, bool]]:
    """Parse WebSocket frames from a buffer. Returns (opcode, payload, fin)."""
    frames = []
    i = 0
    while i < len(data):
        if i + 2 > len(data):
            break
        b0 = data[i]; b1 = data[i + 1]
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        offset = 2
        if length == 126:
            if i + 4 > len(data): break
            length = struct.unpack("!H", data[i+2:i+4])[0]
            offset = 4
        elif length == 127:
            if i + 10 > len(data): break
            length = struct.unpack("!Q", data[i+2:i+10])[0]
            offset = 10
        if masked:
            if i + offset + 4 > len(data): break
            mask_key = data[i+offset:i+offset+4]
            offset += 4
        else:
            mask_key = None
        if i + offset + length > len(data):
            break
        payload = data[i+offset:i+offset+length]
        if mask_key:
            payload = bytes(b ^ mask_key[j % 4] for j, b in enumerate(payload))
        frames.append((opcode, payload, fin))
        i += offset + length
    return frames


def _calc_consumed(buf: bytes, frames: list) -> int:
    """Calculate bytes consumed by the given parsed frames from buf."""
    consumed = 0
    for _opcode, _payload, _fin in frames:
        if consumed + 2 > len(buf):
            break
        b1 = buf[consumed + 1]
        length = b1 & 0x7F
        hdr = 2
        if length == 126:
            hdr = 4
        elif length == 127:
            hdr = 10
        masked = bool(b1 & 0x80)
        if masked:
            hdr += 4
        # Get actual payload length from the header
        if length == 126 and consumed + 4 <= len(buf):
            actual_len = struct.unpack("!H", buf[consumed+2:consumed+4])[0]
        elif length == 127 and consumed + 10 <= len(buf):
            actual_len = struct.unpack("!Q", buf[consumed+2:consumed+10])[0]
        else:
            actual_len = length
        consumed += hdr + actual_len
    return consumed


# ═══════════════════════════════════════════════════════════════════════════════
# MODE 1 — Passive WS listener (connects to bridge, logs frames)
# ═══════════════════════════════════════════════════════════════════════════════

async def mode_listener():
    """Connect to the bridge's WS and log every frame it sends."""
    bridge_port = await _wait_for_bridge()
    if not bridge_port:
        return

    log.info(f"[*] Connecting to bridge WS on port {bridge_port}...")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", bridge_port), timeout=5
        )
    except Exception as e:
        log.error(f"  Connection failed: {e}")
        return

    # Send WS upgrade
    req = create_ws_handshake("127.0.0.1", bridge_port)
    writer.write(req)
    await writer.drain()

    # Read HTTP response
    resp = await reader.readuntil(b"\r\n\r\n")
    log.info(f"  WS handshake response:\n{resp.decode(errors='replace')}")

    if b"101" not in resp.split(b" ")[:2][1]:
        log.error("  WebSocket upgrade rejected!")
        writer.close()
        return

    log.info("  Connected! Listening for frames... (Ctrl+C to stop)")
    log.info("=" * 60)

    buf = b""
    frame_count = 0
    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=120)
            if not chunk:
                break
            buf += chunk
            # Parse as many complete frames as possible
            while True:
                frames = parse_ws_frame(buf)
                if not frames:
                    break  # need more data
                # Calculate how many bytes the parsed frames consumed
                consumed = _calc_consumed(buf, frames)
                if consumed == 0:
                    break
                for opcode, payload, fin in frames:
                    frame_count += 1
                    if opcode == 0x8:
                        log.info(f"  [!] Close frame: {payload.hex()}")
                        break
                    elif opcode == 0x9:
                        log.info(f"  [PING] {payload.hex()}")
                        continue
                    elif opcode == 0xA:
                        log.info(f"  [PONG] {payload.hex()}")
                        continue
                    elif opcode == 0x2:
                        log.info(
                            f"  [FRAME #{frame_count}] BINARY ({len(payload)} bytes)\n"
                            f"    HEX: {hexdump(payload)}\n"
                            f"    PROTO: {try_decode_protobuf(payload)}"
                        )
                    else:
                        log.info(f"  [FRAME #{frame_count}] opcode={opcode} ({len(payload)} bytes)")
                buf = buf[consumed:]
    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
        log.info("  Connection closed (timeout/disconnect)")
    finally:
        writer.close()


# ═══════════════════════════════════════════════════════════════════════════════
# MODE 2 — Full MITM (occupy 10050, patch bridge.port, forward with logging)
# ═══════════════════════════════════════════════════════════════════════════════

BRIDGE_PORT_FILE = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "Volumetrica",
    "bridge.port",
)


def _write_port_to_file(port: int):
    """Write port number to bridge.port so Deepchart connects to us."""
    os.makedirs(os.path.dirname(BRIDGE_PORT_FILE), exist_ok=True)
    with open(BRIDGE_PORT_FILE, "w") as f:
        f.write(str(port))
    log.info(f"  bridge.port → {port}")


DEEPCHART_EXE = os.path.join(ROOT_DIR, "patched_run/Deepchart.exe")
DEEPCHART_DIR = os.path.join(ROOT_DIR, "patched_run")


def _launch_deepchart():
    if not os.path.exists(DEEPCHART_EXE):
        log.error(f"  Deepchart not found at {DEEPCHART_EXE}")
        return
    subprocess.Popen(
        [DEEPCHART_EXE],
        cwd=DEEPCHART_DIR,
    )
    log.info(f"  Deepchart launched from {DEEPCHART_DIR}")


_AUTO_LAUNCH = False


async def mode_mitm(auto_launch=False):
    global _AUTO_LAUNCH
    _AUTO_LAUNCH = auto_launch
    """
    Full MITM flow:
      1. Kill old bridge
      2. Start proxy on PROXY_PORT (outside bridge's range)
      3. Launch bridge — uses BRIDGE_DEFAULT_PORT (10050)
      4. Wait and find the real bridge port (typically 10050)
      5. Overwrite bridge.port with PROXY_PORT
      6. Launch Deepchart — reads bridge.port, connects to our proxy
      7. Forward Deepchart ↔ Bridge with full WS frame logging
    """
    log.info("[*] Full MITM mode")
    log.info("  Killing old bridge...")
    _kill_bridge()
    await asyncio.sleep(2)

    real_bridge_port = None

    async def handler(dc_r, dc_w):
        nonlocal real_bridge_port
        addr = dc_w.get_extra_info("peername")
        log.info(f"[+] Deepchart connected from {addr}")

        if not real_bridge_port:
            log.error("  Bridge port unknown — refusing")
            dc_w.close()
            return

        try:
            b_r, b_w = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", real_bridge_port), timeout=5
            )
        except Exception as e:
            log.error(f"  Cannot reach bridge:{real_bridge_port} — {e}")
            dc_w.close()
            return

        log.info(f"  Forwarding {addr} <-> bridge:{real_bridge_port}")

        async def fwd(r, w, label):
            n = [0]
            ws_upgraded = [False]
            try:
                buf = b""
                while True:
                    chunk = await r.read(65536)
                    if not chunk:
                        break
                    n[0] += 1
                    buf += chunk

                    if not ws_upgraded[0]:
                        if b"\r\n\r\n" in buf and b"HTTP/" in buf:
                            idx = buf.index(b"\r\n\r\n") + 4
                            text = buf[:idx]
                            log.info(
                                f"  {label} #{n[0]} — {len(chunk)} bytes [HTTP]\n"
                                f"{text.decode(errors='replace')[:500]}"
                            )
                            if b"101" in buf:
                                ws_upgraded[0] = True
                                buf = buf[idx:]
                        else:
                            log.info(f"  {label} #{n[0]} — {len(chunk)} bytes [RAW]")
                    else:
                        frames = parse_ws_frame(buf)
                        if frames:
                            consumed = _calc_consumed(buf, frames)
                            log.info(
                                f"  {label} #{n[0]} — {len(chunk)} bytes, {len(frames)} frame(s)"
                            )
                            for opcode, payload, fin in frames:
                                opname = {
                                    0x1: "TEXT",
                                    0x2: "BINARY",
                                    0x8: "CLOSE",
                                    0x9: "PING",
                                    0xA: "PONG",
                                }.get(opcode, f"OP{opcode}")
                                if opcode in (0x8, 0x9, 0xA):
                                    log.info(f"    [{opname}] {payload.hex()}")
                                elif opcode == 0x2:
                                    log.info(
                                        f"    [{opname}] ({len(payload)} bytes)\n"
                                        f"      HEX: {hexdump(payload)}\n"
                                        f"      PROTO: {try_decode_protobuf(payload)}"
                                    )
                                elif opcode == 0x1:
                                    log.info(
                                        f"    [{opname}] {payload.decode(errors='replace')[:200]}"
                                    )
                            buf = buf[consumed:]
                        else:
                            log.info(f"  {label} #{n[0]} — {len(chunk)} bytes (partial)")
                    w.write(chunk)
                    await w.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass
            finally:
                try:
                    w.close()
                except Exception:
                    pass

        await asyncio.gather(
            fwd(dc_r, b_w, "D->B"),
            fwd(b_r, dc_w, "B->D"),
        )
        log.info(f"[-] {addr} disconnected")

    # Start MITM proxy server
    server = await asyncio.start_server(handler, "127.0.0.1", PROXY_PORT)
    log.info(f"  MITM listening on :{PROXY_PORT}")

    # Launch bridge (it will bind BRIDGE_DEFAULT_PORT since it's free)
    log.info("  Launching bridge...")
    _launch_bridge()

    # Wait for bridge — it can take 60-90s (CQG init)
    log.info("  Waiting for bridge (may take 60-90s)...")
    real_bridge_port = _wait_for_bridge_sync(timeout=120)
    if not real_bridge_port:
        log.error("  Bridge never started — aborting")
        server.close()
        return

    log.info(f"  Real bridge on port {real_bridge_port}")

    # Patch bridge.port so Deepchart connects to our proxy
    _write_port_to_file(PROXY_PORT)

    # Give a moment for bridge.port to be flushed to disk
    time.sleep(0.5)

    if _AUTO_LAUNCH:
        log.info("  Auto-launching Deepchart...")
        _launch_deepchart()

    log.info("=" * 60)
    log.info(f"  MITM :{PROXY_PORT} -> Bridge :{real_bridge_port}")
    log.info("  bridge.port rewritten — Deepchart will connect to proxy")
    if not _AUTO_LAUNCH:
        log.info("  Launch Deepchart.exe now")
    log.info("=" * 60)

    try:
        async with server:
            await server.serve_forever()
    except KeyboardInterrupt:
        log.info("  Shutting down MITM...")


# ─── helpers ──────────────────────────────────────────────────────────────────

def _kill_bridge():
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/IM", "VolumetricaBridge.exe"], capture_output=True)
    else:
        subprocess.run(["pkill", "-f", "VolumetricaBridge"], capture_output=True)


def _launch_bridge():
    if not os.path.exists(BRIDGE_EXE):
        log.error(f"  Bridge not found at {BRIDGE_EXE}")
        return None
    return subprocess.Popen([BRIDGE_EXE], cwd=BRIDGE_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def _wait_for_bridge(timeout=15) -> int | None:
    for i in range(timeout):
        port = _find_bridge_port_sync()
        if port:
            log.info(f"  Bridge found on port {port}")
            return port
        log.info(f"  Waiting for bridge... ({i+1}/{timeout})")
        await asyncio.sleep(1)
    return None


def _wait_for_bridge_sync(timeout=15) -> int | None:
    for i in range(timeout):
        port = _find_bridge_port_sync()
        if port:
            log.info(f"  Bridge found on port {port}")
            return port
        log.info(f"  Waiting for bridge... ({i+1}/{timeout})")
        time.sleep(1)
    return None


def _find_bridge_port_sync(fast=True) -> int | None:
    """Scan for bridge WS server, skipping PROXY_PORT.
    fast=True: only scan first 3 ports (10050-10052) for speed."""
    ports = SCAN_RANGE[:3] if fast else SCAN_RANGE
    for port in ports:
        if port == PROXY_PORT:
            continue
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", port))
            s.close()
            return port
        except (ConnectionRefusedError, OSError, TimeoutError):
            pass
        finally:
            s.close()
    return None


# ═══════════════════════════════════════════════════════════════════════════════

async def mode_listener_entry():
    log.info("=" * 60)
    log.info(f"IPC Listener — logging to {LOG_FILE}")
    log.info("=" * 60)
    _kill_bridge()
    await asyncio.sleep(2)
    _launch_bridge()
    await mode_listener()

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "listener"
    if mode == "mitm":
        asyncio.run(mode_mitm(auto_launch=False))
    elif mode == "mitm-auto":
        asyncio.run(mode_mitm(auto_launch=True))
    else:
        asyncio.run(mode_listener_entry())
