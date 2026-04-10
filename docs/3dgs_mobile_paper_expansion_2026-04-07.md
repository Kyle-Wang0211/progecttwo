# A 3DGS Reconstruction and Interaction System for Consumer-Grade Mobile Devices

扩展稿日期：2026-04-07

本文档基于两类材料整理而成：

- 本仓库当前实现，包括 iOS 端、`background_upload_broker`、`control_plane`、`donor_whitebox` 与移动端质量控制相关模块。
- 截至 2026 年 4 月 7 日可公开访问的第一手资料，包括 3DGS、HI-SLAM2、GS-SLAM、Photo-SLAM、WildGS-SLAM、3DGUT、SqueezeMe、PocketGS、Mobile-GS 及 Apple 官方后台传输文档。

下面内容以“可直接并入论文正文”为目标撰写，同时尽量保持与当前代码实现一致。

## 1. Introduction

近年来，消费级移动设备已经成为最普及的视觉采集平台。智能手机与轻量化头显具备高分辨率 RGB 相机、稳定的触摸交互与持续联网能力，使其成为大规模三维内容生产的重要入口。然而，传统高质量三维重建方法通常依赖桌面级 GPU、长时间离线优化和复杂的部署环境，这与普通用户“随手拍摄、快速查看、即时分享”的交互期望之间仍存在明显落差。

3D Gaussian Splatting（3DGS）为这一问题提供了新的技术基础。与早期神经辐射场方法相比，3DGS 通过显式的高斯表示、密度控制和可见性感知的快速光栅化渲染，在保持高质量新视角合成效果的同时显著提升了训练和渲染效率。自 2023 年 3DGS 提出以来，研究重点迅速从静态离线重建扩展到在线 SLAM、动态场景建模、失真相机适配、移动端推理和端侧训练等方向。这说明 3DGS 已从一种高质量渲染表示，逐步演进为一种兼顾表示、优化和交互的系统级技术。

尽管如此，将 3DGS 真正落地到消费级移动设备仍面临三类关键挑战。第一，移动端受限于算力、内存、热功耗和后台执行策略，难以独立完成完整的 3DGS 训练与高质量渲染。第二，真实世界采集过程存在模糊、曝光异常、纹理贫乏、视角重叠不足、动态物体干扰和网络不稳定等问题，这些因素会直接影响重建质量。第三，面向普通用户的系统不仅需要“能重建”，还需要保证后台上传可靠、云端任务状态可追踪、结果可回传、移动端可平滑查看，并且在失败或卡顿时具备明确的恢复路径。

针对上述问题，本文面向消费级移动设备提出一种 3DGS Reconstruction and Interaction System。系统采用端-边-云协同设计：移动端负责视频采集、质量引导、后台传输与结果交互；边缘代理或控制平面负责任务创建、上传编排、状态管理与工人调度；云端 GPU worker 负责基于 HI-SLAM2 的预处理、训练、导出和结果封装。围绕这一总体思路，本文重点研究以下问题：

1. 如何在不牺牲普通用户交互体验的前提下，将 3DGS 重建拆分为可在移动端和云端协同执行的流水线。
2. 如何基于 HI-SLAM2 将单目 RGB 视频转换为具备较好几何一致性和可交互性的 3DGS 结果。
3. 如何在移动设备上同时兼顾查看清晰度、交互流畅性、热约束与弱区域提示。
4. 如何用统一的状态机、产物清单和失败恢复机制，把“研究级算法”转化为“面向终端用户的系统”。

本文的主要贡献可概括为以下四点。其一，设计并实现了一个适用于消费级移动设备的端-边-云 3DGS 重建系统，将后台上传、云端训练和移动端交互连接为统一闭环。其二，在算法层选择 HI-SLAM2 作为重建核心，并结合任务调度、阶段状态、卡顿检测和产物清单机制完成工程化落地。其三，在移动端引入采集质量反馈、视角重叠估计、热约束分级和渐进式预览机制，以提升重建可用性与交互连续性。其四，通过质量、性能和可用性实验验证系统有效性，并进一步分析当前方案在几何一致性、低质量区域修复和对云端资源依赖方面的局限。

## 2. Related Work

