"""Tests for split_dex.strategies.package_order."""

import pytest

from split_dex import strategy
from split_dex.strategies import package_order
from tests import class_view


def testPlan_whenPackagesFit_fillsFilesWithWholePackagesInNameOrder() -> None:
    # Packages La (2 classes), Lb (2) and Lc (1) at cap 3: La alone (Lb would overflow it),
    # then Lb and Lc together.
    classes = class_view.make(["Lb/B1;", "La/A1;", "Lb/B2;", "La/A2;", "Lc/C;"])

    groups = package_order.PackageOrder(cap=3).plan(classes)

    assert [group.class_ids for group in groups] == [[1, 3], [0, 2, 4]]


def testPlan_whenAGroupHoldsSeveralPackages_makesOneNamedBlockPerPackage() -> None:
    classes = class_view.make(["Lb/B;", "La/A1;", "La/A2;"])

    groups = package_order.PackageOrder().plan(classes)

    assert groups == [
        strategy.Group((strategy.Block((1, 2), "package La"), strategy.Block((0,), "package Lb")))
    ]


def testPlan_whenAClassSortsAfterASubpackage_keepsItsPackageWhole() -> None:
    # By descriptor alone, "Lcom/a/z;" sorts after "Lcom/a/m/X;" and would split Lcom/a in two.
    classes = class_view.make(["Lcom/a/z;", "Lcom/a/m/X;", "Lcom/a/A;"])

    groups = package_order.PackageOrder().plan(classes)

    assert groups == [
        strategy.Group(
            (strategy.Block((2, 0), "package Lcom/a"), strategy.Block((1,), "package Lcom/a/m"))
        )
    ]


def testPlan_whenAPackageIsBiggerThanCap_putsItAloneInItsGroup() -> None:
    classes = class_view.make(["La/X;", "Lbig/A;", "Lbig/B;", "Lbig/C;", "Lc/Y;"])

    groups = package_order.PackageOrder(cap=2).plan(classes)

    assert [group.class_ids for group in groups] == [[0], [1, 2, 3], [4]]


def testPlan_whenCalled_readsNoLinks() -> None:
    app = class_view.FakeApp(["Lb/B;", "La/A;"], edges=[(0, 1)])

    package_order.PackageOrder().plan(strategy.Classes([0, 1], app))

    assert app.asked_weights == []


def testPlan_whenTheSetIsEmpty_returnsNoGroups() -> None:
    assert package_order.PackageOrder().plan(class_view.make([])) == []


def testInit_whenCapIsZero_raisesValueError() -> None:
    with pytest.raises(ValueError, match="cap must be >= 1"):
        package_order.PackageOrder(cap=0)
