"""Tests for split_dex.splitter."""

import collections.abc
import dataclasses
import pathlib

import dexpp
import pytest

from split_dex import partition
from split_dex import splitter
from tests import apk_builder


@dataclasses.dataclass(frozen=True)
class _Output:
    """What one split wrote.

    Attributes:
        file_of: The DEX file of each class descriptor, from index.csv.
        tier_of: The tier of each DEX file, from tiers.csv.
        index_rows: The number of rows in index.csv.
    """

    file_of: dict[str, str]
    tier_of: dict[str, str]
    index_rows: int

    def files_of_tier(self, tier: str) -> set[str]:
        return {file for file, file_tier in self.tier_of.items() if file_tier == tier}


def _read_csv(path: pathlib.Path) -> list[tuple[str, str]]:
    """The rows of a two-column CSV file, without its header, split at the last comma."""
    rows: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        first, second = line.rsplit(",", 1)
        rows.append((first, second))
    return rows


def _read_output(out_dir: pathlib.Path) -> _Output:
    index = _read_csv(out_dir / "index.csv")
    return _Output(
        file_of=dict(index),
        tier_of=dict(_read_csv(out_dir / "tiers.csv")),
        index_rows=len(index),
    )


def _options(tmp_path: pathlib.Path, business_cap: int = 1000) -> splitter.Options:
    return splitter.Options(
        apk_path=tmp_path / "app.apk",
        package="com.app.",
        out_dir=tmp_path / "out",
        business_cap=business_cap,
    )


def _sample_app() -> list[list[dexpp.Model]]:
    """Three business packages, and framework classes in two source DEX files."""
    first = [
        apk_builder.make_class("Lcom/app/a/A0;"),
        apk_builder.make_class("Lcom/app/a/A1;", "Lcom/app/a/A0;"),
        apk_builder.make_class("Lcom/app/a/A0$1;"),
        apk_builder.make_class("Lcom/app/b/B0;"),
        apk_builder.make_class("Lcom/app/b/B1;", "Lcom/app/b/B0;"),
        apk_builder.make_class("Lcom/app/c/C0;"),
        apk_builder.make_class("Landroidx/x/X0;"),
        apk_builder.make_class("Landroidx/x/X1;"),
    ]
    second = [
        apk_builder.make_class("Lcom/app/c/C1;", "Lcom/app/c/C0;"),
        apk_builder.make_class("Lkotlin/K0;"),
        apk_builder.make_class("Lkotlin/K1;"),
    ]
    return [first, second]


def _wide_classes(package: str, count: int) -> list[dexpp.Model]:
    return [apk_builder.make_wide_class(f"L{package}/W{index};") for index in range(count)]


def _descriptors(
    dex_files: collections.abc.Sequence[collections.abc.Sequence[dexpp.Model]],
) -> set[str]:
    return {cls.class_descriptors[0] for classes in dex_files for cls in classes}


def testSplit_whenAppHasTwoTiers_listsEveryClassExactlyOnce(tmp_path: pathlib.Path) -> None:
    app = _sample_app()
    apk_builder.write_apk(tmp_path / "app.apk", app)

    result = splitter.split(_options(tmp_path))

    output = _read_output(tmp_path / "out")
    assert result.total_classes == 11
    assert output.index_rows == 11
    assert set(output.file_of) == _descriptors(app)


def testSplit_whenFilesAreWritten_eachFileHoldsExactlyTheClassesTheIndexLists(
    tmp_path: pathlib.Path,
) -> None:
    apk_builder.write_apk(tmp_path / "app.apk", _sample_app())

    splitter.split(_options(tmp_path))

    output = _read_output(tmp_path / "out")
    listed: dict[str, set[str]] = {}
    for descriptor, file in output.file_of.items():
        listed.setdefault(file, set()).add(descriptor)
    assert set(listed) == set(output.tier_of)
    for file, descriptors in listed.items():
        model = dexpp.Model.read_file(str(tmp_path / "out" / file))
        assert set(model.class_descriptors) == descriptors


def testSplit_whenAppHasTwoSourceDexFiles_keepsOneFrameworkFilePerSourceDex(
    tmp_path: pathlib.Path,
) -> None:
    apk_builder.write_apk(tmp_path / "app.apk", _sample_app())

    result = splitter.split(_options(tmp_path))

    output = _read_output(tmp_path / "out")
    assert result.framework_dex == 2
    assert len(output.files_of_tier("framework")) == 2
    # The framework classes of classes.dex stay together, apart from those of classes2.dex.
    assert output.file_of["Landroidx/x/X0;"] == output.file_of["Landroidx/x/X1;"]
    assert output.file_of["Lkotlin/K0;"] == output.file_of["Lkotlin/K1;"]
    assert output.file_of["Landroidx/x/X0;"] != output.file_of["Lkotlin/K0;"]


