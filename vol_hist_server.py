"""
vol_hist_server.py - Historical Data TCP Proxy
Listens on port 12010, forwards raw bytes to real historical server via TLS.
The bridge_mitm_proxy handles WS frame parsing; this is just a byte-level proxy.
"""
import asyncio, logging, datetime, os, ssl

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"vol_hist_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger("vol-hist")

HOST = "0.0.0.0"
PORT = 12010

HIST_UPSTREAM_IP = os.environ.get("HIST_UPSTREAM_IP", "depth-it.historical.deepcharts.com")
HIST_SNI_HOST = "depth-it.historical.deepcharts.com"
HIST_PORT = 443


async def pipe(reader, writer, label):
    """Bidirectional byte forwarding."""
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    except Exception as e:
        log.debug(f"  [{label}] pipe done: {e}")
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle_client(bridge_r, bridge_w):
    addr = bridge_w.get_extra_info("peername")
    log.info(f"[+] Client connected from {addr}")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        real_r, real_w = await asyncio.open_connection(
            HIST_UPSTREAM_IP, HIST_PORT,
            ssl=ctx, server_hostname=HIST_SNI_HOST)
        log.info(f"[+] Upstream {HIST_UPSTREAM_IP}:{HIST_PORT} connected")
    except Exception as e:
        log.error(f"[!] Cannot connect to upstream: {e}")
        bridge_w.close()
        return

    await asyncio.gather(
        pipe(bridge_r, real_w, "C->S"),
        pipe(real_r, bridge_w, "S->C"),
    )

    await asyncio.sleep(0.1)
    try:
        real_w.close()
    except Exception:
        pass
    try:
        bridge_w.close()
    except Exception:
        pass
    log.info(f"[-] {addr} disconnected")


async def main():
    log.info("=" * 60)
    log.info(f"[*] Volumetrica Historical Proxy on {HOST}:{PORT}")
    log.info(f"[*] Forwarding to {HIST_UPSTREAM_IP}:{HIST_PORT} (SNI={HIST_SNI_HOST})")
    log.info(f"[*] Full log: {LOG_FILE}")
    log.info("=" * 60)

    server = await asyncio.start_server(handle_client, HOST, PORT)
    log.info(f"[*] Listening...")

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
