"""
A stand-in for the CEP's backtester: runs one strategy over historical bars and streams what it does into
Deephaven tables at the addresses it is given. The real CEP replaces this file; the contract stays:

  stdin   one JSON spec: {run_id, code, params, speed, dh: {host, port}, data: {table}, and either
            tables: {trades, orders, positions, signals, equity}   central mode: input tables to append to there, or
            engine: {host, port, psk, heap, linger_s}, archive_dir  engine mode: start an engine of our own, publish
                                                                     the tables in it, archive them to Parquet when done}
  stdout  JSON lines: {"event": "started"}, {"event": "progress", "bars": n, "of": total, "equity": x, "trades": n, "pnl": x},
          {"event": "log", "text": ...}, {"event": "done", "summary": {...}} or {"event": "error", "message": ...};
          engine mode adds {"event": "engine", "host", "port", "startup_s"} first, then "archived" and "lingering"

Run by dh_chart_ext.backtests (one process per run, `python -I`); the process is the sandbox, see there.

The strategy DSL (checked with dh_chart_ext.sandbox before it runs):

    def on_start(ctx):                    # optional
        ctx.state["fast"] = ctx.param("fast", 10)

    def on_bar(ctx, bar):                 # every bar of every symbol, in time order
        fast = ctx.ema(bar.sym + ":f", ctx.state["fast"], bar.close)
        slow = ctx.ema(bar.sym + ":s", 30, bar.close)
        ctx.plot("fast", fast, bar.sym)   # a series in the signals table
        if fast > slow and ctx.position(bar.sym) <= 0:
            ctx.target(bar.sym, 1)        # market order, filled at this bar's close
        elif fast < slow and ctx.position(bar.sym) >= 0:
            ctx.target(bar.sym, -1)

    def on_end(ctx):                      # optional
        ctx.log("done")
"""
from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


# ----------------------------------------------------------------------------- the simulation (no Deephaven)

@dataclass(frozen=True)
class Bar:
    sym: str
    time: int  # ns since the epoch, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


