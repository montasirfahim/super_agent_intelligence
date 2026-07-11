import socket

from run import get_available_port


def test_get_available_port_returns_bindable_port():
    port = get_available_port(8000)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))
        assert port >= 8000
