# A 3DGS Reconstruction and Interaction System for Consumer-Grade Mobile Devices

## Abstract

3D Gaussian Splatting (3DGS) has become an efficient representation for photorealistic scene rendering, but most existing pipelines still assume desktop GPUs and offline expert workflows. This paper presents a practical 3DGS reconstruction and interaction system for consumer-grade mobile devices. The system adopts an edge-to-cloud design: the phone handles capture, lightweight quality guidance, background upload, and viewing, while remote GPU workers execute HI-SLAM2-based preprocessing, training, export, and packaging. The implementation includes explicit job states, runtime telemetry, artifact manifests, progressive result delivery, and thermal-aware mobile interaction. Current results show that end-to-end mobile-to-cloud reconstruction is already feasible, although geometric consistency remains weaker than photometric quality in difficult scenes. The paper argues that mobile 3DGS is fundamentally a systems problem involving capture, scheduling, robustness, packaging, and interaction, not only a rendering problem.

## 1. Introduction

Consumer mobile devices are now the most common visual sensing platform. Modern smartphones provide high-resolution RGB cameras, touch interaction, connectivity, and increasingly capable mobile GPUs. Yet high-quality 3D reconstruction still largely depends on desktop-class hardware and offline optimization, leaving a clear gap between research systems and ordinary mobile user expectations.

3D Gaussian Splatting (3DGS) has changed this landscape. Unlike earlier neural radiance field pipelines, 3DGS provides an explicit scene representation and efficient rendering through visibility-aware rasterization. Since 2023, it has expanded from static novel-view synthesis into SLAM, dynamic-scene modeling, and mobile-oriented optimization, making it a strong candidate for practical interactive systems.

However, directly deploying 3DGS on consumer smartphones remains difficult. First, mobile devices are constrained by thermal budget, memory, and sustained performance, so full 3DGS training is rarely realistic on-device. Second, mobile capture in real environments is noisy: users move too fast, scenes may be blurred or poorly exposed, texture can be weak, and viewpoint overlap may be insufficient. Third, a usable system must support more than accurate reconstruction. It must offer background upload, clear job progress, reliable cloud execution, result return, and stable mobile viewing.

This paper addresses these challenges through a full 3DGS reconstruction and interaction system for consumer-grade mobile devices. The phone is used for video input, quality guidance, background transfer, and result interaction. A broker or control plane manages task creation, scheduling, and runtime state. Remote GPU workers execute HI-SLAM2-based preprocessing, training, export, and artifact packaging. The contribution is therefore system-oriented: we show how recent 3DGS research can be reorganized into a robust mobile-facing pipeline.

The main contributions are fourfold. First, we implement an end-to-end mobile 3DGS system that integrates capture, upload, scheduling, cloud training, packaging, return, and interaction. Second, we turn HI-SLAM2 into a staged remote pipeline suitable for long-running user jobs. Third, we add mobile-specific mechanisms including capture quality feedback, overlap estimation, thermal-aware adaptation, and progressive asset delivery. Fourth, we analyze the current behavior of the system and identify geometric consistency as the main remaining bottleneck.

## 2. Related Work

The original 3DGS work by Kerbl et al. demonstrated that anisotropic Gaussian primitives can support high-quality real-time radiance-field rendering. Later work extended Gaussian rendering to more realistic imaging models. In particular, 3DGUT showed that Gaussian splatting can be generalized to distorted cameras, which is important for mobile imaging because consumer cameras often deviate from ideal pinhole assumptions.

At the same time, 3DGS was incorporated into SLAM. Gaussian Splatting SLAM, GS-SLAM, and Photo-SLAM showed that Gaussian representations can jointly support tracking, mapping, and high-quality rendering. WildGS-SLAM further improved robustness in dynamic environments through uncertainty-aware modeling and moving-object suppression. Among these systems, HI-SLAM2 is especially suitable for consumer mobile deployment because it targets monocular RGB input while emphasizing geometric consistency. It combines monocular prior depth, learned dense SLAM, mesh-scale alignment, anchored keyframe update after loop closure, and explicit Gaussian deformation.

Mobile Gaussian research has also accelerated. SqueezeMe showed that optimized Gaussian content can run on mobile-class hardware, while PocketGS and Mobile-GS explored on-device training and efficient mobile inference. These studies suggest that mobile 3DGS is increasingly practical, but for common smartphones a cloud-assisted design is still the most realistic path for high-quality reconstruction.

