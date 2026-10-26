"""Tests for pydep's dependency analysis."""

from pathlib import Path

import pytest

from pydep import main
from pydep.logic import DependencyGraph, build_graph, classify, direct_dependencies


def write_module(tmp_path: Path, source: str, name: str = "m") -> Path:
    """Write `source` to `<tmp_path>/<name>.py` and return the file path."""
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    return path


def graph_of(tmp_path: Path, source: str) -> DependencyGraph:
    """Build the graph of a single module `m` with the given source."""
    return build_graph(write_module(tmp_path, source))


def test_local_or_parameter_shadowing_a_global_is_not_a_dependency(
    tmp_path: Path,
) -> None:
    """Names bound inside a function do not count as module dependencies."""
    graph = graph_of(
        tmp_path,
        "X = 1\ndef f(X):\n    return X\ndef g():\n    X = 2\n    return X\n",
    )
    assert direct_dependencies(graph, "m:f") == set()
    assert direct_dependencies(graph, "m:g") == set()


def test_decorators_defaults_and_annotations_are_dependencies(tmp_path: Path) -> None:
    """Expressions in a def header are dependencies of that def."""
    graph = graph_of(
        tmp_path,
        "def deco(fn):\n    return fn\n"
        "DEFAULT = 1\n"
        "class Hint: ...\n"
        "@deco\n"
        "def f(a: Hint = DEFAULT) -> None: ...\n",
    )
    assert direct_dependencies(graph, "m:f") == {"m:deco", "m:DEFAULT", "m:Hint"}


def test_class_is_one_unit_with_bases_and_method_bodies(tmp_path: Path) -> None:
    """A class depends on its bases and on everything its methods use."""
    graph = graph_of(
        tmp_path,
        "LIMIT = 3\n"
        "class Base: ...\n"
        "class C(Base):\n"
        "    def run(self):\n"
        "        def inner():\n            return LIMIT\n"
        "        return inner()\n",
    )
    assert direct_dependencies(graph, "m:C") == {"m:Base", "m:LIMIT"}


def test_module_level_comprehension_variable_is_not_a_dependency(
    tmp_path: Path,
) -> None:
    """A comprehension's loop variable is local even at module level."""
    graph = graph_of(tmp_path, "v = 0\nITEMS = [1]\nVALS = [v for v in ITEMS]\n")
    assert direct_dependencies(graph, "m:VALS") == {"m:ITEMS"}


def test_imports_resolve_to_external_modules(tmp_path: Path) -> None:
    """Imported names resolve to the module they come from."""
    graph = graph_of(
        tmp_path,
        "import os\n"
        "import numpy as np\n"
        "from collections import OrderedDict\n"
        "def f():\n    return os.getcwd(), np.zeros(1), OrderedDict()\n",
    )
    assert direct_dependencies(graph, "m:f") == {"os", "numpy", "collections"}
    assert classify(graph, "os") == "stdlib"
    assert classify(graph, "numpy") == "third-party"


def test_module_level_statements_belong_to_module_pseudo_symbol(
    tmp_path: Path,
) -> None:
    """Top-level code that is not a definition is collected under `<module>`."""
    graph = graph_of(
        tmp_path,
        'def main(): ...\nif __name__ == "__main__":\n    main()\n',
    )
    assert direct_dependencies(graph, "m:<module>") == {"m:main"}


def test_builtins_are_ignored_and_unknown_names_are_unresolved(
    tmp_path: Path,
) -> None:
    """Builtins are not dependencies; undefined names are reported as unresolved."""
    graph = graph_of(tmp_path, "def f():\n    return len(missing)\n")
    assert direct_dependencies(graph, "m:f") == {"m:missing"}
    assert classify(graph, "m:missing") == "unresolved"


def test_cli_prints_each_symbol_with_its_dependencies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`pydep file.py` lists every symbol with its dependencies."""
    path = write_module(
        tmp_path, "import os\ndef helper(): ...\ndef f():\n    return helper(), os\n"
    )
    main([str(path)])
    out = capsys.readouterr().out
    assert "m:f\n    m:helper\n    stdlib: os\n" in out
