#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-/root/gs_refs/WildGS-SLAM.clean}"

/venv/hislam2/bin/python - <<'PY'
from pathlib import Path

repo = Path("/root/gs_refs/WildGS-SLAM.clean")

slam = repo / "src" / "slam.py"
text = slam.read_text()
if "import threading" not in text:
    text = text.replace("import time\n", "import time\nimport threading\nimport queue\n")
old = """    def run(self):\n        m_pipe, t_pipe = mp.Pipe()\n\n        q_main2vis = mp.Queue() if self.cfg['gui'] else None\n        q_vis2main = mp.Queue() if self.cfg['gui'] else None\n\n        processes = [\n            mp.Process(target=self.tracking, args=(t_pipe,)),\n            mp.Process(target=self.mapping, args=(m_pipe,q_main2vis,q_vis2main)),\n        ]\n        self.num_running_thread += len(processes)\n        if self.cfg['gui']:\n            self.num_running_thread += 1\n        for p in processes:\n            p.start()\n\n        if self.cfg['gui']:\n            pipeline_params = munchify(self.cfg[\"mapping\"][\"pipeline_params\"])\n            bg_color = [0, 0, 0]\n            background = torch.tensor(\n                bg_color, dtype=torch.float32, device=self.device\n            )\n            gaussians = GaussianModel(self.cfg['mapping']['model_params']['sh_degree'], config=self.cfg)\n\n            params_gui = gui_utils.ParamsGUI(\n                pipe=pipeline_params,\n                background=background,\n                gaussians=gaussians,\n                q_main2vis=q_main2vis,\n                q_vis2main=q_vis2main,\n            )\n            gui_process = mp.Process(target=slam_gui.run, args=(params_gui,))\n            gui_process.start()\n            self.all_trigered += 1\n\n\n        for p in processes:\n            p.join()\n\n        self.printer.terminate()\n\n        for process in mp.active_children():\n            process.terminate()\n            process.join()\n"""
new = """    def run(self):\n        single_thread = self.cfg.get('single_thread', False)\n        m_pipe, t_pipe = mp.Pipe()\n\n        if self.cfg['gui']:\n            q_main2vis = queue.Queue() if single_thread else mp.Queue()\n            q_vis2main = queue.Queue() if single_thread else mp.Queue()\n        else:\n            q_main2vis = None\n            q_vis2main = None\n\n        worker_cls = threading.Thread if single_thread else mp.Process\n        worker_kwargs = {'daemon': True} if single_thread else {}\n        processes = [\n            worker_cls(target=self.tracking, args=(t_pipe,), **worker_kwargs),\n            worker_cls(target=self.mapping, args=(m_pipe,q_main2vis,q_vis2main), **worker_kwargs),\n        ]\n        self.num_running_thread += len(processes)\n        if self.cfg['gui']:\n            self.num_running_thread += 1\n        for p in processes:\n            p.start()\n\n        if self.cfg['gui']:\n            pipeline_params = munchify(self.cfg[\"mapping\"][\"pipeline_params\"])\n            bg_color = [0, 0, 0]\n            background = torch.tensor(\n                bg_color, dtype=torch.float32, device=self.device\n            )\n            gaussians = GaussianModel(self.cfg['mapping']['model_params']['sh_degree'], config=self.cfg)\n\n            params_gui = gui_utils.ParamsGUI(\n                pipe=pipeline_params,\n                background=background,\n                gaussians=gaussians,\n                q_main2vis=q_main2vis,\n                q_vis2main=q_vis2main,\n            )\n            gui_process = worker_cls(target=slam_gui.run, args=(params_gui,), **worker_kwargs)\n            gui_process.start()\n            self.all_trigered += 1\n\n        for p in processes:\n            p.join()\n\n        self.printer.terminate()\n\n        if not single_thread:\n            for process in mp.active_children():\n                process.terminate()\n                process.join()\n"""
if old not in text:
    raise SystemExit("slam.py run block not found")
text = text.replace(old, new)
slam.write_text(text)

printer = repo / "src" / "utils" / "Printer.py"
text = printer.read_text()
if "import threading" not in text:
    text = text.replace("from colorama import Fore, Style\n", "from colorama import Fore, Style\nimport threading\nimport queue\nfrom types import SimpleNamespace\n")
old = """class Printer(TrivialPrinter):\n    def __init__(self, total_img_num):\n        self.msg_lock = mp.Lock()\n        self.msg_queue = mp.Queue()\n        self.progress_counter = mp.Value('i', 0)\n        process = mp.Process(target=self.printer_process, args=(total_img_num,))\n        process.start()\n"""
new = """class Printer(TrivialPrinter):\n    def __init__(self, total_img_num, single_thread=False):\n        self.single_thread = single_thread\n        self.msg_lock = threading.Lock() if single_thread else mp.Lock()\n        self.msg_queue = queue.Queue() if single_thread else mp.Queue()\n        self.progress_counter = SimpleNamespace(value=0) if single_thread else mp.Value('i', 0)\n        worker_cls = threading.Thread if single_thread else mp.Process\n        worker_kwargs = {'daemon': True} if single_thread else {}\n        process = worker_cls(target=self.printer_process, args=(total_img_num,), **worker_kwargs)\n        process.start()\n"""
if old not in text:
    raise SystemExit("Printer init block not found")
text = text.replace(old, new)
printer.write_text(text)

slam_init = repo / "src" / "slam.py"
text = slam_init.read_text()
old = """        self.printer = Printer(\n            len(stream)\n        )  # use an additional process for printing all the info\n"""
new = """        self.printer = Printer(\n            len(stream), single_thread=cfg.get('single_thread', False)\n        )  # use an additional process for printing all the info\n"""
if old not in text:
    raise SystemExit("Printer construction block not found")
text = text.replace(old, new)
slam_init.write_text(text)
PY
