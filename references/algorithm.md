# 算法、配置与审计

## 流程

快照输入坐标、有效掩码、ROI、原帧映射和 VSK 布局，再顺序运行 Nexus 原生步骤。每阶段记录新增点帧，恢复原已有值及前面已接受结果。VSK 供体组至少 4 个目标点，再检查点间距离变异和几何退化；同体段不自动等于刚体。

默认上限：Woltring 0.05 秒、刚体 0.25 秒、Pattern 0.10 秒、Kinematic Fill 0.25 秒。ComputeSubjectMotion 使用已有标定 VSK，不重做静态。未补入可能来自上限，不能认为 Nexus 在其他设置下绝对无解。

备用先用至少 3 供体的局部刚体扩展；随后用原始输入共同完整帧学习 PCA 子空间，执行随机游走 Kalman 滤波和 RTS 后向平滑。全局共同完整帧不足时尝试局部体段，再退到岭回归、相对偏移插值/保持，最后时间插值/最近观测保持。不把原生/备用估计当训练真值；已有边界可用于预测偏差衔接，边界不改。

这是独立实现，不是 MoGapFill 原代码移植/数值等价，也不是 Gløersen PCA-R2。没有周期识别、深度模型、全身骨架 IK 优化或自动算法选择交叉验证。

## CONFIG

| 参数 | 默认与含义 |
|---|---|
| `allow_low_reliability_completion` | True；接受 LOW 写回时使用，策略未决定时先澄清或设 False |
| `kalman_min_complete_frames` | 20；输入共同完整帧最低数量 |
| `kalman_max_dimensions` / `kalman_energy` | 8 / 0.995；子空间维度上限/能量 |
| `observation_noise_mm` | 1.5；模型假设，不是测量精度标定 |
| `maximum_rigid_distance_cv` | 0.10；候选组距离变异阈值 |
| `maximum_rigid_fit_rmse_mm` | 20；内部残差筛选，不是目标真实误差 |
| `invalidate_old_model_outputs` | True；轨迹变更后失效旧角度/力矩等 |
| `protected_roots` | 空列表；自行填需保护原始目录，无个人盘符预设 |
| `cache_root` | 空字符串；默认 LOCALAPPDATA，可改英文绝对缓存根 |

## 审计

导出 `trial._NGF.c3d`，只含 ROI，源 C3D/VSK 保留，同名旧 NGF 导出先备份。算法不滤波/建模模拟量，但 Nexus 导出器可能重写重复标签/元数据，不能承诺字节级保留模拟量。

`trial_NGF.provenance.csv` 每 ROI 帧/目标点一行，包含 OriginalFrame、坐标、Source、Method、LowReliability、Reliability。INPUT_EXISTING 来源未知；NEXUS_ESTIMATE 是本次原生补入，NATIVE_UNVALIDATED；FALLBACK_ESTIMATE 全部 LowReliability=1、LOW。排除/未解决点明确列出。

`ModelSD_mm_not_validated_error` 仅模型内部标准差，不是真实误差、误差上界或95%置信保证。

`trial_NGF.summary.json` 包含计数、排除点、警告、哈希和独立 BTK 核对；NGF_reports 保存输入数组/磁盘备份及阶段。只有 export_verified 可报告完整完成。SDK/BTK 容差 0.02 mm 用于序列化一致性，不是位置精度验收值。

ROI 外冻结；Nexus 数组可能从第 1 帧填充或只含加载区间，按数组长度/试验范围确定原帧映射，不一律把第 0 行当原始第 1 帧。
