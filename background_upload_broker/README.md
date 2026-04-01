# Aether3D Background Upload Broker

当前这版 broker 已经不再依赖 FastAPI/uvicorn，可以直接用系统自带的 `python3` 启动。

## 当前职责

- 给手机端创建 mobile job
- 返回 `URLSession background upload` 可直接使用的 `PUT` 上传地址
- 本机落盘上传视频
- broker 代表手机去 SSH 丹麦机
- 轮询丹麦机 runtime status，并把状态转成 app 需要的 job status
- 在完成时把 artifact 通过 broker 下载地址回传给 app

## 本地启动

```bash
BROKER_PUBLIC_BASE_URL='http://KaidongdeMacBook-Pro.local:8787' python3 app.py
```

如果你的 Mac 主机名或端口变了，可以改：

- `BROKER_PUBLIC_BASE_URL`
- `BROKER_BIND_HOST`
- `BROKER_PORT`

## 目录结构

- `runtime/jobs.json`
  持久化 job 记录
- `runtime/uploads/`
  手机上传到 broker 的原始视频
- `runtime/artifacts/`
  broker 从远端拉回的产物缓存

## 对外接口

- `POST /v1/mobile-jobs`
- `PUT /v1/uploads/{job_id}`
- `GET /v1/mobile-jobs/{job_id}`
- `DELETE /v1/mobile-jobs/{job_id}`
- `GET /v1/mobile-jobs/{job_id}/artifact`
- `GET /health`

## 说明

当前 worker 调度仍然先接丹麦机，但手机端已经不再直接持有 GPU SSH 连接。后续如果要扩成多 GPU，只需要继续扩 broker 里的 dispatcher 即可。
