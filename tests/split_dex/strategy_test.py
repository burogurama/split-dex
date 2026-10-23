"""Tests for split_dex.strategy."""

from split_dex import strategy
from tests import class_view


def testOfClasses_whenGivenClasses_makesOneUnnamedBlockPerClass() -> None:
    group = strategy.Group.of_classes([3, 1, 2])

    assert group.blocks == (strategy.Block((3,)), strategy.Block((1,)), strategy.Block((2,)))


def testOfPackages_whenPackagesAreInRuns_makesOneNamedBlockPerRun() -> None:
    descriptors = ["La/A1;", "La/A2;", "Lb/B;", "LTop;"]

    group = strategy.Group.of_packages([1, 0, 2, 3], descriptors)

    assert group.blocks == (
        strategy.Block((1, 0), "package La"),
        strategy.Block((2,), "package Lb"),
        strategy.Block((3,), "package L"),
    )


def testClassIds_whenGroupHasSeveralBlocks_returnsEveryClassInWriteOrder() -> None:
    group = strategy.Group((strategy.Block((4, 5), "package La"), strategy.Block((1,))))

    assert group.class_ids == [4, 5, 1]


def testSourceDex_whenAskedAboutAClass_returnsItsInputDexFile() -> None:
    classes = class_view.make(["La/A;", "Lb/B;"], sources=[0, 1])

    assert classes.source_dex(1) == 1


def testLinks_whenNoWeightsAreGiven_weighsEveryKindOne() -> None:
    app = class_view.FakeApp(["La/A;", "Lb/B;"])

    strategy.Classes([0, 1], app).links()

    assert app.asked_weights == [dict.fromkeys(strategy.LinkKind, 1)]


def testLinks_whenSomeWeightsAreGiven_keepsOneForTheOtherKinds() -> None:
    app = class_view.FakeApp(["La/A;", "Lb/B;"])

    strategy.Classes([0, 1], app).links({strategy.LinkKind.CALL: 3, strategy.LinkKind.TYPE: 0})

    expected = dict.fromkeys(strategy.LinkKind, 1)
    expected[strategy.LinkKind.CALL] = 3
    expected[strategy.LinkKind.TYPE] = 0
    assert app.asked_weights == [expected]


def testLinks_whenCalled_returnsTheLinksAmongTheClassesOfTheSet() -> None:
    app = class_view.FakeApp(["La/A;", "Lb/B;", "Lc/C;"], edges=[(0, 1), (1, 2)])

    graph = strategy.Classes([0, 1], app).links()

    assert graph.neighbours(0) == {1: 1}
    assert graph.neighbours(1) == {0: 1}
    assert len(graph.neighbours(2)) == 0
