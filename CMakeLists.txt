cmake_minimum_required(VERSION 3.18)

# The version lives in src/version.hpp.
file(STRINGS src/version.hpp SPLITDEX_VERSION_LINE REGEX "^#define SPLITDEX_VERSION \"")
string(REGEX REPLACE "^#define SPLITDEX_VERSION \"([^\"]+)\".*" "\\1" SPLITDEX_VERSION
       "${SPLITDEX_VERSION_LINE}")
project(split-dex VERSION ${SPLITDEX_VERSION} LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# An unoptimized build is about five times slower on a large APK, so build
# Release unless a build type is given (single-config generators only).
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

option(SPLITDEX_BUILD_TESTS "Build the unit tests" ON)
set(SPLITDEX_SANITIZE "" CACHE STRING
    "Sanitizers to build everything with, e.g. 'address,undefined' (empty = off)")

# Sanitizer instrumentation is applied directory-wide (before any target is
# declared, and before dexpp is added) so split-dex, dexpp and the test
# framework are all built consistently.
if(SPLITDEX_SANITIZE)
    add_compile_options(-fsanitize=${SPLITDEX_SANITIZE}
                        -fno-sanitize-recover=all -fno-omit-frame-pointer -g)
    add_link_options(-fsanitize=${SPLITDEX_SANITIZE})
endif()

include(FetchContent)

# dexpp reads, analyses and writes the DEX files.  It is pinned to an exact
# commit.  To build against a local checkout instead, configure with
# -DFETCHCONTENT_SOURCE_DIR_DEXPP=/path/to/dexpp.
set(DEXPP_BUILD_TESTS OFF)
set(DEXPP_BUILD_C OFF)
set(DEXPP_BUILD_PYTHON OFF)
FetchContent_Declare(
        dexpp
        GIT_REPOSITORY https://github.com/burogurama/dexpp.git
        GIT_TAG 17c65fbb3c0e42237e24a009952105fa992e2c69
)
FetchContent_MakeAvailable(dexpp)

add_library(splitdex_core STATIC
    src/partition.cpp
    src/splitter.cpp
)
target_include_directories(splitdex_core PUBLIC src)
target_link_libraries(splitdex_core PUBLIC dexpp_core)

add_executable(split-dex src/main.cpp)
target_link_libraries(split-dex PRIVATE splitdex_core)

install(TARGETS split-dex RUNTIME DESTINATION bin)

if(SPLITDEX_BUILD_TESTS)
    # Keep `cmake --install` to split-dex itself.
    set(INSTALL_GTEST OFF)
    FetchContent_Declare(
            googletest
            URL https://github.com/google/googletest/archive/52eb8108c5bdec04579160ae17225d66034bd723.zip
    )
    FetchContent_MakeAvailable(googletest)

    enable_testing()

    file(GLOB_RECURSE TEST_SOURCES CONFIGURE_DEPENDS tests/*.cc)
    add_executable(unit_tests ${TEST_SOURCES})
    target_link_libraries(unit_tests PRIVATE splitdex_core GTest::gtest_main)

    include(GoogleTest)
    gtest_discover_tests(unit_tests)
endif()
