"""Tests for split_dex.cli."""

import pathlib

import pytest

from split_dex import cli
from tests import apk_builder


def _write_app(path: pathlib.Path) -> None:
    """An APK with two business packages and one framework class."""
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
    return [
        "--apk",
        str(tmp_path / "app.apk"),
        "--package",
        "com.app.",
        "--out",
        str(tmp_path / "out"),
        *extra,
    ]


def testMain_whenSplitSucceeds_printsTheSummary(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_app(tmp_path / "app.apk")

    status = cli.main(_arguments(tmp_path))

    assert status == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "total_classes=3 business_classes=2 framework_classes=1"
    assert lines[1] == "business_dex=1 framework_dex=1 overflow_splits=0 duplicate_classes=0"
    assert lines[2].startswith("total_dex=2 total_mb=") is True


def testMain_whenApkIsMissing_printsTheErrorAndReturnsOne(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = cli.main(_arguments(tmp_path))

    assert status == 1
    assert capsys.readouterr().err == f"split-dex: apk not found: {tmp_path / 'app.apk'}\n"


def testMain_whenHotlistIsGiven_readsOneClassNamePerLine(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_app(tmp_path / "app.apk")
    hotlist = tmp_path / "hot.txt"
    hotlist.write_bytes(b"\ncom.app.a.A\r\n\r\ncom.app.nothing.Here\n")

    status = cli.main(_arguments(tmp_path, "--hotlist", str(hotlist)))

    assert status == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "total_classes=3 business_classes=1 framework_classes=2"


def testMain_whenHotlistIsMissing_printsTheErrorAndReturnsOne(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_app(tmp_path / "app.apk")
    hotlist = tmp_path / "hot.txt"

    status = cli.main(_arguments(tmp_path, "--hotlist", str(hotlist)))

    assert status == 1
    assert capsys.readouterr().err == f"split-dex: hotlist not found: {hotlist}\n"


def testMain_whenAPackageIsTooBigForOneDexFile_printsAWarning(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    big = [apk_builder.make_wide_class(f"Lcom/app/big/W{index};") for index in range(8)]
    apk_builder.write_apk(tmp_path / "app.apk", [big[:4], big[4:]])

    status = cli.main(_arguments(tmp_path))

    assert status == 0
    assert capsys.readouterr().err == (
        "split-dex: warning: package Lcom/app/big is too big for one DEX file and was split "
        "across files; package-private access between those files will fail\n"
    )


def testMain_whenARequiredArgumentIsMissing_exitsWithStatusTwo(tmp_path: pathlib.Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--apk", str(tmp_path / "app.apk"), "--package", "com.app."])

    assert exit_info.value.code == 2


def testMain_whenPackageIsEmpty_exitsWithStatusTwo(tmp_path: pathlib.Path) -> None:
    arguments = _arguments(tmp_path)
    arguments[3] = ""

    with pytest.raises(SystemExit) as exit_info:
        cli.main(arguments)

    assert exit_info.value.code == 2
