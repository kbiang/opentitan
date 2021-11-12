# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

load("@com_github_bazelbuild_buildtools//buildifier:def.bzl", "buildifier")
load("//rules:quality.bzl", "license_check")

package(default_visibility = ["//visibility:public"])

buildifier(
    name = "buildifier",
    exclude_patterns = ["./**/vendor/**"],
)

license_check(
    name = "license_check",
)
