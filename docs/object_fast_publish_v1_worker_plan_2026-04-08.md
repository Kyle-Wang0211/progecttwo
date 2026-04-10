# Object Fast Publish V1

## 目标

新远端不要再是“更快的老远端 HI-SLAM2”。

新远端定义为：

- 同一个 `control_plane`
- 一个新 `strategy`
- 一个新 `worker family`
- 一个更像产品的 first artifact

主路线：

`RGB guided capture -> fast SfM -> object mask -> support plane -> textured mesh/surface first result -> optional 3DGS refinement`

约束：

- 不依赖深度相机
- 适配所有手机/所有设备
- 1 到 2 分钟交付 first result
- 10 到 20 分钟交付 HQ enhancement
- 不重写现有 control plane
- 不动老 HI-SLAM2 worker

## 上游开源栈

这一版只包成熟开源项目，不自创底层算法：

- `SfM`: [COLMAP](https://colmap.github.io/tutorial.html) / [pycolmap](https://github.com/colmap/pycolmap)
- `可选 matching frontend`: [hloc](https://github.com/cvg/Hierarchical-Localization)
- `Object mask`: [SAM 2](https://github.com/facebookresearch/sam2)
- `Surface + texture`: [OpenMVS](https://github.com/cdcseacave/openMVS)
- `Geometry cleanup / plane fit`: `open3d`, `trimesh`
- `HQ splat`: [Graphdeco 3D Gaussian Splatting](https://github.com/graphdeco-inria/gaussian-splatting)

原则：

- 算法层全部挂明确 repo
- 我们只写 `worker orchestration + adapters + artifact publish`
- 不自己发明 SfM、mask、meshing、3DGS 核心实现

## 和现有系统的关系

### 保留

- `/Users/kaidongwang/Documents/progecttwo/control_plane/app/main.py`
- `/Users/kaidongwang/Documents/progecttwo/control_plane/app/models.py`
- `/Users/kaidongwang/Documents/progecttwo/control_plane/app/repository.py`
- `/Users/kaidongwang/Documents/progecttwo/control_plane/app/storage.py`
- `/Users/kaidongwang/Documents/progecttwo/control_plane/worker_agent/main.py`
- `/Users/kaidongwang/Documents/progecttwo/control_plane/worker_agent/config.py`

### 不动

- 老 HI-SLAM2 worker
- 老 5090 机器
- 老 `strategy`

### 新增

- `/Users/kaidongwang/Documents/progecttwo/control_plane/worker_object_fast_publish_v1`

## 目录结构

```text
control_plane/
  app/
    main.py
    models.py
    repository.py
    storage.py

  worker_agent/                         # 老 HI-SLAM2，不动
    main.py
    config.py

  worker_object_fast_publish_v1/       # 新远端
    __init__.py
    main.py
    config.py
    claim_loop.py
    runtime.py
    artifacts.py
    paths.py
    context.py

    pipeline/
      __init__.py
      extract_frames.py
      curate_frames.py
      run_sfm.py
      run_masks.py
      run_support_plane.py
      run_openmvs.py
      run_cleanup.py
      publish_default.py
      run_hq_3dgs.py
      publish_hq.py

    adapters/
      __init__.py
      pycolmap_adapter.py
      hloc_adapter.py
      sam2_adapter.py
      openmvs_adapter.py
      open3d_adapter.py
      graphdeco_3dgs_adapter.py

    scripts/
      bootstrap_env.sh
      run_default_pipeline.sh
      run_hq_pipeline.sh
```

## API 契约

### 1. 建单

继续使用：

- `POST /v1/mobile-jobs`

请求体不改 schema，只用现有的 `pipeline_profile: Dict[str, Any]`：

```json
{
  "tenant_id": "tenant_demo",
  "user_id": "user_123",
  "file_name": "capture.mov",
  "file_size_bytes": 182345678,
  "content_type": "video/quicktime",
  "capture_origin": "object_mode_v2",
  "client_record_id": "record_123",
  "pipeline_profile": {
    "strategy": "object_fast_publish_v1",
    "capture_mode": "guided_object",
    "artifact_contract_version": "object_publish_v1",
    "stack": {
      "sfm": "pycolmap",
      "matching_frontend": "hloc_optional",
      "mask": "sam2",
      "surface": "openmvs",
      "cleanup": "open3d",
      "hq_refine": "graphdeco_3dgs"
    },
    "target_first_result_sec": 120,
    "target_hq_result_sec": 1200
  }
}
```

### 2. 上传

继续使用现有：

- `PUT /v1/mobile-jobs/{job_id}/upload`
- `POST /v1/mobile-jobs/{job_id}/multipart-complete`
- `POST /v1/mobile-jobs/{job_id}/chunked-complete`

### 3. 轮询

继续使用现有：

- `GET /v1/mobile-jobs/{job_id}`

### 4. runtime updates

继续使用现有：

- `POST /v1/jobs/{job_id}/runtime`

建议的 `stage`：

- `curate`
- `sfm_fast`
- `object_mask`
- `support_plane`
- `surface_fast`
- `texture_bake`
- `cleanup`
- `publish_default`
- `refine_3dgs`
- `publish_hq`

### 5. artifact publish

继续使用现有：

- `POST /v1/jobs/{job_id}/artifact-manifest`

关键变化：

- 允许同一个 job 多次 publish manifest
- `publish_default` 时先发一次
- `publish_hq` 时再更新一次

## First Result Artifact Contract

first result 不再是 raw splat/raw ply。

### 第一次 manifest

```json
{
  "worker_id": "worker_ofp_01",
  "manifest": {
    "primary_artifact": {
      "type": "default_object_glb",
      "storage_key": "artifacts/<job_id>/default/default_object.glb"
    },
    "preview": {
      "type": "poster_jpg",
      "storage_key": "artifacts/<job_id>/default/poster.jpg"
    },
    "viewer_manifest": {
      "type": "viewer_manifest_json",
      "storage_key": "artifacts/<job_id>/default/viewer_manifest.json"
    }
  }
}
```

### viewer_manifest.json

```json
{
  "version": "object_publish_v1",
  "default_asset": {
    "kind": "glb",
    "path": "default/default_object.glb",
    "ready": true
  },
  "poster": {
    "kind": "jpg",
    "path": "default/poster.jpg"
  },
  "orbit_preview": {
    "kind": "mp4",
    "path": "default/orbit.mp4"
  },
  "camera_preset": {
    "path": "default/camera_preset.json"
  },
  "hq_asset": {
    "kind": "splat",
    "path": "hq/hq.splat",
    "ready": false
  }
}
```

### 第二次 manifest

HQ 好了之后，只更新 `viewer_manifest`，补 `hq_asset.ready = true`。

## control_plane 接法

### 必做改动 1：strategy 分流

在 `main.py` 的建单和 `claim-next` 逻辑里识别：

- 老链：`legacy_hislam2` / 现有 `autofallback`
- 新链：`object_fast_publish_v1`

### 必做改动 2：worker family 匹配

利用现有的 `capability_flags`：

新 worker 注册：

```json
{
  "pipeline_families": ["object_fast_publish_v1"],
  "supports_default_textured_surface": true,
  "supports_hq_splat": true
}
```

老 worker 继续注册自己的 donor/HI-SLAM2 能力。

`claim-next` 只允许：

- `job.pipeline_profile.strategy == object_fast_publish_v1`
- 命中 `worker.capability_flags.pipeline_families`

### 必做改动 3：允许 early publish

control plane 不需要新路由，只要允许：

- first manifest
- later manifest update

这本质上只是把现有 `artifact-manifest` 从“一次性交付”改成“可早发 + 可更新”。

## 新 worker 文件模板

以下模板都是“复制老 worker 骨架，再替换 pipeline”。

### main.py

职责：

- register
- heartbeat
- claim-next
- dispatch 到 `claim_loop.run_once`

```python
from __future__ import annotations

import time

from .claim_loop import run_once
from .config import config
from .runtime import ControlPlaneClient


def main() -> None:
    client = ControlPlaneClient(config.control_plane_base_url)
    registration = client.register(worker_id=None)
    worker_id = registration["worker_id"]

    while True:
        try:
            run_once(client=client, worker_id=worker_id)
        finally:
            client.heartbeat(worker_id, state="idle", current_job_id=None)
        time.sleep(config.scheduler_tick_interval_sec)


if __name__ == "__main__":
    main()
```

### claim_loop.py

职责：

- claim 一个新远端 job
- 建上下文目录
- 顺序调用 `pipeline/*`
- 发 runtime updates
- 发 artifact manifest

```python
from __future__ import annotations

from .context import JobContext
from .pipeline.extract_frames import extract_frames
from .pipeline.curate_frames import curate_frames
from .pipeline.run_sfm import run_sfm
from .pipeline.run_masks import run_masks
from .pipeline.run_support_plane import run_support_plane
from .pipeline.run_openmvs import run_openmvs
from .pipeline.run_cleanup import run_cleanup
from .pipeline.publish_default import publish_default
from .pipeline.run_hq_3dgs import run_hq_3dgs
from .pipeline.publish_hq import publish_hq


def run_once(client, worker_id: str) -> None:
    assignment = client.claim_next(worker_id)
    if not assignment:
        return

    ctx = JobContext.from_assignment(assignment, worker_id=worker_id)
    try:
        extract_frames(ctx)
        curate_frames(ctx)
        run_sfm(ctx)
        run_masks(ctx)
        run_support_plane(ctx)
        run_openmvs(ctx)
        run_cleanup(ctx)
        publish_default(ctx, client)

        if ctx.should_run_hq_refine:
            run_hq_3dgs(ctx)
            publish_hq(ctx, client)

        client.complete(ctx.job_id, worker_id, "已完成", "对象成品已准备好")
    except Exception as exc:
        client.fail(
            ctx.job_id,
            worker_id,
            reason="object_fast_publish_failed",
            detail=str(exc),
            stage=ctx.current_stage,
        )
        raise
```

### runtime.py

职责：

- 从老 worker `ControlPlaneClient` 拆出来复用
- 唯一变化是 `capability_flags`

```python
def capability_flags() -> dict:
    return {
        "pipeline_families": ["object_fast_publish_v1"],
        "supports_default_textured_surface": True,
        "supports_hq_splat": True,
    }
```

### context.py

职责：

- job_id
- input paths
- output dirs
- current stage
- 记录中间产物路径

```python
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class JobContext:
    job_id: str
    worker_id: str
    root_dir: Path
    input_video: Path
    current_stage: str = "created"
    frames_dir: Path | None = None
    curated_dir: Path | None = None
    sfm_dir: Path | None = None
    masks_dir: Path | None = None
    surface_dir: Path | None = None
    default_publish_dir: Path | None = None
    hq_dir: Path | None = None
    should_run_hq_refine: bool = True
```

### artifacts.py

职责：

- 统一生成 `ArtifactManifestRequest`
- 统一写 `viewer_manifest.json`

### pipeline/extract_frames.py

职责：

- 从视频抽帧
- 不做算法，只做 ffmpeg/pyav 调用

### pipeline/curate_frames.py

职责：

- 模糊过滤
- 去重
- 保留 object-mode 关键帧

建议：

- 先做简单版：laplacian blur + spacing gate
- 不做自研评分器

### pipeline/run_sfm.py

职责：

- 先调用 `pycolmap`
- 如果 job profile 开了 `matching_frontend=hloc_optional`，就先调 `hloc`

```python
from .adapters.pycolmap_adapter import run_pycolmap
from .adapters.hloc_adapter import maybe_run_hloc


def run_sfm(ctx):
    ctx.current_stage = "sfm_fast"
    maybe_run_hloc(ctx)
    run_pycolmap(ctx)
```

### adapters/pycolmap_adapter.py

职责：

- 不发明 SfM
- 直接包 `pycolmap` 官方 API
- 输出：
  - sparse model
  - poses
  - registered images

### pipeline/run_masks.py

职责：

- 跑主体 mask
- 写每张帧的 mask png

```python
from .adapters.sam2_adapter import run_sam2_batch


def run_masks(ctx):
    ctx.current_stage = "object_mask"
    run_sam2_batch(ctx)
```

### pipeline/run_support_plane.py

职责：

- 用 sparse/depth-less points + masks 估计支撑面
- 求 canonical center / up
- 不做复杂研究，只做产品必要几何约束

### pipeline/run_openmvs.py

职责：

- 用 `OpenMVS` 从 SFM 输出到 mesh/surface + texture
- 这是 2 分钟 first result 核心

```python
from .adapters.openmvs_adapter import run_openmvs_reconstruct


def run_openmvs(ctx):
    ctx.current_stage = "surface_fast"
    run_openmvs_reconstruct(ctx)
```

### pipeline/run_cleanup.py

职责：

- 清背景残渣
- 裁桌面 patch
- 收紧边界
- 保留“像产品”的 artifact，而不是“满场景残渣”

### pipeline/publish_default.py

职责：

- 生成：
  - `default_object.glb`
  - `poster.jpg`
  - `orbit.mp4`
  - `camera_preset.json`
  - `viewer_manifest.json`
- 调 `artifact-manifest`

```python
def publish_default(ctx, client):
    ctx.current_stage = "publish_default"
    manifest = build_default_manifest(ctx)
    client.upload_artifact_manifest(ctx.job_id, {
        "worker_id": ctx.worker_id,
        "manifest": manifest,
    })
```

### pipeline/run_hq_3dgs.py

职责：

- 用相同：
  - frames
  - masks
  - poses
  - canonical frame
- 跑 Graphdeco 官方 3DGS

### pipeline/publish_hq.py

职责：

- 更新 `viewer_manifest.json`
- 把 `hq_splat` 标成 ready
- 再发一次 `artifact-manifest`

## 哪些现有代码复用，哪些不复用

### 直接复用

- `/Users/kaidongwang/Documents/progecttwo/control_plane/app/main.py`
- `/Users/kaidongwang/Documents/progecttwo/control_plane/app/models.py`
- `/Users/kaidongwang/Documents/progecttwo/control_plane/app/repository.py`
- `/Users/kaidongwang/Documents/progecttwo/control_plane/app/storage.py`
- `/Users/kaidongwang/Documents/progecttwo/control_plane/worker_agent/main.py`
- `/Users/kaidongwang/Documents/progecttwo/control_plane/worker_agent/config.py`

### 借协议思路，不直接拿来跑生产

- `/Users/kaidongwang/Documents/progecttwo/progect2/progect2/Core/Pipeline/HTTPObjectModeV2RemoteClient.swift`
- `/Users/kaidongwang/Documents/progecttwo/progect2/progect2/Core/Pipeline/ObjectModeV2PipelineRunner.swift`

### 不复用为新远端主线

- HI-SLAM2 donor train 主链
- raw PLY/raw splat first-result 思路
- old `PipelineRunner.swift` 那种“等最终结果再给用户看”的单阶段心智

## 最后拍板

新远端 v1 只做一件事：

- 用成熟开源栈，在现有 control_plane 上，交付一个 **2 分钟内可看的 textured object asset**

然后 v1.5 再补：

- 同一 job 的 `HQ 3DGS refinement`

这条路不是研究最纯，但最像消费产品。
