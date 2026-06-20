"""
vol_hist_server.py - Mock Volumetrica Historical Server

Listens on port 12010 via WebSocket, intercepts Deepchart.exe's connection.
Responds with {"IsComplete":true} as a valid compressed protobuf so Deepchart doesn't hang/time out.
"""
# made by illnoobis
import asyncio, logging, datetime, json, subprocess, zlib, os
import websockets

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


def encode_varint(value):
    """Encode an integer as a protobuf varint."""
    res = bytearray()
    while True:
        towrite = value & 0x7f
        value >>= 7
        if value:
            res.append(towrite | 0x80)
        else:
            res.append(towrite)
            break
    return bytes(res)


def get_powershell_signature(key):
    """
    Executes standard .NET Cryptography in PowerShell to encrypt "-" with the session key.
    This exactly replicates the client's custom Rijndael-256 CBC decryption check.
    """
    ps_script = f"""
$plainBytes = [System.Text.Encoding]::UTF8.GetBytes('-')
$salt = New-Object Byte[] 32
$iv = New-Object Byte[] 32
$rng = [System.Security.Cryptography.RNGCryptoServiceProvider]::new()
$rng.GetBytes($salt)
$rng.GetBytes($iv)
$pbkdf2 = [System.Security.Cryptography.Rfc2898DeriveBytes]::new('{key}', $salt, 1230)
$keyBytes = $pbkdf2.GetBytes(32)
$rijndael = [System.Security.Cryptography.RijndaelManaged]::new()
$rijndael.KeySize = 256
$rijndael.BlockSize = 256
$rijndael.Mode = [System.Security.Cryptography.CipherMode]::CBC
$rijndael.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
$rijndael.Key = $keyBytes
$rijndael.IV = $iv
$encryptor = $rijndael.CreateEncryptor()
$ms = [System.IO.MemoryStream]::new()
$cs = [System.Security.Cryptography.CryptoStream]::new($ms, $encryptor, [System.Security.Cryptography.CryptoStreamMode]::Write)
$cs.Write($plainBytes, 0, $plainBytes.Length)
$cs.FlushFinalBlock()
$encryptedBytes = $ms.ToArray()
$cs.Dispose()
$ms.Dispose()
$rijndael.Dispose()
$pbkdf2.Dispose()
$rng.Dispose()
$result = New-Object Byte[] (32 + 32 + $encryptedBytes.Length)
[System.Buffer]::BlockCopy($salt, 0, $result, 0, 32)
[System.Buffer]::BlockCopy($iv, 0, $result, 32, 32)
[System.Buffer]::BlockCopy($encryptedBytes, 0, $result, 64, $encryptedBytes.Length)
[System.Convert]::ToBase64String($result)
"""
    try:
        log.info(f"  [SIGN] Invoking PowerShell to encrypt '-' with session key...")
        res = subprocess.run(["powershell", "-Command", ps_script], capture_output=True, text=True, check=True)
        sig = res.stdout.strip()
        log.info(f"  [SIGN] Signature generated: {sig[:32]}...")
        return sig
    except Exception as e:
        log.error(f"  [SIGN] Failed to generate PowerShell signature: {e}")
        return ""


async def handle_client(ws):
    addr = ws.remote_address
    log.info(f"[+] WS client connected from {addr}")
    use_compression = False

    try:
        async for message in ws:
            if isinstance(message, bytes):
                log.info(f"  [BINARY] Received {len(message)} bytes")
                # Decompress the request using raw DEFLATE (wbits = -15)
                try:
                    decompressed = zlib.decompress(message, -15)
                    log.info(f"  [BINARY] Decompressed: {len(decompressed)} bytes")
                except Exception as ex:
                    log.error(f"  [BINARY] Failed to decompress raw request: {ex}")
                    continue

                # Extract the session key from field 8 of the Request sub-message (tag 0x42, string length 95)
                session_key = None
                idx = decompressed.find(b'\x42\x5f')
                if idx != -1:
                    session_key = decompressed[idx+2 : idx+2+95].decode('ascii', errors='ignore')
                    log.info(f"  [SESSION KEY] Extracted session key: '{session_key}'")
                else:
                    log.warning("  [SESSION KEY] Marker 0x425f not found in decompressed request.")

                if session_key:
                    # Generate standard PowerShell Rijndael signature
                    sig = get_powershell_signature(session_key)

                    # Build Protobuf response matching outer: _0008_2002_2004, inner: _0003_2008_2004
                    # inner message:
                    #   ProtoMember 4: bool _0006 = True (IsComplete) -> 0x20 0x01
                    #   ProtoMember 5: string _0002 = sig -> 0x2a [len] [sig]
                    sig_bytes = sig.encode('ascii')
                    inner_bytes = b'\x20\x01\x2a' + encode_varint(len(sig_bytes)) + sig_bytes

                    # outer message:
                    #   ProtoMember 1: _0003_2008_2004 = inner_bytes -> 0x0a [len] [inner_bytes]
                    outer_bytes = b'\x0a' + encode_varint(len(inner_bytes)) + inner_bytes

                    # Compress the response bytes using raw DEFLATE as required by the client's decompressor
                    compressor = zlib.compressobj(level=9, method=zlib.DEFLATED, wbits=-15)
                    compressed = compressor.compress(outer_bytes) + compressor.flush()

                    log.info(f"  [SEND] Sending compressed protobuf response ({len(compressed)} bytes)...")
                    await ws.send(compressed)
                else:
                    # Fallback default empty complete response if no session key found
                    log.warning("  [SESSION KEY] Sending empty/un-signed fallback response.")
                    # inner message with IsComplete=True, signature=""
                    inner_bytes = b'\x20\x01\x2a\x00'
                    outer_bytes = b'\x0a' + encode_varint(len(inner_bytes)) + inner_bytes
                    compressor = zlib.compressobj(level=9, method=zlib.DEFLATED, wbits=-15)
                    compressed = compressor.compress(outer_bytes) + compressor.flush()
                    await ws.send(compressed)

            else:
                # Text message
                log.info(f"  [TEXT] Received: {message[:500]}")
                if message.strip() == "compress":
                    use_compression = True
                    log.info("  [COMPRESSION] Handshake 'compress' received. Compression enabled.")

    except websockets.ConnectionClosed as e:
        log.info(f"  [CLOSED] {e.code} {e.reason}")
    except Exception as e:
        log.error(f"  [ERROR] {e}")
    finally:
        log.info(f"[-] {addr} disconnected")


async def process_request(connection, request):
    """Handle any connection that fails the WebSocket handshake gracefully."""
    return None  # None = continue with normal WebSocket handshake


async def main():
    log.info("=" * 60)
    log.info(f"[*] Volumetrica Historical Mock Server on ws://{HOST}:{PORT}")
    log.info(f"[*] Full log: {LOG_FILE}")
    log.info("=" * 60)
    log.info("Make sure hosts file has:")
    log.info("  192.168.29.244  depth-it.historical.deepcharts.com")
    log.info("=" * 60)

    # Suppress noisy websockets internal logs for expected connection drops
    logging.getLogger("websockets.server").setLevel(logging.WARNING)

    async with websockets.serve(
        handle_client,
        HOST,
        PORT,
        process_request=process_request,
        ping_interval=None,
        close_timeout=None,
    ):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
