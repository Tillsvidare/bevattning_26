"""Kör backenden lokalt på Windows utan Docker.

aiomqtt kräver en selector-eventloop, men uvicorn väljer själv
ProactorEventLoop på Windows (uvicorn/loops/asyncio.py) — därför körs
servern här med en egen loop-factory. I Docker (Linux) behövs inget av
detta: kör `docker compose up` istället.

    cd backend && python ../tools/run_dev.py
"""

import asyncio
import os
import sys

# Python lägger skriptets katalog (tools/) på sys.path, inte cwd —
# "app.main" finns i backend/ som skriptet ska köras ifrån.
sys.path.insert(0, os.getcwd())

import uvicorn

# HOST=0.0.0.0 exponerar servern på LAN (t.ex. för test i mobilen).
config = uvicorn.Config("app.main:app",
                        host=os.environ.get("HOST", "127.0.0.1"), port=8000)
server = uvicorn.Server(config)

if sys.platform == "win32":
    asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
else:
    asyncio.run(server.serve())