### 2.1 3DGS Representation and Rendering

Kerbl 等人在 2023 年提出 3D Gaussian Splatting，将场景表示为各向异性三维高斯，并结合交错优化、密度控制和可见性感知光栅化，实现了高质量且接近实时的新视角合成。该工作奠定了后续 3DGS 系统的核心表示基础，也使 3DGS 成为连接三维重建与实时交互的关键中间层。

在此之后，3DGS 渲染能力持续扩展。Wu 等人在 CVPR 2025 提出的 3DGUT 用 Unscented Transform 替代 EWA splatting，将 3DGS 从理想针孔相机拓展到非线性投影、滚动快门和二次光线等更复杂的成像场景。对于面向手机拍摄的系统而言，这一点尤其重要，因为真实移动相机通常伴随畸变、曝光波动和非理想成像过程。因此，在本文系统中，3DGS 不仅是训练后的结果表达方式，也应被视为未来支持移动端复杂成像条件的重要扩展接口。

### 2.2 3DGS-Based SLAM and Online Reconstruction

随着 3DGS 的普及，其与 SLAM 的结合成为近两年的热点方向。Gaussian Splatting SLAM（CVPR 2024）证明了 3DGS 可作为单目 SLAM 的统一场景表示，用于跟踪、建图和高质量渲染。GS-SLAM（CVPR 2024）进一步在稠密视觉 SLAM 中引入自适应高斯扩展与粗到细位姿跟踪策略，在效率和精度之间取得平衡。Photo-SLAM（CVPR 2024）则通过 hyper-primitives 与 Gaussian-Pyramid 训练策略，在单目立体、双目和 RGB-D 输入下实现实时定位与高保真贴图，并展示了在 Jetson AGX Orin 等嵌入式平台上的可运行性。

面向更具挑战性的场景，WildGS-SLAM（CVPR 2025）将不确定性引入动态环境中的单目高斯 SLAM，通过动态物体抑制增强跟踪与重建鲁棒性。HI-SLAM2（T-RO 2025）则从几何一致性出发，将单目先验深度、学习式稠密 SLAM 与 3DGS 建图结合起来，并通过基于网格的尺度对齐策略、闭环后的 anchored keyframe update 和高斯显式变形，实现了单目 RGB 条件下兼顾重建质量与渲染质量的在线场景重建。

总体来看，现有工作主要解决“如何让 3DGS 与 SLAM 结合得更准、更快”的问题，而本文更加关注“如何让这一能力在消费级移动设备上成为可用系统”。因此，本文并不把贡献重点放在新型 3DGS 表达本身，而是放在面向手机场景的系统组织、后台传输、状态管理、移动端优化和交互链路上。

### 2.3 Mobile Gaussian Rendering and On-Device Optimization

移动端 3DGS 已经成为最新研究前沿，但整体上仍处于快速发展阶段。SqueezeMe（SIGGRAPH 2025）针对高斯人体 avatar，利用轻量化纠正项蒸馏与 Vulkan 自定义 splatting pipeline，在 Meta Quest 3 上实现了 72 FPS 的多 avatar 实时动画与渲染，说明移动级硬件已经具备承载特定 3DGS 任务的可能性。PocketGS（2026）更进一步研究了端侧训练问题，提出面向手机训练预算和峰值内存约束的三类协同算子，使 3DGS 在移动设备上的本地优化成为可能。Mobile-GS（2026）则聚焦移动端推理，通过去排序的深度感知 order-independent 渲染与视角相关增强策略，降低移动设备上的高斯渲染成本。

这些工作表明，未来的 3DGS 系统不一定必须完全依赖云端。然而，就当前消费级手机而言，完整的高质量三维重建仍然更适合采用“移动端采集与交互 + 云端重建训练”的协同模式。因此，本文的系统设计遵循一条更务实的路径：在移动端执行对时延敏感、对交互友好的轻量逻辑，在云端执行高负载的 HI-SLAM2 预处理与 3DGS 训练，从而在工程可行性和用户体验之间取得平衡。

## 3. Overall System Design

### 3.1 Design Goals

