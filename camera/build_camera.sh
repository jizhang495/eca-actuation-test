#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
edsdk_root="$script_dir/EDSDK/EDSDKv132010L/Linux/EDSDK"
include_dir="$edsdk_root/Header"
lib_dir="$edsdk_root/Library/x86_64"

g++ \
  -std=c++17 \
  -Wall \
  -Wextra \
  -DTARGET_OS_LINUX \
  -I"$include_dir" \
  "$script_dir/CameraControl.cpp" \
  "$lib_dir/libEDSDK.so" \
  -pthread \
  -Wl,-rpath,'$ORIGIN' \
  -o "$script_dir/CameraControl"

cp "$lib_dir/libEDSDK.so" "$script_dir/libEDSDK.so"
echo "Built $script_dir/CameraControl"
