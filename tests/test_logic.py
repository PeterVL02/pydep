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
    find_path,
    modules_of,
    render_tree,
    reverse_dependencies,
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


IMPORT_STYLES = {
    "pkg/__init__.py": "from .a import f\n",
    "pkg/a.py": ("LIMIT = 1\ndef f():\n    return LIMIT\ndef other():\n    return 2\n"),
    "pkg/sub/__init__.py": "",
    "pkg/sub/d.py": "def helper():\n    return 1\n",
    "pkg/sub/c.py": (
        "from ..a import f\n"
        "from . import d\n"
        "import pkg.a as m\n"
        "import pkg\n"
        "from pkg.a import *\n"
        "from os.path import *\n"
        "def uses_relative():\n    return f()\n"
        "def uses_sibling_module():\n    return d.helper()\n"
        "def uses_attribute():\n    return m.other()\n"
        "def uses_dotted():\n    return pkg.a.other()\n"
        "def uses_module_value():\n    return m\n"
        "def uses_star():\n    return other()\n"
        "def uses_external_star():\n    return join('a')\n"
    ),
}


def test_relative_import_from_init_resolves(tmp_path: Path) -> None:
    """`from .a import f` inside pkg/__init__.py points at pkg.a."""
    graph = build_graph(write_package(tmp_path, IMPORT_STYLES))
    assert collect_dependencies(graph, "pkg") == {"pkg.a:f", "pkg.a:LIMIT"}


def test_relative_import_two_levels_up_resolves(tmp_path: Path) -> None:
    """`from ..a import f` inside pkg/sub/c.py points at pkg.a."""
    graph = build_graph(write_package(tmp_path, IMPORT_STYLES))
    deps = collect_dependencies(graph, "pkg.sub.c:uses_relative")
    assert deps == {"pkg.a:f", "pkg.a:LIMIT"}


def test_attribute_on_relatively_imported_module_resolves(tmp_path: Path) -> None:
    """`from . import d; d.helper()` depends on pkg.sub.d:helper only."""
    graph = build_graph(write_package(tmp_path, IMPORT_STYLES))
    deps = collect_dependencies(graph, "pkg.sub.c:uses_sibling_module")
    assert deps == {"pkg.sub.d:helper"}


def test_attribute_on_module_alias_depends_on_that_symbol_only(tmp_path: Path) -> None:
    """`import pkg.a as m; m.other()` depends on pkg.a:other, not all of pkg.a."""
    graph = build_graph(write_package(tmp_path, IMPORT_STYLES))
    assert collect_dependencies(graph, "pkg.sub.c:uses_attribute") == {"pkg.a:other"}


def test_dotted_attribute_chain_resolves_through_packages(tmp_path: Path) -> None:
    """`import pkg; pkg.a.other()` depends on pkg.a:other."""
    graph = build_graph(write_package(tmp_path, IMPORT_STYLES))
    assert collect_dependencies(graph, "pkg.sub.c:uses_dotted") == {"pkg.a:other"}


def test_module_used_as_a_value_depends_on_the_whole_module(tmp_path: Path) -> None:
    """Passing a module around may use any of it."""
    graph = build_graph(write_package(tmp_path, IMPORT_STYLES))
    deps = collect_dependencies(graph, "pkg.sub.c:uses_module_value")
    assert deps == {"pkg.a", "pkg.a:f", "pkg.a:LIMIT", "pkg.a:other"}


def test_star_import_from_project_module_resolves_names(tmp_path: Path) -> None:
    """A name from `from pkg.a import *` resolves to its definition in pkg.a."""
    graph = build_graph(write_package(tmp_path, IMPORT_STYLES))
    assert collect_dependencies(graph, "pkg.sub.c:uses_star") == {"pkg.a:other"}


def test_star_import_from_external_module_falls_back_to_that_module(
    tmp_path: Path,
) -> None:
    """A name that could come from `from os.path import *` depends on os.path."""
    graph = build_graph(write_package(tmp_path, IMPORT_STYLES))
    assert collect_dependencies(graph, "pkg.sub.c:uses_external_star") == {"os.path"}


def test_star_import_honours_literal_dunder_all(tmp_path: Path) -> None:
    """Names left out of `__all__` are not star-imported."""
    files = {
        "pkg/__init__.py": "",
        "pkg/e.py": "__all__ = ['pub']\ndef pub(): ...\ndef hidden(): ...\n",
        "pkg/f.py": (
            "from pkg.e import *\n"
            "def g():\n    return pub()\n"
            "def h():\n    return hidden()\n"
        ),
    }
    graph = build_graph(write_package(tmp_path, files))
    assert collect_dependencies(graph, "pkg.f:g") == {"pkg.e:pub"}
    assert collect_dependencies(graph, "pkg.f:h") == {"pkg.f:hidden"}


