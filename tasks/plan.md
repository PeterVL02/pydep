# Implementation Plan: pydep — per-symbol dependency tracing

## Overview
A CLI that statically analyses a local Python project and lists, for every module and for any
single function, class or constant, everything it transitively depends on inside the project.
Tracing stops at leaves: symbols with no further deps, or external packages (stdlib / PyPI).
Module view = union of all its symbols + module-level code. Symbol view = only that symbol's
closure, so module deps it doesn't touch are excluded.

```
pydep src/                      # every module, its transitive deps
pydep src/ pkg.mod              # one module
pydep src/ pkg.mod:Thing        # zoom: one function / class / constant
```

## Architecture Decisions
- **Stdlib only, zero runtime deps.** `ast` for structure, `symtable` for scope-correct
  "which global names does this body read" (handles params/locals shadowing globals, nested
  functions, comprehensions). `sys.stdlib_module_names` classifies stdlib vs third-party.
  `builtins` filters `len`, `print`, etc.
- **Graph model: `dict[str, set[str]]`.** Node ids are `pkg.mod:name` (symbol), `pkg.mod`
  (module, incl. pseudo-symbol `pkg.mod:<module>` for top-level statements that aren't
  defs/assignments, e.g. `if __name__ == "__main__":`), or an external module name (`numpy`,
  `os.path`). External nodes have no outgoing edges, which is what stops the tracing.
- **Imports are aliases, not leaves.** `from pkg.a import f` in module `b` creates node
  `pkg.b:f -> pkg.a:f`. Resolution follows aliases, so re-exports through `__init__.py` work
  for free; aliases are collapsed in output.
- **Symbol units:** top-level `def`, `class`, and module-level assignment targets (constants).
  A class is one unit: bases, decorators, class body and all methods. Header expressions
  (decorators, bases, defaults, annotations) are evaluated in module scope, so they're collected
  via `ast` on the def node; bodies come from the matching `symtable` child table.
- **Transitive closure = DFS with a visited set** (cycles are fine). Text output: one sorted
  flat list per target. The main section has project symbols and third-party packages. Stdlib
  goes in a separate trailing section, so it stays out of the main list.
- **The graph is the searchable structure, not a tree or linked list.** Deps share nodes and can
  be cyclic, so a real tree would copy every shared subtree. `--tree` is a rendering: DFS from the
  target that prints each node once and marks repeats `(see above)`. Searches run on the same
  graph: `--why X` (BFS shortest path target→X) and `--rdeps` (reverse graph: who depends on the
  target).
- **Type annotations and `if TYPE_CHECKING:` imports count as deps.** External packages are named
  by import name (`yaml`, not `PyYAML`).
- **Project root is given explicitly** (`src/` or repo root). Module names = dotted path
  relative to it. Anything importable from inside the root counts as local.
- Code lives in `src/pydep/logic.py` (analysis) and `src/pydep/__init__.py` (`main`, argparse),
  plus a single `tests/test_logic.py` that builds fixture packages in `tmp_path`.

## Dependency Graph (implementation order)
```
parse one file -> per-symbol direct deps (T1)
        └── transitive closure + zoom + module view (T2)
                └── multi-file index + absolute import resolution (T3)
                        └── relative imports, module attributes, star imports (T4)
                                ├── --tree rendering (T5)
                                ├── --why / --rdeps searches (T6)
                                └── real-world run + README (T7)
```

## Task List
Full task details are in `tasks/todo.md`.

### Phase 1: Single file, end to end
- [x] Task 0: Dev tooling (pytest + ruff as dev deps; approved)
- [ ] Task 1: Direct deps per symbol for one file, printed by the CLI
- [ ] Task 2: Transitive closure, `module:symbol` zoom, module-wide view

### Checkpoint: Single file
- [ ] `pydep file.py:func` shows only func's closure; tests and ruff are clean

### Phase 2: Whole project
- [ ] Task 3: Index a project root and resolve absolute imports across modules
- [ ] Task 4: Relative imports, `import x as m; m.f` refinement, star imports

### Checkpoint: Whole project
- [ ] Cross-module zoom correct on the fixture package; review output format with human

### Phase 3: Explore the graph
- [ ] Task 5: `--tree` view of a target's deps
- [ ] Task 6: `--why X` path search and `--rdeps` reverse lookup

### Checkpoint: Explore
- [ ] Tree and searches agree with the flat list on the fixture package

### Phase 4: Hardening
- [ ] Task 7: Run against a real project, fix crashes, document usage and limits

### Checkpoint: Complete
- [ ] All acceptance criteria met, ready for review

## Risks and Mitigations
| Risk | Impact | Mitigation |
|------|--------|------------|
| Untyped attribute calls (`def f(x): x.bar()`) can't be resolved without type inference | Med | Out of scope. Covered when `x` is annotated or the class is used in the body (class = one unit). Document it. |
| Dynamic imports / `getattr` / `globals()` | Low | Out of scope; list unresolved global names under an *unresolved* group instead of dropping them silently. |
| `symtable` behaviour under 3.14+ lazy annotations (annotation scopes) | Med | Do it in T1 with a test that checks annotation-only deps; fall back to `ast` for annotations if symtable hides them. |
| Same name bound in several places (`try: import x / except: x = None`) | Low | Union of all bindings. |
| Syntax errors in analysed files | Low | Skip the file and print a warning, don't abort the run. |

## Decisions (answered 2026-10-26)
1. Dev deps pytest + ruff: approved.
2. A class is one unit. No `Class.method` zoom.
3. Stdlib is listed in a separate section, kept out of the main output.
4. Tree and search: added as T5/T6, built on the graph (see Architecture Decisions).
5. Annotations and `TYPE_CHECKING` imports count as deps.
6. External packages are named by import name.

7. Main output = project symbols + third-party packages; stdlib in a separate trailing section.
8. No searches beyond `--why` and `--rdeps` for now.
9. Task 1 replaces the user's line-based draft (`find_resources`) in `logic.py`.

## Open Questions
None.
