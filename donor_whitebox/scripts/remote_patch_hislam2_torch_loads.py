from pathlib import Path

PATCHES = {
    Path('/root/gs_refs/HI-SLAM2/hislam2/midas/omnidata.py'): {
        "checkpoint = torch.load(self.model_path, map_location=device)": "checkpoint = torch.load(self.model_path, map_location=device, weights_only=False)",
    },
    Path('/root/gs_refs/HI-SLAM2/hislam2/midas/base_model.py'): {
        "parameters = torch.load(path, map_location=torch.device('cpu'))": "parameters = torch.load(path, map_location=torch.device('cpu'), weights_only=False)",
    },
    Path('/root/gs_refs/HI-SLAM2/hislam2/hi2.py'): {
        "(k.replace(\"module.\", \"\"), v) for (k, v) in torch.load(weights).items()])": "(k.replace(\"module.\", \"\"), v) for (k, v) in torch.load(weights, weights_only=False).items()])",
    },
}

for path, replacements in PATCHES.items():
    text = path.read_text()
    original = text
    for old, new in replacements.items():
        text = text.replace(old, new)
    if text != original:
        path.write_text(text)
    print(f'{path} patched={text != original}')
