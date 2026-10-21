#include "partition.hpp"

#include <algorithm>
#include <map>
#include <numeric>
#include <set>
#include <string>
#include <vector>

#include <gtest/gtest.h>

using splitdex::AffinityGraph;
using splitdex::package_of;
using splitdex::partition_by_package_affinity;

namespace {

/** A graph with no edges over `n` classes. */
AffinityGraph empty_graph(std::size_t n)
{
    return AffinityGraph{std::vector<std::unordered_map<uint32_t, uint32_t>>(n)};
}

std::vector<uint32_t> all_ids(std::size_t n)
{
    std::vector<uint32_t> ids(n);
    std::iota(ids.begin(), ids.end(), 0u);
    return ids;
}

/** The group index of every class id. */
std::map<uint32_t, std::size_t> group_of(const std::vector<std::vector<uint32_t>> &groups)
{
    std::map<uint32_t, std::size_t> where;
    for (std::size_t g = 0; g < groups.size(); ++g)
        for (uint32_t c : groups[g])
            where[c] = g;
    return where;
}

} // namespace

TEST(PackageOf, TakesTheDescriptorUpToTheLastSlash)
{
    EXPECT_EQ(package_of("Lcom/a/B;"), "Lcom/a");
    EXPECT_EQ(package_of("Lcom/a/B$1;"), "Lcom/a");
    EXPECT_EQ(package_of("Lcom/B;"), "Lcom");
}

TEST(PackageOf, UnnamedPackageIsL) { EXPECT_EQ(package_of("LFoo;"), "L"); }

TEST(AffinityGraph, AddEdgeIsSymmetricAndAccumulates)
{
    AffinityGraph graph = empty_graph(3);
    graph.add_edge(0, 1);
    graph.add_edge(1, 0, 2);
    graph.add_edge(2, 2); // self-edge: ignored
    EXPECT_EQ(graph.neighbours[0].at(1), 3u);
    EXPECT_EQ(graph.neighbours[1].at(0), 3u);
    EXPECT_TRUE(graph.neighbours[2].empty());
}

TEST(Partition, EveryMemberAppearsExactlyOnce)
{
    std::vector<std::string> descriptors;
    for (int p = 0; p < 7; ++p)
        for (int c = 0; c < 5; ++c)
            descriptors.push_back("Lp" + std::to_string(p) + "/C" + std::to_string(c) + ";");
    AffinityGraph graph = empty_graph(descriptors.size());
    for (uint32_t i = 0; i + 7 < descriptors.size(); ++i)
        graph.add_edge(i, i + 7);

    auto groups = partition_by_package_affinity(all_ids(descriptors.size()), graph, descriptors, 8);

    std::vector<uint32_t> seen;
    for (const auto &g : groups) {
        EXPECT_FALSE(g.empty());
        seen.insert(seen.end(), g.begin(), g.end());
    }
    std::sort(seen.begin(), seen.end());
    EXPECT_EQ(seen, all_ids(descriptors.size()));
}

TEST(Partition, NeverSplitsAPackage)
{
    // Packages of sizes 1..9; a small cap forces many bins, so a package that
    // could be split would be.
    std::vector<std::string> descriptors;
    for (int p = 1; p <= 9; ++p)
        for (int c = 0; c < p; ++c)
            descriptors.push_back("Lcom/app/p" + std::to_string(p) + "/C" + std::to_string(c) +
                                  ";");
    AffinityGraph graph = empty_graph(descriptors.size());
    for (uint32_t i = 1; i < descriptors.size(); ++i)
        graph.add_edge(i - 1, i); // a chain across package boundaries

    auto groups = partition_by_package_affinity(all_ids(descriptors.size()), graph, descriptors, 4);

    std::map<std::string, std::set<std::size_t>> groups_of_package;
    auto where = group_of(groups);
    for (auto [c, g] : where)
        groups_of_package[std::string(package_of(descriptors[c]))].insert(g);
    for (const auto &[package, in_groups] : groups_of_package)
        EXPECT_EQ(in_groups.size(), 1u) << package << " is split across groups";
}

