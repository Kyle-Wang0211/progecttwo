# Minimal Whitebox Pruning Inventory

Date: 2026-03-20

Goal:
- Keep only the minimum closed loop:
  `Home -> Scan -> local quality review -> upload to Danish 5090 -> remote training -> download result -> local 3DGS viewer`
- Remove history/provenance/compliance/blockchain-like/platform-scale modules that do not serve the current single-user whitebox loop.

## 1. Main Product Path

The current user-facing closed loop is already concentrated here:

- `/Users/kaidongwang/Documents/Aether3D/App/Aether3DApp.swift`
- `/Users/kaidongwang/Documents/Aether3D/App/Home/HomePage.swift`
- `/Users/kaidongwang/Documents/Aether3D/App/Scan/ScanView.swift`
- `/Users/kaidongwang/Documents/Aether3D/App/Scan/ScanViewModel.swift`
- `/Users/kaidongwang/Documents/Aether3D/App/Viewer/SplatViewerView.swift`
- `/Users/kaidongwang/Documents/Aether3D/App/GaussianSplatting/GaussianSplatViewController.swift`

This is the only path we should optimize for.

## 2. Important Structural Finding

`Aether3DCore` is still compiled from the entire `Core/` directory:

- `/Users/kaidongwang/Documents/Aether3D/Package.swift:170`

And `CAetherNativeBridge` still compiles a very large mixed C++ source set:

- `/Users/kaidongwang/Documents/Aether3D/Package.swift:49`
- `/Users/kaidongwang/Documents/Aether3D/Package.swift:53`
- `/Users/kaidongwang/Documents/Aether3D/Package.swift:160`

That means a lot of historical modules are not reachable from the product UI anymore, but are still being compiled into the app because target boundaries are too wide.

## 3. What The Minimal Closed Loop Actually Uses

### App-side minimum

- `App/Home/*`
- `App/Scan/*`
- `App/Viewer/SplatViewerView.swift`
- `App/GaussianSplatting/GaussianSplatViewController.swift`
- `App/ScanGuidance/GuidanceToastPresenter.swift`
- `App/ScanGuidance/GuidanceHapticEngine.swift`
- `App/ScanGuidance/GuidanceHints.swift`
- `App/ScanGuidance/ScanCaptureControls.swift`

### Core-side minimum

Current direct runtime center:

- `Core/Pipeline/*`
- `Core/Models/GenerateResult.swift`
- small parts of `Core/Artifacts/*`
- small parts of `Core/Audit/*`
- a subset of `Core/Quality/*`

Evidence:

- `PipelineRunner` drives the remote loop:
  `/Users/kaidongwang/Documents/Aether3D/Core/Pipeline/PipelineRunner.swift`
- Danish SSH client is the real remote backend:
  `/Users/kaidongwang/Documents/Aether3D/Core/Pipeline/DanishGoldenRemoteB1Client.swift`
- Scan runtime still depends on the C++ coordinator bridge:
  `/Users/kaidongwang/Documents/Aether3D/Core/Pipeline/PipelineCoordinatorBridge.swift`

### C++ bridge minimum

The Swift side currently calls only these C-API families:

- `aether_pipeline_coordinator_*`
- `aether_gpu_device_create_metal`
- `aether_splat_engine_*`

Evidence:

- `/Users/kaidongwang/Documents/Aether3D/Core/Pipeline/PipelineCoordinatorBridge.swift:35`
- `/Users/kaidongwang/Documents/Aether3D/Core/Pipeline/PipelineCoordinatorBridge.swift:91`
- `/Users/kaidongwang/Documents/Aether3D/Core/Pipeline/PipelineCoordinatorBridge.swift:164`
- `/Users/kaidongwang/Documents/Aether3D/App/Scan/ScanViewModel.swift:1210`
- `/Users/kaidongwang/Documents/Aether3D/App/GaussianSplatting/GaussianSplatViewController.swift:152`

This is the key pruning clue:
the mobile product path is centered on `pipeline + splat + render + quality + thermal + training + tsdf support`,
not on `geo + merkle + upload + provenance + sealing`.

## 4. Historical Baggage That Is Very Likely Not Needed

These modules are strong candidates for removal from the app build for the current version.

### Swift Core directories

Likely removable from the minimal whitebox app target:

- `Core/Compliance`
- `Core/Jobs`
- `Core/MerkleTree`
- `Core/PIZ`
- `Core/Replay`
- `Core/TimeAnchoring`
- `Core/Upload`
- `Core/Network` if it only supports old HTTP upload infrastructure
- most of `Core/Evidence`
- most of `Core/Audit`
- most of `Core/Quality/WhiteCommitter`

Why:

- They target compliance retention, signed logs, seal/provenance, resumable upload infrastructure, or multi-job orchestration.
- Your current product loop is single-user, SSH-based, and does not need platform-scale upload/session machinery.
- The user-facing app no longer exposes those capabilities.

### C++ source groups

Likely removable from the minimal bridge target after target split:

- `aether_cpp/src/geo`
- `aether_cpp/src/merkle`
- `aether_cpp/src/upload`
- most of `aether_cpp/src/innovation`

Why:

- None of those families appear in the current mobile bridge call surface.
- They are not part of the current scan -> train -> viewer path.

### Legacy products / executables

Likely removable from the package graph for this version:

