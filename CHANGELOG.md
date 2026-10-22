# Changelog

## Unreleased

First version of `split-dex`: a Python package with a `split-dex` command,
built on dexpp's Python binding.

- Splits an APK's classes into grouped DEX files. Business classes (under
  `--package`) are grouped by how they are linked, in files of about
  `--business-cap` classes, and a Java package is never split across files.
  Framework classes keep one DEX file per original `classes*.dex`.
- Writes `index.csv` (`class,file`) and `tiers.csv` (`file,tier`) next to the
  DEX files.
- `--hotlist` keeps the whole package of each listed business class in the
  framework tier.
- A group over the 64K DEX reference limit is cut at a package boundary. A
  single package too big for one DEX file is cut and reported with a warning.
- A class defined in more than one DEX file is written once: the first copy in
  multidex order.