def testSplit_whenClassIsUnderThePackage_tagsItsFileBusiness(tmp_path: pathlib.Path) -> None:
    apk_builder.write_apk(tmp_path / "app.apk", _sample_app())

    result = splitter.split(_options(tmp_path))

    output = _read_output(tmp_path / "out")
    assert result.business_classes == 7
    for descriptor, file in output.file_of.items():
        expected = "business" if descriptor.startswith("Lcom/app/") else "framework"
        assert output.tier_of[file] == expected


def testSplit_whenCapIsSmall_keepsEachBusinessPackageInOneFile(tmp_path: pathlib.Path) -> None:
    classes = [
        apk_builder.make_class(f"Lcom/app/p{package}/C{index};")
        for package in range(12)
        for index in range(1 + package % 4)
    ]
    apk_builder.write_apk(tmp_path / "app.apk", [classes])

    result = splitter.split(_options(tmp_path, business_cap=3))

    output = _read_output(tmp_path / "out")
    assert result.business_dex > 1
    files_of_package: dict[str, set[str]] = {}
    for descriptor, file in output.file_of.items():
        files_of_package.setdefault(partition.package_of(descriptor), set()).add(file)
    assert all(len(files) == 1 for files in files_of_package.values()) is True


def testSplit_whenClassesAreLinked_putsThemInOneFile(tmp_path: pathlib.Path) -> None:
    # a and b call each other; d extends c. With room for one pair per file, each pair stays
    # together.
    a = apk_builder.make_caller("Lcom/app/a/A;", "Lcom/app/b/B;")
    b = apk_builder.make_caller("Lcom/app/b/B;", "Lcom/app/a/A;")
    c = apk_builder.make_class("Lcom/app/c/C;")
    d = apk_builder.make_class("Lcom/app/d/D;", "Lcom/app/c/C;")
    apk_builder.write_apk(tmp_path / "app.apk", [[a, c, b, d]])

    splitter.split(_options(tmp_path, business_cap=2))

    output = _read_output(tmp_path / "out")
    assert output.file_of["Lcom/app/a/A;"] == output.file_of["Lcom/app/b/B;"]
    assert output.file_of["Lcom/app/c/C;"] == output.file_of["Lcom/app/d/D;"]
    assert output.file_of["Lcom/app/a/A;"] != output.file_of["Lcom/app/c/C;"]


def testSplit_whenHotlistNamesABusinessClass_movesItsWholePackageToFramework(
    tmp_path: pathlib.Path,
) -> None:
    apk_builder.write_apk(tmp_path / "app.apk", _sample_app())
    options = dataclasses.replace(_options(tmp_path), hotlist=["com.app.a.A1"])

    result = splitter.split(options)

    output = _read_output(tmp_path / "out")
    for descriptor in ["Lcom/app/a/A0;", "Lcom/app/a/A1;", "Lcom/app/a/A0$1;"]:
        assert output.tier_of[output.file_of[descriptor]] == "framework"
    for descriptor in ["Lcom/app/b/B0;", "Lcom/app/c/C1;"]:
        assert output.tier_of[output.file_of[descriptor]] == "business"
    assert result.business_classes == 4


def testSplit_whenHotlistNamesUnknownOrFrameworkClasses_ignoresThem(
    tmp_path: pathlib.Path,
) -> None:
    apk_builder.write_apk(tmp_path / "app.apk", _sample_app())
    options = dataclasses.replace(
        _options(tmp_path), hotlist=["com.app.nothing.Here", "androidx.x.X0"]
    )

    result = splitter.split(options)

    assert result.business_classes == 7


def testSplit_whenMoreSourceDexThanFrameworkCap_mergesDownToTheCap(tmp_path: pathlib.Path) -> None:
    app = [[apk_builder.make_class(f"Llib{index}/L;")] for index in range(4)]
    apk_builder.write_apk(tmp_path / "app.apk", app)
    options = dataclasses.replace(_options(tmp_path), framework_cap=2)

    result = splitter.split(options)

    assert result.framework_dex == 2
    assert len(_read_output(tmp_path / "out").files_of_tier("framework")) == 2


def testSplit_whenArchiveListsClasses2First_readsDexEntriesInMultidexOrder(
    tmp_path: pathlib.Path,
) -> None:
    first = apk_builder.write_dex([apk_builder.make_class("Lfirst/F;")])
    second = apk_builder.write_dex([apk_builder.make_class("Lsecond/S;")])
    apk_builder.write_zip(tmp_path / "app.apk", [("classes2.dex", second), ("classes.dex", first)])

    splitter.split(_options(tmp_path))

    output = _read_output(tmp_path / "out")
    assert output.file_of["Lfirst/F;"] == "0.dex"
    assert output.file_of["Lsecond/S;"] == "1.dex"


