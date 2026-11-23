"""
Backtests from the UI: traders save strategy code, submit it, and watch the results stream into tables, on
any request API (rpc.Api).

    backtests = BacktestService(dh_port=10040, path=app_dir / "strategies.parquet")
    backtests.register(api)
    backtest_runs = backtests.runs        # every run: user, strategy, status, progress, P&L (publish it)

The flow:
  1. `backtest_submit {code | strategy, params}`: the code is checked (sandbox.check_strategy); the server
     creates the run's tables (input tables, published as bt<N>_trades, _orders, _positions, _signals,
     _equity), holds them for the stream, and answers with their names and the tables themselves.
  2. A Launcher starts the strategy's process (the CEP; here cep_sim/runner.py) with the addresses: the
     Deephaven host and port and the table names (or names the UI passes in `targets`, an override). The
     process appends rows as it goes; the tables tick in every client watching them.
  3. Its JSON lines (progress, done, error) update the run's row in `runs`.

Methods:
  strategies        {}                          -> [{name, saved_at}]                     this user's
  strategy_save     {name, code, params?}       -> {name, saved_at, problems: []}       checked, saved even with problems
  strategy_load     {name}                      -> {name, code, params, saved_at}
  strategy_delete   {name}                      -> {deleted}
  strategy_check    {code}                      -> {problems: [...]}
  strategy_example  {}                          -> {code, params}                        a starting point
  backtest_submit   {code?, strategy?, params?, targets?}  -> {run_id, tables: {kind: name}, symbols, status}  + the tables
  backtest_cancel   {run_id}                    -> {cancelled}
  backtest_runs     {}                          -> this user's runs, newest first
  backtest_engine   {run_id}                    -> {url, psk, up, archive}            an engine run's address and key
  backtest_archive  {run_id}                    -> {tables: {kind: name}}             its Parquet archive, opened here

Engine mode (params.engine = true): the run's process starts a Deephaven engine of its own (a free port on
engine_host, a pre-shared key) and publishes the standard tables there (trades, orders, positions, signals,
equity, prices, run_info), as a CEP with its own engine would; the registry (`runs`) holds its address, and
the key goes only to its owner (backtest_engine). When the run ends it writes the tables to Parquet under
archive_dir/<run_id>/ and keeps serving for engine_linger_s; after that, backtest_archive opens the archive.

The process is the sandbox, and the Launcher is where that is decided: LocalLauncher runs `python -I` in a
temporary directory with a minimal environment, a time limit, and on POSIX memory and CPU limits; it is for
development. In production, pass a CommandLauncher whose command runs the runner as its own OS user in a
container or jail (nsjail, bwrap, docker) with only the Deephaven address reachable, or a launcher that
hands the spec to the CEP's own runner, local or remote.
"""
from __future__ import annotations

import itertools
import secrets
import socket
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, Optional

from deephaven import dtypes, new_table
from deephaven.column import bool_col, datetime_col, double_col, long_col, string_col
from deephaven.table_factory import input_table

from .rpc import Api, ApiError, Reply
from .sandbox import check_strategy
from .store import epoch_ms, persistent_input_table

# the CEP stand-in: shipped in the wheel as dh_chart_ext/_cep_sim/runner.py; in a source checkout, the repo's cep_sim/
_HERE = pathlib.Path(__file__).resolve().parent
RUNNER = next((p for p in (_HERE / "_cep_sim" / "runner.py", _HERE.parents[2] / "cep_sim" / "runner.py") if p.exists()),
              _HERE / "_cep_sim" / "runner.py")

OUTPUTS = {
    "trades": ({"Timestamp": dtypes.Instant, "Sym": dtypes.string, "Side": dtypes.string, "Qty": dtypes.double,
                "Price": dtypes.double, "Commission": dtypes.double, "RealizedPnl": dtypes.double}, None),
    "orders": ({"Timestamp": dtypes.Instant, "Sym": dtypes.string, "Side": dtypes.string, "Qty": dtypes.double,
                "Type": dtypes.string, "Price": dtypes.double, "Status": dtypes.string}, None),
    "positions": ({"Sym": dtypes.string, "Qty": dtypes.double, "AvgPrice": dtypes.double, "Unrealized": dtypes.double,
                   "Updated": dtypes.Instant}, ["Sym"]),
    "signals": ({"Timestamp": dtypes.Instant, "Sym": dtypes.string, "Kind": dtypes.string, "Name": dtypes.string,
                 "Value": dtypes.double}, None),
    "equity": ({"Timestamp": dtypes.Instant, "Equity": dtypes.double, "Cash": dtypes.double,
                "Drawdown": dtypes.double}, None),
}
RUN_COLUMNS = {"RunId": dtypes.int64, "User": dtypes.string, "Strategy": dtypes.string, "Status": dtypes.string,
               "Started": dtypes.Instant, "Ended": dtypes.Instant, "Bars": dtypes.int64, "Of": dtypes.int64,
               "Trades": dtypes.int64, "Equity": dtypes.double, "Pnl": dtypes.double, "MaxDrawdown": dtypes.double,
               "Message": dtypes.string, "Engine": dtypes.string, "EngineUp": dtypes.bool_, "Archive": dtypes.string}
