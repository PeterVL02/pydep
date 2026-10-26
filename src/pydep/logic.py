"""Static analysis that maps every module-level symbol to what it depends on.

Node ids in the graph:
- ``pkg.mod:name`` is a symbol (function, class, constant or import) of a project module.
- ``pkg.mod:<module>`` is the module's top-level code that isn't a definition.
- ``pkg.mod`` is a project module; it depends on everything the module binds.
- Anything else without a colon is an external module, e.g. ``numpy`` or ``os.path``.
A ``module:name`` id that is not a graph key is a name that couldn't be resolved.
"""

import ast
import builtins
import symtable
import sys
from collections import Counter, deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

MODULE_CODE = "<module>"
BUILTIN_NAMES = set(dir(builtins)) | {
    "__file__",
    "__builtins__",
    "__path__",
    "__cached__",
    "__annotations__",
    "__annotate__",
    "__conditional_annotations__",
}

type Category = Literal["project", "third-party", "stdlib", "unresolved"]


@dataclass
class DependencyGraph:
    edges: dict[str, set[str]] = field(default_factory=dict)
    aliases: set[str] = field(default_factory=set)  # nodes bound only by imports
    star_imports: dict[str, set[str]] = field(default_factory=dict)  # module -> sources
    exports: dict[str, set[str]] = field(default_factory=dict)  # literal __all__


def build_graph(path: Path) -> DependencyGraph:
    """Analyse a Python file, or every `.py` file under a directory.

    Module names are dotted paths relative to the directory; if the directory is a
    package itself, its parent is used so the package name is kept. Hidden
    directories are skipped, and so are files that can't be parsed (with a warning
    on stderr).
    """
    if path.is_file():
        files = {path.stem: path}
    else:
        base = path.parent if (path / "__init__.py").exists() else path
        files = {
            module_name(file, base): file
            for file in path.rglob("*.py")
            if not any(part.startswith(".") for part in file.relative_to(path).parts)
        }
    # Every package prefix counts as a module, so namespace packages resolve too.
    local_modules = {
        ".".join(parts[:i])
        for parts in (name.split(".") for name in files)
        for i in range(1, len(parts) + 1)
    }
    graph = DependencyGraph(edges={name: set() for name in local_modules})
    for module, file in sorted(files.items()):
        try:
            source = file.read_text(encoding="utf-8")
            is_package = file.name == "__init__.py"
            analyze_module(source, module, str(file), graph, local_modules, is_package)
        except (SyntaxError, UnicodeDecodeError) as err:
            print(f"warning: skipping {file}: {err}", file=sys.stderr)
    resolve_star_imports(graph)
    return graph


