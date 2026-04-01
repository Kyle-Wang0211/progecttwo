# White-Box Checkpoint Matrix

Current date: 2026-03-12

Rule for the "current result" column:
- Use the strongest/latest `3x3` donor white-box evidence by default.
- Official benchmark results are only used as code/environment sanity checks, not as the main white-box result.

Primary threshold source:
- `/Users/kaidongwang/Documents/Aether3D/aether_cpp/tests/pipeline/test_7layer_full_validation.cpp`

Key donor artifacts used below:
- `Photo-SLAM` best `I/J`:
  - `/root/donor_whitebox/outputs/photoslam_runs/room3x3_tumlike_slow200_unbounded_clean_5090_r2/68341_shutdown/ply/point_cloud/iteration_68341/point_cloud.ply`
- `MonoGS` best `O` / latest `D` evidence:
  - `/root/donor_whitebox/outputs/monogs_runs/donor_whitebox_outputs/2026-03-10-02-47-32/plot/stats_final.json`
  - `/root/donor_whitebox/outputs/monogs_runs/donor_whitebox_outputs/2026-03-10-02-47-32/plot/trj_final.json`
- `WildGS` latest `3x3` static-style geometry:
  - `/root/donor_whitebox/outputs/wildgs_runs/room3x3_tumrgbd_wildgs_static_r11/room3x3_tumrgbd_wildgs_static_r11/geometry_eval_aligned.txt`

## A-Q

| Checkpoint | 测试类目 | 计算方式 | 是否有硬阈值 | 当前结果 | 是否通过 |
| --- | --- | --- | --- | --- | --- |
| A | `3x3` 房间/手持路径生成 | 生成相机路径并校验总帧数与相邻帧最大位移 | 有：`camera_path.size == kTotalPathFrames` 且 `max_frame_dist < 0.05m` | `3x3 dense600`、`tumrgbd static r11` 等序列均已成功生成；最新 `r11` 为 `2364` 帧 donor-style static 路径 | 通过 |
| B | 队列/吞吐压力 | SPSC burst 测试，验证 burst 被优雅吸收或丢弃 | 有：`burst_dropped > 0 || burst_accepted >= 8` | 当前 donor 白盒链路没有对应的 burst 实测值；只有产品侧测试定义 | 未验证 |
| C | 帧接收率 | 统计扫描阶段被接受的 frame 数 | 有：`scan_accepted >= 100` | `HI-SLAM2/Photo-SLAM/WildGS/MonoGS` 都已稳定吃满远超 `100` 帧；`WildGS r11` 处理 `1182/1182` | 通过 |
| D | 帧选择 / keyframe 触发 | `MonoGS` keyframe trigger, overlap + translation, window/backpressure | 有：产品侧最低门槛 `selected_frames >= 10`；但白盒实际要求是触发质量不能导致米级漂移 | 最新 best `MonoGS` `camera_count=600 / kf_count=185 / RMSE≈0.989m`；官方 benchmark sanity 可到厘米级，但 `3x3` 仍是米级 | 未通过 |
| E | 扫描中训练是否启动 | 训练是否在 scan 期间进入 active | 有：`training_started_during_scan == true` | `Photo-SLAM` 和 `HI-SLAM2` 都已明确在扫描期间启动 GPU 训练 | 通过 |
| F | 扫描中高斯/点云是否增长 | 观察 scan 期间 gaussian / point 数是否增长 | 有：`max_gaussians_during_scan > 0` | `Photo-SLAM` 从 `3882` 长到 `7,959,829`；多条 donor 线都已证明真实增长 | 通过 |
| G | loss 是否真实收敛 | 记录 `first_loss` 与 `final_loss` 或至少训练 step 推进 | 有：`final_loss / first_loss < 3.0`，否则至少 `progress.step > 0` | `Photo-SLAM` 最佳 run `ema_loss` 持续下降；`HI-SLAM2`/`Photo-SLAM` 都有真实训练推进 | 通过 |
| H | 总耗时统计 | 统计 scan + post-train wall time | 有，但仅报告，不做 hard fail | 耗时统计已稳定落盘 | 通过 |
| I | 最终高斯数量 | 取最终有效 gaussian / point 数 | 有：`>= 1,000,000` | `Photo-SLAM` best `7,959,829` 点 | 通过 |
| J | 颜色准确性 | 以 `PSNR/DSSIM/render_time` 与最终颜色化 Gaussian 输出为 donor 白盒依据 | 有：产品侧 `9/9` 区域准确；当前 donor 白盒按最终彩色高斯结果判 | `Photo-SLAM` best `psnr_gaussian_splatting mean 14.6463 / last 12.2161`，最终彩色 PLY 已稳定导出 | 通过（donor white-box 层） |
| K | 表面漂移 P95 | `K_P95_MM`，即点/高斯到真实表面的 P95 距离 | 有：`P95 < 50mm` | 最新 `WildGS r11`：`K_P95_MM = 794.808` | 未通过 |
| L | 体积/质心匹配 | `L_CENTROID_OFFSET_MM` + `L_BBOX_EXTENT_M` + volume ratio/in-room ratio | 有：当前产品侧测试是 `centroid < 1200mm` 且 volume ratio in `[0.02, 5.0]`；白盒期望仍应接近真实房间体量/中心 | 最新 `WildGS r11`：`L_CENTROID_OFFSET_MM = 766.103`，bbox `1.8497 x 1.8979 x 2.2660m`，明显仍未贴合房间表面 | 未通过 |
| M | TSDF active block 数 | 统计 active TSDF blocks | 有：`>= 30000` | 当前 donor 白盒没有直接产出这个产品侧 TSDF 指标 | 未验证 |
| N | tile hit rate | `assigned_blocks / tsdf_blocks` | 有：`>= 20%` | 当前 donor 白盒没有 product surface-cell overlay / tile hit rate 实测值 | 未验证 |
| O | integrated frame count | 统计真正被系统处理/集成的 frame 数；不要把 `trj_final` 错当 full-frame | 有：产品侧测试是 `integrated_frames >= 100` | `MonoGS` 修正口径后 `camera_count=600 / camera_max_id=599`；`HI-SLAM2 traj_full=600` | 通过 |
| P (旧粗粒度) | 状态不回溯 | 旧粗粒度，现已拆成 `P1-P3` | 无单独新阈值，按 `P1-P3` 聚合 | `P1/P2/P3` 还没全过 | 未通过 |
| Q (旧粗粒度) | 表面吸附 | 旧粗粒度，现已拆成 `Q1-Q5` | 无单独新阈值，按 `Q1-Q5` 聚合 | `Q1` 明确失败，`Q2-Q5` 仍缺产品世界态 evaluator | 未通过 |