## 3. System Design

The system is organized into three layers: mobile client, coordination layer, and cloud execution layer. The mobile client handles capture or file selection, lightweight quality guidance, background upload, polling, and result viewing. The coordination layer appears in two forms in the current implementation. The first is a local broker prototype that receives uploads, stores them locally, and forwards them via SSH to a remote GPU machine. The second is a production-oriented control plane backed by object storage, a database, and pull-based workers. The cloud execution layer consists of GPU workers that claim jobs, download inputs, run the HI-SLAM2-based donor pipeline, upload artifacts, and report runtime status.

This architecture follows a simple principle: the phone stays responsive while long-running optimization is moved off-device. The workflow is exposed through explicit states such as upload, queueing, preprocessing, training, exporting, and completion, making the pipeline easier to schedule, diagnose, and present to users.

The end-to-end workflow is straightforward. A user captures or selects a video on the phone. The mobile app creates a job and starts a background upload. Once the upload completes, the coordination layer marks the task as queued and assigns it to an available worker. The worker performs CPU-side preprocessing, enters GPU-side training, exports the final artifacts, and reports completion. The mobile client then downloads the result and opens an interactive viewer. This staged design is essential for handling long cloud-side execution in a mobile-friendly way.

## 4. Reconstruction Pipeline Implementation

The reconstruction process begins with a typed mobile build request that encapsulates the input video, requested mode, and device tier. Once generation starts, the client records an audit event, uploads the video, starts a remote job, and polls for status. In the broker path, the input is first persisted locally and then forwarded to a remote machine. In the production path, the control plane manages signed upload targets, worker registration, heartbeats, scheduling, and artifact manifests.

On the worker side, HI-SLAM2 is converted from a research script into a staged job. The worker distinguishes preprocessing, waiting-for-GPU, training, artifact upload, and terminal states. This separation matters because CPU-side preparation and GPU-side optimization have different bottlenecks. It also allows the system to emit structured runtime payloads that include state, stage, phase name, progress fraction, and metrics. These runtime messages are useful not only for debugging but also for turning a long reconstruction job into a manageable user experience.

HI-SLAM2 is a suitable backbone for this design because it supports monocular RGB input while aiming for better geometry than simpler photometric pipelines. At the implementation level, the system exposes parameters such as Gaussian budget, densification interval, and quality floor through a lower-level training API. This makes the pipeline controllable as a service rather than a fixed black-box script, which is important when cloud cost, turnaround time, and user expectations must be balanced.

After training, the system packages results instead of returning only a raw geometry file. The pipeline writes outputs into a staging directory, computes hashes, generates a manifest, validates the package, and finalizes the artifact directory. On the control-plane side, the artifact manifest can describe the primary result, preview, metrics, and viewer metadata, which is important for progressive delivery and reliable redownload.

Robustness is built into the pipeline. The polling logic distinguishes queued and active states, uses stall detection based on progress changes, and enforces absolute timeout bounds. Worker-side execution includes cleanup, failure reporting, cancellation, and artifact handling for stale or terminated runs. In a real consumer setting, background suspension, unstable networks, and remote worker failures are normal conditions, not exceptions.

## 5. Mobile Interaction and Quality Optimization

The mobile client is more than an upload endpoint. During capture, it acts as a lightweight front-end intelligence layer. The system computes rolling-window quality feedback based on blur, exposure, texture richness, and motion indicators. It also estimates frame overlap and applies conservative thresholds to reduce the risk of insufficient coverage. This means that the phone can intervene before poor capture quality turns into an expensive cloud-side reconstruction failure.

For viewing, the system already supports progressive multi-stage result delivery. A dedicated object-mode pipeline models three stages: preview, default, and high-quality. As remote stages become ready, the client downloads the corresponding assets, updates a manifest, and opens them in an interactive viewer. The viewer supports orbit, zoom, and reset operations and automatically loads the highest available stage. This design improves time-to-first-view, which is a critical usability metric on mobile devices.

Mobile smoothness is further protected through thermal-aware adaptation. The system defines multiple rendering tiers with different triangle budgets and target frame rates. Under higher thermal pressure, frame targets are reduced and expensive visual effects are selectively disabled. This mechanism treats thermal state as a live control signal rather than a passive warning. For sustained mobile interaction, stable viewing quality is often more valuable than briefly maximizing visual fidelity.

