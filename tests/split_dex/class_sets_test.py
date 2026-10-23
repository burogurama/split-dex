"""Tests for split_dex.class_sets."""

import pytest

from split_dex import class_sets
from split_dex.strategies import single

_DESCRIPTORS = [
    "Lcom/app/Main;",
    "Lcom/app/ui/View$1;",
    "Landroidx/core/A;",
    "LTop;",
]


def _sets(*names: str) -> list[class_sets.ClassSet]:
    return [class_sets.ClassSet(name, single.Single()) for name in names]


def testBinaryName_whenTheClassIsNested_keepsTheDollar() -> None:
    assert class_sets.binary_name("Lcom/app/ui/View$1;") == "com.app.ui.View$1"


def testAssign_whenThePatternEndsWithTwoStars_matchesTheSubpackagesToo() -> None:
    rules = [class_sets.Assignment("com.app.**", "app"), class_sets.Assignment("**", "rest")]

    members = class_sets.assign(_DESCRIPTORS, _sets("app", "rest"), rules)

    assert members == {"app": [0, 1], "rest": [2, 3]}


def testAssign_whenThePatternEndsWithOneStar_matchesOnlyThePackage() -> None:
    rules = [class_sets.Assignment("com.app.*", "app"), class_sets.Assignment("**", "rest")]

    members = class_sets.assign(_DESCRIPTORS, _sets("app", "rest"), rules)

    assert members == {"app": [0], "rest": [1, 2, 3]}


def testAssign_whenSeveralPatternsMatch_usesTheFirstOne() -> None:
    rules = [
        class_sets.Assignment("com.app.ui.*", "ui"),
        class_sets.Assignment("com.app.**", "app"),
        class_sets.Assignment("**", "rest"),
    ]

    members = class_sets.assign(_DESCRIPTORS, _sets("app", "ui", "rest"), rules)

    assert members == {"app": [0], "ui": [1], "rest": [2, 3]}


def testAssign_whenNoPatternMatchesASet_leavesItEmpty() -> None:
    rules = [class_sets.Assignment("org.**", "org"), class_sets.Assignment("**", "rest")]

    members = class_sets.assign(_DESCRIPTORS, _sets("org", "rest"), rules)

    assert members == {"org": [], "rest": [0, 1, 2, 3]}


def testAssign_whenThereIsOneSetAndNoAssignment_putsEveryClassInIt() -> None:
    members = class_sets.assign(_DESCRIPTORS, _sets("all"), [])

    assert members == {"all": [0, 1, 2, 3]}


def testAssign_whenThereAreSeveralSetsAndNoAssignment_raisesAssignmentError() -> None:
    with pytest.raises(class_sets.AssignmentError, match="with several sets"):
        class_sets.assign(_DESCRIPTORS, _sets("a", "b"), [])


def testAssign_whenAClassMatchesNoPattern_raisesAssignmentError() -> None:
    rules = [class_sets.Assignment("com.**", "app")]

    with pytest.raises(class_sets.AssignmentError, match="2 classes match no pattern, such as"):
        class_sets.assign(_DESCRIPTORS, _sets("app"), rules)


def testAssign_whenAnAssignmentNamesAnUnknownSet_raisesAssignmentError() -> None:
    with pytest.raises(class_sets.AssignmentError, match="no set is named 'nope'"):
        class_sets.assign(_DESCRIPTORS, _sets("all"), [class_sets.Assignment("**", "nope")])


def testAssign_whenTwoSetsShareAName_raisesAssignmentError() -> None:
    with pytest.raises(class_sets.AssignmentError, match="two sets are named 'a'"):
        class_sets.assign(_DESCRIPTORS, _sets("a", "a"), [class_sets.Assignment("**", "a")])