def module_name(file: Path, base: Path) -> str:
    """Return the dotted module name of `file` relative to `base`."""
    parts = file.relative_to(base).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def analyze_module(
    source: str,
    module: str,
    filename: str,
    graph: DependencyGraph,
    local_modules: set[str],
    is_package: bool,
) -> None:
    """Add the symbols of one module and their direct dependencies to `graph`."""
    tree = ast.parse(source, filename)
    statements = list(flatten_statements(tree.body))
    package = module if is_package else module.rpartition(".")[0]
    imports: dict[str, set[str]] = {}
    definitions: set[str] = set()
    for stmt in statements:
        match stmt:
            case ast.Import() | ast.ImportFrom():
                for name, target in import_bindings(stmt, package, local_modules):
                    imports.setdefault(name, set()).add(target)
                continue
            case ast.Assign(
                targets=[ast.Name(id="__all__")],
                value=ast.List(elts=elts) | ast.Tuple(elts=elts),
            ):
                graph.exports[module] = {
                    elt.value
                    for elt in elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                }
        definitions.update(statement_owners(stmt))
    definitions.discard(MODULE_CODE)
    if stars := imports.pop("*", set()):
        graph.star_imports[module] = stars
    # Names bound to exactly one project module, so `name.attr` can be resolved.
    module_aliases = {
        name: min(targets)
        for name, targets in imports.items()
        if len(targets) == 1 and targets <= local_modules and name not in definitions
    }

    reads: dict[str, set[str]] = {}
    attribute_targets: dict[str, set[str]] = {}
    bare_aliases: dict[str, set[str]] = {}
    spans: list[tuple[int, int, list[str]]] = []
    for stmt in statements:
        if isinstance(stmt, ast.Import | ast.ImportFrom):
            continue
        owners = statement_owners(stmt)
        targets, bare = module_attribute_reads(stmt, module_aliases, local_modules)
        for owner in owners:
            reads.setdefault(owner, set()).update(module_reads(stmt))
            attribute_targets.setdefault(owner, set()).update(targets)
            bare_aliases.setdefault(owner, set()).update(bare)
        start = min(
            [stmt.lineno, *(d.lineno for d in getattr(stmt, "decorator_list", []))]
        )
        spans.append((start, stmt.end_lineno or stmt.lineno, owners))

    def_lines = {
        s.lineno
        for s in statements
        if isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    for table in nested_tables(symtable.symtable(source, filename, "exec"), def_lines):
        line = table.get_lineno()
        owners = next((o for a, b, o in spans if a <= line <= b), [MODULE_CODE])
        referenced = {
            s.get_name()
            for s in table.get_symbols()
            if s.is_global() and s.is_referenced()
        }
        for owner in owners:
            reads.setdefault(owner, set()).update(referenced)

    if not reads.get(MODULE_CODE):  # e.g. just a docstring
        reads.pop(MODULE_CODE, None)
    for owner, names in reads.items():
        # A module alias only used as `alias.attr` is replaced by those attributes.
        names -= module_aliases.keys() - bare_aliases.get(owner, set())
        graph.edges.setdefault(f"{module}:{owner}", set()).update(
            f"{module}:{name}"
            for name in names
            if name in definitions or name in imports or name not in BUILTIN_NAMES
        )
        graph.edges[f"{module}:{owner}"].update(attribute_targets.get(owner, set()))
    for name, targets in imports.items():
        graph.edges.setdefault(f"{module}:{name}", set()).update(targets)
        if name not in definitions:
            graph.aliases.add(f"{module}:{name}")
    graph.edges[module] = {f"{module}:{name}" for name in [*reads, *imports]}


def flatten_statements(body: list[ast.stmt]) -> Iterator[ast.stmt | ast.expr]:
    """Yield top-level statements, looking inside `if` and `try` blocks.

    Conditions and exception types are yielded as expressions; they belong to the
    module's top-level code.
    """
    # ponytail: for/while/with/match blocks are kept whole as top-level code, so defs
    # inside them aren't symbols. Flatten them here too if that ever matters.
    for stmt in body:
        match stmt:
            case ast.If():
                yield stmt.test
                yield from flatten_statements(stmt.body)
                yield from flatten_statements(stmt.orelse)
            case ast.Try() | ast.TryStar():
                yield from flatten_statements(stmt.body)
                for handler in stmt.handlers:
                    if handler.type:
                        yield handler.type
                    yield from flatten_statements(handler.body)
                yield from flatten_statements(stmt.orelse)
                yield from flatten_statements(stmt.finalbody)
            case _:
                yield stmt


def statement_owners(stmt: ast.stmt | ast.expr) -> list[str]:
    """Return the module-level names a statement defines, or `<module>` if none."""
    match stmt:
        case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
            return [stmt.name]
        case ast.TypeAlias(name=ast.Name(id=name)):
            return [name]
        case ast.Assign() | ast.AnnAssign() | ast.AugAssign():
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            names = [
                node.id
                for target in targets
                for node in unpack_target(target)
                if isinstance(node, ast.Name)
            ]
            return names or [MODULE_CODE]
    return [MODULE_CODE]


def unpack_target(target: ast.expr) -> Iterator[ast.expr]:
    """Yield the individual targets of a possibly nested tuple/list assignment."""
    match target:
        case ast.Tuple(elts=elts) | ast.List(elts=elts):
            for elt in elts:
                yield from unpack_target(elt)
        case ast.Starred(value=value):
            yield from unpack_target(value)
        case _:
            yield target


def import_bindings(
    stmt: ast.Import | ast.ImportFrom, package: str, local_modules: set[str]
) -> Iterator[tuple[str, str]]:
    """Yield `(bound name, target node id)` for each name an import binds.

    `from pkg import x` targets the module `pkg.x` if that is a project module,
    else the symbol `pkg:x` if `pkg` is a project module, else the external `pkg`.
    A star import yields `("*", source module)`.
    """
    for alias in stmt.names:
        if isinstance(stmt, ast.Import):
            if alias.asname:
                yield alias.asname, alias.name
            else:
                yield alias.name.split(".")[0], alias.name.split(".")[0]
            continue
        source = absolute_module(package, stmt.level, stmt.module)
        bound = alias.asname or alias.name
        if alias.name == "*":
            yield "*", source
        elif f"{source}.{alias.name}" in local_modules:
            yield bound, f"{source}.{alias.name}"
        elif source in local_modules:
            yield bound, f"{source}:{alias.name}"
        else:
            yield bound, source


def absolute_module(package: str, level: int, name: str | None) -> str:
    """Turn the source of a possibly relative `from` import into a module name."""
    if level == 0:
        return name or ""
    parts = package.split(".") if package else []
    base = parts[: max(0, len(parts) - (level - 1))]
    return ".".join([*base, *([name] if name else [])])


def module_attribute_reads(
    stmt: ast.stmt | ast.expr, module_aliases: dict[str, str], local_modules: set[str]
) -> tuple[set[str], set[str]]:
    """Resolve `alias.attr.attr` chains on imported project modules in `stmt`.

    Returns the node ids the chains point at, and the aliases `stmt` also uses
    bare (not as the start of a chain), which then depend on the whole module.
    """
    # ponytail: walks nested function bodies without scope info, so a local that
    # shadows a module alias is treated as the module. Rare in practice.
    targets: set[str] = set()
    chained: Counter[str] = Counter()
    inner: set[int] = set()
    for node in ast.walk(stmt):  # breadth-first, so outer chains come first
        if not isinstance(node, ast.Attribute) or id(node) in inner:
            continue
        attrs: list[str] = []
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            inner.add(id(current))
            attrs.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name) and current.id in module_aliases:
            chained[current.id] += 1
            resolved = module_aliases[current.id]
            for attr in reversed(attrs):
                if f"{resolved}.{attr}" not in local_modules:
                    resolved = f"{resolved}:{attr}"
                    break
                resolved = f"{resolved}.{attr}"
            targets.add(resolved)
    loads = Counter(
        node.id
        for node in ast.walk(stmt)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id in module_aliases
    )
    return targets, {name for name, count in loads.items() if count > chained[name]}


