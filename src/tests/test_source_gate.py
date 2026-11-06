"""D-20261010-32: /source_file and /figure let a caller in without a token
only when it sends no proxy header AND its socket address is private.

TestClient(client=(host, port)) sets the socket address; the harness lists
the default "testclient" socket as private. The routes are pointed at a real
temp file so "allowed" is a 200 and "denied" is the middleware's 404.
"""
import os
import tempfile
from unittest.mock import patch

import _harness  # noqa: F401 -- load main with lightweight test dependencies
import main
import quota
from fastapi.testclient import TestClient

PUBLIC = ("198.51.100.4", 50000)          # TEST-NET-2, not in PRIVATE_NETWORKS
EXT = {"cf-connecting-ip": "203.0.113.9"}  # what the tunnel adds
URLS = ("/source_file/Manual.pdf", "/api/source_file/Manual.pdf",
        "/figure/Manual.pdf/p1_1.png")


def _served(fn):
    import docstore
    import figures
    import retrieval_db
    fd, path = tempfile.mkstemp(suffix=".png")
    os.write(fd, b"x")
    os.close(fd)
    try:
        with patch.object(docstore, "find", return_value=path), \
             patch.object(figures, "figure_path", return_value=path), \
             patch.object(retrieval_db, "is_excluded", return_value=False):
            fn()
    finally:
        os.remove(path)


def test_no_header_from_a_public_address_is_denied():
    def run():
        c = TestClient(main.app, client=PUBLIC)
        for url in URLS:
            assert c.get(url).status_code == 404, url
    _served(run)


def test_no_header_from_a_private_address_is_allowed():
    def run():
        for host in ("127.0.0.1", "192.168.1.20", "172.17.0.1", "testclient"):
            c = TestClient(main.app, client=(host, 50000))
            for url in URLS:
                assert c.get(url).status_code == 200, (host, url)
    _served(run)


def test_a_token_still_opens_it_from_anywhere():
    def run():
        member = {"authorization": "Bearer " + quota.issue_token("m-32", "member")}
        staff = {"authorization": "Bearer " + quota.issue_token("s-32", "staff")}
        pub = TestClient(main.app, client=PUBLIC)
        lan = TestClient(main.app, client=("127.0.0.1", 50000))
        for url in URLS:
            assert pub.get(url, headers=staff).status_code == 200, url
            assert pub.get(url, headers=member).status_code == 200, url
            assert lan.get(url, headers={**EXT, **member}).status_code == 200, url
            # proxied without a token stays denied, even on a private socket
            assert lan.get(url, headers=EXT).status_code == 404, url
            assert pub.get(url, headers={"authorization": "Bearer bad.token"}).status_code == 404, url
    _served(run)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
