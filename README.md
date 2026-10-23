# split-dex

`split-dex` splits the classes of an Android APK into grouped DEX files.

It reads every `classes*.dex` in the APK, sorts the classes into sets, and
splits each set into DEX files with a strategy. Both are chosen on the command
line. Every class ends up in exactly one output DEX file, two sets never share a
file, and maps record where each class went.

It is written in Python, on the Python binding of
[dexpp](https://github.com/burogurama/dexpp), which reads, analyses and writes
the DEX files.

## Usage

```sh
split-dex --apk app.apk --out out/
```

With no other option, every class is in one set, `all`, split by the
`package-affinity` strategy into files of about 1000 classes.

| Option | Meaning |
| ------ | ------- |
| `--apk FILE` | Input APK. All its `classes*.dex` entries are read. Required. |
| `--out DIR` | Output directory. Created if it does not exist. Required. |
| `--set NAME=STRATEGY[,KEY=VALUE...]` | A set of classes and the strategy that splits it, with the strategy's settings. `STRATEGY` is a built-in or plugin name (`--list-strategies` shows them), or your own class as `MODULE:CLASS` or `FILE.py:CLASS` (see [Writing a strategy](#writing-a-strategy)). Repeat it for more sets. The DEX files are written set by set, in this order. |
| `--assign PATTERN[,PATTERN...]=NAME` | Send the classes whose names match a pattern to the set `NAME`. Repeat it; each class goes to the first pattern it matches. |
| `--list-strategies` | Show the strategies and their settings. |

With a single `--set`, `--assign` is not needed: every class goes to that set.
With several, every class must match some pattern; end with `'**=NAME'` to
catch the rest.

A DEX file can reference at most 65,536 methods, 65,536 fields and 65,535
types. When a group of classes goes over one of these limits, it is cut in two,
and each half is written again (and cut again if needed). The cut falls nearest
the middle, between the units the strategy keeps together, such as packages.
Only a unit too big for one DEX file is cut inside, and `split-dex` prints a
warning that names it.

If the APK defines a class in more than one DEX file, only the first copy in
multidex order is kept. That is the copy Android loads.

### Patterns

Patterns match class names such as `com.myapp.Main` or `com.myapp.Outer$Inner`.
`*` matches any characters except a dot, and `**` matches any characters:

| Pattern | Matches |
| ------- | ------- |
| `com.myapp.**` | Every class under `com.myapp`, subpackages included. |
| `com.myapp.*` | The classes directly in `com.myapp`. |
| `com.myapp.Main` | One class. |
| `**` | Every class. |
| `@FILE` | The patterns listed in `FILE`, one per line. Blank lines and lines starting with `#` are skipped. |

### Examples

Smaller files:

```sh
split-dex --apk app.apk --out out/ --set all=package-affinity,cap=500
```

Split the app's own classes, and put all the library classes in one DEX file
(cut in halves where it goes over the 64K limits):

```sh
split-dex --apk app.apk --out out/ \
    --set app=package-affinity,cap=500 \
    --set libraries=single \
    --assign 'com.myapp.**=app' \
    --assign '**=libraries'
```

Split the app's own classes, but keep some of its packages with the libraries,
which stay in their original DEX files (at most 16):

```sh
split-dex --apk app.apk --out out/ \
    --set app=package-affinity,cap=1000 \
    --set libraries=original,max_files=16 \
    --assign '@startup-packages.txt=libraries' \
    --assign 'com.myapp.**=app' \
    --assign '**=libraries'
```

## Output

In the output directory:

| File        | Content                                                 |
| ----------- | ------------------------------------------------------- |
| `<k>.dex`   | The DEX files: `0.dex`, `1.dex`, ..., set by set.       |
| `index.csv` | `class,file`: every class descriptor and its DEX file.  |
| `sets.csv`  | `file,set`: the set of each DEX file.                   |

Each DEX file is a valid, standalone DEX.

Running again into the same directory replaces these files. If an earlier run
wrote more `<k>.dex` files, the extra ones are removed, so every DEX file in the
directory is listed in `index.csv`. Other files in the directory are not
touched.

At the end, `split-dex` prints a summary: how many classes were read, how many
files were written, how many groups had to be cut for the 64K limits, and the
classes and files of each set.

## Install

split-dex needs Python 3.14 or newer. Install the `split-dex` command from
PyPI with [uv](https://docs.astral.sh/uv/) or pip:

```sh
uv tool install split-dex
# or
pip install split-dex
```

Its dependency dexpp has prebuilt wheels for Linux x86_64 (glibc 2.28+ and
musl). On other platforms, the installer builds dexpp from source, which needs
a C++23 compiler (GCC 13 or newer, or Clang 19 or newer) and CMake 3.18 or
newer.

From a checkout, `uv tool install .` installs the command.

## Writing a strategy

The command-line options cover the common setups. A strategy of your own is for
the cases they don't. A strategy is a Python class:

```python
from split_dex import strategy


class OnePerFile:
    """Each class in a DEX file of its own, up to `limit` files."""

    def __init__(self, *, limit: int = 100) -> None:
        self._limit = limit

    def plan(self, classes: strategy.Classes) -> list[strategy.Group]:
        ids = list(classes.ids)
        groups = [strategy.Group.of_classes([class_id]) for class_id in ids[: self._limit - 1]]
        return groups + [strategy.Group.of_classes(ids[self._limit - 1 :])]
```

- Its keyword-only constructor parameters are its settings. The command line
  converts each `KEY=VALUE` to the parameter's type: int, float, bool, str or
  Path, optionally `| None`.
- `plan(classes)` receives the classes of one set and returns the groups to
  write, in order. Each group (`strategy.Group`) becomes one DEX file, and is
  made of blocks (`strategy.Block`): classes that should stay in one file.
  When a group goes over a 64K limit, the splitter cuts it at the block
  boundary nearest its middle. `Group.of_classes(ids)` makes one block per
  class.
- The splitter checks that every class of the set is in exactly one group;
  otherwise the run fails with an error that names the strategy's mistake.

What `classes` (`strategy.Classes`) gives:

| Member | Meaning |
| ------ | ------- |
| `ids` | The class ids of the set, in multidex order. |
| `descriptors` | The descriptor of every class of the APK, indexed by class id. |
| `source_dex(id)` | The input DEX file of a class: 0 for `classes.dex`, 1 for `classes2.dex`, ... |
| `links(weights=None)` | The links among the classes of the set, as a `partition.AffinityGraph`. `weights` maps a `strategy.LinkKind` (superclass, interface, nesting, call, field, type) to its weight; a kind left out weighs 1, and 0 ignores it. |
| `analysis` | dexpp's `AnalysisContext` for the whole APK: classes, methods, call graph, class hierarchy, cross-references. |
| `apk` | dexpp's `Apk`: manifest, resources, entries. |

There are four ways to use it:

- From a file: `--set all=./my_strategy.py:OnePerFile,limit=8`.
- From a module Python can import: `--set all=my_package.strategies:OnePerFile`.
- As a plugin. An installed package declares the strategy under the
  entry-point group `split_dex.strategies` in its `pyproject.toml`; it is then
  used by name, and `--list-strategies` shows it:

  ```toml
  [project.entry-points."split_dex.strategies"]
  one-per-file = "my_package.strategies:OnePerFile"
  ```

- Built into split-dex: put the module in `split_dex/strategies/` and add the
  class to `STRATEGIES` in `split_dex/registry.py`.

A strategy is ordinary Python code that runs inside split-dex, so only use
files, modules and plugins you trust.

From Python, `split_dex.splitter.split()` takes `split_dex.splitter.Options`,
whose sets can use any object with a `plan` method.

## Development

```sh
uv sync                     # .venv with split-dex, dexpp and the dev tools
uv run pytest               # the tests
uv run ruff format --check  # formatting
uv run ruff check           # lint
uv run mypy                 # types
```

The tests build small APKs in memory. Their classes are copies of the template
classes in `tests/data/templates.dex`, renamed for each test. The templates are
compiled from `tests/data/Templates.java`; `tests/data/make_templates.sh`
rebuilds them (it needs `javac` and the Android SDK's `d8`).

To move to a newer dexpp, update its version range in `dependencies` in
`pyproject.toml` and run `uv lock`.

To release: set `version` in `pyproject.toml`, turn the `## Unreleased`
section of `CHANGELOG.md` into `## <version>`, and push a `v<version>` tag. The
release workflow builds the wheel and the sdist, checks that the wheel installs
and runs, creates the GitHub release with that changelog section as notes, and
uploads the same files to PyPI.

## Layout

- `split_dex/cli.py` — the command-line interface.
- `split_dex/splitter.py` — reads the APK, sorts the classes into sets, runs
  each set's strategy, checks its groups, and writes the DEX files and maps.
- `split_dex/class_sets.py` — sets, patterns, and how classes are assigned.
- `split_dex/strategy.py` — the interface between the splitter and strategies.
- `split_dex/registry.py` — finds strategies (built-in, plugin, module or file)
  and creates them from their settings.
- `split_dex/strategies/` — the built-in strategies.
- `split_dex/partition.py` — the package-affinity grouping algorithm.
- `tests/` — the unit tests (pytest).