class Context:
    """What a strategy sees and does."""

    def __init__(self, symbols: list[str], params: dict, capital: float, commission: float, emit: Callable[[str, dict], None]):
        self.symbols = list(symbols)
        self.params = dict(params)
        self.state: dict = {}
        self.cash = float(capital)
        self.commission = float(commission)
        self._emit = emit
        self._pos: dict[str, float] = {}
        self._avg: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._ind: dict[str, list] = {}
        self._now = 0
        self._logs = 0
        self.trades = 0
        self.realized = 0.0

    # reading

    def param(self, name: str, default=None):
        return self.params.get(name, default)

    def position(self, sym: str) -> float:
        return self._pos.get(sym, 0.0)

    def avg_price(self, sym: str) -> float:
        return self._avg.get(sym, 0.0)

    @property
    def equity(self) -> float:
        return self.cash + sum(q * self._last.get(s, 0.0) for s, q in self._pos.items())

    # indicators: state by key, so a strategy can keep any number

    def ema(self, key: str, length: int, value: float) -> float:
        s = self._ind.setdefault(f"ema:{key}", [None])
        a = 2.0 / (int(length) + 1)
        s[0] = value if s[0] is None else s[0] + a * (value - s[0])
        return s[0]

    def sma(self, key: str, length: int, value: float) -> float:
        s = self._ind.setdefault(f"sma:{key}", [[], 0.0])
        s[0].append(value)
        s[1] += value
        if len(s[0]) > int(length):
            s[1] -= s[0].pop(0)
        return s[1] / len(s[0])

    # acting

    def buy(self, sym: str, qty: float = 1) -> None:
        self._order(sym, abs(qty))

    def sell(self, sym: str, qty: float = 1) -> None:
        self._order(sym, -abs(qty))

    def target(self, sym: str, qty: float) -> None:
        delta = qty - self.position(sym)
        if delta:
            self._order(sym, delta)

    def plot(self, name: str, value: float, sym: str = "") -> None:
        if value is not None and math.isfinite(value):
            self._emit("signals", {"Timestamp": self._now, "Sym": sym, "Kind": "plot", "Name": str(name)[:64], "Value": float(value)})

    def signal(self, sym: str, name: str, value: float = 1.0) -> None:
        self._emit("signals", {"Timestamp": self._now, "Sym": sym, "Kind": "signal", "Name": str(name)[:64], "Value": float(value)})

    def log(self, text) -> None:
        self._logs += 1
        if self._logs <= 200:
            self._emit("log", {"text": str(text)[:500]})

    # the fill model: market orders at the current bar's close, a commission per unit

    def _order(self, sym: str, qty: float) -> None:
        if sym not in self._last:
            raise ValueError(f"no price for {sym} yet (subscribed: {', '.join(self.symbols)})")
        price = self._last[sym]
        side = "BUY" if qty > 0 else "SELL"
        self._emit("orders", {"Timestamp": self._now, "Sym": sym, "Side": side, "Qty": abs(qty), "Type": "MKT",
                              "Price": price, "Status": "FILLED"})
        pos, avg = self.position(sym), self.avg_price(sym)
        realized = 0.0
        if pos and (pos > 0) != (qty > 0):  # closing some or all
            closed = min(abs(qty), abs(pos)) * (1 if pos > 0 else -1)
            realized = closed * (price - avg)
        new = pos + qty
        if new == 0:
            self._avg.pop(sym, None)
        elif pos == 0 or (pos > 0) != (new > 0):
            self._avg[sym] = price  # opened, or flipped through zero
        elif (pos > 0) == (qty > 0):
            self._avg[sym] = (avg * pos + price * qty) / new  # added
        self._pos[sym] = new
        fee = abs(qty) * self.commission
        self.cash -= qty * price + fee
        self.realized += realized - fee
        self.trades += 1
        self._emit("trades", {"Timestamp": self._now, "Sym": sym, "Side": side, "Qty": abs(qty), "Price": price,
                              "Commission": fee, "RealizedPnl": realized - fee})
        self._emit("positions", {"Sym": sym, "Qty": new, "AvgPrice": self._avg.get(sym, 0.0), "Updated": self._now})


@dataclass
class Summary:
    bars: int = 0
    trades: int = 0
    equity: float = 0.0
    pnl: float = 0.0
    max_drawdown: float = 0.0
    extra: dict = field(default_factory=dict)


def simulate(code: str, bars: list[Bar], params: dict, emit: Callable[[str, dict], None],
             on_step: Callable[[int, float, int], None] = lambda n, eq, trades: None) -> Summary:
    """Run `code` over `bars` (sorted by time); emit(kind, row) for each output row. Raises on strategy errors."""
    from dh_chart_ext.sandbox import require_valid, restricted_globals

    require_valid(code)
    env = restricted_globals()
    exec(compile(code, "<strategy>", "exec"), env)  # noqa: S102 (checked code, restricted builtins, own process)
    on_start, on_bar, on_end = env.get("on_start"), env["on_bar"], env.get("on_end")
    capital = float(params.get("capital", 100_000))
    symbols = sorted({b.sym for b in bars})
    ctx = Context(params.get("symbols", symbols), params, capital, float(params.get("commission", 0.0)), emit)
    if on_start:
        on_start(ctx)
    peak, max_dd, n = capital, 0.0, 0
    i = 0
    while i < len(bars):
        t = bars[i].time
        ctx._now = t
        while i < len(bars) and bars[i].time == t:  # every symbol's bar at this time, then equity once
            b = bars[i]
            ctx._last[b.sym] = b.close
            on_bar(ctx, b)
            i += 1
            n += 1
        eq = ctx.equity
        peak = max(peak, eq)
        dd = (peak - eq) / peak if peak else 0.0
        max_dd = max(max_dd, dd)
        emit("equity", {"Timestamp": t, "Equity": eq, "Cash": ctx.cash, "Drawdown": dd})
        for s, q in ctx._pos.items():
            if q:
                emit("positions", {"Sym": s, "Qty": q, "AvgPrice": ctx._avg.get(s, 0.0), "Updated": t,
                                   "Unrealized": q * (ctx._last[s] - ctx._avg.get(s, 0.0))})
        on_step(n, eq, ctx.trades)
    if on_end:
        on_end(ctx)
    return Summary(bars=n, trades=ctx.trades, equity=ctx.equity, pnl=ctx.equity - capital, max_drawdown=max_dd)


