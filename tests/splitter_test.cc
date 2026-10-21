#include "splitter.hpp"

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <map>
#include <set>
#include <string>
#include <vector>

#include <gtest/gtest.h>

#include "dex.hpp"
#include "partition.hpp"
#include "test_apk.hpp"

namespace fs = std::filesystem;
using splitdex::package_of;
using splitdex::test::add_call;
using splitdex::test::make_class;
using splitdex::test::write_apk;

namespace {

/** Rows of a two-column CSV with a header line, split at the last comma. */
std::vector<std::pair<std::string, std::string>> read_csv(const fs::path &path)
{
    std::vector<std::pair<std::string, std::string>> rows;
    std::ifstream file(path);
    std::string line;
    std::getline(file, line); // header
    while (std::getline(file, line)) {
        auto comma = line.rfind(',');
        rows.emplace_back(line.substr(0, comma), line.substr(comma + 1));
    }
    return rows;
}

/** What one split wrote: the class -> file map and the file -> tier map. */
struct Output
{
    std::map<std::string, std::string> file_of; // class descriptor -> file
    std::map<std::string, std::string> tier_of; // file -> "business" | "framework"
    std::size_t index_rows = 0;

    std::set<std::string> files_of_tier(const std::string &tier) const
    {
        std::set<std::string> files;
        for (const auto &[file, t] : tier_of)
            if (t == tier)
                files.insert(file);
        return files;
    }
};

Output read_output(const fs::path &dir)
{
    Output out;
    auto index = read_csv(dir / "index.csv");
    out.index_rows = index.size();
    for (const auto &[cls, file] : index)
        out.file_of[cls] = file;
    for (const auto &[file, tier] : read_csv(dir / "tiers.csv"))
        out.tier_of[file] = tier;
    return out;
}

class SplitterTest : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        const auto *info = ::testing::UnitTest::GetInstance()->current_test_info();
        dir_ = fs::path(::testing::TempDir()) /
               (std::string("split-dex-") + info->test_suite_name() + "-" + info->name());
        fs::remove_all(dir_);
        fs::create_directories(dir_);
    }
    void TearDown() override { fs::remove_all(dir_); }

    fs::path apk() const { return dir_ / "app.apk"; }
    fs::path out() const { return dir_ / "out"; }

    splitdex::Options options(std::size_t business_cap = 1000) const
    {
        splitdex::Options o;
        o.apk_path = apk().string();
        o.package = "com.app.";
        o.out_dir = out().string();
        o.business_cap = business_cap;
        return o;
    }

    fs::path dir_;
};

/** A class declaring `count` native methods: enough of them overflow the 64K
 *  method limit of one DEX file. */
dex::model::ClassDef class_with_methods(std::string type, int count)
{
    auto cls = make_class(std::move(type));
    for (int i = 0; i < count; ++i) {
        dex::model::MethodDef method;
        method.name = "m" + std::to_string(i);
        method.proto = "()V";
        method.access = dex::AccessFlags::Public | dex::AccessFlags::Native;
        cls.methods.push_back(std::move(method));
    }
    return cls;
}

/** Three business packages and two source DEX files of framework classes. */
std::vector<std::vector<dex::model::ClassDef>> sample_app()
{
    std::vector<dex::model::ClassDef> first = {
        make_class("Lcom/app/a/A0;"),
        make_class("Lcom/app/a/A1;", "Lcom/app/a/A0;"),
        make_class("Lcom/app/a/A0$1;"),
        make_class("Lcom/app/b/B0;"),
        make_class("Lcom/app/b/B1;", "Lcom/app/b/B0;"),
        make_class("Lcom/app/c/C0;"),
        make_class("Landroidx/x/X0;"),
        make_class("Landroidx/x/X1;"),
    };
    std::vector<dex::model::ClassDef> second = {
        make_class("Lcom/app/c/C1;", "Lcom/app/c/C0;"),
        make_class("Lkotlin/K0;"),
        make_class("Lkotlin/K1;"),
    };
    return {first, second};
}

} // namespace

TEST_F(SplitterTest, EveryClassLandsInExactlyOneFile)
{
    auto app = sample_app();
    ASSERT_NO_FATAL_FAILURE(write_apk(apk(), app));

    auto result = splitdex::split(options());
    ASSERT_TRUE(result.has_value()) << result.error();

    Output output = read_output(out());
    std::size_t total = app[0].size() + app[1].size();
    EXPECT_EQ(result->total_classes, total);
    EXPECT_EQ(output.index_rows, total); // no class listed twice
    for (const auto &dex_file : app)
        for (const auto &cls : dex_file)
            EXPECT_TRUE(output.file_of.contains(cls.type)) << cls.type << " is missing";
}