def resolve_star_imports(graph: DependencyGraph) -> None:
    """Point unresolved names in modules with star imports at their source."""
    unresolved = {
        dep
        for deps in graph.edges.values()
        for dep in deps
        if ":" in dep and dep not in graph.edges
    }
    for node in unresolved:
        module, _, name = node.partition(":")
        if targets := star_import_targets(graph, module, name, set()):
            graph.edges[node] = targets
            graph.aliases.add(node)


def star_import_targets(
    graph: DependencyGraph, module: str, name: str, seen: set[str]
) -> set[str]:
    """Find where `name` comes from through `module`'s star imports.

    Returns the defining project symbol, or else every external module that was
    star-imported (any of them may provide it), or an empty set.
    """
    seen.add(module)
    external: set[str] = set()
    for source in sorted(graph.star_imports.get(module, set())):
        if source not in graph.edges:
            external.add(source)
            continue
        exports = graph.exports.get(source)
        if name not in exports if exports is not None else name.startswith("_"):
            continue
        if f"{source}:{name}" in graph.edges:
            return {f"{source}:{name}"}
        if source not in seen and (
            found := star_import_targets(graph, source, name, seen)
        ):
            return found
    return external


def module_reads(node: ast.AST) -> set[str]:
    """Return names that `node` reads in module scope, skipping nested scopes.

    Bodies of functions, classes and lambdas are left to `symtable`.
    """
    match node:
        case ast.Name(ctx=ast.Load()):
            return {node.id}
        case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.Lambda():
            # Annotations live in their own annotation scope, so symtable has them.
            args = node.args
            parts: list[ast.AST] = [
                *getattr(node, "decorator_list", []),
                *args.defaults,
                *(d for d in args.kw_defaults if d),
            ]
        case ast.ClassDef():
            parts = [*node.decorator_list, *node.bases, *node.keywords]
        case ast.TypeAlias():
            return set()
        case ast.ListComp() | ast.SetComp() | ast.GeneratorExp() | ast.DictComp():
            return comprehension_reads(node)
        case _:
            parts = list(ast.iter_child_nodes(node))
    reads = set().union(*map(module_reads, parts))
    return reads - {param.name for param in getattr(node, "type_params", [])}