# ----------------------------------------------------------------------------- the Deephaven side

# What a run publishes, wherever it runs: names and column types. The central service creates these as
# input tables (dh_chart_ext.backtests.OUTPUTS); an engine run creates them in its own engine.
SCHEMAS = {
    "trades": {"Timestamp": "Instant", "Sym": "str", "Side": "str", "Qty": "double", "Price": "double",
               "Commission": "double", "RealizedPnl": "double"},
    "orders": {"Timestamp": "Instant", "Sym": "str", "Side": "str", "Qty": "double", "Type": "str", "Price": "double",
               "Status": "str"},
    "positions": {"Sym": "str", "Qty": "double", "AvgPrice": "double", "Unrealized": "double", "Updated": "Instant"},
    "signals": {"Timestamp": "Instant", "Sym": "str", "Kind": "str", "Name": "str", "Value": "double"},
    "equity": {"Timestamp": "Instant", "Equity": "double", "Cash": "double", "Drawdown": "double"},
    # engine runs only: the bars the strategy saw, and the run's own status (for a viewer with no central server)
    "prices": {"Timestamp": "Instant", "Sym": "str", "Open": "double", "High": "double", "Low": "double", "Close": "double"},
    "run_info": {"RunId": "long", "Strategy": "str", "User": "str", "Status": "str", "Bars": "long", "Of": "long",
                 "Trades": "long", "Equity": "double", "Pnl": "double", "MaxDrawdown": "double", "Message": "str"},
}
KEYS = {"positions": ["Sym"], "run_info": ["RunId"]}


def say(event: str, **kw) -> None:
    sys.stdout.write(json.dumps({"event": event, **kw}, default=str) + "\n")
    sys.stdout.flush()


class CentralSink:
    """Appends to input tables on the central server, by the names the spec gives (pydeephaven)."""

    def __init__(self, session, names: dict):
        import pyarrow as pa
        from pydeephaven.table import InputTable

        self.pa, self.session = pa, session
        self.outs, self.schemas, self.opened = {}, {}, []
        for kind, name in names.items():
            t = session.open_table(name)
            self.opened.append(t)  # keep it: a pydeephaven Table releases its export when collected (C27)
            self.outs[kind] = InputTable(session, t.ticket, schema=t.schema)
            self.schemas[kind] = pa.schema([pa.field(f.name, f.type) for f in t.schema])

    def add(self, kind: str, rows: list[dict]) -> None:
        if kind not in self.outs or not rows:
            return
        sch = self.schemas[kind]
        cols = {f.name: [r.get(f.name) for r in rows] for f in sch}
        self.outs[kind].add(self.session.import_table(self.pa.Table.from_pydict(cols, schema=sch)))

    def finish(self, run_id) -> Optional[str]:
        return None


class EngineSink:
    """Publishes the run's tables in this process's own engine (started by start_engine), under the standard
    names; on finish, writes them to Parquet under archive_dir/<run_id>/."""

    def __init__(self, archive_dir: Optional[str]):
        import __main__

        from deephaven import dtypes
        from deephaven.table_factory import input_table

        types = {"Instant": dtypes.Instant, "str": dtypes.string, "double": dtypes.double, "long": dtypes.int64}
        self.tables = {}
        scope = vars(__main__)  # an embedded server's console scope is the launcher's __main__
        for kind, cols in SCHEMAS.items():
            t = input_table(col_defs={c: types[v] for c, v in cols.items()}, key_cols=KEYS.get(kind))
            self.tables[kind] = t
            scope[kind] = t
        self.archive_dir = archive_dir

    def add(self, kind: str, rows: list[dict]) -> None:
        if kind not in self.tables or not rows:
            return
        from deephaven import new_table
        from deephaven.column import datetime_col, double_col, long_col, string_col

        make = {"Instant": datetime_col, "str": string_col, "double": double_col, "long": long_col}
        cols = [make[ty](name, [r.get(name) for r in rows]) for name, ty in SCHEMAS[kind].items()]
        self.tables[kind].add(new_table(cols))

    def finish(self, run_id) -> Optional[str]:
        if not self.archive_dir:
            return None
        import pathlib

        from deephaven import parquet

        d = pathlib.Path(self.archive_dir).resolve() / str(run_id)
        d.mkdir(parents=True, exist_ok=True)
        for kind, t in self.tables.items():
            parquet.write(t.snapshot(), (d / f"{kind}.parquet").as_uri())  # file:/// URIs on Windows (store.py)
        return str(d)


