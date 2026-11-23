"""
The rates terminal's web server (aiohttp): the built front ends (versions C and B) and a reverse proxy to Deephaven on
the same origin, so the browser talks to one host and port (no CORS), as Vite's dev server does in development.

  /, /index.html, /b.html, /popout.html, /assets/...    the built front ends (the package's web/, or the repo's dist/)
  /jsapi/...                                            Deephaven's JS API (dh-core.js)
  /js-plugins/...                                       the server's JS plugins (B draws its widgets with them)
  /io.deephaven.*, /arrow.*                             the JS API's gRPC-web calls (HTTP/1.1, streamed both ways)
  /iframe/...                                           Deephaven's embedded-widget pages (version A on this port too)
  any WebSocket upgrade, / included                     the JS API's websocket transport, proxied frame by frame

With Deephaven down, the proxied paths answer 502 at once, and C runs on its in-page mock.
"""
from __future__ import annotations

import asyncio
import mimetypes
import pathlib
import threading
from typing import Optional

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from aiohttp.client_exceptions import ClientError

PACKAGE = pathlib.Path(__file__).resolve().parent
PROXIED = ("/jsapi/", "/js-plugins/", "/io.deephaven.", "/arrow.", "/iframe/")
# hop-by-hop headers (RFC 9110 7.6.1), and the ones aiohttp sets itself
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection", "te", "trailer",
       "transfer-encoding", "upgrade"}
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")


def default_root() -> pathlib.Path:
    """The built front ends: the wheel's rates_terminal/web, or, in the repo, dist/."""
    for d in (PACKAGE / "web", PACKAGE.parents[1] / "dist"):
        if (d / "index.html").is_file():
            return d
    raise SystemExit("No built front ends: in the repo, run `npm run build` (it writes dist/).")


def make_app(dh_url: str, root: Optional[pathlib.Path] = None) -> web.Application:
    root = (root or default_root()).resolve()
    dh = dh_url.rstrip("/")
    dh_ws = "ws" + dh[4:] if dh.startswith("http") else dh
    app = web.Application(client_max_size=1 << 30)
    session: dict = {}

    async def on_startup(_app):
        # raw bytes both ways (no decompression), and no timeouts: subscriptions stream for as long as a page is open
        session["s"] = ClientSession(auto_decompress=False, timeout=ClientTimeout(total=None, connect=5, sock_read=None))

    async def on_cleanup(_app):
        await session["s"].close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    async def proxy_ws(request: web.Request) -> web.StreamResponse:
        protocols = [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",") if p.strip()]
        fwd = {k: v for k, v in request.headers.items() if k.lower() in ("authorization", "cookie")}
        try:
            up = await session["s"].ws_connect(dh_ws + request.rel_url.path_qs, protocols=protocols, headers=fwd, max_msg_size=0)
        except (ClientError, OSError) as e:
            return web.Response(status=502, text=f"Deephaven is not reachable at {dh}: {e}")
        down = web.WebSocketResponse(protocols=[up.protocol] if up.protocol else (), max_msg_size=0)
        await down.prepare(request)

        async def pump(src, dst):
            try:
                async for msg in src:
                    if msg.type == WSMsgType.BINARY:
                        await dst.send_bytes(msg.data)
                    elif msg.type == WSMsgType.TEXT:
                        await dst.send_str(msg.data)
                    else:
                        break
            except (ClientError, ConnectionError, RuntimeError):
                pass
            finally:
                if not dst.closed:
                    await dst.close()

        await asyncio.gather(pump(down, up), pump(up, down))
        return down

    async def proxy_http(request: web.Request) -> web.StreamResponse:
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP | {"host", "content-length"}}
        try:
            upstream = await session["s"].request(
                request.method, dh + request.rel_url.path_qs, headers=headers, allow_redirects=False,
                data=request.content if request.body_exists else None)
        except (ClientError, OSError) as e:
            return web.Response(status=502, text=f"Deephaven is not reachable at {dh}: {e}")
        async with upstream:
            resp = web.StreamResponse(status=upstream.status, reason=upstream.reason)
            for k, v in upstream.headers.items():
                if k.lower() not in HOP:
                    resp.headers.add(k, v)
            await resp.prepare(request)
            try:
                async for chunk in upstream.content.iter_any():
                    await resp.write(chunk)
                await resp.write_eof()
            except (ConnectionError, ClientError):
                pass  # the browser or the server went away mid-stream
            return resp

    async def static(request: web.Request) -> web.StreamResponse:
        path = request.path
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (root / name).resolve()
        if not f.is_file() or root not in f.parents:
            raise web.HTTPNotFound()
        hashed = path.startswith("/assets/")
        return web.FileResponse(f, headers={"Cache-Control": "public, max-age=31536000, immutable" if hashed else "no-cache"})

    async def handle(request: web.Request) -> web.StreamResponse:
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await proxy_ws(request)
        if request.path.startswith(PROXIED):
            return await proxy_http(request)
        if request.method not in ("GET", "HEAD"):
            raise web.HTTPMethodNotAllowed(request.method, ["GET", "HEAD"])
        return await static(request)

    app.router.add_route("*", "/{tail:.*}", handle)
    return app


def serve(port: int, dh_url: str, *, host: str = "localhost", root: Optional[str] = None) -> None:
    """Serve until interrupted."""
    web.run_app(make_app(dh_url, pathlib.Path(root) if root else None), host=host, port=port, print=None, access_log=None)


def start_in_thread(port: int, dh_url: str, *, host: str = "localhost", root: Optional[str] = None) -> threading.Thread:
    """Serve from a daemon thread (beside Deephaven in the same process); returns once the port is open."""
    ready = threading.Event()
    error: list = []

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            runner = web.AppRunner(make_app(dh_url, pathlib.Path(root) if root else None), access_log=None)
            loop.run_until_complete(runner.setup())
            loop.run_until_complete(web.TCPSite(runner, host, port).start())
        except BaseException as e:  # noqa: BLE001 (reported to the caller)
            error.append(e)
            ready.set()
            return
        ready.set()
        loop.run_forever()

    t = threading.Thread(target=run, name="rates-terminal-web", daemon=True)
    t.start()
    ready.wait(30)
    if error:
        raise SystemExit(f"The web server could not start on {host}:{port}: {error[0]}")
    return t
