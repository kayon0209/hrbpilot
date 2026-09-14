"""本地 HTTPS 的 CIMD 文档服务器 —— **仅供验收与测试使用**。

为什么需要它
------------
CIMD 的核心不变式是"文档自证它属于被抓取的 URL"。这条不变式只有在**真的发出一趟
HTTPS 请求**时才会被执行：``resolve_cimd_client`` 先抓取，再比对文档里的 ``client_id``。
把抓取函数替换成桩，等于把这条不变式连同整个模块的存在理由一起测掉了。

而 CIMD 只接受 ``https://`` 的 ``client_id``，所以要本地跑就必须真的起一个 TLS 服务
并让 AS 信任它的证书 —— 也就是这个模块。

它不是生产代码
--------------
放在 ``scripts/`` 而不是 ``app/``：这里没有任何东西应该进入请求路径。它生成自签证书、
起一个只服务固定几份 JSON 的服务器，除此之外不做事。

与之配套的两项配置（``OAUTH_CIMD_ALLOWED_PRIVATE_HOSTS`` / ``OAUTH_CIMD_CA_BUNDLE``）
里，前一项在 production / staging 下设置会让进程启动失败 —— 见 ``app/config/settings.py``。
"""

from __future__ import annotations

import ipaddress
import json
import ssl
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def generate_self_signed_certificate(cert_path: Path, key_path: Path, *, host: str = "127.0.0.1") -> None:
    """为 ``host`` 生成一对自签证书与私钥，写入给定路径。

    SAN 里放的是 **IP 地址**而不是 DNS 名：调用方用的是
    ``https://127.0.0.1:PORT/...``，而现代 TLS 栈只认 SAN、不看 CN —— 少了这条，
    握手会以"证书与主机名不匹配"失败，而那个错误信息不会提示你 SAN 才是原因。

    证书同时充当自己的根（``BasicConstraints(ca=True)``），因此可以直接把它交给
    ``OAUTH_CIMD_CA_BUNDLE``：链就是一个节点。
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


@dataclass
class DocumentServer:
    """一个只服务内存里那几份文档的 HTTPS 服务器。

    ``routes`` 是**可变**的：负例需要在跑的过程中把某条路径换成 302、换成超大响应、
    或让文档改成声明另一个 ``client_id``，而每换一次就重启一次服务器会让"缓存"那类
    用例（服务器停了还要能用）没法表达。
    """

    base_url: str
    ca_bundle_path: Path
    routes: dict[str, tuple[int, str, bytes]] = field(default_factory=dict)
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def serve_json(self, path: str, payload: dict[str, object]) -> str:
        """登记一份 JSON 文档，返回它的完整 URL（也就是那个 CIMD ``client_id``）。"""
        self.routes["/" + path.lstrip("/")] = (200, "application/json", json.dumps(payload).encode("utf-8"))
        return self.url(path)

    def serve_raw(self, path: str, *, status: int, body: bytes, content_type: str = "application/json") -> str:
        self.routes["/" + path.lstrip("/")] = (status, content_type, body)
        return self.url(path)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def start_document_server(cert_path: Path, key_path: Path, *, host: str = "127.0.0.1") -> DocumentServer:
    """起一个本地 HTTPS 文档服务器。调用方负责 ``stop()``。

    ``serve_forever`` 跑在守护线程里：验收脚本与测试都不该因为一个测试辅助服务的
    线程没有退出而挂住。
    """
    server = DocumentServer(base_url="", ca_bundle_path=cert_path)
    routes = server.routes
    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            entry = routes.get(self.path)
            if entry is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status, content_type, body = entry
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            """静音访问日志：它会盖住验收脚本自己的输出，而它的信息量是零。"""

    httpd = ThreadingHTTPServer((host, 0), _Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)

    port = int(httpd.server_address[1])
    server.base_url = f"https://{host}:{port}"
    server._server = httpd
    server._thread = threading.Thread(target=httpd.serve_forever, name="cimd-doc-server", daemon=True)
    server._thread.start()
    return server
