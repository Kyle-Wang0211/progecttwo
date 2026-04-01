# 手机后台上传 + 对象存储 + GPU 调度改造方案

## 目标

把当前：

`手机 -> SSH/SFTP -> 丹麦单机 GPU`

改成：

`手机 -> URLSession background upload -> 对象存储/上传服务 -> 任务调度服务 -> GPU worker -> 对象存储 -> 手机轮询 job 状态并下载成果`

## 设计原则

- 手机端不再直接持有 GPU SSH 连接
- 上传必须使用 `URLSessionConfiguration.background`
- 后端以 `job_id` 为唯一外部主键
- GPU worker 可替换，不绑定丹麦机
- job 状态必须是可恢复、可估时、可取消的

## 手机端流程

1. 用户选择视频或拍摄完成
2. app `POST /v1/mobile-jobs`
3. broker 返回：
   - `job_id`
   - `upload.method`
   - `upload.url`
   - `upload.headers`
4. app 用 `URLSession background upload` 把视频直接发往对象存储
5. app 只轮询 `GET /v1/mobile-jobs/{job_id}`
6. broker 发现对象已落库后，调度可用 GPU worker
7. worker 把阶段状态持续写回 broker
8. 任务完成后，broker 在 job status 中暴露 `artifact.download_url`
9. 手机端下载结果并展示

## 对外 API

### `POST /v1/mobile-jobs`

请求：

```json
{
  "fileName": "scan.mov",
  "fileSizeBytes": 1488327217,
  "contentType": "video/quicktime",
  "captureOrigin": "mobile_app",
  "clientRecordId": "2C1674D5-7F1D-49AA-8C17-6321E8C1A1F0"
}
```

响应：

```json
{
  "jobId": "job_01hq8n6d23y0q6v8x3h2m9wq4b",
  "upload": {
    "method": "PUT",
    "url": "https://storage.example.com/mobile/job_01hq.../source.mov?signature=...",
    "headers": {
      "Content-Type": "video/quicktime"
    }
  },
  "pollPath": "/v1/mobile-jobs/job_01hq8n6d23y0q6v8x3h2m9wq4b",
  "cancelPath": "/v1/mobile-jobs/job_01hq8n6d23y0q6v8x3h2m9wq4b"
}
```

### `GET /v1/mobile-jobs/{job_id}`

响应：

```json
{
  "job_id": "job_01hq8n6d23y0q6v8x3h2m9wq4b",
  "state": "training",
  "title": "正在训练 3D 模型",
  "detail": "official_default_probe 已生成 248 / 900 帧。",
  "progress_fraction": 0.58,
  "elapsed_seconds": 812,
  "estimated_remaining_seconds": 451,
  "progress_basis": "runtime_render_count",
  "artifact": null,
  "failure_reason": null
}
```

可选 `state`：

- `preparing_upload`
- `uploading`
- `queued`
- `reconstructing`
- `training`
- `packaging`
- `downloading`
- `completed`
- `failed`
- `cancelled`

### `DELETE /v1/mobile-jobs/{job_id}`

语义：
- 取消 broker 侧 job
- 如果对象还在上传，标记取消
- 如果 worker 已启动，向 worker 发送取消信号

## broker 内部职责

- 为手机端生成预签名上传 URL
- 接收对象存储上传完成事件，或主动轮询对象状态
- 维护 job 数据库
- 维护 worker 分配和 GPU 选择
- 汇总 worker 的 `runtime_status.json`
- 生成统一的对外状态 JSON

## worker 侧状态要求

worker 必须持续回传：

- `stage`
- `phase_name`
- `current_tier`
- `detail`
- `progress`
- `elapsed_sec`
- `estimated_remaining_sec`
- `progress_basis`

## 失败回传要求

不能只回 `all_tiers_failed`，至少要带：

- 当前 tier
- 当前 phase
- 失败原因
- 原始工具报错摘要

例如：

```json
{
  "job_id": "job_01hq8n6d23y0q6v8x3h2m9wq4b",
  "state": "failed",
  "title": "远端生成失败",
  "detail": "global100_seq_prep: COLMAP feature_extractor died with SIGKILL(9)",
  "progress_fraction": 0.37,
  "elapsed_seconds": 946,
  "estimated_remaining_seconds": null,
  "progress_basis": "runtime_budget",
  "artifact": null,
  "failure_reason": "colmap_feature_extractor_sigkill"
}
```

## app 侧本轮已实现的骨架

- 新增 `brokeredBackgroundUpload` backend
- 新增 `BackgroundUploadBrokerClient`
- 用 `URLSessionConfiguration.background` 直接上传到对象存储
- app 生命周期接入 `handleEventsForBackgroundURLSession`
- `PipelineRunner` 已支持 broker 路径
- `HomeViewModel` / `ScanViewModel` 可切换到产品默认后端

## 部署前还需要的真实后端工作

- 部署 broker 服务
- 接上对象存储
- 接上 job 持久化
- 接上 GPU worker 分配器
- 在 app 的 `Info.plist` 或环境变量里配置：
  - `AETHER_BROKER_BASE_URL`
  - `AETHER_BROKER_API_KEY`（可选）
  - `AETHER_BROKER_BACKGROUND_SESSION_ID`（可选）
