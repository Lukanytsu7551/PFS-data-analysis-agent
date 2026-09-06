import socket
import unittest
from unittest.mock import patch

from flask import Flask

from api.system import _validate_proxy_image_url, bp


class ProxyImageSecurityTests(unittest.TestCase):
    @staticmethod
    def _app():
        app = Flask(__name__)
        app.register_blueprint(bp)
        app.config.update(TESTING=True)
        return app

    def test_rejects_loopback_literal_without_dns(self):
        valid, reason = _validate_proxy_image_url("http://127.0.0.1/image.png")

        self.assertFalse(valid)
        self.assertEqual("Image URL must target a public address.", reason)

    def test_rejects_private_dns_answer(self):
        records = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.8", 443)),
        ]
        with patch("api.system.socket.getaddrinfo", return_value=records):
            valid, reason = _validate_proxy_image_url("https://image.example.test/chart.png")

        self.assertFalse(valid)
        self.assertEqual("Image URL must target a public address.", reason)

    def test_accepts_public_dns_answer(self):
        records = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
        ]
        with patch("api.system.socket.getaddrinfo", return_value=records):
            valid, reason = _validate_proxy_image_url("https://image.example.test/chart.png")

        self.assertTrue(valid)
        self.assertEqual("", reason)

    def test_rejects_credentials_and_non_default_port(self):
        credentialed, credential_reason = _validate_proxy_image_url(
            "https://user:secret@example.com/image.png"
        )
        non_default_port, port_reason = _validate_proxy_image_url("https://example.com:8443/image.png")

        self.assertFalse(credentialed)
        self.assertIn("credentials", credential_reason)
        self.assertFalse(non_default_port)
        self.assertIn("default HTTP(S) port", port_reason)

    def test_route_rejects_private_target_before_opening_network(self):
        app = self._app()
        with patch("api.system._PROXY_IMAGE_OPENER.open") as opener:
            response = app.test_client().get("/api/proxy-image?url=http%3A%2F%2F127.0.0.1%2Fimage.png")

        self.assertEqual(400, response.status_code)
        self.assertEqual("Image URL must target a public address.", response.get_json()["error"])
        opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
