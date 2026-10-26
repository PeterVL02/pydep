"""Command-line entry point for pydep."""

import argparse
from pathlib import Path

from pydep.logic import (
    build_graph,
    collect_dependencies,
    format_dependencies,
    modules_of,
    render_tree,
    symbols_of,
)


def main(argv: list[str] | None = None) -> None:
    """Print what modules, or a single function, class or constant, depend on."""
    parser = argparse.ArgumentParser(
        prog="pydep", description="List what each function, class and constant uses."
    )
    parser.add_argument(
        "path", type=Path, help="Python file or project directory (e.g. src/)"
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="module (pkg.mod) or symbol (pkg.mod:name) to zoom in on",
    )
    parser.add_argument(
        "--tree", action="store_true", help="show dependencies as a tree"
    )
    args = parser.parse_args(argv)

    graph = build_graph(args.path)
    if args.target and args.target not in graph.edges:
        parser.error(f"unknown target {args.target!r}")
    if args.target:
        targets = [args.target]
    else:
        targets = [t for m in modules_of(graph) for t in [m, *symbols_of(graph, m)]]
    for target in targets:
        if args.tree:
            print(render_tree(graph, target))
        else:
            deps = collect_dependencies(graph, target)
            print(format_dependencies(graph, target, deps))