## Q1-Q5

| Checkpoint | 测试类目 | 计算方式 | 是否有硬阈值 | 当前结果 | 是否通过 |
| --- | --- | --- | --- | --- | --- |
| Q1 | Surface Adhesion | tile 中心到真实表面距离 + 法线点积 | 有：中心 `< 20mm`，normal dot `> 0.70`，且 pass rate `>= 80%` | 最新 `WildGS r11`：`Q1_STRICT_RATE = 0.0`，`Q1_ABSDOT_RATE = 0.0` | 未通过 |
| Q2 | Support Coverage | tile 四角中至少 `3/4` 在真实表面邻域内 | 有：每 tile `>= 3` 个角支持，整体 pass rate `>= 80%` | 当前没有 product surface-cell evaluator 结果 | 未验证 |
| Q3 | Unique Cell Ownership | 一个 cell 不能有多个 tile owner | 有：`duplicate_cells == 0` | 当前没有 product tile/cell 归属 evaluator 结果 | 未验证 |
| Q4 | No Overlap | 邻近 tile 投影重叠面积接近 0 | 有：pair overlap ratio `<= 1%`，fail pair 必须 `0` | 当前没有 product tile overlap evaluator 结果 | 未验证 |
| Q5 | Grid Regularity | 邻接 tile 是否落在同一局部规则格网 | 有：regular pair rate `>= 85%` | 当前没有 product grid regularity evaluator 结果 | 未验证 |

## P1-P3

| Checkpoint | 测试类目 | 计算方式 | 是否有硬阈值 | 当前结果 | 是否通过 |
| --- | --- | --- | --- | --- | --- |
| P1 | State Non-Regression | 状态机 `empty -> provisional -> confirmed -> locked` 不允许回退 | 有：`tsdf_state_regression == false` | 当前 donor 白盒没有产品状态机回放/审计结果 | 未验证 |
| P2 | Pose-Stable Drift | 仅在相机近静止窗口中统计 tile 中心/法线漂移 | 有：中心漂移 `< 3mm`，法线漂移 `< 5°`，且 `samples > 0`、`violations == 0` | 最新 `WildGS r11`：`P2_STATIC_WINDOWS = 0`，`P2_MIN_GT_WINDOW_MM = 4.54`，当前序列仍不满足可判通过的静止窗口条件 | 未通过 |
| P3 | Temporal Flicker | 已确认 tile 在固定窗口内不能频繁开关翻转 | 有：candidate tiles `> 0` 且 `flicker_fail_tiles == 0` | 当前 donor 白盒没有 product flicker evaluator 结果 | 未验证 |

## V1-V2

| Checkpoint | 测试类目 | 计算方式 | 是否有硬阈值 | 当前结果 | 是否通过 |
| --- | --- | --- | --- | --- | --- |
| V1 | Coverage Monotonicity | 全局覆盖率不能整体回退 | 有：`coverage_regression == false` | 当前 donor 白盒没有产品 coverage monotonicity evaluator 结果 | 未验证 |
| V2 | Gap Consistency | tile 间缝隙宽度稳定 | 有：gap stddev `<= 2.0mm` | 当前 donor 白盒没有产品 gap consistency evaluator 结果 | 未验证 |

## Current pass/fail snapshot

- 已通过：
  - `A`
  - `C`
  - `E`
  - `F`
  - `G`
  - `H`
  - `I`
  - `J`
  - `O`
- 明确未通过：
  - `D`
  - `K`
  - `L`
  - `P`
  - `Q`
  - `Q1`
  - `P2`
- 仍未验证/缺产品 evaluator：
  - `B`
  - `M`
  - `N`
  - `Q2`
  - `Q3`
  - `Q4`
  - `Q5`
  - `P1`
  - `P3`
  - `V1`
  - `V2`

## Product-owned world-state / evaluator bridge

- 产品态对齐说明：
  - `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/docs/world_state_evaluator_alignment.md`
- 自动评测脚本：
  - `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/scripts/eval_world_state.py`

The missing product-owned checkpoints should no longer be treated as "undefined".
They are now blocked only by one thing:

- the product UI / world-state layer must export a frame-by-frame JSON record matching the documented schema

Once that JSON exists, the evaluator can compute:

- `Q2`
- `Q3`
- `Q4`
- `Q5`
- `P1`
- `P3`
- `V1`
- `V2`

and can optionally compute `Q1` if surface distance / normal-dot fields are present.

## Unpassed checkpoint diagnosis

- Detailed cause split:
  - `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/docs/unpassed_checkpoint_diagnosis.md`
