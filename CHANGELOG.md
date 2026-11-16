# Changelog

## 0.2.0

split-dex now needs dexpp 0.3 (`dexpp>=0.3.0,<0.4`). dexpp 0.3 changed its
Python API, so this version does not work with dexpp 0.2, and split-dex 0.1.0
does not work with dexpp 0.3.

- Breaking for strategies of your own: `classes.analysis` and `classes.apk`
  are dexpp 0.3 objects. For example, `Class.name` is now `Class.descriptor`,
  and `AnalysisContext.classes`, `Apk.entries` and `Method.calls` are now
  methods: `classes()`, `entries()` and `calls()`. dexpp's changelog lists
  every change.
- An `--apk` path that is a directory now fails with the error
  `cannot open apk`, instead of a `MemoryError` traceback.
- dexpp 0.3 has prebuilt wheels for macOS 14 or newer and for Windows x64, so
  installing there no longer builds dexpp from source.

## 0.1.0

First release of `split-dex`: a Python package with a `split-dex` command,
built on dexpp's Python binding. It needs Python 3.14 or newer and dexpp
0.2.0, and is licensed under the Apache License 2.0.

- Splits an APK's classes into grouped DEX files. `--set` declares sets of
  classes and the strategy that splits each one, and `--assign` sends classes
  to sets by patterns on their names. Two sets never share a DEX file.
- Built-in strategies: `package-affinity` (classes grouped by how they are
  linked, a Java package never split across files), `package-order` (whole
  packages in name order, without reading the code), `original` (the input DEX
  layout, optionally merged down to a number of files) and `single` (one DEX
  file). `--list-strategies` shows them and their settings.
- A strategy of your own can be used from a file (`FILE.py:CLASS`), a module
  (`MODULE:CLASS`), or an installed plugin (entry-point group
  `split_dex.strategies`). Strategies can read the links between classes with a
  weight per kind of link, and dexpp's analysis of the APK.
- Writes `index.csv` (`class,file`) and `sets.csv` (`file,set`) next to the
  DEX files.
- A group over the 64K DEX reference limit is cut at the block boundary nearest
  its middle (a package, for `package-affinity` and `package-order`). A single
  package too big for one DEX file is cut and reported with a warning.
- A class defined in more than one DEX file is written once: the first copy in
  multidex order.