STRATEGY_COLUMNS = {"User": dtypes.string, "Name": dtypes.string, "Code": dtypes.string, "Params": dtypes.string,
                    "SavedAt": dtypes.Instant}


# ----------------------------------------------------------------------------- launchers (the sandbox hook)

class Launcher:
    """Starts a strategy process for a spec; returns a Popen-like object with stdout lines and kill()."""

    def launch(self, spec: dict) -> subprocess.Popen:
        raise NotImplementedError


def _start(argv: list[str], spec: dict, **kw) -> subprocess.Popen:
    """Start the process in a fresh directory and send it the spec. Its stderr goes to a file there, never
    an unread pipe (a full pipe blocks the process); the path is `proc.stderr_path`."""
    cwd = tempfile.mkdtemp(prefix=f"bt{spec['run_id']}_")
    path = os.path.join(cwd, "stderr.txt")
    with open(path, "w") as err:
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, cwd=cwd, text=True, **kw)
    p.stderr_path = path  # type: ignore[attr-defined]
    p.stdin.write(json.dumps(spec) + "\n")
    p.stdin.close()
    return p


class LocalLauncher(Launcher):
    """For development: `python -I runner.py` in a temporary directory, a minimal environment, a time limit
    (enforced by the service), and on POSIX address-space and CPU limits. Not a security boundary."""

    # memory_mb caps the address space (RLIMIT_AS), not resident memory: gRPC and Arrow reserve far more virtual
    # memory than they use (thread stacks, arenas), and at 1 GB the runner's gRPC threads failed to start on a
    # Linux CI runner ("Failed to start lifeguard thread"). A container's memory limit is the better control.
    def __init__(self, python: str = sys.executable, runner: pathlib.Path = RUNNER, memory_mb: int = 4096, cpu_s: int = 600):
        self.python, self.runner, self.memory_mb, self.cpu_s = python, runner, memory_mb, cpu_s

    def _limits(self, address_space: bool = True):  # pragma: no cover (POSIX only)
        import resource

        if address_space:
            resource.setrlimit(resource.RLIMIT_AS, (self.memory_mb << 20, self.memory_mb << 20))
        resource.setrlimit(resource.RLIMIT_CPU, (self.cpu_s, self.cpu_s))

    def launch(self, spec: dict) -> subprocess.Popen:
        keep = ("SYSTEMROOT", "PATH", "TEMP", "TMP", "LANG", "JAVA_HOME")  # SYSTEMROOT: sockets on Windows; JAVA_HOME: engine runs
        env = {k: v for k, v in os.environ.items() if k in keep}
        # an engine run starts a JVM, which reserves far more address space than its heap (class space, code
        # cache, thread stacks): no RLIMIT_AS for it; its heap is -Xmx, and a container bounds the rest
        engine = bool(spec.get("engine"))
        return _start([self.python, "-I", str(self.runner)], spec, env=env,
                      preexec_fn=(lambda: self._limits(address_space=not engine)) if os.name == "posix" else None)


class CommandLauncher(LocalLauncher):
    """Runs `command` (a list; the runner reads the spec on stdin), e.g. a jail around the runner:
    ["nsjail", "--config", "bt.cfg", "--", "python3", "-I", "/opt/cep/runner.py"]."""

    def __init__(self, command: list[str]):
        super().__init__()
        self.command = list(command)

    def launch(self, spec: dict) -> subprocess.Popen:
        return _start(self.command, spec)


# ----------------------------------------------------------------------------- the service

