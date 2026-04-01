# White-Box Checkpoint Report

| Checkpoint | 测试类目 | 计算方式 | 是否有硬阈值 | 当前结果 | 是否通过 |
| --- | --- | --- | --- | --- | --- |
| A | 3x3 房间/路径生成 | sequence/export count | `expected frames generated` | hislam2_traj_full=600, monogs_camera_count=600 | 通过 |
| B | 队列/吞吐压力 | product burst evaluator | `burst metrics required` | missing queue/burst metrics | 未验证 |
| C | 帧接收率 | accepted frame count | `>=100` | hislam2=600, monogs_camera_count=600 | 通过 |
| D | 帧选择 / keyframe 触发 | MonoGS RMSE + keyframe stats | `RMSE <= 0.03m and kf_count >= 10` | rmse=0.9890m, kf_count=185, camera_count=600 | 未通过 |
| E | 扫描中训练启动 | training/loss evidence | `training starts during scan` | loss_steps=1366, points=7959829 | 通过 |
| F | 扫描中点数增长 | point growth | `points > 0 and growth evidence` | final_points=7959829 | 通过 |
| G | loss 收敛 | Photo-SLAM ema_loss | `final/first < 3 or steps > 0` | first=0.333076, last=0.090794, steps=1366 | 通过 |
| H | 总耗时统计 | presence of metric logs | `(soft) timing artifacts exist` | render_metric_count=216 | 通过 |
| I | 最终高斯数量 | Photo-SLAM point count | `>=1000000` | points=7959829 | 通过 |
| J | 颜色准确性 | PSNR / final color PLY | `psnr_gaussian mean > 10` | psnr_mean=14.7141, psnr_last=12.2161 | 通过 |
| K | 表面漂移 P95 | WildGS geometry eval | `K_P95_MM < 50` | K_P95_MM=906.854 | 未通过 |
| L | 体积/质心匹配 | WildGS geometry eval | `centroid <= 200mm and extents within 20% of 3m` | centroid_mm=520.565, bbox=[1.2021, 0.7519, 1.1075] | 未通过 |
| M | TSDF active block 数 | product TSDF export | `tsdf_active_blocks >= 30000` | missing product TSDF export | 未验证 |
| N | tile hit rate | product world-state | `tile hit metrics required` | missing tile hit-rate export | 未验证 |
| O | integrated frame count | camera_count / traj_full | `>=100 and full path reached` | camera_count=600, camera_max_id=599, hislam2_traj_full=600 | 通过 |
| Q1 | Surface Adhesion | world-state or WildGS geometry | `pass rate >= 0.8, <20mm, dot>0.70` | pass_rate=1.0000 | 通过 |
| Q2 | Support Coverage | product world-state evaluator | `pass rate >= 0.8` | pass_rate=0.6666666666666666, passing_tiles=416, tiles_with_support=624 | 未通过 |
| Q3 | Unique Cell Ownership | product world-state evaluator | `duplicate_cells == 0` | duplicate_cells=0, frames_with_duplicates=0 | 通过 |
| Q4 | No Overlap | product world-state evaluator | `violating_pairs == 0 and overlap <= 1%` | checked_pairs=1560, max_overlap_ratio=1.0000003170460439, violating_pairs=1560 | 未通过 |
| Q5 | Grid Regularity | product world-state evaluator | `regular_pair_rate >= 0.85` | duplicate_uv=0, pair_total=0, regular_pair_rate=0.0, regular_pairs=0, spacing_stddev=0.0 | 未通过 |
| P1 | State Non-Regression | product world-state evaluator | `state_regressions == 0` | state_regressions=0 | 通过 |
| P3 | Temporal Flicker | product world-state evaluator | `flicker_fail_tiles == 0` | candidate_tiles=6, flicker_fail_tiles=0 | 通过 |
| V1 | Coverage Monotonicity | product world-state evaluator | `coverage_regressions == 0` | coverage_regressions=0 | 通过 |
| V2 | Gap Consistency | product world-state evaluator | `gap_stddev_mm <= 2.0` | gap_count=0, gap_stddev_mm=0.0 | 未通过 |
| P2 | Pose-Stable Drift | WildGS geometry eval | `samples > 0 and center drift < 3mm and normal drift < 5deg` | static_windows=0.0, min_gt_window_mm=4.540 | 未通过 |
| P | 状态不回溯（粗粒度） | aggregate P1/P2/P3 | `(soft) all subchecks pass` | P1=PASS, P2=FAIL, P3=PASS | 未通过 |
| Q | 表面吸附（粗粒度） | aggregate Q1-Q5 | `(soft) all subchecks pass` | Q1=PASS, Q2=FAIL, Q3=PASS, Q4=FAIL, Q5=FAIL | 未通过 |
