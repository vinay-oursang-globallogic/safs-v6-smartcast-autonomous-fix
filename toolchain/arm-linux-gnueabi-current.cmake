# ARM Linux GNUEABIhf Current Toolchain (MTK_CURRENT: MT5896 / MT5900 / MT5935)
# Targets ARMv7-A Cortex-A53/A55 with hard-float ABI (kernel 5.4+ based)
#
# Usage:
#   cmake -DCMAKE_TOOLCHAIN_FILE=toolchain/arm-linux-gnueabi-current.cmake ..
#
# Requires: gcc-arm-linux-gnueabihf installed on build host.
# Docker: ghcr.io/buddytv/safs:6.0.0 includes gcc-arm-linux-gnueabihf.

cmake_minimum_required(VERSION 3.18)

# ── System ────────────────────────────────────────────────────────────────────
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR arm)

# Target: ARMv7-A Cortex-A55 with hard-float ABI and NEON (current kernel 5.4)
set(SAFS_TARGET_CHIP "MTK_CURRENT" CACHE STRING "Target MediaTek chipset family")
set(SAFS_KERNEL_VERSION "5.4" CACHE STRING "Target kernel version")

# ── Toolchain prefix ──────────────────────────────────────────────────────────
find_program(CROSS_GCC arm-linux-gnueabihf-gcc
    PATHS /usr/bin /usr/local/bin
    DOC "ARM hard-float GCC cross compiler"
)

if(NOT CROSS_GCC)
    message(FATAL_ERROR
        "arm-linux-gnueabihf-gcc not found.\n"
        "Install with: apt-get install gcc-arm-linux-gnueabihf\n"
        "Or use the SAFS Docker image: ghcr.io/buddytv/safs:6.0.0"
    )
endif()

get_filename_component(TOOLCHAIN_DIR "${CROSS_GCC}" DIRECTORY)
set(TOOLCHAIN_PREFIX "arm-linux-gnueabihf")

set(CMAKE_C_COMPILER   "${TOOLCHAIN_DIR}/${TOOLCHAIN_PREFIX}-gcc")
set(CMAKE_CXX_COMPILER "${TOOLCHAIN_DIR}/${TOOLCHAIN_PREFIX}-g++")
set(CMAKE_AR           "${TOOLCHAIN_DIR}/${TOOLCHAIN_PREFIX}-ar")
set(CMAKE_RANLIB       "${TOOLCHAIN_DIR}/${TOOLCHAIN_PREFIX}-ranlib")
set(CMAKE_STRIP        "${TOOLCHAIN_DIR}/${TOOLCHAIN_PREFIX}-strip")
set(CMAKE_OBJDUMP      "${TOOLCHAIN_DIR}/${TOOLCHAIN_PREFIX}-objdump")
set(CMAKE_ADDR2LINE    "${TOOLCHAIN_DIR}/${TOOLCHAIN_PREFIX}-addr2line"
    CACHE STRING "addr2line for SAFS ELF symbolication")

# ── Compiler flags: MTK_CURRENT ARMv7-A Cortex-A55 ───────────────────────────
# Cortex-A55 supports Thumb-2, NEON, crypto extensions
set(SAFS_ARCH_FLAGS "-march=armv7-a+fp -mtune=cortex-a55 -mfpu=neon-fp-armv8 -mfloat-abi=hard")

# Kernel 5.4 supports modern POSIX and AT_HWCAP2
set(SAFS_KERNEL_FLAGS "-D_GNU_SOURCE -DLINUX_VERSION_CODE=0x050400")

# Security hardening
set(SAFS_SECURITY_FLAGS
    "-fstack-protector-strong"
    "-fstack-clash-protection"
    "-D_FORTIFY_SOURCE=2"
    "-Wformat -Wformat-security"
)
string(JOIN " " SAFS_SECURITY_FLAGS_STR ${SAFS_SECURITY_FLAGS})

set(CMAKE_C_FLAGS_INIT   "${SAFS_ARCH_FLAGS} ${SAFS_KERNEL_FLAGS} ${SAFS_SECURITY_FLAGS_STR}")
set(CMAKE_CXX_FLAGS_INIT "${SAFS_ARCH_FLAGS} ${SAFS_KERNEL_FLAGS} ${SAFS_SECURITY_FLAGS_STR}")

# Debug build: DWARF-5 for best addr2line resolution
set(CMAKE_C_FLAGS_DEBUG_INIT   "-O0 -g3 -gdwarf-5 -fno-omit-frame-pointer -DDEBUG")
set(CMAKE_CXX_FLAGS_DEBUG_INIT "-O0 -g3 -gdwarf-5 -fno-omit-frame-pointer -DDEBUG")

# RelWithDebInfo: production binary + separate .debug symbol file
set(CMAKE_C_FLAGS_RELWITHDEBINFO_INIT   "-O2 -g2 -gdwarf-4 -DNDEBUG")
set(CMAKE_CXX_FLAGS_RELWITHDEBINFO_INIT "-O2 -g2 -gdwarf-4 -DNDEBUG")

# Release: maximum optimization
set(CMAKE_C_FLAGS_RELEASE_INIT   "-O3 -DNDEBUG -flto")
set(CMAKE_CXX_FLAGS_RELEASE_INIT "-O3 -DNDEBUG -flto")

# ── Sysroot (optional) ────────────────────────────────────────────────────────
if(DEFINED ENV{MTK_CURRENT_SYSROOT})
    set(CMAKE_SYSROOT "$ENV{MTK_CURRENT_SYSROOT}")
    set(CMAKE_FIND_ROOT_PATH "${CMAKE_SYSROOT}")
    set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
    set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
    set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
endif()

# ── Post-build helper: create separate symbol file ────────────────────────────
# Call this function after add_executable/add_library to split debug symbols.
#
#   safs_split_debug_symbols(my_target)
#   → my_target.debug  (debug symbols for addr2line)
#   → my_target        (stripped binary for deployment)
#
function(safs_split_debug_symbols TARGET)
    add_custom_command(TARGET ${TARGET} POST_BUILD
        COMMAND ${CMAKE_OBJDUMP} --only-keep-debug $<TARGET_FILE:${TARGET}>
                $<TARGET_FILE:${TARGET}>.debug
        COMMAND ${CMAKE_STRIP} --strip-debug --strip-unneeded $<TARGET_FILE:${TARGET}>
        COMMAND ${CMAKE_OBJDUMP} --add-gnu-debuglink=$<TARGET_FILE:${TARGET}>.debug
                $<TARGET_FILE:${TARGET}>
        COMMENT "Splitting debug symbols for ${TARGET}"
        VERBATIM
    )
endfunction()

# ── SAFS-specific: addr2line path for ElfSymbolicator ─────────────────────────
set(SAFS_ADDR2LINE "${CMAKE_ADDR2LINE}" CACHE STRING
    "Full path to addr2line binary; used by SAFS ElfSymbolicator")

message(STATUS "SAFS MTK_CURRENT toolchain: ${CMAKE_C_COMPILER}")
message(STATUS "SAFS addr2line: ${SAFS_ADDR2LINE}")
