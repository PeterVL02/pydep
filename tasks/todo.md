# pydep — Task List

Verification commands used throughout:
`uv run pytest` · `uv run ruff check` · `uv run ruff format`

---

## Task 0: Dev tooling
**Description:** Add pytest and ruff as dev dependencies so the verification steps below can run.
Needs explicit user approval first (global rule: no new deps without asking).

**Acceptance criteria:**
- [x] `uv add --dev pytest ruff` done, `uv.lock` committed
- [x] `uv run pytest` and `uv run ruff check` both run (0 tests is fine)

**Verification:**
- [x] `uv sync` from clean works

**Dependencies:** None (approval)
**Files likely touched:** `pyproject.toml`, `uv.lock`
**Estimated scope:** XS

---

## Task 1: Direct deps per symbol for one file
**Description:** Parse a single `.py` file. For each top-level function, class (one unit) and
module-level constant, collect the module-level names it reads (via `symtable` for bodies, `ast` for
decorators/bases/defaults/annotations), drop builtins, and map each one to a same-file symbol
or an imported module (`stdlib` / `third-party`). Wire `main()` so `pydep file.py` prints each
symbol with its *direct* deps. Add a pseudo-symbol `<module>` for top-level statements that aren't
defs or assignments. This replaces the line-based draft `find_resources` in `logic.py` (approved).

**Acceptance criteria:**
- [x] A local variable or parameter that shadows a global is not reported as a dep
- [x] Decorator, base class, default value and annotation references are reported
- [x] `import os` / `import numpy as np` usages show up as `os` (stdlib) / `numpy` (third-party)

**Verification:**
- [x] `uv run pytest`: fixture file covering shadowing, decorators, annotations, imports
- [x] Manual: `uv run pydep src/pydep/logic.py` prints sensible output on itself

**Dependencies:** Task 0
**Files likely touched:** `src/pydep/logic.py`, `src/pydep/__init__.py`, `tests/test_logic.py`
**Estimated scope:** S

---

## Task 2: Transitive closure, zoom and module view
**Description:** DFS with a visited set over the direct-deps graph. `pydep file.py:name` prints
only that symbol's transitive closure. `pydep file.py` prints the module view (union of all
symbols + `<module>`). Output is a sorted flat list. The main section has project symbols and
third-party packages, and stdlib goes in a separate trailing section.

**Acceptance criteria:**
- [ ] `a -> b -> c` gives zoom(a) = {b, c}, and zoom(a) excludes unrelated symbol `d` and its imports
- [ ] Mutual recursion (`a <-> b`) terminates and reports each once
- [ ] Module view equals the union of all symbol closures plus `<module>` deps

**Verification:**
- [ ] `uv run pytest`: chain, cycle, exclusion and union tests
- [ ] Manual: zoom on one function of a small file, check by eye

**Dependencies:** Task 1
**Files likely touched:** `src/pydep/logic.py`, `src/pydep/__init__.py`, `tests/test_logic.py`
**Estimated scope:** S

---

## Checkpoint: Single file
- [ ] Tests pass, ruff clean
- [ ] Human looks at the output format before going multi-module

---

## Task 3: Project index and absolute imports
**Description:** `pydep ROOT [target]` indexes every `.py` under ROOT (module name = dotted
relative path, `__init__.py` = package). `from pkg.a import f` becomes an alias edge
`pkg.b:f -> pkg.a:f`, and resolution follows alias chains (re-exports via `__init__.py`).
Imports of modules not under ROOT are external leaves. Aliases are collapsed in output.
Files with syntax errors are skipped with a warning.

**Acceptance criteria:**
- [ ] Zoom on `pkg.b:g` follows into `pkg.a:f` and on into `f`'s own deps, but not `pkg.a`'s other symbols
- [ ] Re-export `from pkg.a import f` in `pkg/__init__.py` resolves when imported as `from pkg import f`
- [ ] Unresolvable global names are listed under *unresolved*, not dropped

**Verification:**
- [ ] `uv run pytest`: multi-module fixture package in `tmp_path`
- [ ] Manual: `uv run pydep src/` on this repo

