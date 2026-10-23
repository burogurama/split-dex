"""Tests for split_dex.splitter."""

import collections.abc
import dataclasses
import pathlib

import dexpp
import pytest

from split_dex import class_sets
from split_dex import partition
from split_dex import splitter
from split_dex import strategy
from split_dex.strategies import original
from split_dex.strategies import package_affinity
from split_dex.strategies import single
from tests import apk_builder


@dataclasses.dataclass(frozen=True)
class _ReturnGroups:
    """A strategy that returns fixed groups, to check how the splitter treats bad ones."""

    groups: tuple[strategy.Group, ...]

    def plan(self, classes: strategy.Classes) -> list[strategy.Group]:
        return list(self.groups)


class _RecordLinks:
    """A strategy that keeps the link graph it asks for, and puts every class in one group."""

    def __init__(self, weights: dict[strategy.LinkKind, int] | None) -> None:
        self._weights = weights
        self.graph: partition.AffinityGraph | None = None

    def plan(self, classes: strategy.Classes) -> list[strategy.Group]:
        self.graph = classes.links(self._weights)
        return [strategy.Group.of_classes(classes.ids)]


class _RecordAnalysis:
    """A strategy that looks its classes up in the analysis, and puts them in one group."""

    def __init__(self) -> None:
        self.found: list[bool] = []
        self.entries: list[str] = []

    def plan(self, classes: strategy.Classes) -> list[strategy.Group]:
        for class_id in classes.ids:
            found = classes.analysis.find_class(classes.descriptors[class_id])
            self.found.append(found is not None)
        self.entries = classes.apk.entries
        return [strategy.Group.of_classes(classes.ids)]


def _read_csv(path: pathlib.Path) -> dict[str, str]:
    """The rows of a two-column CSV file, without its header, as a mapping."""
    rows: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        first, second = line.rsplit(",", 1)
        rows[first] = second
    return rows


def _index_rows(out_dir: pathlib.Path) -> int:
    return len((out_dir / "index.csv").read_text(encoding="utf-8").splitlines()) - 1


def _options(
    tmp_path: pathlib.Path,
    sets: collections.abc.Sequence[class_sets.ClassSet] | None = None,
    assignments: collections.abc.Sequence[class_sets.Assignment] = (),
) -> splitter.Options:
    if sets is None:
        sets = splitter.default_sets()
    return splitter.Options(
        apk_path=tmp_path / "app.apk",
        out_dir=tmp_path / "out",
        sets=sets,
        assignments=assignments,
    )


def _one_set(cap: int) -> list[class_sets.ClassSet]:
    return [class_sets.ClassSet("all", package_affinity.PackageAffinity(cap=cap))]


def _sample_app() -> list[list[dexpp.Model]]:
    """Five packages in two source DEX files."""
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


def _app_and_libraries(
    tmp_path: pathlib.Path, app: strategy.Strategy, libraries: strategy.Strategy
) -> splitter.Options:
    """Options with two sets: classes under com.app in `app`, the others in `libraries`."""
    return _options(
        tmp_path,
        sets=[class_sets.ClassSet("app", app), class_sets.ClassSet("libraries", libraries)],
        assignments=[
            class_sets.Assignment("com.app.**", "app"),
            class_sets.Assignment("**", "libraries"),
        ],
    )


def _wide_classes(package: str, count: int) -> list[dexpp.Model]:
    return [apk_builder.make_wide_class(f"L{package}/W{index};") for index in range(count)]


def _descriptors(
    dex_files: collections.abc.Sequence[collections.abc.Sequence[dexpp.Model]],
) -> set[str]:
    return {cls.class_descriptors[0] for classes in dex_files for cls in classes}


def testSplit_whenAppHasSeveralDexFiles_listsEveryClassExactlyOnce(tmp_path: pathlib.Path) -> None:
    app = _sample_app()
    apk_builder.write_apk(tmp_path / "app.apk", app)

    result = splitter.split(_options(tmp_path))

    assert result.total_classes == 11
    assert _index_rows(tmp_path / "out") == 11
    assert set(_read_csv(tmp_path / "out" / "index.csv")) == _descriptors(app)


def testSplit_whenFilesAreWritten_eachFileHoldsExactlyTheClassesTheIndexLists(
    tmp_path: pathlib.Path,
) -> None:
    apk_builder.write_apk(tmp_path / "app.apk", _sample_app())

    splitter.split(_options(tmp_path, _one_set(cap=3)))

    out_dir = tmp_path / "out"
    listed: dict[str, set[str]] = {}
    for descriptor, file in _read_csv(out_dir / "index.csv").items():
        listed.setdefault(file, set()).add(descriptor)
    assert set(listed) == {path.name for path in out_dir.glob("*.dex")}
    assert set(listed) == set(_read_csv(out_dir / "sets.csv"))
    for file, descriptors in listed.items():
        model = dexpp.Model.read_file(str(out_dir / file))
        assert set(model.class_descriptors) == descriptors


