# pydep

List what each module, function, class and constant in a Python project depends on,
traced back through your own code until only external packages are left.

```
pydep PATH [TARGET] [--tree | --why NODE | --rdeps]
```

- `PATH` is a Python file or a project directory. Point it at the directory that holds
  your top-level package (e.g. `src/`), so module names match your imports.
- `TARGET` narrows the output to one module (`pkg.mod`) or one symbol (`pkg.mod:name`).
  Without it, every module and every symbol is listed.

## Examples

```console
$ pydep src/ pydep.logic:collect_dependencies
pydep.logic:collect_dependencies
    pydep.logic:DependencyGraph
    pydep.logic:direct_dependencies
    pydep.logic:resolve
    stdlib: dataclasses

$ pydep src/ pydep.logic:collect_dependencies --tree
pydep.logic:collect_dependencies
    pydep.logic:DependencyGraph
    pydep.logic:direct_dependencies
        pydep.logic:DependencyGraph (see above)
        pydep.logic:resolve
            pydep.logic:DependencyGraph (see above)
    stdlib: dataclasses

$ pydep src/ pydep:main --why pydep.logic:walk_tables
pydep:main -> pydep.logic:build_graph -> pydep.logic:analyze_module -> pydep.logic:nested_tables -> pydep.logic:walk_tables

$ pydep src/ pydep.logic:resolve --rdeps
pydep.logic:resolve is used by
    pydep.logic:collect_dependencies
    ...
```

## Reading the output

- `pkg.mod:name` is a symbol in your project, and `pkg.mod:<module>` is the module's
  top-level code that isn't a definition (e.g. `if __name__ == "__main__":`).
- Names without a colon are external packages (third-party, by import name).
- `stdlib:` lists standard-library modules separately, after the main list.
- `unresolved:` lists names that couldn't be traced to a definition (see limits below).
- A **symbol** target shows only what that symbol needs. A **module** target shows the
  union of all its symbols and top-level code, minus the module's own symbols.
- A class is one unit: its bases, decorators, class body and every method.
- Type annotations count as dependencies, including `if TYPE_CHECKING:` imports, string
  forward references and annotations under `from __future__ import annotations`.

## Modes

- `--tree` shows the same dependencies as a tree. Each node is expanded once; later
  occurrences are marked `(see above)`.
- `--why NODE` prints the shortest dependency chain from the target to `NODE`.
- `--rdeps` lists everything that depends on the target, which can be a symbol, a module
  or an external package (e.g. `pydep src/ numpy --rdeps`).

## Limits

pydep reads code without running it, so some things can't be traced:

- Attribute calls on untyped values: in `def f(x): x.run()`, pydep can't know what `x`
  is. It works if `x` has a type hint or its class is used in `f`.
- Dynamic lookups: `getattr`, `importlib.import_module`, `globals()`, and modules that
  create attributes in a module-level `__getattr__`. These show up as `unresolved:`.
- Names bound inside a class or function are treated as local to that whole unit when
  resolving annotations and `module.attr` chains, so a global with the same name used
  elsewhere in the same class can be missed.
- Definitions inside top-level `for`, `while`, `with` and `match` blocks aren't symbols
  of their own; they count as the module's top-level code.
- Hidden directories (starting with `.`) are skipped, and files that fail to parse are
  skipped with a warning.

## Development

```console
uv sync
uv run pytest
uv run ruff check && uv run ruff format
```
