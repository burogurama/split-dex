"""The split-dex command line."""

import argparse
import collections.abc
import importlib.metadata
import pathlib
import re
import sys
from typing import Any

import split_dex
from split_dex import class_sets
from split_dex import registry
from split_dex import splitter
from split_dex import strategy

_EPILOG = """\
sets:
  Each class goes to the set of the first --assign pattern that matches its
  name, and each set is split by its own strategy. Two sets never share a DEX
  file. Without --set, every class is in one set, "all", split by
  package-affinity. Patterns match class names such as com.myapp.Main: "*"
  matches any characters except a dot, "**" any characters. "@FILE" reads
  patterns from a file, one per line.

strategies:
  STRATEGY is a built-in or plugin name (see --list-strategies), or your own
  strategy class as MODULE:CLASS or FILE.py:CLASS. Its settings are the
  keyword parameters of the class's constructor.

examples:
  split-dex --apk app.apk --out out/
  split-dex --apk app.apk --out out/ --set all=package-affinity,cap=500
  split-dex --apk app.apk --out out/ \\
      --set app=package-affinity,cap=500 --set libraries=single \\
      --assign 'com.myapp.**=app' --assign '**=libraries'
  split-dex --apk app.apk --out out/ --set all=./my_strategy.py:MyStrategy,k=8

output in DIR:
  <k>.dex     the grouped DEX files: 0.dex, 1.dex, ..., set by set
  index.csv   class,file: every class and the DEX file it is in
  sets.csv    file,set: the set of each DEX file
"""

_BYTES_PER_MB = 1048576

# A set name is written to sets.csv, so it cannot hold a comma or a line break.
_SET_NAME = re.compile(r"[A-Za-z0-9_.-]+")


class _UsageError(split_dex.Error):
    """Raised when an option's value is malformed."""


def main(argv: collections.abc.Sequence[str] | None = None) -> int:
    """Run split-dex with command-line arguments.

    Prints a summary of the split on stdout, and a warning on stderr for each block, such as a
    package, that was too big for one DEX file.

    Args:
        argv: The arguments, without the program name. None reads them from `sys.argv`.

    Returns:
        The exit status: 0 when the split succeeds, 1 when it fails. Bad arguments exit with
        status 2 instead, through argparse's SystemExit.
    """
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        options = _options(arguments)
    except (_UsageError, strategy.StrategyError) as error:
        parser.error(str(error))
    try:
        result = splitter.split(options)
    except split_dex.Error as error:
        print(f"split-dex: {error}", file=sys.stderr)
        return 1
    _print_summary(result)
    return 0


