#!/usr/bin/env bash
set -euo pipefail

cd /root/gs_refs/MonoGS

python3 - <<'PY'
from pathlib import Path

path = Path("/root/gs_refs/MonoGS/slam.py")
text = path.read_text()

if "import threading" not in text:
    text = text.replace("import time\n", "import time\nimport threading\n", 1)
if "import queue" not in text:
    if "import torch.multiprocessing as mp\n" in text:
        text = text.replace("import torch.multiprocessing as mp\n", "import torch.multiprocessing as mp\nimport queue\n", 1)
    elif "import multiprocessing as mp\n" in text:
        text = text.replace("import multiprocessing as mp\n", "import multiprocessing as mp\nimport queue\n", 1)

old_queues = """        frontend_queue = mp.Queue()\n        backend_queue = mp.Queue()\n\n        q_main2vis = mp.Queue() if self.use_gui else FakeQueue()\n        q_vis2main = mp.Queue() if self.use_gui else FakeQueue()\n"""
new_queues = """        single_thread = self.config[\"Training\"].get(\"single_thread\", False) or self.config[\"Dataset\"].get(\"single_thread\", False)\n\n        frontend_queue = queue.Queue() if single_thread else mp.Queue()\n        backend_queue = queue.Queue() if single_thread else mp.Queue()\n\n        q_main2vis = mp.Queue() if self.use_gui else FakeQueue()\n        q_vis2main = mp.Queue() if self.use_gui else FakeQueue()\n"""

if old_queues in text:
    text = text.replace(old_queues, new_queues, 1)

old = """        backend_process = mp.Process(target=self.backend.run)\n        if self.use_gui:\n            gui_process = mp.Process(target=slam_gui.run, args=(self.params_gui,))\n            gui_process.start()\n            time.sleep(5)\n\n        backend_process.start()\n        self.frontend.run()\n        backend_queue.put([\"pause\"])\n"""
new = """        backend_process = (\n            threading.Thread(target=self.backend.run, name=\"monogs-backend\", daemon=True)\n            if single_thread\n            else mp.Process(target=self.backend.run)\n        )\n        if self.use_gui:\n            gui_process = mp.Process(target=slam_gui.run, args=(self.params_gui,))\n            gui_process.start()\n            time.sleep(5)\n\n        backend_process.start()\n        self.frontend.run()\n        backend_queue.put([\"pause\"])\n"""

old_patched = """        single_thread = self.config[\"Training\"].get(\"single_thread\", False) or self.config[\"Dataset\"].get(\"single_thread\", False)\n        backend_process = (\n            threading.Thread(target=self.backend.run, name=\"monogs-backend\", daemon=True)\n            if single_thread\n            else mp.Process(target=self.backend.run)\n        )\n        if self.use_gui:\n            gui_process = mp.Process(target=slam_gui.run, args=(self.params_gui,))\n            gui_process.start()\n            time.sleep(5)\n\n        backend_process.start()\n        self.frontend.run()\n        backend_queue.put([\"pause\"])\n"""

if old in text:
    text = text.replace(old, new, 1)
elif old_patched not in text:
    raise SystemExit("expected backend launch block not found")

path.write_text(text)
PY