def start_engine(cfg: dict):
    """Start an embedded Deephaven engine in this process: its own JVM, port, and pre-shared key."""
    import os
    import pathlib

    jvm = [f"-Xmx{cfg.get('heap', '1g')}"]
    jvm.append(f"-Dauthentication.psk={cfg['psk']}" if cfg.get("psk")
               else "-DAuthHandlers=io.deephaven.auth.AnonymousAuthenticationHandler")
    if os.name == "nt":  # the same workarounds as run_server.py
        java_home = pathlib.Path(os.environ["JAVA_HOME"])
        dlls = [java_home / "bin", java_home / "bin" / "server"]
        os.environ["PATH"] = os.pathsep.join([str(d) for d in dlls] + [os.environ.get("PATH", "")])
        for d in dlls:
            if d.is_dir():
                os.add_dll_directory(str(d))
        jvm += ["-Dweb.storage.layout.directory=layouts", "-Dweb.storage.notebook.directory=notebooks"]
    from deephaven_server import Server

    server = Server(host=cfg.get("host", "127.0.0.1"), port=int(cfg["port"]), jvm_args=jvm)
    server.start()
    return server


def load_bars(session, table: str, symbols: list[str]) -> list[Bar]:
    import pyarrow as pa

    quoted = ", ".join(f"`{s}`" for s in symbols if s.replace("_", "").replace("-", "").isalnum())
    src = session.open_table(table).where(f"Sym in {quoted}").sort(["Timestamp", "Sym"])
    arrow = src.select(["Timestamp", "Sym", "Open", "High", "Low", "Close", "Volume"]).to_arrow()
    ts = arrow.column("Timestamp").cast(pa.int64()).to_pylist()
    c = {k: arrow.column(k).to_pylist() for k in ("Sym", "Open", "High", "Low", "Close", "Volume")}
    return [Bar(c["Sym"][i], ts[i], c["Open"][i], c["High"][i], c["Low"][i], c["Close"][i], float(c["Volume"][i] or 0))
            for i in range(len(ts)) if c["Close"][i] is not None]


