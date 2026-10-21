#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace splitdex {

/** Sentinel package id for a class that is not among the partitioned members. */
inline constexpr uint32_t kNoPackage = ~0u;

/** An undirected, weighted affinity graph over a set of classes.
 *
 *  Each class is a dense id in `[0, size())`.  `neighbours[a]` maps a
 *  neighbour id to the weight of the edge `a—b` (how many relationships link
 *  the two classes: a shared supertype, a nesting link, a method reference).
 *  The graph is symmetric: an edge is stored on both endpoints. */
struct AffinityGraph
{
    std::vector<std::unordered_map<uint32_t, uint32_t>> neighbours;

    std::size_t size() const { return neighbours.size(); }

    /** Add `weight` to the edge `a—b` on both endpoints.  A self-edge
     *  (`a == b`) is ignored. */
    void add_edge(uint32_t a, uint32_t b, uint32_t weight = 1);
};

/** Partition `members` into groups of about `cap` classes, keeping related
 *  classes together and never splitting a Java package across two groups.
 *
 *  The method is a locality-preserving linearization, then a chunk, at the
 *  granularity of whole packages:
 *
 *   1. Group the members by package (the class descriptor up to its last
 *      '/').  A package is the unit that moves and is never split.
 *   2. Sum the class-edge weights of `graph` onto package pairs, then order
 *      the packages so related ones are adjacent — a best-first (Prim-like)
 *      walk that always steps along the heaviest available package edge.
 *   3. Fill bins to about `cap` classes in that order, emitting each package
 *      whole.  A package larger than `cap` becomes its own bin.
 *
 *  Why packages are never split: in a loader that serves each group from its
 *  own class loader, two classes of one package held by different loaders
 *  cannot use package-private (default) access — ART throws
 *  IllegalAccessError.  Keeping a package whole keeps that access inside one
 *  group.
 *
 *  The result has no empty groups and no stranded single-class groups (isolated
 *  packages sort last and are chunked together).  It is deterministic: the same
 *  inputs always give the same groups.  Ties are broken by the order of
 *  `members`, never by hash-map iteration order.
 *
 *  @param members      ids of the classes to partition (a subset of the graph).
 *  @param graph        affinity over all classes; only edges between two members
 *                      are used.
 *  @param descriptors  descriptor of every class id (e.g. "Lcom/a/B;"); indexed
 *                      by id, so it must cover every member.
 *  @param cap          target classes per group; must be >= 1. */
std::vector<std::vector<uint32_t>>
partition_by_package_affinity(const std::vector<uint32_t> &members, const AffinityGraph &graph,
                              const std::vector<std::string> &descriptors, std::size_t cap);

/** The package of a class descriptor: the substring up to its last '/', e.g.
 *  "Lcom/a/B;" -> "Lcom/a".  A descriptor with no '/' (a class in the
 *  unnamed package) maps to "L".  The result is a view into `descriptor`. */
std::string_view package_of(std::string_view descriptor);

} // namespace splitdex