本文系统的总体目标并不是将所有重建环节强行压缩到手机上，而是围绕消费级移动设备构建一个“可采集、可上传、可训练、可回传、可交互”的闭环系统。为此，系统需要满足以下设计原则：一是前台轻交互、后台长任务解耦；二是任务状态可观测；三是训练结果可封装、可校验、可渐进呈现；四是当网络、GPU worker 或训练过程出现异常时，系统能够检测、终止并给出明确状态。

### 3.2 Architecture

从实现角度看，当前代码库已经形成三层架构。

第一层是移动客户端。iOS 端使用后台 `URLSession` 管理上传，允许任务在应用进入后台后继续执行；同时客户端承担视频选择、采集质量反馈、结果下载与查看功能。第二层是任务编排层。当前仓库中存在两个部署形态：其一是本地 `background_upload_broker` 原型，用于把手机上传的视频落盘到本机，再通过 SSH 转发到远端 GPU；其二是面向生产的 `control_plane`，通过对象存储签名上传、数据库持久化和 pull-based worker 调度实现更可扩展的云端服务。第三层是云端 GPU worker。worker 从控制平面领取任务，下载输入视频，运行 donor pipeline 中的 HI-SLAM2/3DGS 重建流程，上传最终产物并回报运行时状态。

如果按论文表述，可将其概括为“移动端采集与交互层、边缘/控制平面编排层、云端重建执行层”三部分。相比把所有逻辑硬塞到单个 App 内部，这一分层更适合处理消费级场景中的大视频上传、长时训练和不稳定网络。

### 3.3 End-to-End Workflow

系统完整流程如下。用户首先在手机端采集或选择视频，客户端根据后台上传接口创建任务并发起传输。上传完成后，控制平面将任务置入队列，并由可用 worker 领取。worker 先执行 CPU 侧预处理与相机/几何准备，再进入 GPU 训练阶段，随后导出 3DGS 产物、度量信息和 viewer manifest。最终，移动端轮询任务状态并下载结果，用户可以在手机上查看和交互该三维结果。

这一流程的关键不是单个算法步骤，而是步骤之间的“相位衔接”。当前控制平面状态模型已经显式区分 `created`、`uploading`、`queued`、`assigned`、`reconstructing`、`training_full`、`exporting`、`completed`、`failed` 等阶段，并支持时间线摘要和工人状态心跳。这种明确的阶段划分使系统非常适合在论文中作为“面向终端用户的研究系统”进行表述，因为它将算法运行状态转化为用户可理解的产品状态。

### 3.4 Prototype Path and Production Path

值得说明的是，当前代码中同时存在一条本地原型路径与一条生产化路径。原型路径通过本地 broker 和 SSH 方式将视频发往丹麦的远端 5090 机器，适合快速调试真实手机闭环；生产路径则以对象存储、Postgres 和 pull-based worker 为核心，更适合多 GPU 扩展和公网部署。论文中可以把这种设计写成“分阶段演进的系统实现”：先通过单机中继打通手机到 GPU 的最小闭环，再演进为标准化控制平面。这种叙述既符合当前代码现实，也能体现系统工程的完整性。

## 4. Implementation of the 3DGS Reconstruction Pipeline

### 4.1 Mobile-Side Input Preparation and Background Upload

在移动端，重建任务以视频为主要输入。当前 `BuildRequest` 已将输入源、构建模式和设备等级显式抽象出来，使系统能够在不同设备条件下采用不同策略。`PipelineRunner` 在接收到视频输入后，会记录 `generate_start` 审计事件，上传视频、创建远端任务并轮询状态。对普通用户而言，这一层的关键要求不是“最快”，而是“后台可靠”。因此，控制平面的 iOS 协调器采用后台 `URLSessionConfiguration.background(withIdentifier:)`，并设置 `sessionSendsLaunchEvents` 和 `waitsForConnectivity`，与 Apple 官方后台传输机制保持一致。这样一来，即使应用进入后台，上传也可以由系统托管继续执行。

这一路径契合消费级手机的真实使用方式：用户在拍摄后往往不会持续停留在 App 前台等待上传完成，因此后台上传能力是系统可用性的前提，而不是附属优化。

### 4.2 Task Scheduling and Runtime State Management