TEST_F(SplitterTest, EachFileHoldsExactlyTheClassesTheIndexLists)
{
    ASSERT_NO_FATAL_FAILURE(write_apk(apk(), sample_app()));
    ASSERT_TRUE(splitdex::split(options()).has_value());

    Output output = read_output(out());
    std::map<std::string, std::set<std::string>> listed;
    for (const auto &[cls, file] : output.file_of)
        listed[file].insert(cls);

    ASSERT_EQ(listed.size(), output.tier_of.size());
    for (const auto &[file, classes] : listed) {
        auto dex = dex::model::read_file((out() / file).string());
        ASSERT_TRUE(dex.has_value()) << file << ": " << dex.error().message;
        std::set<std::string> actual;
        for (const auto &cls : dex->classes)
            actual.insert(cls.type);
        EXPECT_EQ(actual, classes) << file;
    }
}

TEST_F(SplitterTest, FrameworkKeepsOneFilePerSourceDex)
{
    ASSERT_NO_FATAL_FAILURE(write_apk(apk(), sample_app()));
    auto result = splitdex::split(options());
    ASSERT_TRUE(result.has_value()) << result.error();

    Output output = read_output(out());
    EXPECT_EQ(result->framework_dex, 2u);
    ASSERT_EQ(output.files_of_tier("framework").size(), 2u);
    // classes.dex's framework classes stay together, apart from classes2.dex's.
    EXPECT_EQ(output.file_of["Landroidx/x/X0;"], output.file_of["Landroidx/x/X1;"]);
    EXPECT_EQ(output.file_of["Lkotlin/K0;"], output.file_of["Lkotlin/K1;"]);
    EXPECT_NE(output.file_of["Landroidx/x/X0;"], output.file_of["Lkotlin/K0;"]);
    for (const auto &cls : {"Landroidx/x/X0;", "Lkotlin/K0;"})
        EXPECT_EQ(output.tier_of[output.file_of[cls]], "framework") << cls;
}

TEST_F(SplitterTest, BusinessClassesAreTaggedBusiness)
{
    ASSERT_NO_FATAL_FAILURE(write_apk(apk(), sample_app()));
    auto result = splitdex::split(options());
    ASSERT_TRUE(result.has_value()) << result.error();

    Output output = read_output(out());
    EXPECT_EQ(result->business_classes, 7u);
    for (const auto &[cls, file] : output.file_of) {
        bool business = cls.starts_with("Lcom/app/");
        EXPECT_EQ(output.tier_of[file], business ? "business" : "framework") << cls;
    }
}

TEST_F(SplitterTest, KeepsEachBusinessPackageInOneFile)
{
    // Many small packages, and a cap small enough to force many files.
    std::vector<dex::model::ClassDef> classes;
    for (int p = 0; p < 12; ++p)
        for (int c = 0; c < 1 + p % 4; ++c)
            classes.push_back(
                make_class("Lcom/app/p" + std::to_string(p) + "/C" + std::to_string(c) + ";"));
    ASSERT_NO_FATAL_FAILURE(write_apk(apk(), {classes}));

    auto result = splitdex::split(options(3));
    ASSERT_TRUE(result.has_value()) << result.error();
    EXPECT_GT(result->business_dex, 1u);

    Output output = read_output(out());
    std::map<std::string, std::set<std::string>> files_of_package;
    for (const auto &[cls, file] : output.file_of)
        files_of_package[std::string(package_of(cls))].insert(file);
    for (const auto &[package, files] : files_of_package)
        EXPECT_EQ(files.size(), 1u) << package << " is split across files";
}

TEST_F(SplitterTest, RelatedClassesShareAFile)
{
    // a and b reference each other through method calls; c and d through a
    // superclass.  Room for exactly one pair per file keeps each pair together.
    auto a = make_class("Lcom/app/a/A;");
    add_call(a, "Lcom/app/b/B;");
    auto b = make_class("Lcom/app/b/B;");
    add_call(b, "Lcom/app/a/A;");
    auto c = make_class("Lcom/app/c/C;");
    auto d = make_class("Lcom/app/d/D;", "Lcom/app/c/C;");
    ASSERT_NO_FATAL_FAILURE(write_apk(apk(), {{a, c, b, d}}));

    auto result = splitdex::split(options(2));
    ASSERT_TRUE(result.has_value()) << result.error();

    Output output = read_output(out());
    EXPECT_EQ(output.file_of["Lcom/app/a/A;"], output.file_of["Lcom/app/b/B;"]);
    EXPECT_EQ(output.file_of["Lcom/app/c/C;"], output.file_of["Lcom/app/d/D;"]);
    EXPECT_NE(output.file_of["Lcom/app/a/A;"], output.file_of["Lcom/app/c/C;"]);
}

