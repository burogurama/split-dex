// split-dex: split an APK's classes into grouped DEX files.
//
// The app's own ("business") classes — those under a given package — are
// grouped by static co-load affinity (shared supertypes, nesting, method and
// field references) into DEX files of a chosen size, keeping each Java package
// whole.  Framework/library classes keep the app's original DEX layout.  Every
// class ends up in exactly one DEX, and a map records which.
//
// It builds on the dexpp library for DEX reading, writing and analysis.

#include <cstdio>
#include <cstdlib>
#include <expected>
#include <fstream>
#include <string>
#include <vector>

#include "splitter.hpp"
#include "version.hpp"

namespace {

void print_usage(std::FILE *out, const char *program)
{
    std::fprintf(out,
                 "split-dex %s - split an APK's classes into grouped DEX files.\n\n"
                 "Usage:\n"
                 "  %s --apk <app.apk> --package <com.myapp.> --out <dir>\n"
                 "     [--business-cap N] [--framework-cap N] [--hotlist <file>]\n\n"
                 "Required:\n"
                 "  --apk FILE         input APK (its classes*.dex are read)\n"
                 "  --package PREFIX   the app's package, e.g. com.myapp.  A class is\n"
                 "                     'business' if its descriptor is under this package,\n"
                 "                     everything else is framework.  Assumes the app is\n"
                 "                     not obfuscated.\n"
                 "  --out DIR          output directory\n\n"
                 "Options:\n"
                 "  --business-cap N   target classes per business DEX group    [1000]\n"
                 "  --framework-cap N  ceiling on framework DEX files           [16]\n"
                 "  --hotlist FILE     file of binary class names (one per line) whose\n"
                 "                     whole package is kept in the framework/base tier\n"
                 "  -h, --help         show this help\n\n"
                 "Output in <dir>:\n"
                 "  <k>.dex            the grouped DEX files\n"
                 "  index.csv          class,file  (every class -> its DEX)\n"
                 "  tiers.csv          file,tier   (business|framework per DEX)\n",
                 SPLITDEX_VERSION, program);
}

[[noreturn]] void fail_missing_value(const char *flag)
{
    std::fprintf(stderr, "split-dex: %s needs a value\n", flag);
    std::exit(2);
}

/** Read a hotlist file: one binary class name per line, blank lines skipped. */
std::expected<std::vector<std::string>, std::string> read_hotlist(const std::string &path)
{
    std::ifstream file(path);
    if (!file)
        return std::unexpected("hotlist not found: " + path);
    std::vector<std::string> names;
    std::string line;
    while (std::getline(file, line)) {
        if (!line.empty() && line.back() == '\r')
            line.pop_back();
        if (!line.empty())
            names.push_back(line);
    }
    return names;
}

} // namespace

int main(int argc, char **argv)
{
    splitdex::Options options;
    std::string hotlist_path;

    auto next_value = [&](int &i) -> std::string {
        if (i + 1 >= argc)
            fail_missing_value(argv[i]);
        return argv[++i];
    };

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "-h" || arg == "--help") {
            print_usage(stdout, argv[0]);
            return 0;
        }
        else if (arg == "--apk")
            options.apk_path = next_value(i);
        else if (arg == "--package")
            options.package = next_value(i);
        else if (arg == "--out")
            options.out_dir = next_value(i);
        else if (arg == "--business-cap")
            options.business_cap = std::strtoull(next_value(i).c_str(), nullptr, 10);
        else if (arg == "--framework-cap")
            options.framework_cap = std::strtoull(next_value(i).c_str(), nullptr, 10);
        else if (arg == "--hotlist")
            hotlist_path = next_value(i);
        else {
            std::fprintf(stderr, "split-dex: unknown argument '%s' (see --help)\n", arg.c_str());
            return 2;
        }
    }

    bool missing = false;
    auto require = [&](const std::string &value, const char *name) {
        if (value.empty()) {
            std::fprintf(stderr, "split-dex: missing required --%s\n", name);
            missing = true;
        }
    };
    require(options.apk_path, "apk");
    require(options.package, "package");
    require(options.out_dir, "out");
    if (missing) {
        print_usage(stderr, argv[0]);
        return 2;
    }

    if (!hotlist_path.empty()) {
        auto names = read_hotlist(hotlist_path);
        if (!names) {
            std::fprintf(stderr, "split-dex: %s\n", names.error().c_str());
            return 1;
        }
        options.hotlist = std::move(*names);
    }

    auto result = splitdex::split(options);
    if (!result) {
        std::fprintf(stderr, "split-dex: %s\n", result.error().c_str());
        return 1;
    }

    std::printf("total_classes=%zu business_classes=%zu framework_classes=%zu\n",
                result->total_classes, result->business_classes, result->framework_classes);
    std::printf("business_dex=%zu framework_dex=%zu overflow_splits=%zu duplicate_classes=%zu\n",
                result->business_dex, result->framework_dex, result->overflow_splits,
                result->duplicate_classes);
    std::printf("total_dex=%zu total_mb=%.1f\n", result->business_dex + result->framework_dex,
                result->total_bytes / 1048576.0);
    for (const std::string &package : result->split_packages)
        std::fprintf(stderr,
                     "split-dex: warning: package %s is too big for one DEX file and was split "
                     "across files; package-private access between those files will fail\n",
                     package.c_str());
    return 0;
}
