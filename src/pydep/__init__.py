"""Command-line entry point for pydep."""

import argparse
from pathlib import Path

from pydep.logic import (
    build_graph,
    direct_dependencies,
    format_dependencies,
    symbols_of,
)


def main(argv: list[str] | None = None) -> None:
    """Print the dependencies of every symbol in the given Python file."""
    parser = argparse.ArgumentParser(
        prog="pydep", description="List what each function, class and constant uses."
    )
    parser.add_argument("path", type=Path, help="Python file to analyse")
    args = parser.parse_args(argv)

    graph = build_graph(args.path)
    module = args.path.stem
    for symbol in symbols_of(graph, module):
        print(format_dependencies(graph, symbol, direct_dependencies(graph, symbol)))
