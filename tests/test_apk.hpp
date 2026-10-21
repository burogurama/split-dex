#pragma once

// Builds small APKs for tests, from classes defined in memory with the dexpp
// model.  An APK here is a ZIP archive of classes*.dex entries; the entries are
// stored (not compressed) and carry correct CRC-32 values, so the archive is
// valid for any zip tool.

#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <optional>
#include <span>
#include <string>
#include <utility>
#include <vector>

#include <gtest/gtest.h>

#include "dex.hpp"

namespace splitdex::test {

inline uint32_t crc32(std::span<const uint8_t> data)
{
    static const std::array<uint32_t, 256> table = [] {
        std::array<uint32_t, 256> t{};
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t c = i;
            for (int k = 0; k < 8; ++k)
                c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            t[i] = c;
        }
        return t;
    }();
    uint32_t crc = 0xFFFFFFFFu;
    for (uint8_t byte : data)
        crc = table[(crc ^ byte) & 0xFF] ^ (crc >> 8);
    return crc ^ 0xFFFFFFFFu;
}

/** A ZIP archive of stored entries, in the given order. */
inline std::vector<uint8_t>
build_zip(const std::vector<std::pair<std::string, std::vector<uint8_t>>> &entries)
{
    std::vector<uint8_t> zip;
    auto put16 = [&](uint16_t v) {
        zip.push_back(static_cast<uint8_t>(v & 0xFF));
        zip.push_back(static_cast<uint8_t>(v >> 8));
    };
    auto put32 = [&](uint32_t v) {
        put16(static_cast<uint16_t>(v & 0xFFFF));
        put16(static_cast<uint16_t>(v >> 16));
    };

    std::vector<uint32_t> offsets;
    for (const auto &[name, data] : entries) {
        offsets.push_back(static_cast<uint32_t>(zip.size()));
        put32(0x04034b50); // local file header
        put16(20);         // version needed
        put16(0);          // flags
        put16(0);          // method: stored
        put32(0);          // time and date
        put32(crc32(data));
        put32(static_cast<uint32_t>(data.size())); // compressed size
        put32(static_cast<uint32_t>(data.size())); // uncompressed size
        put16(static_cast<uint16_t>(name.size()));
        put16(0); // extra length
        zip.insert(zip.end(), name.begin(), name.end());
        zip.insert(zip.end(), data.begin(), data.end());
    }

    const auto directory_offset = static_cast<uint32_t>(zip.size());
    for (std::size_t i = 0; i < entries.size(); ++i) {
        const auto &[name, data] = entries[i];
        put32(0x02014b50); // central directory header
        put16(20);         // version made by
        put16(20);         // version needed
        put16(0);          // flags
        put16(0);          // method: stored
        put32(0);          // time and date
        put32(crc32(data));
        put32(static_cast<uint32_t>(data.size()));
        put32(static_cast<uint32_t>(data.size()));
        put16(static_cast<uint16_t>(name.size()));
        put16(0); // extra length
        put16(0); // comment length
        put16(0); // disk number
        put16(0); // internal attributes
        put32(0); // external attributes
        put32(offsets[i]);
        zip.insert(zip.end(), name.begin(), name.end());
    }
    const auto directory_size = static_cast<uint32_t>(zip.size() - directory_offset);

    put32(0x06054b50); // end of central directory
    put16(0);          // this disk
    put16(0);          // disk with the central directory
    put16(static_cast<uint16_t>(entries.size()));
    put16(static_cast<uint16_t>(entries.size()));
    put32(directory_size);
    put32(directory_offset);
    put16(0); // comment length
    return zip;
}

/** A public class with no members.  `superclass` defaults to Object. */
inline dex::model::ClassDef make_class(std::string type,
                                       std::string superclass = "Ljava/lang/Object;",
                                       std::vector<std::string> interfaces = {})
{
    dex::model::ClassDef cls;
    cls.type = std::move(type);
    cls.access = dex::AccessFlags::Public;
    cls.superclass = std::move(superclass);
    cls.interfaces = std::move(interfaces);
    return cls;
}

/** Give `cls` a static method `run()V` whose body calls the static method
 *  `target_class->go()V` — a method reference from one class to another. */
inline void add_call(dex::model::ClassDef &cls, const std::string &target_class)
{
    dex::model::Code code;
    code.items.emplace_back(dex::model::Insn{dex::model::Invoke{
        dex::model::Invoke::Kind::Static, {}, dex::MethodId{target_class, "go", "()V"}}});
    code.items.emplace_back(dex::model::Insn{dex::model::ReturnVoid{}});

    dex::model::MethodDef method;
    method.name = "run";
    method.proto = "()V";
    method.access = dex::AccessFlags::Public | dex::AccessFlags::Static;
    method.code = std::move(code);
    cls.methods.push_back(std::move(method));
}

/** Write each DEX model as the next classes*.dex entry (classes.dex,
 *  classes2.dex, ...) of an APK at `path`. */
inline void write_apk(const std::filesystem::path &path,
                      const std::vector<std::vector<dex::model::ClassDef>> &dex_files)
{
    std::vector<std::pair<std::string, std::vector<uint8_t>>> entries;
    for (std::size_t i = 0; i < dex_files.size(); ++i) {
        dex::model::Dex dex;
        dex.classes = dex_files[i];
        auto bytes = dex::model::write(dex);
        ASSERT_TRUE(bytes.has_value()) << bytes.error().message;
        std::string name = i == 0 ? "classes.dex" : "classes" + std::to_string(i + 1) + ".dex";
        entries.emplace_back(std::move(name), std::move(*bytes));
    }
    std::vector<uint8_t> zip = build_zip(entries);
    std::ofstream(path, std::ios::binary)
        .write(reinterpret_cast<const char *>(zip.data()),
               static_cast<std::streamsize>(zip.size()));
}

} // namespace splitdex::test
