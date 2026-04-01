# 手机端对接 5090 SSH 接口规范（丹麦黄金环境）

更新时间：2026-03-20  
适用范围：Aether3D 手机端最小闭环  
闭环目标：`拍摄 -> 本地质量审核 -> 丹麦 5090 训练 -> 回传本地 Viewer 展示`

## 1. 黄金环境

- 机器：丹麦 5090
- Host：`62.107.25.198`
- SSH Port：`48601`
- User：`root`
- SSH 命令：

```bash
ssh -o StrictHostKeyChecking=accept-new -p 48601 root@62.107.25.198
```

## 2. 远端工作区

黄金环境工作根目录：

```text
/root/donor_whitebox
```

关键子目录：

- 输入视频目录：`/root/donor_whitebox/inputs/real_videos`
- 日志目录：`/root/donor_whitebox/logs`
- 输出目录：`/root/donor_whitebox/outputs`
- 脚本目录：`/root/donor_whitebox/scripts`

## 2.1 客户端 SSH 认证

- 当前手机端传输层使用原生 SSH/SFTP，而不是宿主机 `ssh` 子进程。
- `DEBUG` 构建使用调试版 `ed25519` 用户密钥，已对当前丹麦黄金环境授权。
- `Release` 构建应改为设备本地 `Keychain` 持久化私钥，并将对应公钥加入远端 `~/.ssh/authorized_keys`。
- 当前版本为了最小闭环先不做密码认证，也不依赖 HTTP API。

## 3. 输入协议

### 3.1 手机端本地输入

手机端必须产出一份可上传的视频文件，当前规范定为：

- 文件格式：`mp4`
- 建议帧率：`15-24 fps`
- 命名：`<asset_id>.mp4`

`asset_id` 建议使用一次扫描的稳定 UUID 去掉连字符后的 32 位小写字符串。

### 3.2 上传目标目录

上传后的视频必须落到：

```text
/root/donor_whitebox/inputs/real_videos/<asset_id>.mp4
```

示例：

```text
/root/donor_whitebox/inputs/real_videos/cf1ed45be9384168fe6a7f5eff23e703.mp4
```

## 4. 启动协议

### 4.1 启动脚本

远端真实入口脚本为：

```text
/root/donor_whitebox/scripts/remote_start_realvideo_autofallback_run.sh
```

### 4.2 启动命令

```bash
bash /root/donor_whitebox/scripts/remote_start_realvideo_autofallback_run.sh "<run_name>" "<video_path>"
```

其中：

- `run_name`：一次训练任务的稳定标识
- `video_path`：上传后的视频绝对路径

示例：

```bash
bash /root/donor_whitebox/scripts/remote_start_realvideo_autofallback_run.sh \
  "mobile_cf1ed45be9384168fe6a7f5eff23e703_autofallback_20260320T143500Z" \
  "/root/donor_whitebox/inputs/real_videos/cf1ed45be9384168fe6a7f5eff23e703.mp4"
```

## 5. 远端输出布局

对于一次 `run_name`，远端输出根目录为：

```text
/root/donor_whitebox/outputs/hislam2_<run_name>
```

示例：

```text
/root/donor_whitebox/outputs/hislam2_mobile_cf1ed45be9384168fe6a7f5eff23e703_autofallback_20260320T143500Z
```

### 5.1 tier 结构

当前脚本会按顺序尝试：

- `official_default`
- `global100_seq`
- `global200_seq`
- `global200_exhaustive`

每个 tier 可能产出：

- `<tier>_feed`
- `<tier>_probe`
- `<tier>_out`

### 5.2 summaries 目录

任务汇总 JSON 位于：

```text
/root/donor_whitebox/outputs/hislam2_<run_name>/summaries
```

## 6. 轮询协议

### 6.1 不要只看 launch.log

已验证当前远端脚本存在一种情况：

- `launch.log` 末尾写着 `all tiers failed`
- 但某个 tier 仍然已经产出了可用的 `3dgs_final.ply`

因此，客户端 **不能** 只根据 `launch.log` 判定失败。

### 6.2 必轮询的 JSON

客户端轮询以下文件：

- `summaries/<tier>_probe_summary.json`
- `summaries/<tier>_probe_verdict.json`
- `summaries/<tier>_full_summary.json`
- `summaries/<tier>_full_verdict.json`
- `summaries/<tier>_failure.json`

