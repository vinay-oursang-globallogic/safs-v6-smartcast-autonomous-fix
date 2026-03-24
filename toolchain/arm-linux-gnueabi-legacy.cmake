# ARM Linux GNUEABIhf Legacy Toolchain (MTK_LEGACY: MT5670 / MT5690 / MT5895)
# Targets ARMv7-A Cortex-A7/A53 with hard-float ABI (legacy kernel 3.18-based)
#
# Usage:
#   cmake -DCMAKE_TOOLCHAIN_FILE=toolchain/arm-linux-gnueabi-legacy.cmake ..
#
# Requires: gcc-arm-linux-gnueabi (or gnueabihf) installed on build host.
# Docker: ghcr.io/buddytv/safs:6.0.0 includes gcc-arm-linux-gnueabi.

cmake_minimum_required(VERSION 3.16)

# ── System ────────────────────────────────────────────────────────────────────
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR arm)

# Target: ARMv7-A Cortex-A7, soft-float ABI (legacy kernel compatibility)
set(SAFS_TARGET_CHIP "MTK_LEGACY" CACHE STRING "Target MediaTek chipset family")
set(SAFS_KERNEL_VERSION "3.18" CACHE STRING "Target kernel version")

# ── Toolchain prefix ──────────────────────────────────────────────────────────
# Prefer hard-float if available, fall back to soft-float
find_program(CROSS_GCC arm-linux-gnueabihf-gcc
    PATHS /usr/bin /usr/local/bin
    DOC "ARM GCC cross compiler"
)
if(NOT CROSS_GCC)
    find_program(CROSS_GCC arm-linux-gnueabi-gcc
        PATHS /usr/bin /usr/local/bin
        DOC "ARM GCC cross compiler (soft-float fallback)"
    )
endif()

if(NOT CROSS_GCC)
    message(WARNING "ARM cross compiler not found. Install gcc-arm-linux-gnueabihf or gcc-arm-linux-gnueabi.")
endif()

get_filename_component(TOOLCHAIN_DIR "${CROSS_GCC}" DIRECTORY)
get_filename_component(TOOLCHAIN_BIN "${CROSS_GCC}" NAME)
string(REGEX REPLACE "-gcc$" "" TOOLCHAIN_PREFIX "${TOOLCHAIN_BIN}")

set(CMAKE_C_COMPILER   "${TOOLCHAIN_DIR}/${TOOLCHAIN_PREFIX}-gcc")
set(CMAKE_CXX_COMPILER "${TOOLCHAIN_DIR}/${TOOLCHAIN_PREFIX}-g++")
set(CMAKE_AR           "${TOOLCHAIN_DIR}/${TOOLCHAIN_PREFIX}-ar")
set(CMAKE_RANLIB       "${TOOLCHAIN_DIR}/${TOOLCHAIN_PREFIX}-ranlib")
set(CMAKE_STRIP        "${TOOLCHAIN_DIR}/${TOOLCHAIN_PREFIX}-strip")
set(CMAKE_OBJDUMP      "${TOOLCHAIN_DIR}/${TOOLCHAIN_PREFIX}-objdump")
set(CMAKE_ADDR2LINE    "${TOOLCHAIN_DIR}/${TOOLCHAIN_PREFIX}-addr2line"
    CACHE STRING "addr2line for SAFS ELF symbolication")

# ── Compiler flags: MTK_LEGACY ARMv7-A ───────────────────────────────────────
set(SAFS_ARCH_FLAGS "-march=armv7-a -mtune=cortex-a7 -mfpu=neon-vfpv4")

# Legacy kernel 3.18 does not provide AARCH64 atomics; use armv7 primitives
set(SAFS_KERNEL_FLAGS "-D__KERNEL_STRICT_NAMES -DLINUX_VERSION_CODE=0x031200")

set(CMAKE_C_FLAGS_INIT   "${SAFS_ARCH_FLAGS} ${SAFS_KERNEL_FLAGS} -fstack-protector-strong")
set(CMAKE_CXX_FLAGS_INIT "${SAFS_ARCH_FLAGS} ${SAFS_KERNEL_FLAGS} -fstack-protector-strong")

# Debug build: include DWARF-4 for addr2line symbolication
set(CMAKE_C_FLAGS_DEBUG_INIT   "-O0 -g3 -gdwarf-4 -DDEBUG")
set(CMAKE_CXX_FLAGS_DEBUG_INIT "-O0 -g3 -gdwarf-4 -DDEBUG")

# Release build: strip debug info after creating a separate symbol file
set(CMAKE_C_FLAGS_RELEASE_INIT   "-O2 -g1 -DNDEBUG")
set(CMAKE_CXX_FLAGS_RELEASE_INIT "-O2 -g1 -DNDEBUG")

# ── Sysroot (optional) ────────────────────────────────────────────────────────
if(DEFINED ENV{MTK_LEGACY_SYSROOT})
    set(CMAKE_SYSROOT "$ENV{MTK_LEGACY_SYSROOT}")
    set(CMAKE_FIND_ROOT_PATH "${CMAKE_SYSROOT}")
    set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
    set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
    set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
endif()

# ── SAFS-specific: addr2line path for ElfSymbolicator ─────────────────────────
# Stored in CMake cache so build scripts can pass it to ElfSymbolicator
set(SAFS_ADDR2LINE "${CMAKE_ADDR2LINE}" CACHE STRING
    "Full path to addr2line binary; used by SAFS ElfSymbolicator")

message(STATUS "SAFS MTK_LEGACY toolchain: ${CMAKE_C_COMPILER}")
message(STATUS "SAFS addr2line: ${SAFS_ADDR2LINE}")