上传完成后，系统进入任务调度阶段。在 broker 原型中，视频首先被落盘至本地，再由 `GPUDispatcher` 通过 SSH 发送到远端机器，随后启动闭环脚本并持续同步运行状态。在控制平面版本中，任务由 Postgres 和对象存储支撑，worker 通过注册、心跳、领取任务和运行时回报等接口参与整个流程。与简单的“上传文件后等待完成”不同，这一实现已经具备了完整的作业编排特征：任务输入、任务状态、worker 能力、工人分配、运行中度量和产物清单彼此独立，从而能够支撑更复杂的实验设计和系统扩展。

在论文中，可将这一点归纳为“系统通过显式状态机组织 HI-SLAM2 流水线，并通过运行时度量解耦用户视图与算法内部阶段”。这使得系统可以清晰地区分上传、预处理、训练、导出、回传等不同延迟来源，也便于后续开展性能剖析和可用性实验。

### 4.3 HI-SLAM2-Based Preprocessing and Cloud Training

本文系统在重建核心上采用 HI-SLAM2。其适配原因主要有三点。第一，HI-SLAM2 面向单目 RGB 输入即可工作，更符合普通手机场景。第二，该方法强调几何一致性，通过单目先验深度、学习式稠密 SLAM 和 3DGS 地图联合实现较好的几何与外观平衡。第三，HI-SLAM2 在闭环后支持 anchored keyframe update 和显式高斯变形，更适合在线或准在线的系统组织方式。

从当前 worker 实现来看，任务被分为 `prep_pending`、`prepping`、`gpu_ready` 和 `training` 等内部阶段。CPU 预处理完成后，系统会把任务转入 GPU 等待阶段；训练完成后，再统一上传产物并汇报完成状态。这样的组织方式说明，本文系统实际上已经将 HI-SLAM2 从“单个研究脚本”拆解为“可调度的多阶段任务”。这对于消费级移动场景尤为关键，因为在多任务环境中，CPU 预处理和 GPU 训练的资源瓶颈并不相同，若不显式拆分阶段，系统很难在多 worker 或多租户环境中扩展。

此外，底层训练接口已经暴露出若干面向系统化控制的重要参数，例如最大高斯数、densify interval、高斯预算和质量下限 `quality_floor_psnr`。这意味着本文不仅可以从算法层描述 HI-SLAM2，也可以从系统层讨论“如何给 3DGS 训练施加预算约束”。对于消费级移动设备而言，这类预算接口的重要性不亚于最终质量指标本身，因为它们直接决定了可接受的云端成本和用户等待时间。

### 4.4 Export, Packaging, and Data Transmission

在训练结束后，系统并不是简单返回一个裸 `PLY` 文件，而是执行产物封装与校验。`PipelineRunner` 会在本地 staging 目录下写入模型文件，计算文件描述符、策略哈希与总产物哈希，生成 `WhiteboxArtifactManifest` 并执行 package 校验。控制平面一侧则继续维护 `ArtifactManifest`，其中不仅包含 `primary_artifact`，还预留了 `preview`、`metrics` 和 `viewer_manifest` 等描述项。

这一设计值得在论文中明确强调。它表明本文系统把“3DGS 结果”视为一个带有版本、完整性和可视化附属信息的复合产物，而不是单一模型文件。对消费级应用来说，这一差异很重要，因为移动端查看、结果分享和失败恢复都依赖更高层次的产物协议，而不是单个几何文件本身。

### 4.5 Stall Detection, Failure Handling, and Scheduling Robustness

消费级场景中的另一个现实问题是长任务失败。`PipelineRunner` 在轮询远端状态时已经引入卡顿检测逻辑，包括排队与处理中不同轮询间隔、最小进度增量、卡顿超时和绝对最大超时等机制；worker 侧则在本地阶段退出、运行时陈旧、取消请求和产物回传异常等情况下执行失败汇报和资源清理。与许多只关注成功路径的研究原型相比，这种实现更接近真实产品系统。

因此，在论文的实现章节中，建议单独加入“鲁棒性与任务恢复”小节。因为对消费级设备而言，网络波动、App 切后台、worker 断连和远端脚本异常都不是边缘情况，而是设计输入。只有把这些问题纳入系统设计，3DGS 才能真正从实验室演示走向面向普通用户的应用。