TEST_F(SplitterTest, HotlistMovesTheWholePackageToFramework)
{
    ASSERT_NO_FATAL_FAILURE(write_apk(apk(), sample_app()));
    auto o = options();
    o.hotlist = {"com.app.a.A1"}; // one class of package com.app.a
    auto result = splitdex::split(o);
    ASSERT_TRUE(result.has_value()) << result.error();

    Output output = read_output(out());
    for (const auto &cls : {"Lcom/app/a/A0;", "Lcom/app/a/A1;", "Lcom/app/a/A0$1;"})
        EXPECT_EQ(output.tier_of[output.file_of[cls]], "framework") << cls;
    for (const auto &cls : {"Lcom/app/b/B0;", "Lcom/app/c/C1;"})
        EXPECT_EQ(output.tier_of[output.file_of[cls]], "business") << cls;
    EXPECT_EQ(result->business_classes, 4u);
}

TEST_F(SplitterTest, HotlistIgnoresUnknownAndFrameworkNames)
{
    ASSERT_NO_FATAL_FAILURE(write_apk(apk(), sample_app()));
    auto o = options();
    o.hotlist = {"com.app.nothing.Here", "androidx.x.X0"};
    auto result = splitdex::split(o);
    ASSERT_TRUE(result.has_value()) << result.error();
    EXPECT_EQ(result->business_classes, 7u);
}

TEST_F(SplitterTest, MergesFrameworkFilesDownToTheCap)
{
    std::vector<std::vector<dex::model::ClassDef>> app;
    for (int d = 0; d < 4; ++d)
        app.push_back({make_class("Llib" + std::to_string(d) + "/L;")});
    ASSERT_NO_FATAL_FAILURE(write_apk(apk(), app));

    auto o = options();
    o.framework_cap = 2;
    auto result = splitdex::split(o);
    ASSERT_TRUE(result.has_value()) << result.error();
    EXPECT_EQ(result->framework_dex, 2u);
    EXPECT_EQ(read_output(out()).files_of_tier("framework").size(), 2u);
}

TEST_F(SplitterTest, ReadsDexEntriesInMultidexOrder)
{
    // classes2.dex is written before classes.dex in the archive; the framework
    // file of classes.dex must still come first.
    dex::model::Dex first;
    first.classes = {make_class("Lfirst/F;")};
    dex::model::Dex second;
    second.classes = {make_class("Lsecond/S;")};
    auto first_bytes = dex::model::write(first);
    auto second_bytes = dex::model::write(second);
    ASSERT_TRUE(first_bytes && second_bytes);
    auto zip =
        splitdex::test::build_zip({{"classes2.dex", *second_bytes}, {"classes.dex", *first_bytes}});
    std::ofstream(apk(), std::ios::binary)
        .write(reinterpret_cast<const char *>(zip.data()),
               static_cast<std::streamsize>(zip.size()));

    ASSERT_TRUE(splitdex::split(options()).has_value());
    Output output = read_output(out());
    EXPECT_EQ(output.file_of["Lfirst/F;"], "0.dex");
    EXPECT_EQ(output.file_of["Lsecond/S;"], "1.dex");
}

TEST_F(SplitterTest, OverflowIsCutAtAPackageBoundary)
{
    // One group (cap 1000) of 70,000 methods, which does not fit one DEX file:
    // package a is one class of 40,000, package b three classes of 10,000.
    // The middle of the group (class 2 of 4) falls inside package b, so a cut
    // in the middle would split b; the cut must fall between a and b.
    ASSERT_NO_FATAL_FAILURE(write_apk(apk(), {{class_with_methods("Lcom/app/a/A;", 40000)},
                                              {class_with_methods("Lcom/app/b/B1;", 10000),
                                               class_with_methods("Lcom/app/b/B2;", 10000),
                                               class_with_methods("Lcom/app/b/B3;", 10000)}}));

    auto result = splitdex::split(options());
    ASSERT_TRUE(result.has_value()) << result.error();
    EXPECT_EQ(result->overflow_splits, 1u);
    EXPECT_EQ(result->business_dex, 2u);
    EXPECT_TRUE(result->split_packages.empty());

    Output output = read_output(out());
    EXPECT_NE(output.file_of["Lcom/app/a/A;"], output.file_of["Lcom/app/b/B1;"]);
    EXPECT_EQ(output.file_of["Lcom/app/b/B1;"], output.file_of["Lcom/app/b/B2;"]);
    EXPECT_EQ(output.file_of["Lcom/app/b/B1;"], output.file_of["Lcom/app/b/B3;"]);
}