其中优先关注字段：

- `has_3dgs_final`
- `has_tsdf_mesh`
- `mean_psnr`
- `mean_ssim`
- `passed`
- `failures`

### 6.3 推荐轮询节奏

- 启动后前 30 秒：每 `2` 秒轮询一次
- 30 秒后：每 `5` 秒轮询一次
- 最长等待：`30` 分钟

## 7. 成功判定

### 7.1 一级成功判定

任意 tier 满足以下条件即可视为“已有可展示结果”：

- `probe_verdict.passed == true`
- 对应 `probe_summary.has_3dgs_final == true`

这时即可允许客户端下载并展示。

### 7.2 二级成功判定

如果没有 `probe passed`，但某个 full 目录已经产出可用 3DGS，也可视为“可展示但未过门限”：

- `full_summary.has_3dgs_final == true`

注意：此时即使 `full_verdict.passed == false`，仍然允许下载展示，只是质量门限未通过。

### 7.3 失败判定

满足以下全部条件才视为最终失败：

- 所有 tier 的 `probe/full` 都没有可用 `3dgs_final`
- `launch.log` 或 `failure.json` 明确显示任务结束

## 8. 结果选择策略

客户端按以下优先级选回传结果：

1. 第一优先：第一个 `probe passed == true` 且 `has_3dgs_final == true` 的 tier
2. 第二优先：第一个 `full_summary.has_3dgs_final == true` 的 tier
3. 第三优先：如果只有 mesh 没有 splat，则当前版本视为失败，不回传 viewer 主产物

当前建议优先使用的 splat 文件：

```text
<selected_dir>/3dgs_final.ply
```

## 9. 成功后拉回哪些文件

当前最小闭环必须拉回：

- `3dgs_final.ply`
- 对应的 `summary.json`
- 对应的 `verdict.json`

建议同时拉回：

- `intrinsics.npy`
- `traj_full.txt`
- `traj_kf.txt`
- `renders/` 目录
- `tsdf_mesh_w2.0.ply`（如果存在）

### 9.1 手机端本地标准包

手机端下载后，应在本地整理成统一结果包，而不是只存一个裸 `PLY`。

建议本地结果包结构：

```text
artifact/
  artifact.ply
  metrics.json
  viewer_manifest.json
  intrinsics.npy
  traj_full.txt
  traj_kf.txt
  renders/
  tsdf_mesh_w2.0.ply   # optional
```

其中：

- `artifact.ply`：来自远端 `3dgs_final.ply`
- `metrics.json`：由 `summary.json + verdict.json` 整理而成
- `viewer_manifest.json`：本地 viewer 读取入口

## 10. viewer_manifest.json 建议字段

```json
{
  "schema": "aether_remote_artifact_v1",
  "run_name": "mobile_cf1ed45be9384168fe6a7f5eff23e703_autofallback_20260320T143500Z",
  "selected_tier": "global100_seq_probe",
  "artifact_path": "artifact.ply",
  "metrics_path": "metrics.json",
  "preview_path": null,
  "world_state_path": null,
  "source": {
    "backend": "ssh",
    "host": "62.107.25.198",
    "port": 48601
  }
}
```

## 11. 当前 Aether3D 实现建议

手机端最小闭环建议拆成四段：

1. `Scan / Capture`
   产出本地扫描视频 `mp4`
2. `Quality Runtime`
   本地实时审核，不依赖远端
3. `Remote Artifact Sync`
   负责上传、SSH 启动、JSON 轮询、结果下载
4. `Viewer`
   读取本地统一结果包并展示

## 12. 当前已验证的远端事实

- 黄金环境脚本：`remote_start_realvideo_autofallback_run.sh`
- 远端输出目录命名：`/root/donor_whitebox/outputs/hislam2_<run_name>`
- summaries 确实存在 `probe/full summary + verdict + failure` JSON
- `launch.log` 不能单独作为失败依据
- `3dgs_final.ply` 是当前 viewer 的最小必需主产物

## 13. 当前版本的工程边界

这份规范只覆盖：

- 手机端上传到丹麦黄金环境
- SSH 启动训练
- 拉回可展示 3DGS 主产物

这份规范暂不覆盖：

- 多机调度
- 多用户排队
- 分布式任务编排
- MobileGS / MiniSplatting 替换方案
