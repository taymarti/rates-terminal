"""
Static checks on strategy code before it is run, and the restricted builtins it runs with. Pure Python, no
Deephaven: the server checks code when it is saved or submitted, and the CEP (here cep_sim/runner.py) checks
it again before running it.

This is sanitizing, not sandboxing. Python has no safe subset: these checks stop accidents and the obvious
escapes (imports, dunder attributes, eval and friends), but the security boundary is the process the code
runs in (its own OS user, no network but the Deephaven address, memory and CPU limits, a time limit): see
backtests.Launcher.
"""
from __future__ import annotations

import ast
import builtins

MAX_CODE_BYTES = 64 * 1024
ALLOWED_IMPORTS = frozenset({"math", "statistics"})
FORBIDDEN_NAMES = frozenset({
    "eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "getattr", "setattr",
    "delattr", "input", "breakpoint", "help", "memoryview", "exit", "quit", "classmethod", "staticmethod",
    "property", "super", "type", "object", "dir", "id",
})
SAFE_BUILTINS = (
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float", "format", "frozenset",
    "int", "isinstance", "len", "list", "map", "max", "min", "pow", "print", "range", "reversed", "round",
    "set", "slice", "sorted", "str", "sum", "tuple", "zip", "True", "False", "None",
    "ArithmeticError", "Exception", "IndexError", "KeyError", "ValueError", "ZeroDivisionError",
)
HOOKS = ("on_start", "on_bar", "on_end")


class StrategyError(ValueError):
    """The code may not run; `problems` lists why (line: message)."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def check_strategy(code: str) -> list[str]:
    """Problems with `code` (empty: it may run). It must define on_bar(ctx, bar); on_start(ctx) and
    on_end(ctx) are optional."""
    if len(code.encode()) > MAX_CODE_BYTES:
        return [f"the code is over {MAX_CODE_BYTES // 1024} KB"]
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        return [f"line {e.lineno}: {e.msg}"]
    problems: list[str] = []

    def bad(node: ast.AST, msg: str) -> None:
        problems.append(f"line {getattr(node, 'lineno', '?')}: {msg}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    bad(node, f"import {a.name} is not allowed (allowed: {', '.join(sorted(ALLOWED_IMPORTS))})")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS or node.level:
                bad(node, f"from {node.module} import is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            bad(node, f"attribute {node.attr} is not allowed (no names starting with _)")
        elif isinstance(node, ast.Name) and (node.id in FORBIDDEN_NAMES or node.id.startswith("__")):
            bad(node, f"{node.id} is not allowed")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bad(node, "global and nonlocal are not allowed (keep state on ctx.state)")
        elif isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)):
            bad(node, "async code is not allowed")
        elif isinstance(node, ast.ClassDef):
            bad(node, "classes are not allowed (use functions and ctx.state)")
    defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    if "on_bar" not in defined:
        problems.append("define on_bar(ctx, bar)")
    return problems


def require_valid(code: str) -> None:
    problems = check_strategy(code)
    if problems:
        raise StrategyError(problems)


def restricted_globals() -> dict:
    """The globals a checked strategy is executed with: safe builtins, imports limited to ALLOWED_IMPORTS."""
    safe = {name: getattr(builtins, name) for name in SAFE_BUILTINS if hasattr(builtins, name)}

    def limited_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        if level or name.split(".")[0] not in ALLOWED_IMPORTS:
            raise ImportError(f"import {name} is not allowed")
        return __import__(name, globals, locals, fromlist, level)

    safe["__import__"] = limited_import
    return {"__builtins__": safe, "__name__": "strategy"}