class BacktestService:
    def __init__(self, *, dh_host: str = "localhost", dh_port: int = 10040, data_table: str = "bars_1m",
                 launcher: Optional[Launcher] = None, path: Optional[str | pathlib.Path] = None,
                 timeout_s: float = 900.0, max_active_per_user: int = 3, keep_runs_per_user: int = 20,
                 scope: Optional[dict] = None, archive_dir: Optional[str | pathlib.Path] = None,
                 engine_host: str = "127.0.0.1", engine_heap: str = "1g", engine_linger_s: float = 600.0):
        import __main__

        from deephaven.execution_context import get_exec_ctx

        self._ctx = get_exec_ctx()  # the watcher threads write tables: they need the app's execution context
        self.dh = {"host": dh_host, "port": dh_port}
        self.data_table = data_table
        self.archive_dir = str(pathlib.Path(archive_dir).resolve()) if archive_dir else None
        self.engine = {"host": engine_host, "heap": engine_heap, "linger_s": engine_linger_s}
        self._archives: dict[int, dict] = {}  # run_id -> {kind: name} of archives opened here
        self.launcher = launcher or LocalLauncher()
        self.timeout_s = timeout_s
        self.max_active = max_active_per_user
        self.keep_runs = keep_runs_per_user
        self.scope = scope if scope is not None else vars(__main__)  # where run tables are published
        self.runs = input_table(col_defs=RUN_COLUMNS, key_cols=["RunId"])
        self.strategies = persistent_input_table(STRATEGY_COLUMNS, ["User", "Name"], path)
        self._lock = threading.Lock()
        self._ids = itertools.count(int(time.time()) % 100_000 * 10)
        self._run: dict[int, dict] = {}  # run_id -> {user, strategy, proc, names, status, row}
        self._code: dict[str, dict[str, dict]] = {}  # user -> name -> {code, params, saved_at}
        for r in self.strategies.iter_dict():
            self._code.setdefault(r["User"], {})[r["Name"]] = {
                "code": r["Code"], "params": json.loads(r["Params"] or "{}"), "saved_at": epoch_ms(r["SavedAt"])}

    # strategies

    def save(self, user: str, name: str, code: str, params: dict) -> dict:
        name = (name or "").strip()
        if not name or len(name) > 64:
            raise ApiError("a strategy name is 1 to 64 characters", "bad_params")
        if len(code.encode()) > 64 * 1024:
            raise ApiError("strategy code is at most 64 KB", "limit")
        at = int(time.time() * 1000)
        with self._lock:
            mine = self._code.setdefault(user, {})
            if name not in mine and len(mine) >= 200:
                raise ApiError("at most 200 strategies per user", "limit")
            mine[name] = {"code": code, "params": params, "saved_at": at}
        self.strategies.add(new_table([string_col("User", [user]), string_col("Name", [name]), string_col("Code", [code]),
                                       string_col("Params", [json.dumps(params)]), datetime_col("SavedAt", [at * 1_000_000])]))
        return {"name": name, "saved_at": at, "problems": check_strategy(code)}

    # runs

    def submit(self, user: str, code: str, params: dict, strategy: str = "", targets: Optional[dict] = None) -> tuple[int, dict, list]:
        problems = check_strategy(code)
        if problems:
            raise ApiError("the strategy can't run: " + "; ".join(problems), "bad_params")
        symbols = params.get("symbols") or []
        if not isinstance(symbols, list) or not symbols or not all(isinstance(s, str) and s.isalnum() for s in symbols):
            raise ApiError("params.symbols: a list of symbols", "bad_params")
        with self._lock:
            active = sum(1 for r in self._run.values() if r["user"] == user and r["status"] in ("starting", "running"))
            if active >= self.max_active:
                raise ApiError(f"{user} has {active} backtests running (limit {self.max_active})", "limit")
            run_id = next(self._ids)
        engine = bool(params.get("engine"))
        names, tables = {}, []
        for kind, (cols, keys) in ([] if engine else OUTPUTS.items()):
            if targets and targets.get(kind):  # the UI's override: append to existing input tables
                name = str(targets[kind])
                if name not in self.scope:
                    raise ApiError(f"targets.{kind}: no table {name!r}", "bad_params")
                t = self.scope[name]
            else:
                name = f"bt{run_id}_{kind}"
                t = input_table(col_defs=cols, key_cols=keys)
                self.scope[name] = t  # published: the CEP and every client open it by name
            names[kind] = name
            tables.append(t)
        spec = {"run_id": run_id, "code": code, "params": params, "speed": float(params.get("speed", 1000)),
                "dh": self.dh, "data": {"table": params.get("data", self.data_table)}, "strategy": strategy, "user": user}
        key = None
        if engine:  # the run starts an engine of its own: a free port, a key only its owner gets
            key = secrets.token_urlsafe(18)
            spec.update(engine={**self.engine, "port": _free_port(), "psk": key}, archive_dir=self.archive_dir)
            names = {k: k for k in ("trades", "orders", "positions", "signals", "equity", "prices", "run_info")}
        else:
            spec["tables"] = names
        rec = {"user": user, "strategy": strategy or "(unsaved)", "names": names, "status": "starting", "symbols": list(symbols),
               "row": {"Bars": 0, "Of": 0, "Trades": 0, "Equity": float(params.get("capital", 100_000)), "Pnl": 0.0,
                       "MaxDrawdown": 0.0, "Message": ""},
               "started": int(time.time() * 1000), "ended": None, "proc": None, "owned": not targets and not engine,
               "engine": {"url": None, "psk": key, "up": False} if engine else None, "archive": None}
        with self._lock:
            self._run[run_id] = rec
        self._write_run(run_id)
        try:
            rec["proc"] = self.launcher.launch(spec)
        except Exception as e:  # noqa: BLE001
            self._finish(run_id, "error", f"launch failed: {e}")
            raise ApiError(f"the backtest could not start: {e}", "error") from e
        threading.Thread(target=self._watch_safely, args=(run_id,), daemon=True, name=f"bt{run_id}").start()
        self._trim(user)
        return run_id, names, tables

    def cancel(self, user: str, run_id: int) -> bool:
        rec = self._run.get(run_id)
        if not rec or rec["user"] != user:
            raise ApiError(f"no run {run_id}", "not_found")
        if rec["status"] not in ("starting", "running"):
            if rec["engine"] and rec["engine"]["up"] and rec["proc"] is not None:
                rec["proc"].kill()  # a finished run's lingering engine: stop it (its archive stays)
            return False
        if rec["proc"] is not None:
            rec["proc"].kill()
        self._finish(run_id, "cancelled", "cancelled by the user")
        return True

    def engine_of(self, user: str, run_id: int) -> dict:
        rec = self._run.get(run_id)
        if not rec or rec["user"] != user:
            raise ApiError(f"no run {run_id}", "not_found")
        if not rec["engine"]:
            raise ApiError(f"run {run_id} has no engine of its own", "bad_params")
        e = rec["engine"]
        return {"url": e["url"], "psk": e["psk"], "up": e["up"], "archive": rec["archive"] is not None}

    def open_archive(self, user: str, run_id: int) -> dict:
        """Load a finished engine run's Parquet archive here, published as arc<run>_<kind>; names by kind."""
        rec = self._run.get(run_id)
        if not rec or rec["user"] != user:
            raise ApiError(f"no run {run_id}", "not_found")
        if not rec["archive"]:
            raise ApiError(f"run {run_id} has no archive", "not_found")
        with self._lock:
            if run_id in self._archives:
                return self._archives[run_id]
        from deephaven import parquet

        names = {}
        for f in sorted(pathlib.Path(rec["archive"]).glob("*.parquet")):
            name = f"arc{run_id}_{f.stem}"
            self.scope[name] = parquet.read(f.resolve().as_uri())
            names[f.stem] = name
        with self._lock:
            self._archives[run_id] = names
        return names

    def list_runs(self, user: str) -> list[dict]:
        with self._lock:
            mine = [(rid, r) for rid, r in self._run.items() if r["user"] == user]
        return [{"run_id": rid, "strategy": r["strategy"], "status": r["status"], "tables": r["names"], "symbols": r["symbols"],
                 "started": r["started"], "ended": r["ended"], **{k.lower(): v for k, v in r["row"].items()},
                 "engine": {"url": r["engine"]["url"], "up": r["engine"]["up"]} if r["engine"] else None,
                 "archive": r["archive"] is not None}
                for rid, r in sorted(mine, key=lambda x: -x[0])]

    # the process

    def _watch_safely(self, run_id: int) -> None:
        with self._ctx:
            try:
                self._watch(run_id)
            except Exception as e:  # noqa: BLE001 (a dead watcher would leave the run "running" for good)
                print(f"[backtests] run {run_id}: watcher failed: {e!r}", flush=True)
                try:
                    self._run[run_id]["proc"].kill()
                finally:
                    self._finish(run_id, "error", f"internal error: {e}")

    def _watch(self, run_id: int) -> None:
        rec = self._run[run_id]
        proc = rec["proc"]
        deadline = time.monotonic() + self.timeout_s
        killer = threading.Timer(self.timeout_s, lambda: proc.poll() is None and rec["status"] in ("starting", "running") and proc.kill())
        killer.daemon = True
        killer.start()
        try:
            for line in proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                ev = msg.get("event")
                if ev == "engine":
                    host = msg.get("host", self.engine["host"])
                    rec["engine"].update(url=f"http://{host}:{int(msg['port'])}", up=True)
                    rec["row"]["Message"] = f"engine up in {msg.get('startup_s', '?')} s"
                elif ev == "archived":
                    rec["archive"] = str(msg.get("path"))
                    self._write_run(run_id)
                    continue
                elif ev == "lingering":
                    continue
                elif ev == "started":
                    rec["status"] = "running"
                    rec["row"]["Of"] = int(msg.get("bars", 0))
                elif ev == "progress":
                    rec["row"].update(Bars=int(msg.get("bars", 0)), Equity=float(msg.get("equity", 0)),
                                      Trades=int(msg.get("trades", 0)), Pnl=float(msg.get("pnl", 0)))
                elif ev == "done":
                    s = msg.get("summary", {})
                    rec["row"].update(Bars=s.get("bars", 0), Trades=s.get("trades", 0), Equity=s.get("equity", 0.0),
                                      Pnl=s.get("pnl", 0.0), MaxDrawdown=s.get("max_drawdown", 0.0))
                    self._finish(run_id, "done", "")
                    continue
                elif ev == "error":
                    where = f" (line {msg['line']})" if msg.get("line") else ""
                    self._finish(run_id, "error", f"{msg.get('message', 'failed')}{where}")
                    continue
                elif ev == "log":
                    rec["row"]["Message"] = str(msg.get("text", ""))[:300]
                if rec["status"] in ("starting", "running"):
                    self._write_run(run_id)
            proc.wait()
            if rec["engine"]:
                rec["engine"]["up"] = False
                self._write_run(run_id)
            if rec["status"] in ("starting", "running"):
                err = _tail(getattr(proc, "stderr_path", None))
                timed_out = time.monotonic() >= deadline
                self._finish(run_id, "error", "time limit reached" if timed_out else
                             (err[-1] if err else f"the process exited ({proc.returncode})"))
        finally:
            killer.cancel()

    def _finish(self, run_id: int, status: str, message: str) -> None:
        rec = self._run[run_id]
        if rec["status"] in ("done", "error", "cancelled"):
            return
        rec["status"] = status
        rec["ended"] = int(time.time() * 1000)
        if message:
            rec["row"]["Message"] = message[:300]
        self._write_run(run_id)

    def _write_run(self, run_id: int) -> None:
        rec = self._run[run_id]
        r = rec["row"]
        self.runs.add(new_table([
            long_col("RunId", [run_id]), string_col("User", [rec["user"]]), string_col("Strategy", [rec["strategy"]]),
            string_col("Status", [rec["status"]]), datetime_col("Started", [rec["started"] * 1_000_000]),
            datetime_col("Ended", [rec["ended"] * 1_000_000 if rec["ended"] else None]),
            long_col("Bars", [int(r["Bars"])]), long_col("Of", [int(r["Of"])]), long_col("Trades", [int(r["Trades"])]),
            double_col("Equity", [float(r["Equity"])]), double_col("Pnl", [float(r["Pnl"])]),
            double_col("MaxDrawdown", [float(r["MaxDrawdown"])]), string_col("Message", [r["Message"]]),
            string_col("Engine", [rec["engine"]["url"] if rec["engine"] else None]),
            bool_col("EngineUp", [bool(rec["engine"] and rec["engine"]["up"])]),
            string_col("Archive", [rec["archive"]]),
        ]))

    def _trim(self, user: str) -> None:
        """Keep the newest `keep_runs` runs per user: older finished runs' tables are unpublished."""
        with self._lock:
            mine = sorted((rid for rid, r in self._run.items() if r["user"] == user), reverse=True)
            old = [rid for rid in mine[self.keep_runs:] if self._run[rid]["status"] not in ("starting", "running")]
            for rid in old:
                rec = self._run.pop(rid)
                if rec["owned"]:
                    for name in rec["names"].values():
                        self.scope.pop(name, None)
        if old:
            self.runs.delete(new_table([long_col("RunId", old)]))

    # as request methods

    def register(self, api: Api) -> None:
        svc = self

        @api.method
        def strategies(ctx):
            """This user's saved strategies, newest first."""
            mine = svc._code.get(ctx.user, {})
            return [{"name": n, "saved_at": v["saved_at"]} for n, v in sorted(mine.items(), key=lambda kv: -kv[1]["saved_at"])]

        @api.method
        def strategy_save(ctx, name: str, code: str, params: dict = {}):  # noqa: B006
            """Save a strategy (checked; saved even if it has problems)."""
            return svc.save(ctx.user, name, code, params)

        @api.method
        def strategy_load(ctx, name: str):
            """A saved strategy's code and parameters."""
            hit = svc._code.get(ctx.user, {}).get(name)
            if hit is None:
                raise ApiError(f"no strategy {name!r}", "not_found")
            return {"name": name, **hit}

        @api.method
        def strategy_delete(ctx, name: str):
            """Delete a saved strategy."""
            with svc._lock:
                gone = svc._code.get(ctx.user, {}).pop(name, None) is not None
            if gone:
                svc.strategies.delete(new_table([string_col("User", [ctx.user]), string_col("Name", [name])]))
            return {"deleted": gone}

        @api.method
        def strategy_example(ctx):
            """A starting point: an example strategy and its parameters."""
            return {"code": default_strategy(), "params": {"symbols": ["ES", "NQ"], "capital": 100000, "speed": 2000,
                                                             "fast": 10, "slow": 30}}

        @api.method
        def strategy_check(ctx, code: str):
            """Problems that would stop the code from running."""
            return {"problems": check_strategy(code)}

        @api.method(tables=True)
        def backtest_submit(ctx, code: str = "", strategy: str = "", params: dict = {}, targets: dict = {}):  # noqa: B006
            """Run a strategy (its code, or a saved one by name) over history; results stream into the run's tables."""
            if not code:
                hit = svc._code.get(ctx.user, {}).get(strategy)
                if hit is None:
                    raise ApiError("send code, or the name of a saved strategy", "bad_params")
                code, params = hit["code"], {**hit["params"], **params}
            run_id, names, tables = svc.submit(ctx.user, code, params, strategy, targets or None)
            ctx.hold(("backtest", run_id), lambda: None)  # the run shows in this stream's holds
            return Reply({"run_id": run_id, "tables": names, "symbols": svc._run[run_id]["symbols"],
                          "status": svc._run[run_id]["status"]}, tables=tables)

        @api.method
        def backtest_cancel(ctx, run_id: int):
            """Stop a running backtest."""
            return {"cancelled": svc.cancel(ctx.user, run_id)}

        @api.method
        def backtest_engine(ctx, run_id: int):
            """Where a run's own engine is and its key (the run's owner only)."""
            return svc.engine_of(ctx.user, run_id)

        @api.method
        def backtest_archive(ctx, run_id: int):
            """Open a finished engine run's archive here: the table names by kind."""
            return {"tables": svc.open_archive(ctx.user, run_id)}

        @api.method
        def backtest_runs(ctx):
            """This user's runs, newest first."""
            return svc.list_runs(ctx.user)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _tail(path: Optional[str]) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:  # type: ignore[arg-type]
            return [line for line in f.read().strip().splitlines() if line.strip()][-5:]
    except (OSError, TypeError):
        return []


def default_strategy() -> str:
    """The example a new strategy panel starts with."""
    return '''# An EMA crossover on each subscribed symbol: long when the fast EMA is above the slow one, short below.
def on_start(ctx):
    ctx.state["fast"] = ctx.param("fast", 10)
    ctx.state["slow"] = ctx.param("slow", 30)

def on_bar(ctx, bar):
    fast = ctx.ema(bar.sym + ":fast", ctx.state["fast"], bar.close)
    slow = ctx.ema(bar.sym + ":slow", ctx.state["slow"], bar.close)
    ctx.plot("fast", fast, bar.sym)
    ctx.plot("slow", slow, bar.sym)
    if fast > slow and ctx.position(bar.sym) <= 0:
        ctx.target(bar.sym, 1)
        ctx.signal(bar.sym, "long")
    elif fast < slow and ctx.position(bar.sym) >= 0:
        ctx.target(bar.sym, -1)
        ctx.signal(bar.sym, "short", -1)
'''
