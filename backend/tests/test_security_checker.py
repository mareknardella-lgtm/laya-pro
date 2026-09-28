from __future__ import annotations

import ast


def violations_for(source: str) -> list[str]:
    tree = ast.parse(source)
    violations: list[str] = []
    forbidden_imports = {"subprocess", "pty"}
    forbidden_calls = {"eval", "exec", "compile", "system", "popen"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            violations.extend(alias.name for alias in node.names if alias.name.split(".")[0] in forbidden_imports)
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in forbidden_imports:
            violations.append(node.module)
        elif isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if name in forbidden_calls:
                violations.append(name)
    return violations


def test_dynamic_execution_is_a_detectable_violation() -> None:
    assert violations_for("value = eval('1 + 1')") == ["eval"]


def test_subprocess_is_a_detectable_violation() -> None:
    assert violations_for("import subprocess") == ["subprocess"]


def test_normal_validation_is_allowed() -> None:
    assert violations_for("value = int('2')") == []
