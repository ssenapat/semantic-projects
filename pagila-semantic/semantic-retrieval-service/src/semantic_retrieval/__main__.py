import argparse

import uvicorn

from .app import create_app

p = argparse.ArgumentParser()
p.add_argument("--host", default="127.0.0.1")
p.add_argument("--port", type=int, default=8002)
a = p.parse_args()
uvicorn.run(create_app(), host=a.host, port=a.port)
