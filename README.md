# split-dex

`split-dex` splits the classes of an Android APK into grouped DEX files.

It reads every `classes*.dex` in the APK and sorts the classes into two tiers:

- **Business** classes are the app's own code: every class under the package
  you give with `--package` (for example `com.myapp.`). They are grouped into
  new DEX files of about `--business-cap` classes each.
- **Framework** classes are everything else: AndroidX, Kotlin, and other
  libraries. They keep the app's original layout: one DEX file for each
  original `classes*.dex`, with the business classes taken out.

Every class ends up in exactly one output DEX file. Two maps record where each
class went.

It is written in Python, on the Python binding of
[dexpp](https://github.com/burogurama/dexpp), which reads, analyses and writes
the DEX files.

## Output

In the output directory:

| File        | Content                                                 |
| ----------- | ------------------------------------------------------- |
| `<k>.dex`   | The grouped DEX files: `0.dex`, `1.dex`, ...            |
| `index.csv` | `class,file`: every class descriptor and its DEX file.  |
| `tiers.csv` | `file,tier`: whether each DEX is `business` or `framework`. |

The business files come first, then the framework files. Each DEX file is a
valid, standalone DEX.

Running again into the same directory replaces these files. If an earlier run
wrote more `<k>.dex` files, the extra ones are removed, so every DEX file in the
directory is listed in `index.csv`. Other files in the directory are not
touched.

## How business classes are grouped

Classes that are loaded together should be in the same file. A class usually
loads the classes it is linked to, so `split-dex` links two business classes
when:

- one is the superclass of the other, or an interface it implements;
- one is nested in the other (`LFoo$Bar;` is linked to `LFoo;`);
- a method of one refers to the other: it calls one of its methods, uses one of
  its fields, or uses the type (for example `new`, a cast, or `instanceof`).

The grouping works on **whole Java packages**. A package is never split across
two files. This matters when each DEX file is served by its own class loader.
Two classes of one package that live in different class loaders cannot use each
other's package-private members. ART then throws `IllegalAccessError`. Keeping
each package in one file keeps that access inside one class loader.

The steps:

1. Group the business classes by package.
2. Add up the links between classes into links between packages.
3. Put the packages in order so that linked packages are next to each other.
   The order starts from the package linked to the most other packages. Each
   next package is the one with the strongest link to the packages already
   placed.
4. Cut that order into files of about `--business-cap` classes. Each package
   goes into a file whole. A package bigger than the cap gets a file of its own.

Packages with no links come last and are packed together, so no file is left
with a single stray class.

A DEX file can reference at most 65,536 methods, 65,536 fields and 65,535
types. When a group goes over one of these limits, it is split in two and each
half is written again. A business group is cut at the package boundary nearest
its middle, so packages stay whole. Only a single package that is too big for
one DEX file has to be cut inside the package. `split-dex` then prints a
warning that names the package.

## Install

Requirements: Python 3.12 or newer. dexpp is not on PyPI, so it is built from
source, at the commit pinned in `pyproject.toml`, when split-dex is installed.
That build needs a C++23 compiler (GCC 13 or newer, or Clang 19 or newer),
CMake 3.18 or newer, and the Python headers (on Debian and Ubuntu, the
`python3-dev` package of your Python version).

From a checkout, install the `split-dex` command with
[uv](https://docs.astral.sh/uv/) or pip:

```sh
uv tool install .
# or
pip install .
```

## Usage

```sh
split-dex --apk app.apk --package com.myapp. --out out/
```

| Option              | Meaning                                                    | Default |
| ------------------- | ---------------------------------------------------------- | ------- |
| `--apk FILE`        | Input APK. All its `classes*.dex` entries are read.        | required |
| `--package PREFIX`  | The app's package, e.g. `com.myapp.`. Classes under it are business. | required |
| `--out DIR`         | Output directory. Created if it does not exist.            | required |
| `--business-cap N`  | Target number of classes per business DEX file.            | 1000 |
| `--framework-cap N` | Most framework DEX files. If the app has more `classes*.dex` than this, the smallest are merged. | 16 |
| `--hotlist FILE`    | A file with one class name per line, e.g. `com.myapp.Startup`. The whole package of each listed business class stays in the framework tier. | none |

A larger `--business-cap` gives fewer and fuller files. That means less
overhead per file and fewer strings and type names repeated across files. A
smaller cap gives finer groups.

`--package` reads the package straight from each class name, so the app must
not be obfuscated.

If the APK defines a class in more than one DEX file, only the first copy in
multidex order is kept. That is the copy Android loads.

At the end, `split-dex` prints a summary: how many classes are in each tier, how
many files were written, and how many groups had to be split for the 64K
limit.

The same work is available from Python: `split_dex.splitter.split()` takes the
options as a `split_dex.splitter.Options` and returns the counts.

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

dexpp is pinned to an exact commit in `pyproject.toml`. To move to a newer
dexpp, change that commit and run `uv lock`.

## Layout

- `split_dex/partition.py` — the package-safe grouping algorithm. It uses only
  the standard library.
- `split_dex/splitter.py` — reads the APK, sorts the classes into tiers, builds
  the links, groups them, and writes the DEX files and the maps.
- `split_dex/cli.py` — the command-line interface.
- `tests/` — the unit tests (pytest).
