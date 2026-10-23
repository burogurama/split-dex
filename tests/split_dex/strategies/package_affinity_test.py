"""Tests for split_dex.strategies.package_affinity."""

import pytest

from split_dex import strategy
from split_dex.strategies import package_affinity
from tests import class_view


def testPlan_whenPackagesAreLinked_putsLinkedPackagesInOneGroup() -> None:
    # a-b and c-d are linked; with room for one pair per group, each pair stays together.
    classes = class_view.make(
        ["La/A;", "Lc/C;", "Lb/B;", "Ld/D;"], edges=[(0, 2), (2, 0), (1, 3), (3, 1)]
    )

    groups = package_affinity.PackageAffinity(cap=2).plan(classes)

    assert [group.class_ids for group in groups] == [[0, 2], [1, 3]]


def testPlan_whenAGroupHoldsSeveralPackages_makesOneNamedBlockPerPackage() -> None:
    classes = class_view.make(["La/A1;", "La/A2;", "Lb/B;"], edges=[(0, 2)])

    groups = package_affinity.PackageAffinity().plan(classes)

    assert groups == [
        strategy.Group((strategy.Block((0, 1), "package La"), strategy.Block((2,), "package Lb")))
    ]


def testPlan_whenTheSetIsEmpty_returnsNoGroupsWithoutBuildingLinks() -> None:
    app = class_view.FakeApp(["La/A;"])

    groups = package_affinity.PackageAffinity().plan(strategy.Classes([], app))

    assert groups == []
    assert app.asked_weights == []


def testInit_whenCapIsZero_raisesValueError() -> None:
    with pytest.raises(ValueError, match="cap must be >= 1"):
        package_affinity.PackageAffinity(cap=0)
