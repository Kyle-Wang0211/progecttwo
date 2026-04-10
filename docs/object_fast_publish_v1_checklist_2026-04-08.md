# Object Fast Publish V1 Checklist

## 目标

- 老 HI-SLAM2 worker 不动
- 新开 `object_fast_publish_v1` worker family
- app 继续走现有上传/轮询
- 2 分钟 first result 先交 `textured mesh/surface`
- HQ 再补 `3DGS/splat`

## 第一阶段：骨架搭建

- [x] 新增方案文档
- [x] 新增 `worker_object_fast_publish_v1/` 目录
- [x] 新增 `main.py`
- [x] 新增 `claim_loop.py`
- [x] 新增 `runtime.py`
- [x] 新增 `config.py`
- [x] 新增 `context.py`
- [x] 新增 `paths.py`
- [x] 新增 `artifacts.py`
- [x] 新增 `pipeline/*` 文件骨架
- [x] 新增 `adapters/*` 文件骨架
- [ ] Python 语法校验

## 第二阶段：control_plane 接法

- [ ] `pipeline_profile.strategy == object_fast_publish_v1` 分流
- [ ] `capability_flags.pipeline_families` 过滤 claim-next
- [ ] 允许同一个 job 多次 `artifact-manifest`
- [ ] `viewer_manifest` early publish

## 第三阶段：default first result

- [ ] `extract_frames`
- [ ] `curate_frames`
- [ ] `pycolmap`
- [ ] `SAM2`
- [ ] `support_plane`
- [ ] `OpenMVS`
- [ ] `cleanup`
- [ ] `default_object.glb`
- [ ] `poster.jpg`
- [ ] `orbit.mp4`
- [ ] `viewer_manifest.json`

## 第四阶段：HQ enhancement

- [ ] crop 后帧集复用
- [ ] poses / masks / canonical frame 复用
- [ ] Graphdeco 3DGS
- [ ] `hq.splat`
- [ ] second `artifact-manifest`

## 当前原则

- 不自创 SfM / mask / meshing / GS 核心算法
- 只包成熟开源项目
- 只写 orchestration / adapters / artifact publish
