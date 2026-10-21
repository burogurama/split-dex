#include "partition.hpp"

#include <algorithm>
#include <numeric>
#include <queue>
#include <string_view>

namespace splitdex {

void AffinityGraph::add_edge(uint32_t a, uint32_t b, uint32_t weight)
{
    if (a == b)
        return;
    neighbours[a][b] += weight;
    neighbours[b][a] += weight;
}

std::string_view package_of(std::string_view descriptor)
{
    auto slash = descriptor.rfind('/');
    return slash == std::string_view::npos ? std::string_view("L") : descriptor.substr(0, slash);
}

std::vector<std::vector<uint32_t>>
partition_by_package_affinity(const std::vector<uint32_t> &members, const AffinityGraph &graph,
                              const std::vector<std::string> &descriptors, std::size_t cap)
{
    if (cap < 1)
        cap = 1;

    // Map each member to a package id; collect each package's classes.  A
    // class's package id is also stored per class id so the affinity sum below
    // can look it up in O(1).
    std::unordered_map<std::string_view, uint32_t> package_id;
    std::vector<std::vector<uint32_t>> package_classes;
    std::vector<uint32_t> class_package(graph.size(), kNoPackage);
    for (uint32_t c : members) {
        auto [it, inserted] = package_id.try_emplace(package_of(descriptors[c]),
                                                     static_cast<uint32_t>(package_classes.size()));
        if (inserted)
            package_classes.emplace_back();
        package_classes[it->second].push_back(c);
        class_package[c] = it->second;
    }
    const std::size_t package_count = package_classes.size();

    // Package-level affinity: sum class-edge weights between distinct packages.
    std::vector<std::unordered_map<uint32_t, uint32_t>> package_adj(package_count);
    for (uint32_t c : members) {
        uint32_t pc = class_package[c];
        for (auto [neighbour, weight] : graph.neighbours[c]) {
            uint32_t pn = class_package[neighbour];
            if (pn != kNoPackage && pn != pc)
                package_adj[pc][pn] += weight;
        }
    }

    // Order packages so related ones are adjacent: a best-first (Prim-like)
    // walk that always steps along the heaviest available package edge.  Seeds
    // are taken by descending degree (then ascending id) so the walk starts
    // from the most-connected packages and the order is deterministic.
    std::vector<uint32_t> seeds(package_count);
    std::iota(seeds.begin(), seeds.end(), 0u);
    std::sort(seeds.begin(), seeds.end(), [&](uint32_t a, uint32_t b) {
        std::size_t da = package_adj[a].size(), db = package_adj[b].size();
        return da != db ? da > db : a < b;
    });

    std::vector<uint32_t> order;
    order.reserve(package_count);
    std::vector<char> visited(package_count, 0);
    using HeapItem = std::pair<uint32_t, uint32_t>; // (edge weight, package id), max-first
    std::priority_queue<HeapItem> heap;
    for (uint32_t seed : seeds) {
        if (visited[seed])
            continue;
        heap.push({~0u, seed});
        while (!heap.empty()) {
            auto [weight, p] = heap.top();
            heap.pop();
            if (visited[p])
                continue;
            visited[p] = 1;
            order.push_back(p);
            for (auto [neighbour, edge_weight] : package_adj[p])
                if (!visited[neighbour])
                    heap.push({edge_weight, neighbour});
        }
    }

    // Fill bins to about `cap` classes in that order; each package whole.  A
    // package at least `cap` classes large ends the current bin after it, so an
    // oversized package becomes its own bin.
    std::vector<std::vector<uint32_t>> groups;
    std::vector<uint32_t> current;
    for (uint32_t p : order) {
        const auto &classes = package_classes[p];
        if (!current.empty() && current.size() + classes.size() > cap) {
            groups.push_back(std::move(current));
            current.clear();
        }
        current.insert(current.end(), classes.begin(), classes.end());
        if (current.size() >= cap) {
            groups.push_back(std::move(current));
            current.clear();
        }
    }
    if (!current.empty())
        groups.push_back(std::move(current));
    return groups;
}

} // namespace splitdex
