cmake_minimum_required(VERSION 3.13)
project(grbl_sim C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

# Reuse the core's own source list rather than duplicating it, so a file added
# to ../CMakeLists.txt is picked up here automatically.
include(${CMAKE_CURRENT_LIST_DIR}/../CMakeLists.txt)

include(CheckSymbolExists)
check_symbol_exists(strlcpy "string.h" HAVE_STRLCPY)

add_executable(grbl_sim ${CMAKE_CURRENT_LIST_DIR}/sim_driver.c)

if(NOT HAVE_STRLCPY)
    target_compile_definitions(grbl_sim PRIVATE SIM_NEED_STRLCPY)
endif()

# Core sources include each other as "grbl/<file>.h" in driver builds, so the
# parent of the core directory has to be on the include path.
target_include_directories(grbl_sim PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}/..
    ${CMAKE_CURRENT_LIST_DIR}/../..
)

target_link_libraries(grbl_sim PRIVATE grbl)

# The core is built for embedded targets where these are noise; turning them
# into errors here would block the build on upstream code we do not own.
target_compile_options(grbl_sim PRIVATE -Wall -Wno-unused-parameter -Wno-unused-function)

find_library(M_LIB m)
if(M_LIB)
    target_link_libraries(grbl_sim PRIVATE ${M_LIB})
endif()
