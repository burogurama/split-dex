#pragma once

#include <cstddef>
#include <expected>
#include <string>
#include <vector>

namespace splitdex {

/** What to split and how. */
struct Options
{
    /** Input APK; every top-level classes*.dex entry is read. */
    std::string apk_path;
    /** The app's package, e.g. "com.myapp.".  A class is *business* if its
     *  descriptor is under this package ("Lcom/myapp/...;"); everything else
     *  (AndroidX, Kotlin, libraries) is *framework*.  Read straight off the
     *  descriptor, so it assumes the app is not obfuscated. */
    std::string package;
    /** Output directory; created if missing. */
    std::string out_dir;
    /** Target classes per business DEX group.  Larger means fewer, fuller
     *  files and less duplicated symbols. */
    std::size_t business_cap = 1000;
    /** Ceiling on the number of framework DEX files.  Framework is normally one
     *  DEX per original source DEX; only if the app has more source DEX than
     *  this are the smallest merged together down to the ceiling. */
    std::size_t framework_cap = 16;
    /** Binary class names (e.g. "com.myapp.Startup") whose *whole package* is
     *  moved into the framework tier.  Use it to keep hot startup code in the
     *  base DEX.  The package moves as a unit, so no package is split across
     *  the business/framework boundary. */
    std::vector<std::string> hotlist;
};

/** What the split produced. */
struct Result
{
    std::size_t total_classes = 0;
    std::size_t business_classes = 0;
    std::size_t framework_classes = 0;
    std::size_t business_dex = 0;
    std::size_t framework_dex = 0;
    /** How many groups were split again because they exceeded the 64K DEX
     *  reference limit.  A business group is cut at a package boundary. */
    std::size_t overflow_splits = 0;
    /** Packages (e.g. "Lcom/myapp/big") too big for one DEX file, which had to
     *  be split across business files.  Classes of such a package can no
     *  longer rely on package-private access between files.  Normally empty. */
    std::vector<std::string> split_packages;
    /** Later copies of classes defined in more than one DEX file, which were
     *  skipped (the first copy in multidex order is kept). */
    std::size_t duplicate_classes = 0;
    /** Total size of every written DEX, in bytes. */
    std::size_t total_bytes = 0;
};

/** Split the APK's classes into grouped DEX files under `options.out_dir`.
 *
 *  The output is:
 *   - `<k>.dex` — the grouped DEX files, each a valid standalone DEX.
 *   - `index.csv` — `class,file`: every class and the DEX it landed in.
 *   - `tiers.csv` — `file,tier`: whether each DEX is business or framework.
 *
 *  Business classes are grouped by package affinity at `business_cap`
 *  (see partition_by_package_affinity).  Framework classes keep the app's own
 *  layout: one DEX per original source DEX, so the Android class loader loads
 *  them normally.  Every class appears in exactly one DEX.
 *
 *  Returns the counts, or an error message if the APK cannot be read or a DEX
 *  cannot be written. */
std::expected<Result, std::string> split(const Options &options);

} // namespace splitdex