TEST_F(SplitterTest, OverflowInsideOnePackageIsReported)
{
    // One package too big for one DEX file: it must be cut, and reported.
    ASSERT_NO_FATAL_FAILURE(write_apk(apk(), {{class_with_methods("Lcom/app/big/X;", 40000)},
                                              {class_with_methods("Lcom/app/big/Y;", 40000)}}));

    auto result = splitdex::split(options());
    ASSERT_TRUE(result.has_value()) << result.error();
    EXPECT_EQ(result->business_dex, 2u);
    EXPECT_EQ(result->split_packages, std::vector<std::string>{"Lcom/app/big"});
}

TEST_F(SplitterTest, KeepsOnlyTheFirstCopyOfADuplicatedClass)
{
    // Lcom/app/a/A; is defined in classes.dex and again in classes2.dex.
    // Android loads the first copy; it must be written once, from classes.dex.
    auto first = make_class("Lcom/app/a/A;");
    auto duplicate = make_class("Lcom/app/a/A;");
    add_call(duplicate, "Lcom/app/a/Other;"); // a different body: the copies differ
    ASSERT_NO_FATAL_FAILURE(write_apk(apk(), {{first}, {duplicate, make_class("Llib/L;")}}));

    auto result = splitdex::split(options());
    ASSERT_TRUE(result.has_value()) << result.error();
    EXPECT_EQ(result->duplicate_classes, 1u);
    EXPECT_EQ(result->total_classes, 2u);

    Output output = read_output(out());
    EXPECT_EQ(output.index_rows, 2u);
    auto dex = dex::model::read_file((out() / output.file_of["Lcom/app/a/A;"]).string());
    ASSERT_TRUE(dex.has_value()) << dex.error().message;
    const auto *kept = dex->find_class("Lcom/app/a/A;");
    ASSERT_NE(kept, nullptr);
    EXPECT_TRUE(kept->methods.empty()); // the copy from classes.dex
}

TEST_F(SplitterTest, ARerunRemovesStaleDexFilesOfAnEarlierRun)
{
    ASSERT_NO_FATAL_FAILURE(write_apk(apk(), sample_app()));
    // Cap 1: one file per business package, 3 + 2 framework = 5 files.
    ASSERT_TRUE(splitdex::split(options(1)).has_value());
    ASSERT_TRUE(fs::exists(out() / "4.dex"));
    std::ofstream(out() / "notes.txt") << "not ours";
    std::ofstream(out() / "07.dex") << "not a name split-dex writes";

    // Cap 1000: 1 + 2 = 3 files.  3.dex and 4.dex are left from the first run.
    auto result = splitdex::split(options());
    ASSERT_TRUE(result.has_value()) << result.error();

    std::set<std::string> on_disk;
    for (const auto &entry : fs::directory_iterator(out()))
        if (entry.path().extension() == ".dex")
            on_disk.insert(entry.path().filename().string());
    EXPECT_EQ(on_disk, (std::set<std::string>{"0.dex", "1.dex", "2.dex", "07.dex"}));
    EXPECT_EQ(read_output(out()).tier_of.size(), 3u);
    EXPECT_TRUE(fs::exists(out() / "notes.txt"));
}

TEST_F(SplitterTest, FailsOnAMissingApk)
{
    auto result = splitdex::split(options());
    ASSERT_FALSE(result.has_value());
    EXPECT_NE(result.error().find("apk not found"), std::string::npos) << result.error();
}

TEST_F(SplitterTest, FailsOnAnApkWithoutDex)
{
    auto zip = splitdex::test::build_zip({{"assets/readme.txt", {'h', 'i'}}});
    std::ofstream(apk(), std::ios::binary)
        .write(reinterpret_cast<const char *>(zip.data()),
               static_cast<std::streamsize>(zip.size()));

    auto result = splitdex::split(options());
    ASSERT_FALSE(result.has_value());
    EXPECT_NE(result.error().find("no classes*.dex"), std::string::npos) << result.error();
}

TEST_F(SplitterTest, RejectsZeroCaps)
{
    ASSERT_NO_FATAL_FAILURE(write_apk(apk(), sample_app()));
    auto o = options(0);
    EXPECT_FALSE(splitdex::split(o).has_value());
    o = options();
    o.framework_cap = 0;
    EXPECT_FALSE(splitdex::split(o).has_value());
}