## 5. Mobile Interaction, Rendering, and Quality Optimization

### 5.1 Capture-Side Quality Guidance

移动端采集质量直接决定后续 3DGS 重建的上限。当前代码中，`QualityFeedback` 会在一个约 30 帧的滚动窗口内持续统计模糊、曝光、纹理和运动等指标，并根据拒绝帧和告警帧比例生成实时质量等级与提示。与此同时，`OverlapEstimator` 通过原生桥接估计相邻帧重叠率，并以正向 0.80、侧向 0.65 的经验阈值为下限，从而约束视角冗余与覆盖不足问题。

这类设计使系统能够在“问题形成之前”介入，而不是等到云端训练完成后才告诉用户结果不好。论文中可以据此强调：本文并不把移动端视为单纯的数据采集器，而是视为“低成本前置质量控制器”。这种角色划分是面向消费级系统的重要工程经验。

### 5.2 Mobile Interaction and Progressive Result Delivery

在结果查看层面，当前代码已经出现了较清晰的渐进式交互思路。`ObjectModeV2PipelineRunner` 支持 `preview`、`default` 和 `hq` 三个阶段资产的逐步就绪；`ObjectModeV2ViewerViewController` 则在移动端根据 manifest 加载不同阶段的模型，并提供旋转、缩放和视角复位等基础交互。虽然这条对象模式路径仍可视为正在演进的原型，但其系统意义非常明确：对于移动设备而言，time-to-first-view 往往比“一次性拿到最高质量结果”更重要。

因此，本文在论文中可以将“渐进式结果返回”写成一项面向移动端体验的重要策略：系统先回传可快速查看的预览结果，随后再以默认成品和高清成品逐步替换。这一设计能够显著缩短用户等待首个可交互结果的时间，并避免移动端一次性加载过大资产造成的卡顿。

### 5.3 Thermal-Aware Rendering and Smoothness Control

移动端查看 3DGS 时，除了清晰度之外，另一个核心目标是保持稳定帧率并控制热量积累。当前 `ThermalQualityAdapter` 已把渲染质量划分为 `nominal`、`fair`、`serious` 和 `critical` 四个等级，并分别对应不同的三角面预算与目标帧率，其中目标帧率从 60 FPS 逐步降低到 30 FPS 与 24 FPS。同时，系统会按等级关闭翻转动画、波纹效果、金属 BRDF 和部分触觉反馈，以减少额外开销。

这说明本文系统已经具备“把热状态作为实时控制信号”的能力，而不是单纯在达到过热后粗暴降频。对消费级移动设备而言，这一点至关重要，因为连续查看三维内容本身就是一种持续负载场景。如果没有热量感知的质量调度，短时间内得到的高清显示可能会以长时间卡顿和功耗飙升为代价。

### 5.4 Handling Low-Quality Regions

低质量区域的处理可以分为两个时间尺度。第一是在采集时提前避免。当前系统已经具备覆盖网格、重叠估计、模糊与纹理告警等机制，因此能够把未覆盖区域、低纹理区域和过快运动区域转化为采集阶段的即时反馈。第二是在查看时进行柔性处理。当前代码中的渐进式多阶段结果、热约束降级和状态化 viewer manifest，本质上都属于“不要强迫移动端一次性承载最重结果”的策略。

从论文写作角度，建议将这一部分表述为：“系统当前通过前置质量引导与渐进式显示共同应对低质量区域，而针对区域级不确定性的自动修复和局部重建仍有待进一步研究。”这种写法既符合当前实现，也为后续扩展到 patch-level uncertainty、局部补拍或视点推荐保留空间。

## 6. Experiments and Analysis

### 6.1 Experimental Objectives

本文实验建议围绕三个维度展开。第一是结果质量，包括新视角合成质量、几何一致性和覆盖完整性。第二是运行性能，包括上传耗时、排队耗时、预处理耗时、训练耗时、导出与回传耗时、移动端查看帧率与热状态。第三是系统可用性，包括后台上传成功率、任务失败恢复率、time-to-first-preview、用户完成一次有效扫描所需时长，以及最终可交互结果的主观满意度。