def test_tree_expands_a_shared_dependency_once(tmp_path: Path) -> None:
    """In a diamond, the second path to d points back instead of repeating d."""
    graph = graph_of(
        tmp_path,
        "import os\n"
        "def d():\n    return os.sep\n"
        "def b():\n    return d()\n"
        "def c():\n    return d()\n"
        "def a():\n    return b(), c()\n",
    )
    assert render_tree(graph, "m:a") == (
        "m:a\n    m:b\n        m:d\n    m:c\n        m:d (see above)\n    stdlib: os\n"
    )


def test_tree_terminates_on_cycles(tmp_path: Path) -> None:
    """A cycle shows the start again as a back-reference and stops."""
    graph = graph_of(tmp_path, "def a():\n    return b()\ndef b():\n    return a()\n")
    assert render_tree(graph, "m:a") == "m:a\n    m:b\n        m:a (see above)\n"


def test_tree_contains_the_same_nodes_as_the_flat_list(tmp_path: Path) -> None:
    """The tree is just another view of the same closure."""
    graph = build_graph(write_package(tmp_path, IMPORT_STYLES))
    target = "pkg.sub.c:uses_module_value"
    tree_nodes = {
        line.strip().removesuffix(" (see above)")
        for line in render_tree(graph, target).splitlines()[1:]
    }
    assert tree_nodes == collect_dependencies(graph, target)


def test_cli_tree_flag_prints_the_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--tree` switches the output to the tree view."""
    main([str(write_module(tmp_path, CHAIN_SOURCE)), "m:b", "--tree"])
    assert capsys.readouterr().out == "m:b\n    m:c\n        numpy\n\n"


TWO_PATHS = (
    "def d(): ...\n"
    "def c():\n    return d()\n"
    "def b():\n    return c()\n"
    "def e():\n    return d()\n"
    "def a():\n    return b(), e()\n"
)


def test_why_returns_the_shortest_path(tmp_path: Path) -> None:
    """With a->b->c->d and a->e->d, the path via e is reported."""
    graph = graph_of(tmp_path, TWO_PATHS)
    assert find_path(graph, "m:a", "m:d") == ["m:a", "m:e", "m:d"]


def test_why_returns_none_when_there_is_no_path(tmp_path: Path) -> None:
    """Dependencies only point one way."""
    graph = graph_of(tmp_path, TWO_PATHS)
    assert find_path(graph, "m:d", "m:a") is None


def test_rdeps_includes_users_that_go_through_reexports(tmp_path: Path) -> None:
    """Users of pkg.a:f include those importing it via pkg/__init__.py."""
    graph = build_graph(write_package(tmp_path, PACKAGE))
    assert reverse_dependencies(graph, "pkg.a:f") == {"pkg.b:g", "pkg.b:h"}


def test_rdeps_of_an_external_package_lists_its_users(tmp_path: Path) -> None:
    """`--rdeps numpy` lists every project symbol that ends up using numpy."""
    graph = build_graph(write_package(tmp_path, PACKAGE))
    assert reverse_dependencies(graph, "numpy") == {"pkg.a:other"}


def test_rdeps_of_a_module_lists_users_of_any_of_its_symbols(tmp_path: Path) -> None:
    """Users of a module are users of anything in it, outside the module."""
    graph = build_graph(write_package(tmp_path, PACKAGE))
    assert reverse_dependencies(graph, "pkg.a") == {"pkg.b:g", "pkg.b:h"}


def test_cli_why_prints_the_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--why X` prints the chain from the target to X."""
    main([str(write_module(tmp_path, TWO_PATHS)), "m:a", "--why", "m:d"])
    assert capsys.readouterr().out == "m:a -> m:e -> m:d\n"


def test_cli_rdeps_accepts_an_external_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--rdeps` works for external packages, which aren't graph keys."""
    main([str(write_module(tmp_path, CHAIN_SOURCE)), "numpy", "--rdeps"])
    assert capsys.readouterr().out == (
        "numpy is used by\n    m:<module>\n    m:a\n    m:b\n    m:c\n\n"
    )


def test_module_dunder_attribute_is_not_a_symbol(tmp_path: Path) -> None:
    """`pkg.__file__` doesn't depend on a symbol called `__file__`."""
    files = {"pkg/__init__.py": "", "pkg/m.py": "import pkg\nHERE = pkg.__file__\n"}
    graph = build_graph(write_package(tmp_path, files))
    assert collect_dependencies(graph, "pkg.m:HERE") == set()


def test_name_bound_by_top_level_loop_comes_from_module_code(tmp_path: Path) -> None:
    """A loop variable at module level is a global set by the module's own code."""
    graph = graph_of(tmp_path, "for c in 'ab':\n    pass\ndef f():\n    return c\n")
    assert collect_dependencies(graph, "m:f") == {"m:<module>"}


def test_global_assigned_inside_a_function_comes_from_that_function(
    tmp_path: Path,
) -> None:
    """`global CACHE; CACHE = {}` in setup() makes setup the source of CACHE."""
    graph = graph_of(
        tmp_path,
        "def setup():\n    global CACHE\n    CACHE = {}\n"
        "def use():\n    return CACHE\n",
    )
    assert collect_dependencies(graph, "m:use") == {"m:setup"}


