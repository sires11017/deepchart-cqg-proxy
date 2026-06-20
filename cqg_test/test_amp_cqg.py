"""Test AMP CQG credentials against CQG WebAPI demo server"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import socket, ssl
from hashlib import sha1
from base64 import b64encode as b64e
from WebAPI.webapi_2_pb2 import ClientMsg
from WebAPI.user_session_2_pb2 import LogonResult
from WebAPI import webapi_client
from WebAPI import websocket

# Bypass hosts file redirect - connect to real IP with correct SNI
CONFIG = {
    'real_ip': '208.48.16.22',
    'hostname': 'demoapi.cqg.com',
}

class FixedWebSocket(websocket.WebSocket):
    """WebSocket that connects to real IP using correct SNI hostname"""
    def connect(self, uri, origin=None, protocols=[]):
        self.client = True
        from urllib.parse import urlparse
        parsed = urlparse(uri)
        port = parsed.port or 443
        cfg = __import__(__name__).CONFIG
        
        if self._state == "new":
            import random
            self.socket = socket.create_connection((cfg['real_ip'], port))
            context = ssl.create_default_context()
            self.socket = context.wrap_socket(self.socket, server_hostname=cfg['hostname'])
            
            self._key = ''
            for i in range(16):
                self._key += chr(random.randrange(256))
            self._key = b64e(self._key.encode("latin-1")).decode("ascii")
            
            path = parsed.path or "/"
            cfg2 = __import__(__name__).CONFIG
            self.send_request("GET", path)
            self.send_header("Host", cfg2['hostname'])
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "upgrade")
            self.send_header("Sec-WebSocket-Key", self._key)
            self.send_header("Sec-WebSocket-Version", 13)
            if origin: self.send_header("Origin", origin)
            if protocols: self.send_header("Sec-WebSocket-Protocol", ", ".join(protocols))
            self.end_headers()
            self._state = "send_headers"
        
        if self._state == "send_headers":
            self._flush()
            self._state = "response"
        
        if self._state == "response":
            if not self._recv():
                raise Exception("Socket closed unexpectedly")
            if self._recv_buffer.find(b'\r\n\r\n') == -1:
                raise websocket.WebSocketWantReadError
            (request, self._recv_buffer) = self._recv_buffer.split(b'\r\n', 1)
            request = request.decode("latin-1")
            words = request.split()
            if words[1] != "101":
                raise Exception("WebSocket request denied: %s" % " ".join(words[1:]))
            (headers, self._recv_buffer) = self._recv_buffer.split(b'\r\n\r\n', 1)
            headers = headers.decode('latin-1') + '\r\n'
            import email
            headers = email.message_from_string(headers)
            if headers.get("Upgrade", "").lower() != "websocket":
                raise Exception("Missing or incorrect upgrade header")
            accept = headers.get('Sec-WebSocket-Accept')
            if accept is None:
                raise Exception("Missing Sec-WebSocket-Accept header")
            expected = sha1((self._key + websocket.WebSocket.GUID).encode("ascii")).digest()
            expected = b64e(expected).decode("ascii")
            del self._key
            if accept != expected:
                raise Exception("Invalid Sec-WebSocket-Accept header")
            self.protocol = headers.get('Sec-WebSocket-Protocol')
            if protocols and self.protocol not in protocols:
                raise Exception("Invalid protocol chosen by server")
            self._state = "done"
            return
        raise Exception("WebSocket is in an invalid state")

class FixedClient(webapi_client.WebApiClient):
    def __init__(self):
        super().__init__()
        self.websocket_client = FixedWebSocket()
HOST = 'wss://demoapi.cqg.com:443'
PROD_HOST = 'wss://api.cqg.com:443'
USER = 'demo601'
PASS = '$3_2oNfD'

def test_logon(private_label, client_app_id, label):
    print(f"\n{'='*60}")
    print(f"Trying {label}: private_label='{private_label}', client_app_id='{client_app_id}'")
    print(f"{'='*60}")
    client = FixedClient()
    try:
        client.connect(HOST)
        print("[OK] WebSocket connected")

        msg = ClientMsg()
        logon = msg.logon
        logon.user_name = USER
        logon.password = PASS
        if private_label is not None and private_label != '':
            logon.private_label = private_label
        logon.client_app_id = client_app_id
        logon.client_version = '7.0.238'
        logon.protocol_version_major = 2
        logon.protocol_version_minor = 230

        client.send_client_message(msg)
        server_msg = client.receive_server_message()

        result = server_msg.logon_result
        code = result.result_code
        if code == LogonResult.ResultCode.RESULT_CODE_SUCCESS:
            print(f"[SUCCESS] Logged in! base_time={result.base_time}")
            return True
        else:
            text = result.text_message
            print(f"[FAILED] code={code}, text='{text}'")
            # Try to get readable enum name
            for name, val in LogonResult.ResultCode.items():
                if val == code:
                    print(f"  = {name}")
            return False
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        return False
    finally:
        try:
            client.disconnect()
        except:
            pass

if __name__ == '__main__':
    # Demo server tests
    print(">>> DEMO SERVER TESTS <<<")
    test_logon('WebAPITest', 'WebAPITest', 'Both = WebAPITest (CQG docs)')
    test_logon('CQG', 'WebAPITest', 'PL=CQG, CI=WebAPITest (CQG docs alt)')
    test_logon('', 'WebAPITest', 'PL=default, CI=WebAPITest')
    
    # MotiveWave captured values
    test_logon('AMPConnect', 'AMPConnect', 'PL=AMPConnect, CI=AMPConnect (captured from MW)')
    
    # Try known platform PrivateLabels
    platforms = [
        ('MotiveWave', 'MotiveWave'),
        ('MotiveWave', 'WebAPITest'),
        ('QuantTower', 'QuantTower'),
        ('SierraChartData', 'SierraChartData'),
        ('Bookmap', 'Bookmap'),
        ('AMP', 'AMP'),
        ('AMPCQG', 'AMPCQG'),
        ('CQG_API', 'CQG_API'),
        ('Continuum', 'Continuum'),
        ('CQGTrader', 'CQGTrader'),
        ('Rithmic', 'Rithmic'),
        ('CQGIC', 'CQGIC'),
        ('CQGOne', 'CQGOne'),
        ('Demo', 'Demo'),
        ('Volumetrica', 'Volumetrica'),
        ('AMPConnect', 'AMPConnect'),
    ]
    for pl, ci in platforms:
        test_logon(pl, ci, f'PL={pl}, CI={ci}')
    
    # Try multiple client_app_id with same private_label
    for ci in ['MotiveWave', 'QuantTower', 'WebAPITest', 'CQGTrader', 'CQGIC']:
        test_logon('WebAPITest', ci, f'PL=WebAPITest, CI={ci}')
    
    # Try ObtainDemoCredentials
    print("\n>>> OBTAIN DEMO CREDENTIALS <<<")
    client = FixedClient()
    try:
        client.connect(HOST)
        print("[OK] WebSocket connected")
        msg = ClientMsg()
        odc = msg.obtain_demo_credentials
        odc.client_app_id = 'WebAPITest'
        odc.first_name = 'Test'
        odc.second_name = 'User'
        odc.e_mail = 'test@example.com'
        client.send_client_message(msg)
        resp = client.receive_server_message()
        if resp.obtain_demo_credentials_results:
            r = resp.obtain_demo_credentials_results[0]
            print(f"code={r.result_code}, user={r.user_name}, pass={r.password}, msg={r.text_message}")
        client.disconnect()
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
    
    # Production server test 
    print("\n>>> PRODUCTION SERVER TEST <<<")
    CONFIG['real_ip'] = '3.108.220.127'
    CONFIG['hostname'] = 'api.cqg.com'
    test_logon('WebAPITest', 'WebAPITest', 'Production with WebAPITest')
    CONFIG['real_ip'] = '208.48.16.22'
    CONFIG['hostname'] = 'demoapi.cqg.com'