def testSplit_whenCapIsSmall_keepsEachPackageInOneFile(tmp_path: pathlib.Path) -> None:
    classes = [
        apk_builder.make_class(f"Lcom/app/p{package}/C{index};")
        for package in range(12)
        for index in range(1 + package % 4)
    ]
    apk_builder.write_apk(tmp_path / "app.apk", [classes])

    result = splitter.split(_options(tmp_path, _one_set(cap=3)))

    assert result.dex_files > 1
    files_of_package: dict[str, set[str]] = {}
    for descriptor, file in _read_csv(tmp_path / "out" / "index.csv").items():
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

    splitter.split(_options(tmp_path, _one_set(cap=2)))

    file_of = _read_csv(tmp_path / "out" / "index.csv")
    assert file_of["Lcom/app/a/A;"] == file_of["Lcom/app/b/B;"]
    assert file_of["Lcom/app/c/C;"] == file_of["Lcom/app/d/D;"]
    assert file_of["Lcom/app/a/A;"] != file_of["Lcom/app/c/C;"]


def testSplit_whenArchiveListsClasses2First_readsDexEntriesInMultidexOrder(
    tmp_path: pathlib.Path,
) -> None:
    # Two unlinked classes, one file each: the class of classes.dex comes first.
    first = apk_builder.write_dex([apk_builder.make_class("Lfirst/F;")])
    second = apk_builder.write_dex([apk_builder.make_class("Lsecond/S;")])
    apk_builder.write_zip(tmp_path / "app.apk", [("classes2.dex", second), ("classes.dex", first)])

    splitter.split(_options(tmp_path, _one_set(cap=1)))

    file_of = _read_csv(tmp_path / "out" / "index.csv")
    assert file_of["Lfirst/F;"] == "0.dex"
    assert file_of["Lsecond/S;"] == "1.dex"


def testSplit_whenTwoSetsAreGiven_neverMixesThemAndWritesTheFirstSetFirst(
    tmp_path: pathlib.Path,
) -> None:
    apk_builder.write_apk(tmp_path / "app.apk", _sample_app())
    options = _app_and_libraries(tmp_path, package_affinity.PackageAffinity(), single.Single())

    result = splitter.split(options)

    file_of = _read_csv(tmp_path / "out" / "index.csv")
    set_of = _read_csv(tmp_path / "out" / "sets.csv")
    assert set_of == {"0.dex": "app", "1.dex": "libraries"}
    for descriptor, file in file_of.items():
        expected = "app" if descriptor.startswith("Lcom/app/") else "libraries"
        assert set_of[file] == expected
    assert result.sets == [
        splitter.SetResult("app", classes=7, dex_files=1),
        splitter.SetResult("libraries", classes=4, dex_files=1),
    ]


def testSplit_whenASetUsesTheOriginalStrategy_keepsItsClassesInTheirInputDexFiles(
    tmp_path: pathlib.Path,
) -> None:
    apk_builder.write_apk(tmp_path / "app.apk", _sample_app())
    options = _app_and_libraries(tmp_path, package_affinity.PackageAffinity(), original.Original())

    splitter.split(options)

    file_of = _read_csv(tmp_path / "out" / "index.csv")
    assert file_of["Landroidx/x/X0;"] == file_of["Landroidx/x/X1;"]
    assert file_of["Lkotlin/K0;"] == file_of["Lkotlin/K1;"]
    assert file_of["Landroidx/x/X0;"] != file_of["Lkotlin/K0;"]


def testSplit_whenAClassMatchesNoPattern_raisesAssignmentError(tmp_path: pathlib.Path) -> None:
    apk_builder.write_apk(tmp_path / "app.apk", _sample_app())
    options = _options(
        tmp_path,
        sets=[class_sets.ClassSet("app", single.Single())],
        assignments=[class_sets.Assignment("com.app.**", "app")],
    )

    with pytest.raises(class_sets.AssignmentError, match="4 classes match no pattern"):
        splitter.split(options)


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        ((strategy.Group.of_classes([0]),), "left out 1 classes, such as LLib;"),
        ((strategy.Group.of_classes([0, 1, 1]),), "returned LLib; twice"),
        ((strategy.Group.of_classes([0, 1, 7]),), "class id 7, which is not in the set"),
        ((strategy.Group(()), strategy.Group.of_classes([0, 1])), "an empty group"),
        ((strategy.Group((strategy.Block(()),)),), "an empty block"),
    ],
)
def testSplit_whenAStrategyReturnsBadGroups_raisesStrategyError(
    tmp_path: pathlib.Path, groups: tuple[strategy.Group, ...], message: str
) -> None:
    apk_builder.write_apk(
        tmp_path / "app.apk", [[apk_builder.make_class("LApp;"), apk_builder.make_class("LLib;")]]
    )
    options = _options(tmp_path, sets=[class_sets.ClassSet("all", _ReturnGroups(groups))])

    with pytest.raises(strategy.StrategyError, match=message):
        splitter.split(options)


