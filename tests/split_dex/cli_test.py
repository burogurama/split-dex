"""Tests for split_dex.cli."""

import pathlib

import pytest

from split_dex import cli
from tests import apk_builder


def _write_app(path: pathlib.Path) -> None:
    """An APK with three unlinked classes: two under com.app and one library class."""
    apk_builder.write_apk(
        path,
        [
            [
                apk_builder.make_class("Lcom/app/a/A;"),
                apk_builder.make_class("Lcom/app/b/B;"),
                apk_builder.make_class("Llib/L;"),
            ]
        ],
    )


def _arguments(tmp_path: pathlib.Path, *extra: str) -> list[str]:
    return ["--apk", str(tmp_path / "app.apk"), "--out", str(tmp_path / "out"), *extra]


def _exit_status(arguments: list[str]) -> int | str | None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(arguments)
    return exit_info.value.code


def testMain_whenNoSetIsGiven_splitsEveryClassInTheSetAll(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_app(tmp_path / "app.apk")

    status = cli.main(_arguments(tmp_path))

    assert status == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "total_classes=3 duplicate_classes=0"
    assert lines[1].startswith("total_dex=1 overflow_splits=0 total_mb=") is True
    assert lines[2] == "set=all classes=3 dex=1"


def testMain_whenASetHasSettings_passesThemToItsStrategy(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_app(tmp_path / "app.apk")

    status = cli.main(_arguments(tmp_path, "--set", "all=package-affinity,cap=1"))

    assert status == 0
    assert "set=all classes=3 dex=3" in capsys.readouterr().out.splitlines()


def testMain_whenASetUsesAStrategyFromAFile_loadsItWithItsSettings(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_app(tmp_path / "app.apk")
    strategy_file = tmp_path / "one_per_file.py"
    strategy_file.write_text(
        '''
from split_dex import strategy


class OnePerFile:
    """Each class in a DEX file of its own, up to `limit` files; the rest in the last one."""

    def __init__(self, *, limit: int = 100) -> None:
        self._limit = limit

    def plan(self, classes: strategy.Classes) -> list[strategy.Group]:
        ids = list(classes.ids)
        groups = [strategy.Group.of_classes([class_id]) for class_id in ids[: self._limit - 1]]
        return groups + [strategy.Group.of_classes(ids[self._limit - 1 :])]
''',
        encoding="utf-8",
    )

    status = cli.main(_arguments(tmp_path, "--set", f"all={strategy_file}:OnePerFile,limit=2"))

    assert status == 0
    assert "set=all classes=3 dex=2" in capsys.readouterr().out.splitlines()


def testMain_whenAStrategyFileIsMissing_exitsWithStatusTwo(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _exit_status(_arguments(tmp_path, "--set", f"all={tmp_path / 'nope.py'}:Mine"))

    assert status == 2
    assert "strategy file not found" in capsys.readouterr().err


def testMain_whenClassesAreAssignedToTwoSets_splitsEachSetWithItsStrategy(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_app(tmp_path / "app.apk")

    status = cli.main(
        _arguments(
            tmp_path,
            "--set",
            "app=package-affinity,cap=1",
            "--set",
            "libraries=single",
            "--assign",
            "com.app.**=app",
            "--assign",
            "**=libraries",
        )
    )

    assert status == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[2:] == ["set=app classes=2 dex=2", "set=libraries classes=1 dex=1"]
    sets_csv = (tmp_path / "out" / "sets.csv").read_text(encoding="utf-8")
    assert sets_csv == "file,set\n0.dex,app\n1.dex,app\n2.dex,libraries\n"


def testMain_whenPatternsComeFromAFile_readsOnePatternPerLine(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_app(tmp_path / "app.apk")
    patterns = tmp_path / "app-patterns.txt"
    patterns.write_text("# the app's packages\ncom.app.a.*\r\n\ncom.app.b.*\n", encoding="utf-8")

    status = cli.main(
        _arguments(
            tmp_path,
            "--set",
            "app=single",
            "--set",
            "rest=single",
            "--assign",
            f"@{patterns}=app",
            "--assign",
            "**=rest",
        )
    )

    assert status == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[2:] == ["set=app classes=2 dex=1", "set=rest classes=1 dex=1"]


def testMain_whenAClassMatchesNoPattern_printsTheErrorAndReturnsOne(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_app(tmp_path / "app.apk")

    status = cli.main(_arguments(tmp_path, "--set", "app=single", "--assign", "com.app.**=app"))

    assert status == 1
    assert capsys.readouterr().err.startswith(
        "split-dex: 1 classes match no pattern, such as lib.L"
    )


def testMain_whenApkIsMissing_printsTheErrorAndReturnsOne(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = cli.main(_arguments(tmp_path))

    assert status == 1
    assert capsys.readouterr().err == f"split-dex: apk not found: {tmp_path / 'app.apk'}\n"


def testMain_whenAPackageIsTooBigForOneDexFile_printsAWarning(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    big = [apk_builder.make_wide_class(f"Lcom/app/big/W{index};") for index in range(8)]
    apk_builder.write_apk(tmp_path / "app.apk", [big[:4], big[4:]])

    status = cli.main(_arguments(tmp_path))

    assert status == 0
    assert capsys.readouterr().err == (
        "split-dex: warning: package Lcom/app/big is too big for one DEX file and was split "
        "across files\n"
    )


def testMain_whenListStrategiesIsGiven_printsThemAndExits(
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = _exit_status(["--list-strategies"])

    assert status == 0
    assert capsys.readouterr().out.startswith("package-affinity: ") is True


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--set", "app"], "expected NAME=STRATEGY"),
        (["--set", "app=nope"], "unknown strategy 'nope'"),
        (["--set", "app=package-affinity,size=3"], "has no setting 'size'"),
        (["--set", "app=package-affinity,cap"], "expected KEY=VALUE"),
        (["--set", "a,b=single"], "a set name uses only"),
        (["--set", "app=single", "--set", "app=single"], "two --set options have the same name"),
        (["--set", "app=single", "--set", "rest=single"], "--assign must say which classes"),
        (["--set", "app=single", "--assign", "**=nope"], "no --set declares: nope"),
        (["--assign", "com.app.**"], "expected PATTERN"),
    ],
)
def testMain_whenASetOrAssignmentIsInvalid_exitsWithStatusTwo(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
    message: str,
) -> None:
    status = _exit_status(_arguments(tmp_path, *extra))

    assert status == 2
    assert message in capsys.readouterr().err


def testMain_whenARequiredArgumentIsMissing_exitsWithStatusTwo(tmp_path: pathlib.Path) -> None:
    assert _exit_status(["--apk", str(tmp_path / "app.apk")]) == 2


def testMain_whenApkIsEmpty_exitsWithStatusTwo(tmp_path: pathlib.Path) -> None:
    assert _exit_status(["--apk", "", "--out", str(tmp_path / "out")]) == 2
