#include "splitter.hpp"

#include "partition.hpp"

#include <algorithm>
#include <charconv>
#include <filesystem>
#include <fstream>
#include <map>
#include <optional>
#include <queue>
#include <string>
#include <string_view>
#include <system_error>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include "dex.hpp"

namespace splitdex {

namespace {

namespace fs = std::filesystem;

/** A class read from the APK: its model definition, its descriptor, and the
 *  original classes*.dex it came from (an index in multidex order). */
struct LoadedClass
{
    dex::model::ClassDef def;
    std::string descriptor;
    int source_dex;
};

/** The multidex order key of a top-level DEX entry name: "classes.dex" is 1,
 *  "classesN.dex" is N (N >= 2); any other name returns nullopt.  This is the
 *  rule dexpp's analysis uses to load an APK, so both read the same files. */
std::optional<uint32_t> multidex_index(std::string_view name)
{
    if (name == "classes.dex")
        return 1;
    if (!name.starts_with("classes") || !name.ends_with(".dex"))
        return std::nullopt;
    std::string_view digits = name.substr(7, name.size() - 7 - 4);
    uint32_t n = 0;
    auto [end, ec] = std::from_chars(digits.data(), digits.data() + digits.size(), n);
    if (ec != std::errc{} || end != digits.data() + digits.size() || n < 2)
        return std::nullopt;
    return n;
}

/** Translate a binary class name ("com.a.B") to a type descriptor ("Lcom/a/B;"). */
std::string binary_to_descriptor(std::string_view binary)
{
    std::string descriptor = "L";
    for (char c : binary)
        descriptor += (c == '.') ? '/' : c;
    descriptor += ';';
    return descriptor;
}

/** Translate a package prefix ("com.myapp.") to a descriptor prefix
 *  ("Lcom/myapp/"). */
std::string package_prefix_descriptor(std::string_view package)
{
    std::string descriptor = "L";
    for (char c : package)
        descriptor += (c == '.') ? '/' : c;
    return descriptor;
}

} // namespace

std::expected<Result, std::string> split(const Options &options)
{
    if (options.business_cap < 1)
        return std::unexpected("business-cap must be >= 1");
    if (options.framework_cap < 1)
        return std::unexpected("framework-cap must be >= 1");
    if (!fs::exists(options.apk_path))
        return std::unexpected("apk not found: " + options.apk_path);

    const std::string package_desc = package_prefix_descriptor(options.package);

    // Read every classes*.dex, in multidex order.
    auto apk = dex::apk::Apk::open(options.apk_path);
    if (!apk)
        return std::unexpected("cannot open apk: " + options.apk_path);

    // A name listed twice in the archive is read once, as dexpp's analysis
    // does.  Ties on the index ("classes2.dex" vs "classes02.dex") are broken
    // by name.
    std::vector<std::pair<uint32_t, std::string>> dex_entries;
    std::unordered_set<std::string> seen_entries;
    for (const std::string &entry : apk->entries())
        if (auto idx = multidex_index(entry); idx && seen_entries.insert(entry).second)
            dex_entries.push_back({*idx, entry});
    std::sort(dex_entries.begin(), dex_entries.end());
    if (dex_entries.empty())
        return std::unexpected("apk has no classes*.dex: " + options.apk_path);

    // A class defined in more than one DEX file is kept once: the first copy
    // in multidex order, which is the one Android loads.  Keeping both would
    // put one class in two output files.
    Result result;
    std::vector<LoadedClass> classes;
    std::unordered_set<std::string> seen_classes;
    uint32_t version = 35;
    for (int source_dex = 0; source_dex < static_cast<int>(dex_entries.size()); ++source_dex) {
        const std::string &entry = dex_entries[source_dex].second;
        auto bytes = apk->read(entry);
        if (!bytes)
            return std::unexpected("cannot read apk entry: " + entry);
        auto dex = dex::model::read(*bytes);
        if (!dex)
            return std::unexpected(entry + ": " + dex.error().message);
        version = std::max(version, dex->version);
        for (dex::model::ClassDef &cls : dex->classes) {
            if (!seen_classes.insert(cls.type).second) {
                ++result.duplicate_classes;
                continue;
            }
            std::string descriptor = cls.type;
            classes.push_back({std::move(cls), std::move(descriptor), source_dex});
        }
    }
    const std::size_t class_count = classes.size();
    std::unordered_map<std::string_view, uint32_t> index_of; // descriptor -> class id
    index_of.reserve(class_count * 2);
    for (uint32_t i = 0; i < class_count; ++i)
        index_of.emplace(classes[i].descriptor, i);

    // The packages pulled into the framework tier by the hotlist: the package
    // of every hot class that is itself business.  Moving the whole package
    // (not just the named class) keeps packages unsplit across the tier
    // boundary.
    std::unordered_set<std::string> hot_packages;
    for (const std::string &binary : options.hotlist) {
        std::string descriptor = binary_to_descriptor(binary);
        auto it = index_of.find(descriptor);
        if (it == index_of.end())
            continue;
        if (!classes[it->second].descriptor.starts_with(package_desc))
            continue;
        hot_packages.emplace(package_of(classes[it->second].descriptor));
    }

    // Label each class.  Business = under the app package and not in a hot
    // package; everything else is framework.
    std::vector<bool> is_business(class_count, false);
    std::size_t business_count = 0;
    for (uint32_t i = 0; i < class_count; ++i) {
        std::string_view descriptor = classes[i].descriptor;
        bool business = descriptor.starts_with(package_desc) &&
                        !hot_packages.contains(std::string(package_of(descriptor)));
        if (business) {
            is_business[i] = true;
            ++business_count;
        }
    }

    // Affinity between business classes: a class and its superclass or an
    // interface it implements, a class and its top-level enclosing class, or a
    // method/field/type reference from one to the other.  Edges never cross
    // the tier boundary, so framework classes do not pull business classes
    // together.  The analysis reuses the archive already loaded above.
    auto ctx = apk->analysis();
    if (!ctx)
        return std::unexpected("analysis failed on apk: " + options.apk_path);

    AffinityGraph graph{std::vector<std::unordered_map<uint32_t, uint32_t>>(class_count)};
    auto link = [&](uint32_t a, std::string_view to_descriptor) {
        auto it = index_of.find(to_descriptor);
        if (it == index_of.end())
            return;
        uint32_t b = it->second;
        if (b == a || !is_business[a] || !is_business[b])
            return;
        graph.add_edge(a, b);
    };
    for (const dex::Class &cls : ctx->classes()) {
        auto found = index_of.find(cls.name());
        if (found == index_of.end())
            continue;
        uint32_t a = found->second;
        if (!is_business[a])
            continue;
        if (auto super = cls.superclass())
            link(a, super->descriptor());
        for (const dex::ClassRef &iface : cls.interfaces())
            link(a, iface.descriptor());
        std::string_view descriptor = cls.name();
        if (auto dollar = descriptor.find('$'); dollar != std::string_view::npos)
            link(a, std::string(descriptor.substr(0, dollar)) + ';');
        for (const dex::Method &method : cls.methods()) {
            for (const dex::MethodCall &call : method.calls())
                link(a, call.target.class_descriptor);
            for (const dex::FieldAccess &access : method.field_accesses())
                link(a, access.field.class_descriptor);
            for (const dex::TypeUse &use : method.type_uses())
                link(a, use.descriptor);
        }
    }

    // Business members and framework groups (one per source DEX).
    std::vector<uint32_t> business_members;
    std::map<int, std::vector<uint32_t>> framework_by_source;
    std::vector<std::string> descriptors(class_count);
    for (uint32_t i = 0; i < class_count; ++i) {
        descriptors[i] = classes[i].descriptor;
        if (is_business[i])
            business_members.push_back(i);
        else
            framework_by_source[classes[i].source_dex].push_back(i);
    }

    std::vector<std::vector<uint32_t>> business_groups =
        partition_by_package_affinity(business_members, graph, descriptors, options.business_cap);

    // Framework keeps the app's layout: one DEX per source DEX.  Only if the
    // app has more source DEX than the ceiling are the smallest merged down to
    // it (rare; most apps have a handful of DEX).
    std::vector<std::vector<uint32_t>> framework_groups;
    for (auto &[source, members] : framework_by_source)
        framework_groups.push_back(std::move(members));
    if (framework_groups.size() > options.framework_cap) {
        std::sort(framework_groups.begin(), framework_groups.end(),
                  [](const auto &a, const auto &b) { return a.size() > b.size(); });
        std::vector<std::vector<uint32_t>> merged(options.framework_cap);
        using BinSize = std::pair<std::size_t, std::size_t>; // (size, bin index), min-first
        std::priority_queue<BinSize, std::vector<BinSize>, std::greater<>> smallest;
        for (std::size_t b = 0; b < options.framework_cap; ++b)
            smallest.push({0, b});
        for (auto &group : framework_groups) {
            auto [size, b] = smallest.top();
            smallest.pop();
            merged[b].insert(merged[b].end(), group.begin(), group.end());
            smallest.push({size + group.size(), b});
        }
        framework_groups = std::move(merged);
    }

    // Emit each group as a standalone DEX, writing its map rows.  A group that
    // overflows the 64K reference limit is cut in two (see cut_point) and each
    // part is written again.
    std::error_code ec;
    fs::create_directories(options.out_dir, ec);
    if (ec)
        return std::unexpected("cannot create output directory: " + options.out_dir);
    const fs::path out_dir = options.out_dir;

    std::ofstream index_file(out_dir / "index.csv");
    std::ofstream tiers_file(out_dir / "tiers.csv");
    if (!index_file || !tiers_file)
        return std::unexpected("cannot write maps in: " + options.out_dir);
    index_file << "class,file\n";
    tiers_file << "file,tier\n";

    result.total_classes = class_count;
    result.business_classes = business_count;
    result.framework_classes = class_count - business_count;

    // Where to cut a group in two when it overflows the 64K limit.  A business
    // group holds whole packages one after another, so it is cut at the package
    // boundary nearest its middle and no package is split.  Only a group that
    // is a single package is cut inside it, and that package is recorded.
    std::unordered_set<std::string> split_packages;
    auto cut_point = [&](const std::vector<uint32_t> &group, bool business_tier) {
        const std::size_t half = group.size() / 2;
        if (!business_tier)
            return half;
        auto distance = [&](std::size_t i) { return i > half ? i - half : half - i; };
        std::size_t best = 0; // 0: no package boundary in the group
        for (std::size_t i = 1; i < group.size(); ++i) {
            bool boundary = package_of(classes[group[i]].descriptor) !=
                            package_of(classes[group[i - 1]].descriptor);
            if (boundary && (best == 0 || distance(i) < distance(best)))
                best = i;
        }
        if (best == 0) {
            split_packages.emplace(package_of(classes[group.front()].descriptor));
            return half;
        }
        return best;
    };

    std::size_t file_no = 0;
    std::vector<std::vector<uint32_t>> stack;
    auto emit = [&](std::vector<uint32_t> members,
                    bool business_tier) -> std::optional<std::string> {
        stack.clear();
        stack.push_back(std::move(members));
        while (!stack.empty()) {
            std::vector<uint32_t> group = std::move(stack.back());
            stack.pop_back();

            dex::model::Dex dex;
            dex.version = version;
            for (uint32_t id : group)
                dex.classes.push_back(classes[id].def);

            auto out = dex::model::write(dex);
            if (!out) {
                if (out.error().kind == dex::model::Error::Kind::TooLarge && group.size() > 1) {
                    ++result.overflow_splits;
                    auto cut = static_cast<long>(cut_point(group, business_tier));
                    stack.emplace_back(group.begin() + cut, group.end());
                    stack.emplace_back(group.begin(), group.begin() + cut);
                    continue;
                }
                return "write failed: " + out.error().message;
            }

            std::string file = std::to_string(file_no) + ".dex";
            std::ofstream dex_file(out_dir / file, std::ios::binary);
            dex_file.write(reinterpret_cast<const char *>(out->data()),
                           static_cast<std::streamsize>(out->size()));
            if (!dex_file)
                return "cannot write DEX: " + (out_dir / file).string();
            for (uint32_t id : group)
                index_file << classes[id].descriptor << ',' << file << '\n';
            tiers_file << file << ',' << (business_tier ? "business" : "framework") << '\n';
            result.total_bytes += out->size();
            ++file_no;
        }
        return std::nullopt;
    };

    for (auto &group : business_groups)
        if (auto error = emit(std::move(group), true))
            return std::unexpected(*error);
    result.business_dex = file_no;
    for (auto &group : framework_groups) {
        if (group.empty())
            continue;
        if (auto error = emit(std::move(group), false))
            return std::unexpected(*error);
    }
    result.framework_dex = file_no - result.business_dex;
    result.split_packages.assign(split_packages.begin(), split_packages.end());
    std::sort(result.split_packages.begin(), result.split_packages.end());

    // An earlier, bigger run into the same directory may have left `<k>.dex`
    // files past the last one written now.  Remove them, so that every DEX
    // file in the directory is one that index.csv lists.  Only names this tool
    // writes are touched.
    std::vector<fs::path> stale;
    for (const fs::directory_entry &entry : fs::directory_iterator(out_dir, ec)) {
        const std::string name = entry.path().filename().string();
        if (!name.ends_with(".dex"))
            continue;
        std::string_view digits(name.data(), name.size() - 4);
        std::size_t number = 0;
        auto [end, parse_ec] =
            std::from_chars(digits.data(), digits.data() + digits.size(), number);
        bool written_name = parse_ec == std::errc{} && end == digits.data() + digits.size() &&
                            std::to_string(number) == digits; // "7.dex", never "07.dex"
        if (written_name && number >= file_no)
            stale.push_back(entry.path());
    }
    for (const fs::path &path : stale)
        fs::remove(path, ec);

    return result;
}

} // namespace splitdex
