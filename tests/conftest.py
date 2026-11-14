"""Tests make outbound IPv4/IPv6 network use impossible, no env required."""
import socket
import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name in ("KYC_PROVIDER", "KYC_API_KEY", "KYC_MODEL", "KYC_BASE_URL", "KYC_THRESHOLD", "KYC_AUDIT_DB"):
        monkeypatch.delenv(name, raising=False)
    original = socket.socket.connect

    def blocked(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("Network access forbidden in tests")
        return original(self, address)

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Network forbidden")))
