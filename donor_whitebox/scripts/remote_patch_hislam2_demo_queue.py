from pathlib import Path

p = Path('/root/gs_refs/HI-SLAM2/demo.py')
text = p.read_text()
old_put = '        queue.put((t, image[None], intrinsics[None], is_last))\n'
new_put = '        queue.put((t, image[None].contiguous().numpy(), intrinsics[None].cpu().numpy(), is_last))\n'
old_get = '        (t, image, intrinsics, is_last) = queue.get()\n        pbar.update()\n\n        if hi2 is None:\n'
new_get = '        (t, image, intrinsics, is_last) = queue.get()\n        if isinstance(image, np.ndarray):\n            image = torch.from_numpy(image)\n        if isinstance(intrinsics, np.ndarray):\n            intrinsics = torch.from_numpy(intrinsics)\n        pbar.update()\n\n        if hi2 is None:\n'
if old_put not in text:
    raise SystemExit('queue.put pattern not found')
if old_get not in text:
    raise SystemExit('queue.get pattern not found')
text = text.replace(old_put, new_put, 1)
text = text.replace(old_get, new_get, 1)
p.write_text(text)
print(p)
print('patched demo queue transport')
