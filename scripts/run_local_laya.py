"""Local Laya System 1 Service Runner.

Starts the official open-source laya.serve HTTP server bound to loopback:
http://127.0.0.1:8000/v1/systemone

Using CPU inference and the pre-cached typed-decisions model.
"""

from __future__ import annotations

import os
import sys

# Configure environment before importing torch/laya
os.environ.setdefault("LAYA_HOST", "127.0.0.1")
os.environ.setdefault("LAYA_PORT", "8000")
os.environ.setdefault("LAYA_DEVICE", "cpu")
os.environ.setdefault("LAYA_PRELOAD", "0")

from laya import serve

if __name__ == "__main__":
    host = os.environ.get("LAYA_HOST", "127.0.0.1")
    port = os.environ.get("LAYA_PORT", "8000")
    print(f"Starting official local Laya System 1 service on http://{host}:{port} ...")
    serve.main()
