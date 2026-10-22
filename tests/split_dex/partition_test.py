"""Tests for split_dex.partition."""

import collections.abc

from split_dex import partition


def _all_ids(count: int) -> list[int]:
    return list(range(count))


def _group_of(groups: collections.abc.Sequence[collections.abc.Sequence[int]]) -> dict[int, int]:
    return {class_id: index for index, group in enumerate(groups) for class_id in group}


def testPackageOf_whenClassIsInAPackage_returnsTheDescriptorUpToTheLastSlash() -> None:
    assert partition.package_of("Lcom/a/B;") == "Lcom/a"
    assert partition.package_of("Lcom/a/B$1;") == "Lcom/a"
    assert partition.package_of("Lcom/B;") == "Lcom"


def testPackageOf_whenClassIsInTheUnnamedPackage_returnsL() -> None:
    assert partition.package_of("LFoo;") == "L"


def testAddEdge_whenAddedFromBothEnds_accumulatesOnBothEnds() -> None:
    graph = partition.AffinityGraph(3)

    graph.add_edge(0, 1)
    graph.add_edge(1, 0, 2)

    assert graph.neighbours(0) == {1: 3}
    assert graph.neighbours(1) == {0: 3}


def testAddEdge_whenBothEndsAreOneClass_ignoresTheEdge() -> None:
    graph = partition.AffinityGraph(3)

    graph.add_edge(2, 2)

    assert len(graph.neighbours(2)) == 0


def testPartition_whenPackagesAreLinked_putsEveryMemberInExactlyOneGroup() -> None:
    descriptors = [f"Lp{package}/C{index};" for package in range(7) for index in range(5)]
    graph = partition.AffinityGraph(len(descriptors))
    for class_id in range(len(descriptors) - 7):
        graph.add_edge(class_id, class_id + 7)

    groups = partition.partition_by_package_affinity(
        _all_ids(len(descriptors)), graph, descriptors, 8
    )

    assert all(len(group) > 0 for group in groups) is True
    assert sorted(class_id for group in groups for class_id in group) == _all_ids(len(descriptors))


def testPartition_whenCapIsSmall_neverSplitsAPackage() -> None:
    # Packages of 1 to 9 classes, linked in a chain across package boundaries. A small cap forces
    # many groups, so a package that could be split would be.
    descriptors = [
        f"Lcom/app/p{package}/C{index};" for package in range(1, 10) for index in range(package)
    ]
    graph = partition.AffinityGraph(len(descriptors))
    for class_id in range(1, len(descriptors)):
        graph.add_edge(class_id - 1, class_id)

    groups = partition.partition_by_package_affinity(
        _all_ids(len(descriptors)), graph, descriptors, 4
    )

    groups_of_package: dict[str, set[int]] = {}
    for class_id, group in _group_of(groups).items():
        groups_of_package.setdefault(partition.package_of(descriptors[class_id]), set()).add(group)
    assert all(len(groups) == 1 for groups in groups_of_package.values()) is True


def testPartition_whenPackageIsBiggerThanCap_putsItAloneInItsGroup() -> None:
    # Lbig has 5 classes and the cap is 3: Lbig gets a group of its own, whole.
    descriptors = ["La/X;", "Lbig/A;", "Lbig/B;", "Lbig/C;", "Lbig/D;", "Lbig/E;", "Lc/Y;"]
    graph = partition.AffinityGraph(len(descriptors))
    graph.add_edge(0, 1)
    graph.add_edge(5, 6)

    groups = partition.partition_by_package_affinity(
        _all_ids(len(descriptors)), graph, descriptors, 3
    )

    big_groups = [group for group in groups if 1 in group]
    assert big_groups == [[1, 2, 3, 4, 5]]


def testPartition_whenPackagesHaveNoLinks_fillsFullGroups() -> None:
    # Ten unrelated one-class packages at cap 4: groups of 4, 4 and 2, and no stray single class.
    descriptors = [f"Lp{package}/C;" for package in range(10)]

    groups = partition.partition_by_package_affinity(
        _all_ids(10), partition.AffinityGraph(10), descriptors, 4
    )

    assert [len(group) for group in groups] == [4, 4, 2]


def testPartition_whenTwoPairsOfPackagesAreLinked_keepsEachPairTogether() -> None:
    # Two pairs of packages linked by heavy edges, nothing between the pairs. With room for
    # exactly one pair per group, each pair stays together.
    descriptors = ["La/A1;", "La/A2;", "Lb/B1;", "Lb/B2;", "Lc/C1;", "Lc/C2;", "Ld/D1;", "Ld/D2;"]
    graph = partition.AffinityGraph(len(descriptors))
    graph.add_edge(0, 2, 10)
    graph.add_edge(1, 3, 10)
    graph.add_edge(4, 6, 10)
    graph.add_edge(5, 7, 10)

    groups = partition.partition_by_package_affinity(
        _all_ids(len(descriptors)), graph, descriptors, 4
    )

    assert len(groups) == 2
    where = _group_of(groups)
    assert where[0] == where[2]
    assert where[4] == where[6]
    assert where[0] != where[4]


def testPartition_whenAClassIsNotAMember_ignoresItsEdges() -> None:
    # Class 2 is not a member: its edges must not pull 0 and 1 anywhere, and it must not appear
    # in the output.
    descriptors = ["La/A;", "Lb/B;", "Lc/C;"]
    graph = partition.AffinityGraph(3)
    graph.add_edge(0, 2, 5)
    graph.add_edge(1, 2, 5)

    groups = partition.partition_by_package_affinity([0, 1], graph, descriptors, 10)

    assert len(groups) == 1
    assert sorted(groups[0]) == [0, 1]


def testPartition_whenRunTwice_returnsTheSameGroups() -> None:
    descriptors = [f"Lp{package}/C{index};" for package in range(20) for index in range(3)]
    graph = partition.AffinityGraph(len(descriptors))
    for class_id in range(len(descriptors)):
        graph.add_edge(class_id, (class_id * 7 + 3) % len(descriptors), 1 + class_id % 4)

    first = partition.partition_by_package_affinity(
        _all_ids(len(descriptors)), graph, descriptors, 9
    )
    second = partition.partition_by_package_affinity(
        _all_ids(len(descriptors)), graph, descriptors, 9
    )

    assert first == second


def testPartition_whenThereAreNoMembers_returnsNoGroups() -> None:
    groups = partition.partition_by_package_affinity([], partition.AffinityGraph(0), [], 10)

    assert groups == []


def testPartition_whenEqualEdgesCompete_takesTheHigherPackageFirst() -> None:
    # Package a is linked to b and to c with equal weights. The walk starts at a (the most links)
    # and, on equal weights, takes the higher package id first: c, then b.
    descriptors = ["La/A;", "Lb/B;", "Lc/C;"]
    graph = partition.AffinityGraph(3)
    graph.add_edge(0, 1)
    graph.add_edge(0, 2)

    groups = partition.partition_by_package_affinity(_all_ids(3), graph, descriptors, 10)

    assert groups == [[0, 2, 1]]
