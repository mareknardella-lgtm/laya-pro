"""Static architecture boundary checker for the local AI backend.

This is a targeted lint gate, not a complete security proof. It checks source-level
boundaries to catch accidental arbitrary execution or model-to-executor wiring.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"
FORBIDDEN_IMPORTS = {"subprocess", "pty"}
FORBIDDEN_CALLS = {"eval", "exec"}


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(APP).with_suffix("").parts)


def check() -> list[str]:
    violations: list[str] = []
    parsed: dict[Path, ast.Module] = {}
    for path in sorted(APP.rglob("*.py")):
        try:
            parsed[path] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            violations.append(f"{path.relative_to(ROOT)}: cannot parse source ({type(exc).__name__})")
            continue
        tree = parsed[path]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN_IMPORTS:
                        violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: forbidden import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in FORBIDDEN_IMPORTS:
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: forbidden import {node.module}")
            elif isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
                if name in FORBIDDEN_CALLS or (isinstance(func, ast.Attribute) and name in {"system", "popen"}):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: forbidden dynamic execution call {name}")

    for path, tree in parsed.items():
        module = _module_name(path)
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
        if module.startswith("systems.system2"):
            forbidden = [name for name in imported_modules if any(boundary in name for boundary in ("tools.executor", "tools.registry", "security.policy_engine", "security.approval_gate"))]
            if forbidden:
                violations.append(f"{path.relative_to(ROOT)}: System 2 imports privileged boundaries: {', '.join(sorted(forbidden))}")
        if module.startswith("systems.laya"):
            forbidden = [name for name in imported_modules if any(boundary in name for boundary in ("tools.executor", "tools.registry", "security.policy_engine", "security.approval_gate"))]
            if forbidden:
                violations.append(f"{path.relative_to(ROOT)}: Laya adapter imports privileged execution/policy boundaries: {', '.join(sorted(forbidden))}")
        if module.startswith("api.routes.system2"):
            forbidden = [name for name in imported_modules if any(boundary in name for boundary in ("tools.executor", "tools.registry", "security.policy_engine", "security.approval_gate"))]
            if forbidden:
                violations.append(f"{path.relative_to(ROOT)}: System 2 API imports privileged boundaries: {', '.join(sorted(forbidden))}")

    return violations


if __name__ == "__main__":
    found = check()
    if found:
        print("SECURITY BOUNDARY CHECK: FAIL")
        print("\n".join(found))
        sys.exit(1)
    print("SECURITY BOUNDARY CHECK: PASS (no arbitrary execution imports/calls or model-to-executor imports)")