TEST(Partition, PackageLargerThanCapIsAloneInItsGroup)
{
    // Lbig has 5 classes, cap is 3: Lbig gets a group of its own, whole.
    std::vector<std::string> descriptors = {"La/X;",   "Lbig/A;", "Lbig/B;", "Lbig/C;",
                                            "Lbig/D;", "Lbig/E;", "Lc/Y;"};
    AffinityGraph graph = empty_graph(descriptors.size());
    graph.add_edge(0, 1); // a - big
    graph.add_edge(5, 6); // big - c

    auto groups = partition_by_package_affinity(all_ids(descriptors.size()), graph, descriptors, 3);

    bool found = false;
    for (const auto &g : groups) {
        bool has_big = std::any_of(
            g.begin(), g.end(), [&](uint32_t c) { return package_of(descriptors[c]) == "Lbig"; });
        if (!has_big)
            continue;
        found = true;
        EXPECT_EQ(g.size(), 5u);
        for (uint32_t c : g)
            EXPECT_EQ(package_of(descriptors[c]), "Lbig");
    }
    EXPECT_TRUE(found);
}

TEST(Partition, SingleClassPackagesFillFullBins)
{
    // Ten unrelated one-class packages at cap 4: bins of 4, 4 and 2 — no
    // stranded single-class groups.
    std::vector<std::string> descriptors;
    for (int p = 0; p < 10; ++p)
        descriptors.push_back("Lp" + std::to_string(p) + "/C;");

    auto groups = partition_by_package_affinity(all_ids(10), empty_graph(10), descriptors, 4);

    ASSERT_EQ(groups.size(), 3u);
    EXPECT_EQ(groups[0].size(), 4u);
    EXPECT_EQ(groups[1].size(), 4u);
    EXPECT_EQ(groups[2].size(), 2u);
}

TEST(Partition, RelatedPackagesShareAGroup)
{
    // Two pairs of packages linked by heavy edges, nothing between the pairs.
    // With room for exactly one pair per group, each pair stays together.
    std::vector<std::string> descriptors = {"La/A1;", "La/A2;", "Lb/B1;", "Lb/B2;",
                                            "Lc/C1;", "Lc/C2;", "Ld/D1;", "Ld/D2;"};
    AffinityGraph graph = empty_graph(descriptors.size());
    graph.add_edge(0, 2, 10); // a - b
    graph.add_edge(1, 3, 10);
    graph.add_edge(4, 6, 10); // c - d
    graph.add_edge(5, 7, 10);

    auto groups = partition_by_package_affinity(all_ids(descriptors.size()), graph, descriptors, 4);

    ASSERT_EQ(groups.size(), 2u);
    auto where = group_of(groups);
    EXPECT_EQ(where[0], where[2]); // a with b
    EXPECT_EQ(where[4], where[6]); // c with d
    EXPECT_NE(where[0], where[4]);
}

TEST(Partition, IgnoresEdgesToNonMembers)
{
    // Class 2 is not a member: its edges must not pull 0 and 1 anywhere, and
    // it must not appear in the output.
    std::vector<std::string> descriptors = {"La/A;", "Lb/B;", "Lc/C;"};
    AffinityGraph graph = empty_graph(3);
    graph.add_edge(0, 2, 5);
    graph.add_edge(1, 2, 5);

    auto groups = partition_by_package_affinity({0, 1}, graph, descriptors, 10);

    ASSERT_EQ(groups.size(), 1u);
    EXPECT_EQ(std::set<uint32_t>(groups[0].begin(), groups[0].end()), (std::set<uint32_t>{0, 1}));
}

TEST(Partition, IsDeterministic)
{
    std::vector<std::string> descriptors;
    for (int p = 0; p < 20; ++p)
        for (int c = 0; c < 3; ++c)
            descriptors.push_back("Lp" + std::to_string(p) + "/C" + std::to_string(c) + ";");
    AffinityGraph graph = empty_graph(descriptors.size());
    for (uint32_t i = 0; i < descriptors.size(); ++i)
        graph.add_edge(i, static_cast<uint32_t>((i * 7 + 3) % descriptors.size()), 1 + i % 4);

    auto first = partition_by_package_affinity(all_ids(descriptors.size()), graph, descriptors, 9);
    auto second = partition_by_package_affinity(all_ids(descriptors.size()), graph, descriptors, 9);
    EXPECT_EQ(first, second);
}

TEST(Partition, EmptyMembersGiveNoGroups)
{
    EXPECT_TRUE(partition_by_package_affinity({}, empty_graph(0), {}, 10).empty());
}
