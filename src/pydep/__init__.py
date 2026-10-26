"""Command-line entry point for pydep."""

import argparse
from pathlib import Path

from pydep.logic import (
    build_graph,
    collect_dependencies,
    find_path,
    format_dependencies,
    modules_of,
    render_tree,
    reverse_dependencies,
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tree", action="store_true", help="show dependencies as a tree")
    mode.add_argument(
        "--why", metavar="NODE", help="show the shortest path from target to NODE"
    )
    mode.add_argument(
        "--rdeps", action="store_true", help="show what depends on the target"
    )
    args = parser.parse_args(argv)

    graph = build_graph(args.path)
    if (args.why or args.rdeps) and not args.target:
        parser.error("--why and --rdeps need a target")
    known = graph.edges.keys() | {dep for deps in graph.edges.values() for dep in deps}
    if args.target and args.target not in (known if args.rdeps else graph.edges):
        parser.error(f"unknown target {args.target!r}")

    if args.why:
        path = find_path(graph, args.target, args.why)
        print(
            " -> ".join(path) if path else f"no path from {args.target} to {args.why}"
        )
        return
    if args.rdeps:
        users = reverse_dependencies(graph, args.target)
        print(format_dependencies(graph, f"{args.target} is used by", users))
        return
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