def testSplit_whenGroupOverflowsTheMethodLimit_cutsItAtAPackageBoundary(
    tmp_path: pathlib.Path,
) -> None:
    # One group (cap 1000) of 7 x 10,001 methods, which do not fit one DEX file: package a has 4
    # classes and package b 3. The middle of the group (class 3 of 7) is inside package a, so a
    # cut in the middle would split a; the cut must fall between a and b.
    apk_builder.write_apk(
        tmp_path / "app.apk",
        [_wide_classes("com/app/a", 4), _wide_classes("com/app/b", 3)],
    )

    result = splitter.split(_options(tmp_path))

    output = _read_output(tmp_path / "out")
    assert result.overflow_splits == 1
    assert result.business_dex == 2
    assert result.split_packages == []
    a_files = {output.file_of[f"Lcom/app/a/W{index};"] for index in range(4)}
    b_files = {output.file_of[f"Lcom/app/b/W{index};"] for index in range(3)}
    assert len(a_files) == 1
    assert len(b_files) == 1
    assert a_files != b_files


def testSplit_whenOnePackageOverflowsTheMethodLimit_reportsItAsSplit(
    tmp_path: pathlib.Path,
) -> None:
    big = _wide_classes("com/app/big", 8)
    apk_builder.write_apk(tmp_path / "app.apk", [big[:4], big[4:]])

    result = splitter.split(_options(tmp_path))

    assert result.overflow_splits == 1
    assert result.business_dex == 2
    assert result.split_packages == ["Lcom/app/big"]


def testSplit_whenClassIsDefinedTwice_keepsTheFirstCopy(tmp_path: pathlib.Path) -> None:
    # Lcom/app/a/A; is in classes.dex, and again in classes2.dex with a method more. Android
    # loads the first copy, so that is the one written.
    first = apk_builder.make_class("Lcom/app/a/A;")
    duplicate = apk_builder.make_caller("Lcom/app/a/A;", "Lcom/app/a/Other;")
    apk_builder.write_apk(
        tmp_path / "app.apk", [[first], [duplicate, apk_builder.make_class("Llib/L;")]]
    )

    result = splitter.split(_options(tmp_path))

    output = _read_output(tmp_path / "out")
    assert result.duplicate_classes == 1
    assert result.total_classes == 2
    assert output.index_rows == 2
    written = dexpp.Model.read_file(str(tmp_path / "out" / output.file_of["Lcom/app/a/A;"]))
    kept = written.class_info("Lcom/app/a/A;")
    assert kept is not None
    assert kept.method_count == 1


def testSplit_whenRunAgainWithFewerFiles_removesOnlyTheStaleDexFiles(
    tmp_path: pathlib.Path,
) -> None:
    apk_builder.write_apk(tmp_path / "app.apk", _sample_app())
    # Cap 1: one file per business package, so 3 + 2 framework = 5 files.
    splitter.split(_options(tmp_path, business_cap=1))
    out_dir = tmp_path / "out"
    (out_dir / "notes.txt").write_text("not written by split-dex")
    (out_dir / "07.dex").write_text("not a name split-dex writes")

    # Cap 1000: 1 + 2 = 3 files. 3.dex and 4.dex are left from the first run.
    splitter.split(_options(tmp_path))

    dex_files = {path.name for path in out_dir.glob("*.dex")}
    assert dex_files == {"0.dex", "1.dex", "2.dex", "07.dex"}
    assert len(_read_output(out_dir).tier_of) == 3
    assert (out_dir / "notes.txt").exists() is True


def testSplit_whenApkIsMissing_raisesSplitError(tmp_path: pathlib.Path) -> None:
    with pytest.raises(splitter.SplitError, match="apk not found"):
        splitter.split(_options(tmp_path))


def testSplit_whenApkHasNoDex_raisesSplitError(tmp_path: pathlib.Path) -> None:
    apk_builder.write_zip(tmp_path / "app.apk", [("assets/readme.txt", b"hi")])

    with pytest.raises(splitter.SplitError, match=r"no classes\*\.dex"):
        splitter.split(_options(tmp_path))


def testSplit_whenBusinessCapIsZero_raisesSplitError(tmp_path: pathlib.Path) -> None:
    apk_builder.write_apk(tmp_path / "app.apk", _sample_app())

    with pytest.raises(splitter.SplitError, match="business-cap"):
        splitter.split(_options(tmp_path, business_cap=0))


def testSplit_whenFrameworkCapIsZero_raisesSplitError(tmp_path: pathlib.Path) -> None:
    apk_builder.write_apk(tmp_path / "app.apk", _sample_app())
    options = dataclasses.replace(_options(tmp_path), framework_cap=0)

    with pytest.raises(splitter.SplitError, match="framework-cap"):
        splitter.split(options)
