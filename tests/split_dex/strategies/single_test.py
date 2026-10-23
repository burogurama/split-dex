"""Tests for split_dex.strategies.single."""

from split_dex import strategy
from split_dex.strategies import single
from tests import class_view


def testPlan_whenTheSetHasClasses_putsThemAllInOneGroup() -> None:
    classes = class_view.make(["La/A;", "Lb/B;", "Lc/C;"], sources=[0, 1, 2])

    groups = single.Single().plan(classes)

    assert groups == [strategy.Group.of_classes([0, 1, 2])]


def testPlan_whenTheSetIsEmpty_returnsNoGroups() -> None:
    assert single.Single().plan(class_view.make([])) == []
