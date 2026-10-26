"""Tests for pydep's dependency analysis."""

from pathlib import Path

import pytest

from pydep import main
from pydep.logic import (
    DependencyGraph,
    build_graph,
    classify,
    collect_dependencies,
    direct_dependencies,
    modules_of,
)


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


CHAIN_SOURCE = (
    "import json\n"
    "import numpy\n"
    "def c():\n    return numpy.zeros(1)\n"
    "def b():\n    return c()\n"
    "def a():\n    return b()\n"
    "def d():\n    return json.dumps({})\n"
    "a()\n"
)


def test_zoom_follows_the_chain_and_excludes_unrelated_symbols(tmp_path: Path) -> None:
    """A symbol's dependencies are its transitive closure and nothing else."""
    graph = graph_of(tmp_path, CHAIN_SOURCE)
    assert collect_dependencies(graph, "m:a") == {"m:b", "m:c", "numpy"}


def test_mutual_recursion_terminates(tmp_path: Path) -> None:
    """Cycles are followed once and the start symbol is not its own dependency."""
    graph = graph_of(tmp_path, "def a():\n    return b()\ndef b():\n    return a()\n")
    assert collect_dependencies(graph, "m:a") == {"m:b"}


def test_module_view_is_the_union_of_its_symbols_minus_its_own_symbols(
    tmp_path: Path,
) -> None:
    """A module depends on what any of its symbols or top-level code depends on."""
    graph = graph_of(tmp_path, CHAIN_SOURCE)
    assert collect_dependencies(graph, "m") == {"numpy", "json"}


def test_cli_zoom_prints_only_the_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`pydep file.py m:b` prints b's closure and no other symbol."""
    main([str(write_module(tmp_path, CHAIN_SOURCE)), "m:b"])
    assert capsys.readouterr().out == "m:b\n    m:c\n    numpy\n\n"


def test_cli_rejects_unknown_target(tmp_path: Path) -> None:
    """An unknown target is a usage error, not an empty result."""
    with pytest.raises(SystemExit):
        main([str(write_module(tmp_path, CHAIN_SOURCE)), "m:nope"])


PACKAGE = {
    "pkg/__init__.py": "from pkg.a import f\n",
    "pkg/a.py": (
        "import numpy\n"
        "LIMIT = 1\n"
        "def f():\n    return LIMIT\n"
        "def other():\n    return numpy.zeros(1)\n"
    ),
    "pkg/b.py": (
        "from pkg.a import f\n"
        "from pkg import f as reexported\n"
        "from pkg.a import missing\n"
        "def g():\n    return f()\n"
        "def h():\n    return reexported()\n"
        "def bad():\n    return missing\n"
    ),
}


def write_package(root: Path, files: dict[str, str]) -> Path:
    """Write `files` (relative path -> source) under `root` and return `root`."""
    for relative, source in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return root


def test_zoom_crosses_modules_but_skips_unused_symbols(tmp_path: Path) -> None:
    """Importing f from pkg.a pulls in f's deps, not the rest of pkg.a."""
    graph = build_graph(write_package(tmp_path, PACKAGE))
    assert collect_dependencies(graph, "pkg.b:g") == {"pkg.a:f", "pkg.a:LIMIT"}


def test_reexport_through_init_resolves_to_the_definition(tmp_path: Path) -> None:
    """`from pkg import f` follows pkg/__init__.py's import to pkg.a:f."""
    graph = build_graph(write_package(tmp_path, PACKAGE))
    assert collect_dependencies(graph, "pkg.b:h") == {"pkg.a:f", "pkg.a:LIMIT"}


def test_importing_a_missing_name_is_unresolved(tmp_path: Path) -> None:
    """A name imported from a project module that doesn't define it is unresolved."""
    graph = build_graph(write_package(tmp_path, PACKAGE))
    assert collect_dependencies(graph, "pkg.b:bad") == {"pkg.a:missing"}
    assert classify(graph, "pkg.a:missing") == "unresolved"


def test_root_that_is_itself_a_package_keeps_the_package_name(tmp_path: Path) -> None:
    """Pointing at the package directory gives the same module names as its parent."""
    graph = build_graph(write_package(tmp_path, PACKAGE) / "pkg")
    assert "pkg.a" in modules_of(graph)


def test_files_with_syntax_errors_are_skipped_with_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One broken file doesn't stop the analysis of the rest."""
    files = PACKAGE | {"pkg/broken.py": "def (:\n"}
    graph = build_graph(write_package(tmp_path, files))
    assert "pkg.a" in modules_of(graph)
    assert "broken.py" in capsys.readouterr().err