def main() -> int:
    spec = json.loads(sys.stdin.readline())
    params = spec.get("params", {})
    engine_cfg = spec.get("engine")
    t_start = time.monotonic()
    if engine_cfg:  # before anything imports deephaven
        start_engine(engine_cfg)
        say("engine", host=engine_cfg.get("public_host", engine_cfg.get("host", "127.0.0.1")), port=int(engine_cfg["port"]),
            startup_s=round(time.monotonic() - t_start, 1))
    from pydeephaven import Session

    dh = spec["dh"]
    session = Session(host=dh.get("host", "localhost"), port=int(dh.get("port", 10040)))  # the central server: history
    sink = None
    info = {"RunId": spec["run_id"], "Strategy": spec.get("strategy", ""), "User": spec.get("user", ""), "Status": "starting",
            "Bars": 0, "Of": 0, "Trades": 0, "Equity": float(params.get("capital", 100_000)), "Pnl": 0.0,
            "MaxDrawdown": 0.0, "Message": ""}
    try:
        sink = EngineSink(spec.get("archive_dir")) if engine_cfg else CentralSink(session, spec["tables"])
        sink.add("run_info", [info])
        pending: dict[str, list[dict]] = {k: [] for k in SCHEMAS}
        positions: dict[str, dict] = {}

        def emit(kind: str, row: dict) -> None:
            if kind == "log":
                say("log", text=row["text"])
            elif kind == "positions":
                positions[row["Sym"]] = {**positions.get(row["Sym"], {}), **row}  # keyed: the last one wins
            elif kind in pending:
                pending[kind].append(row)

        symbols = [str(s) for s in params.get("symbols", [])]
        if not symbols:
            raise ValueError("params.symbols is empty")
        bars = load_bars(session, spec.get("data", {}).get("table", "bars_1m"), symbols)
        info.update(Status="running", Of=len(bars))
        say("started", bars=len(bars), symbols=symbols)
        shown = [0]  # bars already published to `prices` (engine runs)

        def flush(n: int) -> None:
            if engine_cfg and n > shown[0]:
                pending["prices"] = [{"Timestamp": b.time, "Sym": b.sym, "Open": b.open, "High": b.high, "Low": b.low,
                                      "Close": b.close} for b in bars[shown[0]:n]]
                shown[0] = n
            if positions:
                pending["positions"] = list(positions.values())
                positions.clear()
            for kind, rows in pending.items():
                if rows:
                    sink.add(kind, rows)
                    rows.clear()
            sink.add("run_info", [info])

        speed = max(1.0, float(spec.get("speed", 1000)))  # bars per second: results stream in as a live run would
        t0 = time.monotonic()
        last = [t0]
        capital = float(params.get("capital", 100_000))

        def on_step(n: int, eq: float, trades: int) -> None:
            ahead = n / speed - (time.monotonic() - t0)
            if ahead > 0:
                time.sleep(ahead)
            now = time.monotonic()
            if now - last[0] >= 0.25:
                info.update(Bars=n, Equity=eq, Trades=trades, Pnl=eq - capital)
                flush(n)
                say("progress", bars=n, of=len(bars), equity=round(eq, 2), trades=trades, pnl=round(eq - capital, 2))
                last[0] = now

        summary = simulate(spec["code"], bars, params, emit, on_step)
        info.update(Status="done", Bars=summary.bars, Trades=summary.trades, Equity=summary.equity, Pnl=summary.pnl,
                    MaxDrawdown=summary.max_drawdown)
        flush(len(bars))
        say("done", summary={"bars": summary.bars, "trades": summary.trades, "equity": round(summary.equity, 2),
                             "pnl": round(summary.pnl, 2), "max_drawdown": round(summary.max_drawdown, 4)})
        archive = sink.finish(spec["run_id"])
        if archive:
            say("archived", path=archive)
        linger(engine_cfg)
        return 0
    except Exception as e:  # noqa: BLE001 (reported to the server, which shows it to the user)
        tb = e.__traceback__
        line = None
        while tb is not None:
            if tb.tb_frame.f_code.co_filename == "<strategy>":
                line = tb.tb_lineno
            tb = tb.tb_next
        root = e
        while root.__cause__ is not None:  # pydeephaven wraps the server's reason
            root = root.__cause__
        detail = f" ({type(root).__name__}: {str(root)[:300]})" if root is not e else ""
        message = f"{type(e).__name__}: {e}{detail}"
        say("error", message=message, line=line)
        if sink is not None:
            try:
                info.update(Status="error", Message=message[:300])
                sink.add("run_info", [info])
            except Exception:  # noqa: BLE001
                pass
        linger(engine_cfg)  # an engine run keeps its tables up for a look at what went wrong
        return 1
    finally:
        session.close()


def linger(engine_cfg: Optional[dict]) -> None:
    """An engine run keeps serving its tables for `linger_s` after it ends (the archive outlives it)."""
    if engine_cfg:
        say("lingering", seconds=float(engine_cfg.get("linger_s", 600)))
        time.sleep(float(engine_cfg.get("linger_s", 600)))


if __name__ == "__main__":
    sys.exit(main())