def comprehension_reads(
    node: ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp,
) -> set[str]:
    """Return names a comprehension reads, minus its own loop variables."""
    first, *rest = node.generators
    elts = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
    inner: list[ast.AST] = [*elts, *first.ifs]
    for gen in rest:
        inner += [gen.iter, *gen.ifs]
    bound = {
        name.id
        for gen in node.generators
        for name in ast.walk(gen.target)
        if isinstance(name, ast.Name)
    }
    return module_reads(first.iter) | (set().union(*map(module_reads, inner)) - bound)


def nested_tables(
    module_table: symtable.SymbolTable, def_lines: set[int]
) -> Iterator[symtable.SymbolTable]:
    """Yield every scope nested in the module, except the module's own annotations.

    Module-level variable annotations share one table, so they are read with `ast`
    instead. A function's annotation table sits on its `def` line.
    """
    for child in module_table.get_children():
        is_module_annotations = (
            child.get_type() == symtable.SymbolTableType.ANNOTATION
            and child.get_lineno() not in def_lines
        )
        if not is_module_annotations:
            yield from walk_tables(child)


def walk_tables(table: symtable.SymbolTable) -> Iterator[symtable.SymbolTable]:
    """Yield `table` and all tables nested inside it."""
    yield table
    for child in table.get_children():
        yield from walk_tables(child)


def resolve(
    graph: DependencyGraph, node: str, seen: frozenset[str] = frozenset()
) -> set[str]:
    """Follow import aliases from `node` to the nodes they point at.

    Aliases that only import each other in a cycle resolve to nothing.
    """
    if node not in graph.aliases:
        return {node}
    if node in seen:
        return set()
    seen |= {node}
    return set().union(*(resolve(graph, t, seen) for t in graph.edges[node]))


def direct_dependencies(graph: DependencyGraph, node: str) -> set[str]:
    """Return the nodes `node` uses directly, with import aliases resolved."""
    return set().union(*(resolve(graph, dep) for dep in graph.edges.get(node, ())))


def collect_dependencies(graph: DependencyGraph, target: str) -> set[str]:
    """Return everything `target` transitively depends on, aliases resolved.

    For a module target, the module's own symbols are left out.
    """
    seen: set[str] = set()
    stack = [target]
    while stack:
        for dep in direct_dependencies(graph, stack.pop()) - seen:
            seen.add(dep)
            stack.append(dep)
    seen.discard(target)
    if ":" in target:
        return seen
    return {node for node in seen if not node.startswith(f"{target}:")}


