"""Open Eosnox Labs as a desktop window instead of a browser tab."""

import socket
import threading
import time
import urllib.request

import webview
from werkzeug.serving import make_server

from app import app, start_instruments


def _free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


class _Server(threading.Thread):
    def __init__(self, port):
        super().__init__(daemon=True)
        self.port = port
        self.httpd = make_server("127.0.0.1", port, app, threaded=True)

    def run(self):
        self.httpd.serve_forever()

    def stop(self):
        self.httpd.shutdown()


def _wait_until_ready(port):
    url = "http://127.0.0.1:{}/".format(port)
    deadline = time.time() + 15
    last_error = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.4).read()
            return url
        except Exception as exc:
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError("VetLab did not start: {}".format(last_error))


def main():
    start_instruments()
    port = _free_port()
    server = _Server(port)
    server.start()
    url = _wait_until_ready(port)

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    window = webview.create_window(
        "Eosnox Labs",
        url,
        width=1280,
        height=860,
        min_size=(960, 680),
        text_select=True,
    )
    window.events.closed += server.stop
    webview.start()


if __name__ == "__main__":
    main()