**Dependencies:** Task 2
**Files likely touched:** `src/pydep/logic.py`, `src/pydep/__init__.py`, `tests/test_logic.py`
**Estimated scope:** M

---

## Task 4: Relative imports, module attributes, star imports
**Description:** Resolve `from . import x` / `from ..a import f` against the importing module's
package. Refine `import pkg.a as m; m.f()` (and `import pkg.a; pkg.a.f()`) to `pkg.a:f` via
`ast.Attribute` chains. A module used as a bare value depends on the whole module.
`from pkg.a import *` binds `pkg.a`'s public names (honouring `__all__` when it's a literal list).

**Acceptance criteria:**
- [ ] Relative imports at depth 1 and 2 resolve correctly, including from inside `__init__.py`
- [ ] `m.f()` gives a dep on `pkg.a:f` only, not all of `pkg.a`
- [ ] Star import from a local module resolves names; from an external module the names fall back to that module

**Verification:**
- [ ] `uv run pytest`: one test per bullet

**Dependencies:** Task 3
**Files likely touched:** `src/pydep/logic.py`, `tests/test_logic.py`
**Estimated scope:** S

---

## Checkpoint: Whole project
- [ ] Tests pass, ruff clean
- [ ] Cross-module zoom verified on the fixture; review with human

---

## Task 5: Tree view
**Description:** `pydep ROOT target --tree` prints the target's deps as an indented tree: DFS
over the graph, children sorted, each node expanded once and repeats shown as `name (see above)`.
Cycles are shown as repeats. Stdlib leaves follow the same main/separate rule as the flat list.

**Acceptance criteria:**
- [ ] A diamond (`a->b->d`, `a->c->d`) expands `d` once, then shows `d (see above)`
- [ ] A cycle terminates
- [ ] The set of nodes in the tree equals the flat-list closure

**Verification:**
- [ ] `uv run pytest`: diamond, cycle and equality tests

**Dependencies:** Task 4
**Files likely touched:** `src/pydep/logic.py`, `src/pydep/__init__.py`, `tests/test_logic.py`
**Estimated scope:** S

---

## Task 6: `--why` and `--rdeps` searches
**Description:** `--why X` prints the shortest dependency path from the target to X (BFS over the
graph), or says there is none. `--rdeps` inverts the graph and prints everything that transitively
depends on the target (e.g. "what breaks if I change `pkg.a:f`").

**Acceptance criteria:**
- [ ] `--why` returns the shortest path when more than one exists, and "no path" for an unrelated node
- [ ] `--rdeps pkg.a:f` lists all transitive users, including users that reach it through re-exports
- [ ] `--rdeps numpy` lists every project symbol that uses numpy

**Verification:**
- [ ] `uv run pytest`: one test per bullet

**Dependencies:** Task 4
**Files likely touched:** `src/pydep/logic.py`, `src/pydep/__init__.py`, `tests/test_logic.py`
**Estimated scope:** S

---

## Checkpoint: Explore
- [ ] Tests pass, ruff clean
- [ ] Tree, `--why` and `--rdeps` agree with the flat list on the fixture package

---

## Task 7: Real-world run and docs
**Description:** Run against a real project, e.g. an installed package's source from `.venv`
or another local repo. Fix crashes and wrong resolutions found there (add a regression test for
each). Write README usage and known limits (untyped attribute calls, dynamic imports).

**Acceptance criteria:**
- [ ] Runs to completion on a real project with no tracebacks
- [ ] Every bug found has a regression test
- [ ] README documents the CLI and its limits

**Verification:**
- [ ] `uv run pytest`, `uv run ruff check`, `uv run ruff format`
- [ ] Manual: spot-check 3 symbols' output against reading the source

**Dependencies:** Tasks 5, 6
**Files likely touched:** `src/pydep/logic.py`, `tests/test_logic.py`, `README.md`
**Estimated scope:** S

---

## Checkpoint: Complete
- [ ] All acceptance criteria met
- [ ] Ready for review
