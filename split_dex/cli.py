"""The split-dex command line."""

import argparse
import collections.abc
import importlib.metadata
import pathlib
import sys

import split_dex
from split_dex import splitter

_EPILOG = """\
output in DIR:
  <k>.dex     the grouped DEX files: 0.dex, 1.dex, ...
  index.csv   class,file: every class and the DEX file it is in
  tiers.csv   file,tier: whether each DEX file is business or framework
"""

_BYTES_PER_MB = 1048576


class _HotlistError(split_dex.Error):
    """Raised when the hotlist file cannot be read."""


def main(argv: collections.abc.Sequence[str] | None = None) -> int:
    """Run split-dex with command-line arguments.

    Prints a summary of the split on stdout, and a warning on stderr for each package that was
    too big for one DEX file.

    Args:
        argv: The arguments, without the program name. None reads them from `sys.argv`.

    Returns:
        The exit status: 0 when the split succeeds, 1 when it fails. Bad arguments exit with
        status 2 instead, through argparse's SystemExit.
    """
    arguments = _parser().parse_args(argv)
    try:
        options = _options(arguments)
        result = splitter.split(options)
    except split_dex.Error as error:
        print(f"split-dex: {error}", file=sys.stderr)
        return 1
    _print_summary(result)
    return 0


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
        "--package",
        required=True,
        type=_non_empty,
        metavar="PREFIX",
        help="the app's package, e.g. com.myapp. A class is business if its name is under this "
        "package, and framework otherwise. The app must not be obfuscated.",
    )
    required.add_argument(
        "--out", required=True, type=_non_empty, metavar="DIR", help="output directory"
    )
    parser.add_argument(
        "--business-cap",
        type=int,
        default=1000,
        metavar="N",
        help="target number of classes per business DEX file (default: %(default)s)",
    )
    parser.add_argument(
        "--framework-cap",
        type=int,
        default=16,
        metavar="N",
        help="most framework DEX files (default: %(default)s)",
    )
    parser.add_argument(
        "--hotlist",
        default="",
        metavar="FILE",
        help="file of class names, one per line (e.g. com.myapp.Startup); the whole package of "
        "each listed business class stays in the framework tier",
    )
    return parser


def _non_empty(value: str) -> str:
    if value == "":
        raise argparse.ArgumentTypeError("must not be empty")
    return value


def _options(arguments: argparse.Namespace) -> splitter.Options:
    hotlist: list[str] = []
    if arguments.hotlist != "":
        hotlist = _read_hotlist(pathlib.Path(arguments.hotlist))
    return splitter.Options(
        apk_path=pathlib.Path(arguments.apk),
        package=arguments.package,
        out_dir=pathlib.Path(arguments.out),
        business_cap=arguments.business_cap,
        framework_cap=arguments.framework_cap,
        hotlist=hotlist,
    )


def _read_hotlist(path: pathlib.Path) -> list[str]:
    """Read a hotlist file: one binary class name per line. Blank lines are skipped."""
    try:
        lines = path.read_bytes().decode("utf-8").split("\n")
    except FileNotFoundError as error:
        raise _HotlistError(f"hotlist not found: {path}") from error
    except (OSError, UnicodeDecodeError) as error:
        raise _HotlistError(f"cannot read hotlist: {path}: {error}") from error
    names = [line.removesuffix("\r") for line in lines]
    return [name for name in names if name != ""]


def _print_summary(result: splitter.Result) -> None:
    print(
        f"total_classes={result.total_classes} business_classes={result.business_classes} "
        f"framework_classes={result.framework_classes}"
    )
    print(
        f"business_dex={result.business_dex} framework_dex={result.framework_dex} "
        f"overflow_splits={result.overflow_splits} duplicate_classes={result.duplicate_classes}"
    )
    print(
        f"total_dex={result.business_dex + result.framework_dex} "
        f"total_mb={result.total_bytes / _BYTES_PER_MB:.1f}"
    )
    for package in result.split_packages:
        print(
            f"split-dex: warning: package {package} is too big for one DEX file and was split "
            "across files; package-private access between those files will fail",
            file=sys.stderr,
        )
