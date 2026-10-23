"""Tests for split_dex.strategies.original."""

import pytest

from split_dex import strategy
from split_dex.strategies import original
from tests import class_view


def testPlan_whenClassesComeFromTwoDexFiles_makesOneGroupPerDexFile() -> None:
    classes = class_view.make(["La/A;", "Lb/B;", "Lc/C;"], sources=[0, 1, 0])

    groups = original.Original().plan(classes)

    assert groups == [strategy.Group.of_classes([0, 2]), strategy.Group.of_classes([1])]


def testPlan_whenMoreDexFilesThanMaxFiles_mergesTheSmallestGroups() -> None:
    # DEX files of 3, 1, 2 and 1 classes into 2 files: the biggest group first, each into the
    # bin with the fewest classes, the lower bin on a tie. So 0-2 go to bin 0, 4-5 to bin 1,
    # 3 to bin 1 (2 < 3), and 6 to bin 0 (3 = 3).
    classes = class_view.make(
        [f"Lp/C{index};" for index in range(7)], sources=[0, 0, 0, 1, 2, 2, 3]
    )

    groups = original.Original(max_files=2).plan(classes)

    assert [group.class_ids for group in groups] == [[0, 1, 2, 6], [4, 5, 3]]


def testInit_whenMaxFilesIsZero_raisesValueError() -> None:
    with pytest.raises(ValueError, match="max_files must be >= 1"):
        original.Original(max_files=0)
