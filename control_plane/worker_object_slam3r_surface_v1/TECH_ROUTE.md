# Object SLAM3R Surface V1

Paper-driven default delivery route for the new remote pipeline only.

| Layer | Paper / Source | Input | Output | User-facing? |
| --- | --- | --- | --- | --- |
| Geometry prior | SLAM3R (CVPR 2025) | Curated monocular RGB frames | Camera / geometry prior under `output/slam3r` | No |
| Stable surface reconstruction | Sparse2DGS (CVPR 2025) | Posed COLMAP scene contract from SLAM3R bridge | Stable surface / 2D Gaussian checkpoint under `output/sparse2dgs` | No |
| Mesh extraction | MAtCha adaptive tetrahedralization (CVPR 2025) | `Sparse2DGS` scene + checkpoint | Mesh under `output/matcha` | No |
| Default delivery layer | Product packaging | MAtCha mesh output | `default/default_mesh.glb` + `viewer_manifest.json` | Yes |

Principles:

- Default user result must be a lightweight mesh/GLB.
- Raw surface / 2D Gaussian assets remain internal intermediate artifacts.
- Multi-resolution TSDF stays a backup path only; adaptive tetrahedralization is the default.