At present, the system handles low-quality regions through two practical strategies. First, it tries to prevent them with capture-time feedback. Second, it reduces device pressure through progressive viewing and quality adaptation. Region-level repair and uncertainty-guided local recapture remain future work, but the current design already shows that many failure cases can be mitigated through earlier feedback and staged result presentation.

## 6. Experiments and Analysis

The system should be evaluated from three perspectives: reconstruction quality, runtime performance, and usability. Quality should include PSNR, SSIM, LPIPS, and geometry-oriented criteria such as drift, support coverage, overlap violations, and consistency. Runtime performance should cover upload, queueing, preprocessing, training, export, return, and mobile-side viewing performance. Usability should include background upload success, time-to-first-preview, cancellation success, and the user’s ability to complete an effective scan.

The current repository already demonstrates meaningful end-to-end feasibility. The white-box checkpoint report indicates successful frame acceptance, during-scan training start, point growth, loss convergence, timing-log availability, large final Gaussian count, and acceptable color quality. In one reported configuration, the final point count reaches about 7.96 million and the mean PSNR reaches 14.7141, showing that real mobile input can already be converted into an interactive 3DGS result.

At the same time, geometry remains the main weakness. Several checks related to drift, support coverage, overlap avoidance, and structural regularity still fail or remain unverified. The present system should therefore be understood as a strong proof of end-to-end systems feasibility rather than a complete answer to consumer mobile 3DGS reconstruction.

## 7. Conclusion

This paper presented a 3DGS reconstruction and interaction system for consumer-grade mobile devices. By combining mobile capture and interaction with cloud-side HI-SLAM2 reconstruction, explicit task orchestration, structured artifact packaging, and thermal-aware viewing, the system transforms a research-grade Gaussian reconstruction workflow into a practical end-to-end service.

The current implementation already shows that smartphones can act as effective front ends for high-quality 3DGS reconstruction when paired with cloud execution. However, high-quality reconstruction still depends strongly on remote GPU resources, and geometric consistency remains weaker than photometric quality in challenging scenes. Future work should therefore focus on stronger geometric supervision, partial on-device optimization, mobile-native Gaussian viewers, and uncertainty-aware next-best-view guidance.

More broadly, the work suggests that progress in mobile 3DGS will depend as much on systems engineering as on core graphics and vision algorithms.

## References

[1] B. Kerbl, G. Kopanas, T. Leimkühler, and G. Drettakis, “3D Gaussian Splatting for Real-Time Radiance Field Rendering,” ACM TOG, 2023.

[2] C. Matsuki, J. Y. Huh, R. Sato, and H. Davison, “Gaussian Splatting SLAM,” CVPR, 2024.

[3] H. Yan, C. Fan, Y. Jiang, X. Shi, H. Chen, and K. Wang, “GS-SLAM: Dense Visual SLAM with 3D Gaussian Splatting,” CVPR, 2024.

[4] H. Huang, L. Sun, Y. Wang, et al., “Photo-SLAM: Real-time Simultaneous Localization and Photorealistic Mapping for Monocular Stereo and RGB-D Cameras,” CVPR, 2024.

[5] Z. Zheng, X. Wang, et al., “WildGS-SLAM: Monocular Gaussian Splatting SLAM in Dynamic Environments,” CVPR, 2025.

[6] X. Wu, J. Chen, et al., “3DGUT: Enabling Distorted Cameras and Secondary Rays in Gaussian Splatting,” CVPR, 2025.

[7] Y. Pan, Y. Wang, et al., “HI-SLAM2: Geometry-Aware Gaussian SLAM for Fast Monocular Scene Reconstruction,” IEEE Transactions on Robotics, 2025.

[8] D. Peng, H. Tang, et al., “SqueezeMe: Mobile-Ready Distillation of Gaussian Full-Body Avatars,” SIGGRAPH, 2025.

[9] Y. Du, Z. Lin, et al., “PocketGS: On-Device Training of 3D Gaussian Splatting for High Perceptual Modeling,” arXiv, 2026.

[10] X. Bian, Y. Zhao, et al., “Mobile-GS: Real-time Gaussian Splatting for Mobile Devices,” arXiv, 2026.
