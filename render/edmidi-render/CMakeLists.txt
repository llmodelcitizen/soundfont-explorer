# edmidi-render: tiny WAV renderer around libEDMIDI (built in render/Dockerfile).
#
#   cmake -S render/edmidi-render -B build -DEDMIDI_SOURCE_DIR=/src/libEDMIDI \
#         -DEDMIDI_LIBRARY=/src/libEDMIDI/build/libEDMIDI.a
#
# EDMIDI_SOURCE_DIR must be the library checkout (include/ for the public API,
# src/ for the internal headers modsel.cpp needs); EDMIDI_LIBRARY the static lib.
cmake_minimum_required(VERSION 3.13)
project(edmidi-render LANGUAGES C CXX)

set(EDMIDI_SOURCE_DIR "" CACHE PATH "libEDMIDI source checkout")
set(EDMIDI_LIBRARY "" CACHE FILEPATH "path to libEDMIDI.a")
if(NOT EDMIDI_SOURCE_DIR OR NOT EDMIDI_LIBRARY)
    message(FATAL_ERROR "set -DEDMIDI_SOURCE_DIR=<checkout> and -DEDMIDI_LIBRARY=<libEDMIDI.a>")
endif()

add_executable(edmidi-render main.c modsel.cpp)
target_include_directories(edmidi-render PRIVATE
    "${EDMIDI_SOURCE_DIR}/include"
    "${EDMIDI_SOURCE_DIR}/src")
set_target_properties(edmidi-render PROPERTIES C_STANDARD 99 CXX_STANDARD 11 CXX_EXTENSIONS OFF)
target_compile_options(edmidi-render PRIVATE -Wall -Wextra)
target_link_libraries(edmidi-render PRIVATE "${EDMIDI_LIBRARY}" m)

install(TARGETS edmidi-render RUNTIME DESTINATION bin)
