#!/usr/bin/env python3
"""
Bridge MITM Proxy — intercepts VolumetricaBridge ↔ CQG WebAPI WebSocket.
Patches logon credentials and logs every protobuf message in both directions.
"""
import asyncio
import ssl
import sys
import os
import struct
import logging
from datetime import datetime, timezone, timedelta
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# ─── Configuration ─────────────────────────────────────────────────────────────
PROXY_PORT          = 443
LOGON_MIN_INTERVAL  = 15
_last_logon_time    = 0
REAL_CQG_PORT       = 443
SNI_HOST            = "demoapi.cqg.com"
HIST_SNI_HOST       = "depth-it.historical.deepcharts.com"
HIST_REAL_PORT      = 443

# Resolve CQG IP: env var (set by start.bat before hosts redirect) or lazy fallback
CQG_UPSTREAM_IP = os.environ.get("CQG_UPSTREAM_IP")
if not CQG_UPSTREAM_IP:
    import socket as _socket
    try:
        CQG_UPSTREAM_IP = _socket.getaddrinfo(SNI_HOST, REAL_CQG_PORT, _socket.AF_INET)[0][4][0]
    except Exception:
        CQG_UPSTREAM_IP = "208.48.16.22"

HIST_UPSTREAM_IP = os.environ.get("HIST_UPSTREAM_IP")
if not HIST_UPSTREAM_IP:
    import socket as _socketh
    try:
        HIST_UPSTREAM_IP = _socketh.getaddrinfo(HIST_SNI_HOST, HIST_REAL_PORT, _socketh.AF_INET)[0][4][0]
    except Exception:
        HIST_UPSTREAM_IP = CQG_UPSTREAM_IP

TARGET_PRIVATE_LABEL  = "AMPConnect"
TARGET_CLIENT_APP_ID  = "AMPConnect"
TARGET_CLIENT_VERSION = "7.0.238"

print(f"[*] Real CQG: {SNI_HOST} -> {CQG_UPSTREAM_IP}")
print(f"[*] Real Hist: {HIST_SNI_HOST} -> {HIST_UPSTREAM_IP}")

CA_DIR   = os.path.join(os.path.dirname(__file__), "mitm_ca")
CA_CERT  = os.path.join(CA_DIR, "ca.pem")
CA_KEY   = os.path.join(CA_DIR, "ca.key")
CERT     = os.path.join(CA_DIR, "cert.pem")
KEY      = os.path.join(CA_DIR, "key.pem")
LOG_DIR  = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOGFILE  = os.path.join(LOG_DIR, f"bridge_mitm_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log")