def test_annotations_count_under_future_annotations(tmp_path: Path) -> None:
    """`from __future__ import annotations` doesn't hide annotation dependencies."""
    graph = graph_of(
        tmp_path,
        "from __future__ import annotations\n"
        "class Hint: ...\n"
        "class C:\n    def m(self) -> Hint: ...\n",
    )
    assert collect_dependencies(graph, "m:C") == {"m:Hint"}


def test_string_forward_reference_is_a_dependency(tmp_path: Path) -> None:
    """`-> "Hint"` depends on Hint."""
    graph = graph_of(tmp_path, "class Hint: ...\ndef f() -> 'Hint': ...\n")
    assert collect_dependencies(graph, "m:f") == {"m:Hint"}


def test_literal_strings_are_not_forward_references(tmp_path: Path) -> None:
    """`Literal["fast"]` doesn't depend on a name called fast."""
    graph = graph_of(
        tmp_path,
        "from typing import Literal\ndef f(mode: Literal['fast']) -> None: ...\n",
    )
    assert collect_dependencies(graph, "m:f") == {"typing"}


def test_local_variable_annotation_is_a_dependency(tmp_path: Path) -> None:
    """An annotation on a local variable counts, though Python never evaluates it."""
    graph = graph_of(
        tmp_path, "class Hint: ...\ndef f():\n    y: Hint = 1\n    return y\n"
    )
    assert collect_dependencies(graph, "m:f") == {"m:Hint"}


def test_annotation_naming_a_nested_class_is_not_a_global(tmp_path: Path) -> None:
    """Under future annotations, a nested class used in a method hint is local."""
    graph = graph_of(
        tmp_path,
        "from __future__ import annotations\n"
        "class Config:\n"
        "    class Params: ...\n"
        "    def get(self) -> Params: ...\n",
    )
    assert collect_dependencies(graph, "m:Config") == set()


def test_evaluated_annotation_naming_a_nested_class_is_not_a_global(
    tmp_path: Path,
) -> None:
    """Without future annotations, a class's annotation scopes see its names too."""
    graph = graph_of(
        tmp_path,
        "class Config:\n    class Params: ...\n    def get(self) -> Params: ...\n",
    )
    assert collect_dependencies(graph, "m:Config") == set()


def test_annotation_naming_a_function_local_import_is_not_a_global(
    tmp_path: Path,
) -> None:
    """A locally imported type in a local hint isn't a global; the import is a dep."""
    graph = graph_of(
        tmp_path,
        "def f():\n    from os import PathLike\n    p: PathLike = None\n    return p\n",
    )
    assert collect_dependencies(graph, "m:f") == {"os"}


def test_import_inside_a_function_is_a_dependency(tmp_path: Path) -> None:
    """Lazy imports inside a function count as that function's dependencies."""
    files = {
        "pkg/__init__.py": "",
        "pkg/a.py": "def helper():\n    return 1\n",
        "pkg/b.py": "def f():\n    from pkg.a import helper\n    return helper()\n",
    }
    graph = build_graph(write_package(tmp_path, files))
    assert collect_dependencies(graph, "pkg.b:f") == {"pkg.a:helper"}


def test_package_init_import_from_submodule_binds_the_submodule_name(
    tmp_path: Path,
) -> None:
    """After `from .a import *` in pkg/__init__.py, the name `a` is pkg.a."""
    files = {
        "pkg/__init__.py": "from .a import *\nALL = a.__all__\n",
        "pkg/a.py": "__all__ = ['f']\ndef f(): ...\n",
    }
    graph = build_graph(write_package(tmp_path, files))
    assert collect_dependencies(graph, "pkg:ALL") == {"pkg.a:__all__"}


def test_local_variable_shadowing_a_module_alias_is_not_an_attribute_chain(
    tmp_path: Path,
) -> None:
    """`errors = []; errors.append(1)` doesn't depend on pkg.errors:append."""
    files = {
        "pkg/__init__.py": "",
        "pkg/errors.py": "class Boom(Exception): ...\n",
        "pkg/b.py": (
            "from pkg import errors\n"
            "class C:\n"
            "    def run(self):\n"
            "        errors = []\n"
            "        errors.append(1)\n"
            "        return errors\n"
        ),
    }
    graph = build_graph(write_package(tmp_path, files))
    assert collect_dependencies(graph, "pkg.b:C") == set()


def test_function_in_package_init_that_imports_a_submodule_keeps_its_deps(
    tmp_path: Path,
) -> None:
    """Functions in pkg/__init__.py are analysed like any other module's."""
    files = {
        "pkg/__init__.py": "from .a import f\ndef g():\n    return f()\n",
        "pkg/a.py": "def f(): ...\n",
    }
    graph = build_graph(write_package(tmp_path, files))
    assert collect_dependencies(graph, "pkg:g") == {"pkg.a:f"}