@pytest.mark.parametrize(
    ("weights", "expected"),
    [
        (None, {1: 1}),
        ({strategy.LinkKind.CALL: 5}, {1: 5}),
        ({strategy.LinkKind.CALL: 0}, {}),
    ],
)
def testSplit_whenAStrategyAsksForLinks_weighsEachLinkByItsKind(
    tmp_path: pathlib.Path,
    weights: dict[strategy.LinkKind, int] | None,
    expected: dict[int, int],
) -> None:
    # The only link between the two classes: a method of A calls a method of B.
    apk_builder.write_apk(
        tmp_path / "app.apk",
        [[apk_builder.make_caller("Lp/A;", "Lp/B;"), apk_builder.make_class("Lp/B;")]],
    )
    recorder = _RecordLinks(weights)

    splitter.split(_options(tmp_path, sets=[class_sets.ClassSet("all", recorder)]))

    assert recorder.graph is not None
    assert recorder.graph.neighbours(0) == expected


def testSplit_whenAStrategyUsesTheAnalysis_findsItsClassesInIt(tmp_path: pathlib.Path) -> None:
    apk_builder.write_apk(tmp_path / "app.apk", _sample_app())
    recorder = _RecordAnalysis()

    splitter.split(_options(tmp_path, sets=[class_sets.ClassSet("all", recorder)]))

    assert recorder.found == [True] * 11
    assert recorder.entries == ["classes.dex", "classes2.dex"]


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

    file_of = _read_csv(tmp_path / "out" / "index.csv")
    assert result.overflow_splits == 1
    assert result.dex_files == 2
    assert result.split_blocks == []
    a_files = {file_of[f"Lcom/app/a/W{index};"] for index in range(4)}
    b_files = {file_of[f"Lcom/app/b/W{index};"] for index in range(3)}
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
    assert result.dex_files == 2
    assert result.split_blocks == ["package Lcom/app/big"]


def testSplit_whenASingleSetOverflows_cutsItInTheMiddleWithoutAWarning(
    tmp_path: pathlib.Path,
) -> None:
    big = _wide_classes("com/app/big", 8)
    apk_builder.write_apk(tmp_path / "app.apk", [big[:4], big[4:]])

    result = splitter.split(_options(tmp_path, [class_sets.ClassSet("all", single.Single())]))

    assert result.overflow_splits == 1
    assert result.dex_files == 2
    assert result.split_blocks == []


def testSplit_whenClassIsDefinedTwice_keepsTheFirstCopy(tmp_path: pathlib.Path) -> None:
    # Lcom/app/a/A; is in classes.dex, and again in classes2.dex with a method more. Android
    # loads the first copy, so that is the one written.
    first = apk_builder.make_class("Lcom/app/a/A;")
    duplicate = apk_builder.make_caller("Lcom/app/a/A;", "Lcom/app/a/Other;")
    apk_builder.write_apk(
        tmp_path / "app.apk", [[first], [duplicate, apk_builder.make_class("Llib/L;")]]
    )

    result = splitter.split(_options(tmp_path))

    file_of = _read_csv(tmp_path / "out" / "index.csv")
    assert result.duplicate_classes == 1
    assert result.total_classes == 2
    assert _index_rows(tmp_path / "out") == 2
    written = dexpp.Model.read_file(str(tmp_path / "out" / file_of["Lcom/app/a/A;"]))
    kept = written.class_info("Lcom/app/a/A;")
    assert kept is not None
    assert kept.method_count == 1


def testSplit_whenRunAgainWithFewerFiles_removesOnlyTheStaleDexFiles(
    tmp_path: pathlib.Path,
) -> None:
    apk_builder.write_apk(tmp_path / "app.apk", _sample_app())
    # Cap 1: one file per package, so 5 files.
    splitter.split(_options(tmp_path, _one_set(cap=1)))
    out_dir = tmp_path / "out"
    (out_dir / "notes.txt").write_text("not written by split-dex")
    (out_dir / "07.dex").write_text("not a name split-dex writes")

    # Cap 1000: 1 file. 1.dex to 4.dex are left from the first run.
    splitter.split(_options(tmp_path))

    dex_files = {path.name for path in out_dir.glob("*.dex")}
    assert dex_files == {"0.dex", "07.dex"}
    assert set(_read_csv(out_dir / "index.csv").values()) == {"0.dex"}
    assert (out_dir / "notes.txt").exists() is True


def testSplit_whenApkIsMissing_raisesSplitError(tmp_path: pathlib.Path) -> None:
    with pytest.raises(splitter.SplitError, match="apk not found"):
        splitter.split(_options(tmp_path))


def testSplit_whenApkHasNoDex_raisesSplitError(tmp_path: pathlib.Path) -> None:
    apk_builder.write_zip(tmp_path / "app.apk", [("assets/readme.txt", b"hi")])

    with pytest.raises(splitter.SplitError, match=r"no classes\*\.dex"):
        splitter.split(_options(tmp_path))