# ─── Logging ───────────────────────────────────────────────────────────────────
_file_handler   = logging.FileHandler(LOGFILE, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setLevel(logging.INFO)

_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_file_handler.setFormatter(_fmt)
_stream_handler.setFormatter(_fmt)

log = logging.getLogger("bridge-mitm")
log.setLevel(logging.DEBUG)
log.addHandler(_file_handler)
log.addHandler(_stream_handler)
log.propagate = False

# ─── Protobuf imports ──────────────────────────────────────────────────────────
_script_dir = os.path.dirname(os.path.abspath(__file__))
possible_paths = [
    os.path.join(_script_dir, "cqg_test"),
    os.path.join(_script_dir, "helpful", "cqg_test"),
]
_parent = os.path.dirname(_script_dir)
while _parent and _parent != os.path.dirname(_parent):
    _candidate = os.path.join(_parent, "cqg_test")
    if os.path.isdir(_candidate) and _candidate not in possible_paths:
        possible_paths.append(_candidate)
    _parent = os.path.dirname(_parent)

PROTOBUF_AVAILABLE = False
for _path in possible_paths:
    if os.path.exists(_path):
        sys.path.insert(0, os.path.abspath(_path))
        try:
            from WebAPI.webapi_2_pb2 import ClientMsg, ServerMsg, InformationReport
            from WebAPI.user_session_2_pb2 import LogonResult
            from WebAPI.historical_2_pb2 import TimeBarReport, TimeBarRequest
            from WebAPI.market_data_2_pb2 import (
                MarketDataSubscription, MarketDataSubscriptionStatus, RealTimeMarketData, Quote
            )
            PROTOBUF_AVAILABLE = True
            log.info(f"[IMPORT] CQG protobufs loaded from: {_path}")
            break
        except Exception as _e:
            log.warning(f"[IMPORT] Failed from {_path}: {_e}")
            if os.path.abspath(_path) in sys.path:
                sys.path.remove(os.path.abspath(_path))

if not PROTOBUF_AVAILABLE:
    log.error("[!] CQG protobufs NOT found — patching and decoding will NOT work!")


# ─── CA / Certificate management ───────────────────────────────────────────────
CERT_SAN_DOMAINS = [
    "demoapi.cqg.com",
    "api.cqg.com",
    "depth-it.historical.deepcharts.com",
    "data-b.historical.deepcharts.com",
]

def ensure_ca():
    os.makedirs(CA_DIR, exist_ok=True)
    ca_exists  = os.path.exists(CA_CERT) and os.path.exists(CA_KEY)
    srv_exists = os.path.exists(CERT)    and os.path.exists(KEY)

    if ca_exists and srv_exists:
        log.info("[CA] Using existing CA and server certificates.")
        return

    now = datetime.now(timezone.utc)

    if ca_exists:
        log.info("[CA] Loading existing CA …")
        try:
            with open(CA_CERT, "rb") as f: ca_cert = x509.load_pem_x509_certificate(f.read())
            with open(CA_KEY,  "rb") as f: ca_key  = serialization.load_pem_private_key(f.read(), password=None)
        except Exception as e:
            log.warning(f"[CA] Failed to load existing CA ({e}), regenerating …")
            ca_exists = False

    if not ca_exists:
        log.info("[CA] Generating new CA …")
        ca_key  = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Bridge MITM CA")])
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(ca_name).issuer_name(ca_name)
            .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(ca_key, hashes.SHA256())
        )
        with open(CA_CERT, "wb") as f: f.write(ca_cert.public_bytes(serialization.Encoding.PEM))
        with open(CA_KEY,  "wb") as f: f.write(ca_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
        log.info(f"[CA] CA saved to {CA_CERT}")

    if not srv_exists:
        log.info("[CA] Generating server certificate …")
        srv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, SNI_HOST)]))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in CERT_SAN_DOMAINS]), critical=False)
            .sign(srv_key, hashes.SHA256())
        )
        srv_cert = (
            x509.CertificateBuilder()
            .subject_name(csr.subject).issuer_name(ca_cert.subject)
            .public_key(csr.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in CERT_SAN_DOMAINS]), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        with open(CERT, "wb") as f: f.write(srv_cert.public_bytes(serialization.Encoding.PEM))
        with open(KEY,  "wb") as f: f.write(srv_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
        log.info("[CA] Server certificate generated.")


# ─── Connection keepalive ────────────────────────────────────────────────────────
PING_INTERVAL = 10

async def upstream_keepalive(cqg_w):
    """Send periodic PING frames upstream to CQG to prevent NAT/firewall timeouts."""
    try:
        while True:
            await asyncio.sleep(PING_INTERVAL)
            raw = build_ws_frame(0x9, b"", fin=1, mask=True)
            try:
                cqg_w.write(raw)
                await asyncio.wait_for(cqg_w.drain(), timeout=5)
            except Exception:
                break
    except asyncio.CancelledError:
        pass


async def upstream_health_check(upstream, interval=3):
    """Exit if upstream writer is closing."""
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                if upstream._aborted or (upstream.writer and upstream.writer.is_closing()):
                    return
            except Exception:
                return
    except asyncio.CancelledError:
        pass


async def respond_pong(cqg_w):
    """Send an unsolicited PONG to CQG (connection keepalive)."""
    try:
        raw = build_ws_frame(0xA, b"", fin=1, mask=True)
        cqg_w.write(raw)
        await asyncio.wait_for(cqg_w.drain(), timeout=5)
    except Exception:
        pass


# ─── Upstream connection manager (transparent reconnect) ─────────────────────────
class UpstreamConnection:
    """Wraps the CQG upstream connection with transparent reconnection."""

    def __init__(self, host, port, ssl_ctx, sni_host, handshake_bytes):
        self._host = host
        self._port = port
        self._ctx = ssl_ctx
        self._sni = sni_host
        self._handshake = handshake_bytes
        self._replay_buffer = []
        self._capturing = True
        self._aborted = False
        self._reconnect_buf = []  # data sent during reconnect window
        self._reconnect_buf_lock = asyncio.Lock()
        self.writer = None
        self.reader = None

    def stop_capture(self):
        self._capturing = False

    def capture(self, frame_bytes):
        if self._capturing:
            self._replay_buffer.append(frame_bytes)
            if len(self._replay_buffer) >= 50:
                self._capturing = False

    async def _write_with_lock(self, data):
        """Write data, buffering during reconnect if writer is being swapped."""
        async with self._reconnect_buf_lock:
            if self._reconnect_buf is not None:
                self._reconnect_buf.append(data)
                return
        if self.writer:
            try:
                self.writer.write(data)
                await asyncio.wait_for(self.writer.drain(), timeout=5)
            except Exception:
                pass

    async def _close_old(self):
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass

    async def connect(self):
        self.reader, self.writer = await asyncio.open_connection(
            self._host, self._port, ssl=self._ctx, server_hostname=self._sni)
        self.writer.write(self._handshake)
        await self.writer.drain()
        for frame in self._replay_buffer:
            self.writer.write(frame)
        await self.writer.drain()

    async def reconnect(self):
        await asyncio.sleep(1)
        old_writer = self.writer
        self.writer = None  # t1 writes will be buffered
        new_reader, new_writer = await asyncio.open_connection(
            self._host, self._port, ssl=self._ctx, server_hostname=self._sni)
        self._aborted = False
        try:
            new_writer.write(self._handshake)
            await new_writer.drain()
            for frame in self._replay_buffer:
                new_writer.write(frame)
            # Flush buffered data from reconnect window
            async with self._reconnect_buf_lock:
                for frame in self._reconnect_buf:
                    new_writer.write(frame)
                self._reconnect_buf = []
            await new_writer.drain()
        except Exception:
            pass
        # Atomically swap
        self.reader, self.writer = new_reader, new_writer
        if old_writer:
            try:
                old_writer.close()
                await old_writer.wait_closed()
            except Exception:
                pass
        log.info("[RECONNECT] Upstream CQG reconnected and replay complete.")

    def abort(self):
        """Force-close upstream — uses transport.abort() to unblock t2's read."""
        if self._aborted:
            return
        self._aborted = True
        try:
            if self.writer:
                transport = getattr(self.writer, '_transport', None)
                if transport:
                    transport.abort()
                else:
                    self.writer.close()
        except Exception:
            pass

    async def close(self):
        await self._close_old()


# ─── WebSocket frame builder / extractor ────────────────────────────────────
def build_ws_frame(opcode: int, payload: bytes, fin: int = 1, mask: bool = True) -> bytes:
    hdr = bytearray([(0x80 if fin else 0) | opcode])
    mask_bit = 0x80 if mask else 0
    plen = len(payload)
    if plen < 126:
        hdr.append(mask_bit | plen)
    elif plen < 65536:
        hdr.extend([mask_bit | 126, (plen >> 8) & 0xFF, plen & 0xFF])
    else:
        hdr.extend([mask_bit | 127] + list(struct.pack(">Q", plen)))
    if mask:
        mk = os.urandom(4)
        hdr.extend(mk)
        return bytes(hdr) + bytes(b ^ mk[i % 4] for i, b in enumerate(payload))
    return bytes(hdr) + payload


class FrameBuffer:
    def __init__(self): self.buf = bytearray()
    def feed(self, data: bytes): self.buf.extend(data)

    def extract_frame(self):
        if len(self.buf) < 2: return None
        b0, b1  = self.buf[0], self.buf[1]
        fin     = (b0 >> 7) & 1
        opcode  = b0 & 0x0F
        masked  = (b1 >> 7) & 1
        plen    = b1 & 0x7F
        pos     = 2
        if plen == 126:
            if len(self.buf) < pos + 2: return None
            plen = struct.unpack(">H", self.buf[pos:pos+2])[0]; pos += 2
        elif plen == 127:
            if len(self.buf) < pos + 8: return None
            plen = struct.unpack(">Q", self.buf[pos:pos+8])[0]; pos += 8
        mask_key = None
        if masked:
            if len(self.buf) < pos + 4: return None
            mask_key = bytes(self.buf[pos:pos+4]); pos += 4
        if len(self.buf) < pos + plen: return None
        payload   = bytes(self.buf[pos : pos + plen])
        raw_frame = bytes(self.buf[:pos + plen])
        self.buf  = self.buf[pos + plen:]
        return opcode, masked, mask_key, payload, raw_frame, fin


# ─── Buffered writer wrapper (for historical — buffers until real writer set) ──
class BufferedWriter:
    """Holds a reference to a real writer; buffers writes until writer is assigned."""

    def __init__(self):
        self._writer = None
        self._buf = []
        self._set = asyncio.Event()

    @property
    def writer(self):
        return self._writer

    @writer.setter
    def writer(self, w):
        self._writer = w
        if w is not None:
            for data in self._buf:
                w.write(data)
            self._buf.clear()
            self._set.set()

    def write(self, data):
        if self._writer is not None:
            self._writer.write(data)
        else:
            self._buf.append(data)

    async def drain(self):
        if self._writer is not None:
            await self._writer.drain()

    def capture(self, _x):
        pass

    async def wait_ready(self):
        if self._writer is None:
            await asyncio.wait_for(self._set.wait(), timeout=30)


# ─── Protobuf decoders ──────────────────────────────────────────────────────────
def log_client_msg(payload: bytes, mask_key: bytes):
    if not PROTOBUF_AVAILABLE: return
    try:
        raw = bytearray(payload)
        for i in range(len(raw)): raw[i] ^= mask_key[i % 4]
        msg = ClientMsg()
        msg.ParseFromString(bytes(raw))

        if msg.HasField("logon"):
            g = msg.logon
            log.info(f"  [C->S] LOGON: user='{g.user_name}' private_label='{g.private_label}' "
                     f"client_app_id='{g.client_app_id}' version='{g.client_version}'")

        if msg.HasField("logoff"):
            log.info("  [C->S] LOGOFF requested by client")

        if msg.HasField("ping"):
            log.debug("  [C->S] PING")
        if msg.HasField("pong"):
            log.debug("  [C->S] PONG")

        for req in msg.market_data_subscriptions:
            log.info(f"  [C->S] MARKET_DATA_SUBSCRIBE: contract_id={req.contract_id} "
                     f"request_id={req.request_id} level={req.level}")

        for req in msg.time_bar_requests:
            p = req.time_bar_parameters if req.HasField("time_bar_parameters") else None
            if p:
                log.info(f"  [C->S] TIME_BAR_REQUEST: request_id={req.request_id} "
                         f"contract_id={p.contract_id} bar_unit={p.bar_unit} "
                         f"unit_number={p.unit_number} "
                         f"from={p.from_utc_time} to={p.to_utc_time} "
                         f"request_type={req.request_type}")
            else:
                log.info(f"  [C->S] TIME_BAR_REQUEST: request_id={req.request_id} (no params)")

        for req in msg.non_timed_bar_requests:
            log.info(f"  [C->S] NON_TIMED_BAR_REQUEST: request_id={req.request_id}")
        for req in msg.time_and_sales_requests:
            log.info(f"  [C->S] TIME_AND_SALES_REQUEST: request_id={req.request_id}")
        for req in msg.information_requests:
            log.info(f"  [C->S] INFORMATION_REQUEST: id={req.id} subscribe={req.subscribe}")
        for req in msg.trade_subscriptions:
            log.info(f"  [C->S] TRADE_SUBSCRIPTION: id={req.id}")
        for req in msg.order_requests:
            log.info(f"  [C->S] ORDER_REQUEST: id={req.request_id}")

    except Exception as e:
        unmasked = bytearray(payload)
        for i in range(len(unmasked)): unmasked[i] ^= mask_key[i % 4]
        log.warning(f"  [C->S] Could not decode ClientMsg: {e}")


def log_server_msg(payload: bytes):
    if not PROTOBUF_AVAILABLE: return
    try:
        msg = ServerMsg()
        msg.ParseFromString(payload)

        if msg.HasField("logon_result"):
            r = msg.logon_result
            level = logging.INFO if r.result_code == 0 else logging.ERROR
            log.log(level, f"  [S->C] LOGON_RESULT: code={r.result_code} "
                           f"text='{r.text_message}' base_time='{r.base_time}' "
                           f"user_id={r.user_id} "
                           f"proto={r.protocol_version_major}.{r.protocol_version_minor}")
            if r.result_code != 0:
                log.error(f"  [S->C] *** LOGON FAILED *** code={r.result_code} — '{r.text_message}'")

        if msg.HasField("logged_off"):
            lo = msg.logged_off
            log.warning(f"  [S->C] LOGGED_OFF: code={lo.result_code} text='{lo.text_message}'")

        if msg.HasField("ping"):
            log.debug("  [S->C] PING from server")
        if msg.HasField("pong"):
            log.debug("  [S->C] PONG from server")

        for um in msg.user_messages:
            log.info(f"  [S->C] USER_MESSAGE: type={um.message_type} "
                     f"subject='{um.subject}' text='{um.text}'")

        for ir in msg.information_reports:
            log.info(f"  [S->C] INFORMATION_REPORT: id={ir.id} "
                     f"status={ir.status_code} complete={ir.is_report_complete} "
                     f"text='{ir.text_message}'")
            if ir.HasField("symbol_resolution_report"):
                srr = ir.symbol_resolution_report
                try:
                    cm = srr.contract_metadata
                    log.info(f"    SYMBOL: contract_id={cm.contract_id} "
                             f"symbol='{cm.contract_symbol}' cqg='{cm.cqg_contract_symbol}' "
                             f"desc='{cm.description}' "
                             f"tick={cm.tick_size} tickval={cm.tick_value}")
                except Exception as e:
                    log.debug(f"    SYMBOL decode skipped: {e}")
            if ir.HasField("accounts_report"):
                for brok in ir.accounts_report.brokerages:
                    log.info(f"    BROKERAGE: id={brok.id} name='{brok.name}'")
                    for ss in brok.sales_series:
                        for acct in ss.accounts:
                            try:
                                brok_id = acct.brokerage_account_id
                            except Exception:
                                brok_id = '?'
                            log.info(f"      ACCOUNT: id={acct.account_id} "
                                     f"name='{acct.name}' brok_id='{brok_id}'")

        for s in msg.market_data_subscription_statuses:
            level = logging.INFO if s.status_code == 0 else logging.WARNING
            log.log(level, f"  [S->C] MKT_DATA_STATUS: contract_id={s.contract_id} "
                           f"status_code={s.status_code} level={s.level} "
                           f"text='{s.text_message}'")

        for rtd in msg.real_time_market_data:
            quote_types = {}
            prices = set()
            for q in rtd.quotes:
                qt = q.type
                quote_types[qt] = quote_types.get(qt, 0) + 1
                if qt in (0, 1, 2) and q.HasField("scaled_price"):
                    prices.add(q.scaled_price)
            type_names = {0:'TRD',1:'BID',2:'ASK',3:'BID_L2',4:'ASK_L2',5:'STL'}
            desc = ', '.join(f"{type_names.get(t,t)}={c}" for t,c in sorted(quote_types.items()))
            mv_count = len(rtd.market_values)
            mv_info = f" mv={mv_count}" if mv_count else ""
            price_info = f" prices={prices}" if prices else ""
            level = logging.INFO if 0 in quote_types and not rtd.is_snapshot else logging.DEBUG
            log.log(level, f"  [S->C] REAL_TIME_DATA: contract_id={rtd.contract_id} "
                      f"snapshot={rtd.is_snapshot} quotes={len(rtd.quotes)} [{desc}]{mv_info}{price_info}")
            for mv in rtd.market_values:
                o = mv.scaled_open_price if mv.HasField("scaled_open_price") else None
                h = mv.scaled_high_price if mv.HasField("scaled_high_price") else None
                l = mv.scaled_low_price if mv.HasField("scaled_low_price") else None
                c = mv.scaled_close_price if mv.HasField("scaled_close_price") else None
                ys = mv.scaled_yesterday_settlement if mv.HasField("scaled_yesterday_settlement") else None
                log.info(f"    [MV] contract={rtd.contract_id} O={o} H={h} L={l} C={c} YSettl={ys}")

        for tbr in msg.time_bar_reports:
            level = logging.INFO if tbr.status_code == 0 else logging.ERROR
            log.log(level, f"  [S->C] TIME_BAR_REPORT: request_id={tbr.request_id} "
                           f"status={tbr.status_code} bars={len(tbr.time_bars)} "
                           f"complete={tbr.is_report_complete} "
                           f"reached_start={tbr.reached_start_of_data} "
                           f"text='{tbr.text_message}'")

        for nbr in msg.non_timed_bar_reports:
            level = logging.INFO if nbr.status_code == 0 else logging.ERROR
            log.log(level, f"  [S->C] NON_TIMED_BAR_REPORT: request_id={nbr.request_id} "
                           f"status={nbr.status_code} bars={len(nbr.non_timed_bars)} "
                           f"complete={nbr.is_report_complete} text='{nbr.text_message}'")

        for tsr in msg.time_and_sales_reports:
            level = logging.INFO if tsr.result_code == 0 else logging.ERROR
            log.log(level, f"  [S->C] TIME_AND_SALES_REPORT: request_id={tsr.request_id} "
                           f"result_code={tsr.result_code} ticks={len(tsr.quotes)} "
                           f"complete={tsr.is_report_complete} text='{tsr.text_message}'")

        for os_ in msg.order_statuses:
            log.info(f"  [S->C] ORDER_STATUS: order_id={os_.order_id}")
        for ps in msg.position_statuses:
            log.info(f"  [S->C] POSITION_STATUS: account_id={ps.account_id}")

    except Exception as e:
        log.error(f"  [S->C] Could not decode ServerMsg: {e}")


# ─── Logon patcher ─────────────────────────────────────────────────────────────
async def patch_logon_protobuf(payload: bytes, mask_key: bytes, fin: int, opcode: int):
    raw = bytearray(payload)
    for i in range(len(raw)): raw[i] ^= mask_key[i % 4]
    msg = ClientMsg()
    try:
        msg.ParseFromString(bytes(raw))
        if msg.HasField("logon"):
            global _last_logon_time
            now = datetime.now(timezone.utc).timestamp()
            elapsed = now - _last_logon_time
            if elapsed < LOGON_MIN_INTERVAL:
                wait = LOGON_MIN_INTERVAL - elapsed
                log.warning(f"[RATE] Last logon was {elapsed:.1f}s ago — delaying {wait:.0f}s to avoid rate-limit")
                await asyncio.sleep(wait)
            _last_logon_time = datetime.now(timezone.utc).timestamp()
            old_pl = msg.logon.private_label
            old_ci = msg.logon.client_app_id
            msg.logon.private_label  = TARGET_PRIVATE_LABEL
            msg.logon.client_app_id  = TARGET_CLIENT_APP_ID
            if msg.logon.client_version:
                msg.logon.client_version = TARGET_CLIENT_VERSION
            log.info("  [PATCH] *** LOGON INTERCEPTED AND PATCHED ***")
            log.info(f"  [PATCH] private_label : '{old_pl}' -> '{TARGET_PRIVATE_LABEL}'")
            log.info(f"  [PATCH] client_app_id : '{old_ci}' -> '{TARGET_CLIENT_APP_ID}'")
            log.info(f"  [PATCH] client_version: -> '{TARGET_CLIENT_VERSION}'")
            return build_ws_frame(opcode, msg.SerializeToString(), fin=fin, mask=True)
    except Exception as e:
        log.error(f"  [PATCH] Failed to parse/patch logon: {e}")
    return None


# ─── Client → CQG forwarder ────────────────────────────────────────────────────
async def forward_client_to_cqg(client_r, upstream, initial_remaining=b"", http_done=False, is_historical=False):
    buf = FrameBuffer()
    if initial_remaining:
        buf.feed(initial_remaining)

    # If using BufferedWriter, wait for writer to be assigned
    if hasattr(upstream, 'wait_ready'):
        try:
            await upstream.wait_ready()
        except asyncio.TimeoutError:
            log.error("[!] Timed out waiting for upstream writer on historical connection")
            return

    while True:
        try:
            while True:
                frame = buf.extract_frame()
                if not frame: break
                opcode, masked, mask_key, payload, raw_frame, fin = frame

                if opcode == 8:
                    if is_historical:
                        log.info("  [C->S] CLOSE frame on HISTORICAL — forwarding to upstream")
                        try:
                            upstream.writer.write(raw_frame)
                            await asyncio.wait_for(upstream.writer.drain(), timeout=3)
                        except Exception:
                            pass
                        return
                    log.info("  [C->S] WebSocket CLOSE frame — client is disconnecting.")
                    try:
                        upstream.writer.write(raw_frame)
                        await asyncio.wait_for(upstream.writer.drain(), timeout=5)
                    except Exception:
                        pass
                    return
                elif opcode == 9:
                    log.debug("  [C->S] PING")
                    try:
                        upstream.writer.write(raw_frame)
                        await asyncio.wait_for(upstream.writer.drain(), timeout=5)
                    except Exception:
                        if not is_historical:
                            upstream.abort()
                    continue
                elif opcode == 10:
                    log.debug("  [C->S] PONG")
                    try:
                        upstream.writer.write(raw_frame)
                        await asyncio.wait_for(upstream.writer.drain(), timeout=5)
                    except Exception:
                        if not is_historical:
                            upstream.abort()
                    continue

                if opcode == 2 and masked:
                    if not is_historical:
                        log_client_msg(payload, mask_key)
                        patched = await patch_logon_protobuf(payload, mask_key, fin, opcode)
                        out = patched if patched else raw_frame
                        upstream.capture(out)
                        try:
                            upstream.writer.write(out)
                            await asyncio.wait_for(upstream.writer.drain(), timeout=5)
                        except Exception:
                            if not is_historical:
                                upstream.abort()
                    else:
                        try:
                            upstream.writer.write(raw_frame)
                            await asyncio.wait_for(upstream.writer.drain(), timeout=5)
                        except Exception:
                            pass
                else:
                    try:
                        upstream.writer.write(raw_frame)
                        await asyncio.wait_for(upstream.writer.drain(), timeout=5)
                    except Exception:
                        if not is_historical:
                            upstream.abort()

            chunk = await client_r.read(65536)
            if not chunk:
                log.info("  [C->S] Client closed connection.")
                break
            buf.feed(chunk)

            if not http_done:
                if b"\r\n\r\n" not in buf.buf:
                    continue
                idx = buf.buf.find(b"\r\n\r\n") + 4
                http_part = bytes(buf.buf[:idx])
                log.info(f"  [C->S] HTTP Upgrade: {http_part.splitlines()[0].decode(errors='replace')}")
                try:
                    upstream.writer.write(http_part)
                    await asyncio.wait_for(upstream.writer.drain(), timeout=5)
                except Exception:
                    pass
                buf.buf = buf.buf[idx:]
                http_done = True
                log.info("  [CLIENT->CQG] HTTP handshake forwarded.")

            try:
                await asyncio.wait_for(upstream.writer.drain(), timeout=5)
            except Exception:
                if not is_historical:
                    upstream.abort()

        except asyncio.CancelledError:
            raise
        except Exception as e:
            estr = str(e)
            if any(x in estr.lower() for x in ["close notify", "shutdown timed out", "connection reset", "broken pipe"]):
                log.info(f"  [C->S] Client disconnected ({e}).")
                break
            if not is_historical and upstream._aborted:
                await asyncio.sleep(1)
            else:
                log.error(f"  [C->S] Error: {e}")


# ─── CQG → Client forwarder ────────────────────────────────────────────────────
async def forward_cqg_to_client(cqg_r, client_w, cqg_w=None, is_historical=False):
    http_done = False
    buf = FrameBuffer()
    try:
        while True:
            try:
                chunk = await cqg_r.read(65536)
            except Exception:
                log.info("  [S->C] CQG connection lost.")
                break
            if not chunk:
                log.info("  [S->C] CQG server closed connection.")
                break
            buf.feed(chunk)

            if not http_done:
                if b"\r\n\r\n" not in buf.buf:
                    continue
                idx = buf.buf.find(b"\r\n\r\n") + 4
                http_part = bytes(buf.buf[:idx])
                log.info(f"  [S->C] HTTP Response: {http_part.splitlines()[0].decode(errors='replace')}")
                client_w.write(http_part)
                await client_w.drain()
                buf.buf = buf.buf[idx:]
                http_done = True
                log.info("  [CQG->CLIENT] HTTP response forwarded.")

            while True:
                frame = buf.extract_frame()
                if not frame:
                    break
                opcode, masked, mask_key, payload, raw_frame, fin = frame

                if opcode == 8:
                    log.warning("  [S->C] WebSocket CLOSE frame from CQG.")
                    client_w.write(raw_frame)
                    await client_w.drain()
                    return
                elif opcode == 9:
                    log.debug("  [S->C] PING from server")
                    if cqg_w is not None:
                        cqg_w.write(build_ws_frame(0xA, payload, fin=1, mask=False))
                    client_w.write(raw_frame)
                    await client_w.drain()
                    if cqg_w is not None:
                        await asyncio.wait_for(cqg_w.drain(), timeout=5)
                    continue
                elif opcode == 10:
                    log.debug("  [S->C] PONG from server")
                    client_w.write(raw_frame)
                    await client_w.drain()
                    continue

                if opcode == 2:
                    if not is_historical:
                        result = process_and_patch_server_msg(payload, fin, opcode)
                        client_w.write(result if result is not None else raw_frame)
                    else:
                        client_w.write(raw_frame)
                else:
                    client_w.write(raw_frame)

            await client_w.drain()

    except Exception as e:
        log.error(f"  [S->C] Error: {e}")


def process_and_patch_server_msg(payload: bytes, fin: int, opcode: int):
    if PROTOBUF_AVAILABLE:
        try:
            log_server_msg(payload)
        except Exception as e:
            log.warning(f"  [PROCESS] log_server_msg failed: {e}")
    return None


# ─── Connection handler ─────────────────────────────────────────────────────────
async def handle(client_r, client_w):
    peer = client_w.get_extra_info("peername")
    log.info(f"[+] Client connected from {peer}")

    sslobj = client_w.get_extra_info("ssl_object")
    sni = sslobj.server_hostname if sslobj else None
    log.info(f"[SNI] Requested SNI server_hostname: '{sni}'")

    handshake_bytes, remaining = b"", b""
    path = ""
    http_done = False
    try:
        initial_buf = bytearray()
        while b"\r\n\r\n" not in initial_buf and len(initial_buf) < 8192:
            chunk = await asyncio.wait_for(client_r.read(4096), timeout=2.0)
            if not chunk:
                break
            initial_buf.extend(chunk)
        if b"\r\n\r\n" in initial_buf:
            idx = initial_buf.find(b"\r\n\r\n") + 4
            handshake_bytes = bytes(initial_buf[:idx])
            remaining = bytes(initial_buf[idx:])
            http_done = True
            first_line = handshake_bytes.splitlines()[0].decode(errors='replace')
            log.info(f"[+] Client request line: {first_line}")
            parts = first_line.split()
            if len(parts) > 1:
                path = parts[1]
    except Exception as e:
        log.warning(f"[-] Failed to read HTTP handshake: {e}")
        if initial_buf:
            remaining = bytes(initial_buf)

    is_historical = False
    if sni and "historical" in sni.lower():
        is_historical = True
    elif not sni and http_done and (path == "/" or "443" not in path):
        is_historical = True

    if handshake_bytes and not is_historical:
        upstream = UpstreamConnection(CQG_UPSTREAM_IP, REAL_CQG_PORT, client_ctx, SNI_HOST, handshake_bytes)
    else:
        upstream = None

    if remaining and upstream is not None:
        upstream.capture(remaining)

    cqg_capture = BufferedWriter() if is_historical else None

    # For CQG: remaining is already in upstream replay buffer (sent via connect())
    # For historical: no replay buffer, so pass remaining as initial data
    t1 = asyncio.create_task(forward_client_to_cqg(
        client_r,
        cqg_capture if is_historical else upstream,
        initial_remaining=remaining if is_historical else b"",
        http_done=http_done,
        is_historical=is_historical
    ))

    first_connect = True
    while True:
        try:
            if is_historical:
                log.info(f"[+] Routing '{sni or 'None'}' to vol_hist_server at 127.0.0.1:12010")
                cqg_r, cqg_w = await asyncio.open_connection("127.0.0.1", 12010, ssl=None)
                cqg_capture.writer = cqg_w
                log.info("[+] vol_hist_server connection established (127.0.0.1:12010)")
                if handshake_bytes:
                    cqg_w.write(handshake_bytes)
                    await asyncio.wait_for(cqg_w.drain(), timeout=5)
            else:
                if upstream is None:
                    upstream = UpstreamConnection(CQG_UPSTREAM_IP, REAL_CQG_PORT, client_ctx, SNI_HOST, handshake_bytes)
                if first_connect:
                    await upstream.connect()
                    first_connect = False
                else:
                    await upstream.reconnect()
                cqg_r, cqg_w = upstream.reader, upstream.writer
                log.info(f"[+] Upstream CQG connection established ({CQG_UPSTREAM_IP}:{REAL_CQG_PORT})")

        except Exception as e:
            log.error(f"[!] Cannot establish upstream connection: {e}")
            if upstream is not None:
                await upstream.close()
            client_w.close()
            return

        if is_historical:
            t2 = asyncio.create_task(forward_cqg_to_client(cqg_r, client_w, cqg_w=cqg_w, is_historical=True))
            t3 = None
            t4 = None
        else:
            t2 = asyncio.create_task(forward_cqg_to_client(cqg_r, client_w, cqg_w=cqg_w, is_historical=False))
            t3 = asyncio.create_task(upstream_keepalive(cqg_w))
            t4 = asyncio.create_task(upstream_health_check(upstream))

        poll_interval = 0.05
        while True:
            if t1.done():
                break
            if t2.done():
                break
            if t3 is not None and t3.done():
                t3 = asyncio.create_task(upstream_keepalive(cqg_w))
            if t4 is not None and t4.done():
                t4 = asyncio.create_task(upstream_health_check(upstream))
            await asyncio.sleep(poll_interval)

        if t1.done():
            log.info("[-] Client disconnected.")
            break

        if is_historical:
            log.info("[-] Historical upstream connection lost — will reconnect on next client request")
            break

        if t4 is not None and not t4.done():
            t4.cancel()

        log.info("[RECONNECT] CQG upstream connection lost. Reconnecting transparently...")
        await asyncio.sleep(1)

    if upstream is not None:
        await upstream.close()
    client_w.close()
    try:
        await client_w.wait_closed()
    except Exception:
        pass
    log.info(f"[-] Disconnected {peer}")


# ─── Main ───────────────────────────────────────────────────────────────────────
async def main():
    global client_ctx
    ensure_ca()

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(CERT, KEY)

    client_ctx = ssl.create_default_context()
    client_ctx.check_hostname = False
    client_ctx.verify_mode    = ssl.CERT_NONE

    server = await asyncio.start_server(handle, "0.0.0.0", PROXY_PORT, ssl=server_ctx)
    log.info("=" * 60)
    log.info(f"[*] Bridge MITM Proxy listening on 0.0.0.0:{PROXY_PORT}")
    log.info(f"[*] Upstream CQG: {CQG_UPSTREAM_IP}:{REAL_CQG_PORT} (SNI={SNI_HOST})")
    log.info(f"[*] Upstream Hist: {HIST_UPSTREAM_IP}:{HIST_REAL_PORT} (SNI={HIST_SNI_HOST})")
    log.info(f"[*] Full log: {LOGFILE}")
    log.info("=" * 60)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("[*] Shutdown")