def find_path(graph: DependencyGraph, start: str, goal: str) -> list[str] | None:
    """Return the shortest dependency chain from `start` to `goal`, or None."""
    previous: dict[str, str] = {}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == goal:
            path = [node]
            while path[-1] != start:
                path.append(previous[path[-1]])
            return path[::-1]
        for dep in sorted(direct_dependencies(graph, node)):
            if dep not in previous and dep != start:
                previous[dep] = node
                queue.append(dep)
    return None


def reverse_dependencies(graph: DependencyGraph, target: str) -> set[str]:
    """Return every project symbol that transitively depends on `target`.

    For a module target, users of any of its symbols count, except the module's
    own symbols.
    """
    users: dict[str, set[str]] = {}
    for node in graph.edges.keys() - graph.aliases:
        for dep in direct_dependencies(graph, node):
            users.setdefault(dep, set()).add(node)
    is_module = target in graph.edges and ":" not in target
    own = set(symbols_of(graph, target)) if is_module else set()
    seen: set[str] = set()
    stack = [target, *own]
    while stack:
        for user in users.get(stack.pop(), set()) - seen:
            seen.add(user)
            stack.append(user)
    # Module nodes are passed through but not reported; every module uses its own.
    return {node for node in seen - own if ":" in node and node != target}


def modules_of(graph: DependencyGraph) -> list[str]:
    """Return the sorted names of all analysed project modules."""
    return sorted(node for node in graph.edges if ":" not in node)


def classify(graph: DependencyGraph, node: str) -> Category:
    """Return which group a dependency node belongs to."""
    if node in graph.edges:
        return "project"
    if ":" in node:
        return "unresolved"
    if node.split(".")[0] in sys.stdlib_module_names:
        return "stdlib"
    return "third-party"


def format_dependencies(graph: DependencyGraph, title: str, deps: set[str]) -> str:
    """Render one target's dependencies: main list first, then unresolved and stdlib."""
    groups: dict[Category, list[str]] = {
        "project": [],
        "third-party": [],
        "unresolved": [],
        "stdlib": [],
    }
    for dep in sorted(deps):
        groups[classify(graph, dep)].append(dep)
    lines = [title]
    lines += [f"    {dep}" for dep in groups["project"] + groups["third-party"]]
    for label in ("unresolved", "stdlib"):
        if groups[label]:
            lines.append(f"    {label}: {', '.join(groups[label])}")
    return "\n".join(lines) + "\n"


def render_tree(graph: DependencyGraph, target: str) -> str:
    """Render `target`'s dependencies as an indented tree.

    Each node is expanded once; later occurrences of a node with dependencies are
    marked `(see above)`. Stdlib leaves are listed once at the end.
    """
    lines: list[str] = []
    expanded: set[str] = set()
    stdlib: set[str] = set()
    stack = [(target, 0)]
    while stack:
        node, depth = stack.pop()
        indent = "    " * depth
        if node in expanded:
            seen_marker = " (see above)" if graph.edges.get(node) else ""
            lines.append(f"{indent}{node}{seen_marker}")
            continue
        expanded.add(node)
        unresolved = classify(graph, node) == "unresolved"
        lines.append(f"{indent}{node}{' (unresolved)' if unresolved else ''}")
        for dep in sorted(direct_dependencies(graph, node) - {node}, reverse=True):
            if classify(graph, dep) == "stdlib":
                stdlib.add(dep)
            else:
                stack.append((dep, depth + 1))
    if stdlib:
        lines.append(f"    stdlib: {', '.join(sorted(stdlib))}")
    return "\n".join(lines) + "\n"


def symbols_of(graph: DependencyGraph, module: str) -> list[str]:
    """Return the sorted non-alias symbol ids defined in `module`."""
    return sorted(
        node
        for node in graph.edges[module]
        if node in graph.edges and node not in graph.aliases
    )