- `PIZFixtureDumper`
- `PIZSealingEvidence`
- `UpdateGoldenDigests`
- most `PR4*` products and executables if they are research-only
- old upload test target families
- evidence grid executables if not used in the app

Evidence:

- `/Users/kaidongwang/Documents/Aether3D/Package.swift:14`
- `/Users/kaidongwang/Documents/Aether3D/Package.swift:182`
- `/Users/kaidongwang/Documents/Aether3D/Package.swift:245`
- `/Users/kaidongwang/Documents/Aether3D/Package.swift:503`

## 5. Modules That Look “Historical”, But Are Still Indirectly Used

These are not sacred, but they are still attached to the current loop and should not be deleted blindly.

### `Core/Artifacts`

Still used by `PipelineRunner` to package local output into a whitebox artifact bundle:

- `manifest.json`
- file descriptors
- policy hash
- canonical encoder
- package validation

Evidence:

- `/Users/kaidongwang/Documents/Aether3D/Core/Pipeline/PipelineRunner.swift:116`
- `/Users/kaidongwang/Documents/Aether3D/Core/Pipeline/PipelineRunner.swift:127`
- `/Users/kaidongwang/Documents/Aether3D/Core/Artifacts/ArtifactManifest.swift:1021`

Interpretation:

- This is not “blockchain”, but it is more formal than the current product probably needs.
- It can likely be simplified later, but today it is still on the success path.

### `Core/Audit`

The full signed-audit system is overkill.
But `PipelineRunner` still writes simple plain audit entries.

Evidence:

- `/Users/kaidongwang/Documents/Aether3D/Core/Pipeline/PipelineRunner.swift:87`
- `/Users/kaidongwang/Documents/Aether3D/Core/Audit/AuditEntry.swift:17`
- `/Users/kaidongwang/Documents/Aether3D/Core/Audit/PlainAuditLog.swift:15`

Interpretation:

- `SignedAuditLog`, `SignedAuditEntry`, `TraceValidator`, `AuditFileWriter` look like historical governance baggage for this version.
- `PlainAuditLog + AuditEntry` are still directly wired into generation flow.

### `world_state.json` generation inside `ScanViewModel`

This is probably optional for the current product cut, but it is still being generated in the scan/export flow.

Evidence:

- `/Users/kaidongwang/Documents/Aether3D/App/Scan/ScanViewModel.swift:157`
- `/Users/kaidongwang/Documents/Aether3D/App/Scan/ScanViewModel.swift:1567`

Interpretation:

- If the professor wants the absolute minimum loop, this is a strong candidate for deletion or feature-flagging.
- It is closer to “rich artifact packaging” than to the essential whitebox loop.

### `PointCloudOIRPipeline` and `TSDF` support

Even after the UI overlay was removed, these parts are still involved in scan runtime and data flow.

Interpretation:

- They may feel legacy because the visible mesh/heatmap UI is gone.
- But the runtime still depends on parts of the underlying pipeline.
- They are not safe first-wave deletion targets.

## 6. Practical Keep / Cut Classification

### Keep now

- `App/Home`
- `App/Scan`
- `App/Viewer/SplatViewerView.swift`
- `App/GaussianSplatting`
- `Core/Pipeline`
- `Core/Models`
- Danish SSH provisioning/client
- minimal quality runtime needed for real-time review
- C++ `pipeline / splat / render / training / thermal / quality / tsdf support`

### Likely cut in first serious pruning wave

- `Core/Compliance`
- `Core/Jobs`
- `Core/MerkleTree`
- `Core/PIZ`
- `Core/Replay`
- `Core/TimeAnchoring`
- `Core/Upload`
- research/test executables around PIZ / PR4 / evidence grid
- C++ `geo / merkle / upload / most innovation`

### Keep temporarily, then simplify

- `Core/Artifacts`
- `Core/Audit` plain logging
- `ScanViewModel` world-state export
- parts of `Core/Quality/WhiteCommitter`

Reason:

- These are not core to the user experience, but they are still coupled to the current generate/export path.

## 7. Strong Recommendation

Do not start by deleting folders inside `Core/` one by one.

Start by splitting targets.

Recommended target strategy:

1. Keep `Aether3DCore` only for the minimal closed loop.
2. Create a separate legacy target for governance/provenance/research modules.
3. Shrink `CAetherNativeBridge` to a mobile-closed-loop source list.
4. After target separation, physically delete the legacy target content that is no longer referenced.

If you skip target splitting and directly delete directories under the current monolithic target, the compile graph will break in unpredictable ways.

## 8. Best Next Deletion Order

If we continue pruning in code, the safest order is:

1. Split `Core/Upload`, `Core/Jobs`, `Core/Compliance`, `Core/MerkleTree`, `Core/PIZ`, `Core/Replay`, `Core/TimeAnchoring` out of `Aether3DCore`.
2. Split `aether_cpp/src/geo`, `src/merkle`, `src/upload`, and non-essential `src/innovation` out of `CAetherNativeBridge`.
3. Simplify `PipelineRunner` so it no longer needs heavy artifact packaging and plain audit logs.
4. Remove `world_state.json` export from `ScanViewModel` if the professor confirms it is not part of the deliverable.

## 9. Bottom Line

Your memory is correct:

- The project still contains a lot of “history / authenticity / provenance / chain / retention / audit” infrastructure.
- For the current professor-approved version, most of that is not product value.
- The right way to cut it is not UI-first anymore.
- The next real simplification is target-boundary surgery in `Package.swift`.