### 6.2 Recommended Experimental Protocol

在数据集层面，可结合公开数据和真实手机视频两类输入。公开数据部分建议延续 HI-SLAM2 的 Replica、ScanNet 和 ScanNet++ 评价逻辑，用于对齐已有高斯 SLAM 研究；真实数据部分建议使用 iPhone 实拍视频，重点评估在消费级设备和真实网络条件下的端到端表现。

在对比方法上，建议至少包括原始 3DGS 离线管线、GS-SLAM、Photo-SLAM、HI-SLAM2 以及系统自身不同配置的消融版本。对于本文系统，应重点做以下消融：有无移动端质量引导，有无热约束适配，有无渐进式预览，有无 broker/control-plane 调度层，以及不同 worker 配置下的排队与吞吐变化。

在指标选择上，建议除了常见的 PSNR、SSIM、LPIPS 和几何误差之外，再加入更符合系统视角的指标，例如：

1. 上传结束到首个可查看结果的延迟。
2. 上传结束到最终高清结果的延迟。
3. 移动端查看平均帧率和 P95 帧时。
4. 热状态停留比例与降级触发次数。
5. 失败任务比例、取消成功率和恢复成功率。
6. 用户在限定时间内完成有效扫描的成功率。

### 6.3 Findings Supported by the Current Repository

根据仓库中的当前 white-box checkpoint report，系统已经在若干关键项上表现出可用性基础。例如，在当前记录中，帧接收率、扫描中训练启动、点数增长、loss 收敛、耗时日志存在性、最终点数规模和颜色准确性等检查项均已通过。其中最终点数达到约 7.96M，`psnr_mean` 为 14.7141，说明当前系统至少已经具备从真实输入走到可用 3DGS 结果的能力。

但同一份报告也清楚表明，系统仍有明显短板。几何相关的 P95 表面漂移、体积/质心匹配、支持覆盖率、非重叠性、网格规则性、姿态稳定漂移和 gap consistency 等指标仍未通过或尚未验证。这意味着当前系统更接近“端到端闭环已经打通、光度结果可接受、几何鲁棒性仍需加强”的阶段。换言之，论文不应把系统描述成已经完全解决移动端 3DGS 的所有问题，而应诚实地把重点放在“系统方法有效，几何质量和弱区域修复仍是主要瓶颈”这一结论上。

### 6.4 Discussion of Limitations

结合代码与现有实验结果，本文系统的局限性主要体现在以下几个方面。首先，尽管移动端已经具备后台上传和轻量交互能力，但高质量 3DGS 训练仍显著依赖云端 GPU，这意味着系统总体时延仍受网络和远端资源影响。其次，当前流程对静态室内场景更友好，在动态物体较多、尺度变化显著或纹理极弱的场景中，几何一致性仍然容易下降。再次，移动端虽然已经具备热约束控制与渐进式查看机制，但真正面向手机 GPU 的统一 3DGS 原生 viewer 仍有进一步优化空间。最后，系统当前对低质量区域的处理更多依赖采集阶段反馈和阶段式显示，尚未形成自动化的区域级补拍或局部增量优化闭环。

尽管存在上述局限，本文系统仍然具有明确价值。它证明了消费级移动设备上的 3DGS 问题并不只是“如何让一个渲染 kernel 更快”，而是“如何把采集、上传、训练、封装、回传和交互组织成一个用户可用的系统”。这一点正是本文相对于单纯算法论文的核心意义。

## 7. Writing Notes and Evidence Mapping

以下内容不建议直接放入论文正文，但可作为后续改稿时的实现依据。

### 7.1 Code-Level Evidence

