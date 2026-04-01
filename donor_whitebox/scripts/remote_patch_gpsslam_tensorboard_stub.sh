#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-/root/gs_refs/GPS-SLAM.clean}"

python3 - <<'PY'
from pathlib import Path

repo = Path("/root/gs_refs/GPS-SLAM.clean")
compat = repo / "include" / "tensorboard_logger_compat.h"
compat.write_text(
    """#pragma once

#include <string>

class TensorBoardLogger {
public:
    explicit TensorBoardLogger(const std::string&) {}

    template <typename T>
    int add_scalar(const std::string&, int, T) { return 0; }
};
""",
    encoding="utf-8",
)

pipeline_h = repo / "include" / "pipeline.h"
text = pipeline_h.read_text(encoding="utf-8")
old = '#include "tensorboard_logger.h"\n'
new = '#include "tensorboard_logger_compat.h"\n'
if old in text:
    text = text.replace(old, new)
elif new not in text:
    raise SystemExit("expected tensorboard include not found")
pipeline_h.write_text(text, encoding="utf-8")
print(f"[gpsslam-tensorboard-stub] patched {pipeline_h}")

pipeline_cpp = repo / "src" / "pipeline.cpp"
text = pipeline_cpp.read_text(encoding="utf-8")
old = "        GOOGLE_PROTOBUF_VERIFY_VERSION;\n"
if old in text:
    text = text.replace(old, "")
pipeline_cpp.write_text(text, encoding="utf-8")
print(f"[gpsslam-tensorboard-stub] patched {pipeline_cpp}")

file_utils_cpp = repo / "src" / "file_utils.cpp"
text = file_utils_cpp.read_text(encoding="utf-8")
if "#include <cstdlib>\n" not in text:
    text = text.replace('#include "file_utils.h"\n', '#include "file_utils.h"\n#include <cstdlib>\n')

helper_old = 'namespace fs = std::filesystem;\n'
helper_new = """namespace fs = std::filesystem;\n\nstatic std::string shellQuote(const std::string &value)\n{\n    std::string quoted = \"'\";\n    for (char ch : value)\n    {\n        if (ch == '\\'')\n            quoted += \"'\\\\''\";\n        else\n            quoted.push_back(ch);\n    }\n    quoted += \"'\";\n    return quoted;\n}\n"""
if helper_old in text and helper_new not in text:
    text = text.replace(helper_old, helper_new)

old = """                // If overwrite is true, remove the existing directory and all its contents\n                fs::remove_all(dirPath);\n"""
new = """                // Work around libtorch/filesystem incompatibility on this runtime.\n                std::string cmd = \"/bin/rm -rf -- \" + shellQuote(dirPath.string());\n                if (std::system(cmd.c_str()) != 0)\n                {\n                    std::cerr << \"Failed to clear directory: \" << path << std::endl;\n                    return false;\n                }\n"""
if old in text:
    text = text.replace(old, new)
elif new not in text:
    raise SystemExit("expected remove_all block not found")

file_utils_cpp.write_text(text, encoding="utf-8")
print(f"[gpsslam-tensorboard-stub] patched {file_utils_cpp}")
PY