class _ListStrategies(argparse.Action):
    """Prints the built-in and plugin strategies and exits, like --help."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        try:
            lines = registry.describe()
        except strategy.StrategyError as error:
            parser.exit(1, f"split-dex: {error}\n")
        print("\n".join(lines))
        parser.exit()


def _parser() -> argparse.ArgumentParser:
    version = importlib.metadata.version("split-dex")
    parser = argparse.ArgumentParser(
        prog="split-dex",
        description=f"split-dex {version}: split an APK's classes into grouped DEX files.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    required = parser.add_argument_group("required arguments")
    required.add_argument(
        "--apk",
        required=True,
        type=_non_empty,
        metavar="FILE",
        help="input APK; all its classes*.dex entries are read",
    )
    required.add_argument(
        "--out", required=True, type=_non_empty, metavar="DIR", help="output directory"
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        dest="sets",
        metavar="NAME=STRATEGY[,KEY=VALUE...]",
        help="a set of classes and the strategy that splits it, with the strategy's settings; "
        "repeat for more sets (DEX files are written in this order)",
    )
    parser.add_argument(
        "--assign",
        action="append",
        default=[],
        dest="assignments",
        metavar="PATTERN[,PATTERN...]=NAME",
        help="send the classes whose names match a pattern to the set NAME; repeat, the first "
        "match wins",
    )
    parser.add_argument(
        "--list-strategies",
        action=_ListStrategies,
        nargs=0,
        help="show the strategies and their settings, and exit",
    )
    return parser


def _non_empty(value: str) -> str:
    if value == "":
        raise argparse.ArgumentTypeError("must not be empty")
    return value


def _options(arguments: argparse.Namespace) -> splitter.Options:
    sets = [_parse_set(text) for text in arguments.sets]
    if len(sets) == 0:
        sets = list(splitter.default_sets())
    names = [class_set.name for class_set in sets]
    if len(set(names)) < len(names):
        raise _UsageError("two --set options have the same name")
    assignments = [rule for text in arguments.assignments for rule in _parse_assignment(text)]
    unknown = sorted({rule.set_name for rule in assignments} - set(names))
    if len(unknown) > 0:
        raise _UsageError(f"--assign names a set that no --set declares: {', '.join(unknown)}")
    if len(sets) > 1 and len(assignments) == 0:
        raise _UsageError("with several --set options, --assign must say which classes go where")
    return splitter.Options(
        apk_path=pathlib.Path(arguments.apk),
        out_dir=pathlib.Path(arguments.out),
        sets=sets,
        assignments=assignments,
    )


def _parse_set(text: str) -> class_sets.ClassSet:
    """A set from `NAME=STRATEGY[,KEY=VALUE...]`."""
    name, separator, spec = text.partition("=")
    if separator == "" or spec == "":
        raise _UsageError(f"--set {text}: expected NAME=STRATEGY[,KEY=VALUE...]")
    if _SET_NAME.fullmatch(name) is None:
        raise _UsageError(f"--set {text}: a set name uses only letters, digits, '_', '.' and '-'")
    strategy_name, *setting_texts = spec.split(",")
    settings: dict[str, str] = {}
    for setting in setting_texts:
        key, separator, value = setting.partition("=")
        if separator == "" or key == "":
            raise _UsageError(f"--set {text}: expected KEY=VALUE, not {setting!r}")
        settings[key] = value
    return class_sets.ClassSet(name, registry.create(strategy_name, settings))


def _parse_assignment(text: str) -> list[class_sets.Assignment]:
    """Assignments from `PATTERN[,PATTERN...]=NAME`, where `@FILE` reads patterns from a file."""
    patterns_text, separator, set_name = text.rpartition("=")
    if separator == "" or patterns_text == "" or set_name == "":
        raise _UsageError(f"--assign {text}: expected PATTERN[,PATTERN...]=NAME")
    patterns: list[str] = []
    for pattern in patterns_text.split(","):
        if pattern.startswith("@"):
            patterns.extend(_read_patterns(pathlib.Path(pattern[1:])))
        else:
            patterns.append(pattern)
    return [class_sets.Assignment(pattern, set_name) for pattern in patterns]


def _read_patterns(path: pathlib.Path) -> list[str]:
    """The patterns of a file: one per line. Blank lines and lines starting with '#' are skipped."""
    try:
        lines = path.read_bytes().decode("utf-8").split("\n")
    except (OSError, UnicodeDecodeError) as error:
        raise _UsageError(f"cannot read patterns from {path}: {error}") from error
    patterns = [line.strip() for line in lines]
    return [pattern for pattern in patterns if pattern != "" and pattern.startswith("#") is False]


def _print_summary(result: splitter.Result) -> None:
    print(f"total_classes={result.total_classes} duplicate_classes={result.duplicate_classes}")
    print(
        f"total_dex={result.dex_files} overflow_splits={result.overflow_splits} "
        f"total_mb={result.total_bytes / _BYTES_PER_MB:.1f}"
    )
    for set_result in result.sets:
        print(f"set={set_result.name} classes={set_result.classes} dex={set_result.dex_files}")
    for block in result.split_blocks:
        print(
            f"split-dex: warning: {block} is too big for one DEX file and was split across files",
            file=sys.stderr,
        )