- 移动端上传与任务启动：`progect2/progect2/Core/Pipeline/PipelineRunner.swift`
- 后台上传协调：`control_plane/ios/ControlPlaneUploadCoordinator.swift`
- 本地 broker 原型：`background_upload_broker/app.py` 与 `background_upload_broker/dispatcher.py`
- 生产控制平面：`control_plane/README.md`、`control_plane/app/models.py`
- worker 阶段管理与产物回传：`control_plane/worker_agent/main.py`
- 底层训练相位与质量接口：`progect2/progect2/aether_cpp/include/aether/aether_training_c_api.h`
- 采集质量反馈：`progect2/progect2/Core/Quality/QualityFeedback.swift`
- 视角重叠估计：`progect2/progect2/Core/Quality/OverlapEstimator.swift`
- 热约束分级：`progect2/progect2/Core/Quality/Performance/ThermalQualityAdapter.swift`
- 渐进式资产返回与 viewer：`progect2/progect2/Core/Pipeline/ObjectModeV2PipelineRunner.swift`、`progect2/progect2/App/ObjectModeV2/ObjectModeV2ViewerViewController.swift`

### 7.2 Online Primary Sources

- [3D Gaussian Splatting for Real-Time Radiance Field Rendering](https://arxiv.org/abs/2308.04079), arXiv:2308.04079, submitted 2023-08-08
- [HI-SLAM2 Project Page](https://hi-slam2.github.io/)；[HI-SLAM2: Geometry-Aware Gaussian SLAM for Fast Monocular Scene Reconstruction](https://arxiv.org/abs/2411.17982), arXiv:2411.17982, latest arXiv revision 2026-02-02
- [Gaussian Splatting SLAM](https://openaccess.thecvf.com/content/CVPR2024/html/Matsuki_Gaussian_Splatting_SLAM_CVPR_2024_paper.html), CVPR 2024
- [GS-SLAM: Dense Visual SLAM with 3D Gaussian Splatting](https://openaccess.thecvf.com/content/CVPR2024/html/Yan_GS-SLAM_Dense_Visual_SLAM_with_3D_Gaussian_Splatting_CVPR_2024_paper.html), CVPR 2024
- [Photo-SLAM: Real-time Simultaneous Localization and Photorealistic Mapping for Monocular Stereo and RGB-D Cameras](https://openaccess.thecvf.com/content/CVPR2024/html/Huang_Photo-SLAM_Real-time_Simultaneous_Localization_and_Photorealistic_Mapping_for_Monocular_Stereo_CVPR_2024_paper.html), CVPR 2024
- [WildGS-SLAM: Monocular Gaussian Splatting SLAM in Dynamic Environments](https://openaccess.thecvf.com/content/CVPR2025/html/Zheng_WildGS-SLAM_Monocular_Gaussian_Splatting_SLAM_in_Dynamic_Environments_CVPR_2025_paper.html), CVPR 2025
- [3DGUT: Enabling Distorted Cameras and Secondary Rays in Gaussian Splatting](https://openaccess.thecvf.com/content/CVPR2025/html/Wu_3DGUT_Enabling_Distorted_Cameras_and_Secondary_Rays_in_Gaussian_Splatting_CVPR_2025_paper.html), CVPR 2025
- [SqueezeMe: Mobile-Ready Distillation of Gaussian Full-Body Avatars](https://arxiv.org/abs/2412.15171), SIGGRAPH 2025 / arXiv:2412.15171
- [PocketGS: On-Device Training of 3D Gaussian Splatting for High Perceptual Modeling](https://arxiv.org/abs/2601.17354), arXiv:2601.17354, latest arXiv revision 2026-03-28
- [Mobile-GS: Real-time Gaussian Splatting for Mobile Devices](https://arxiv.org/abs/2603.11531), arXiv:2603.11531, submitted 2026-03-12
- Apple Developer Documentation: [background(withIdentifier:)](https://developer.apple.com/documentation/foundation/urlsessionconfiguration/background%28withidentifier%3A%29), [isDiscretionary](https://developer.apple.com/documentation/foundation/urlsessionconfiguration/isdiscretionary), [Downloading files in the background](https://developer.apple.com/documentation/foundation/downloading-files-in-the-background?language=objc)

### 7.3 Suggested Next Writing Steps

如果后续继续扩写正文，建议优先补三部分：

1. 增加一张系统架构图，把移动端、broker/control plane、worker、对象存储和结果回传画清楚。
2. 增加一张时序图，把“上传-排队-预处理-训练-导出-回传-查看”各阶段时延标出来。
3. 增加一张实验总结表，将当前已通过与未通过的 white-box 指标对应到“系统优势”和“主要瓶颈”。
