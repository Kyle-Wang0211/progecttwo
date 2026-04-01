#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <random>
#include <sstream>
#include <string>
#include <thread>
#include <numeric>
#include <vector>

namespace fs = std::filesystem;

namespace {

inline float sdf_box(float px, float py, float pz,
                     float cx, float cy, float cz,
                     float hx, float hy, float hz) {
    float dx = std::abs(px - cx) - hx;
    float dy = std::abs(py - cy) - hy;
    float dz = std::abs(pz - cz) - hz;
    float outside = std::sqrt(
        std::max(dx, 0.0f) * std::max(dx, 0.0f) +
        std::max(dy, 0.0f) * std::max(dy, 0.0f) +
        std::max(dz, 0.0f) * std::max(dz, 0.0f));
    float inside = std::min(std::max(dx, std::max(dy, dz)), 0.0f);
    return outside + inside;
}

inline float sdf_cylinder(float px, float py, float pz,
                           float bx, float by, float bz,
                           float radius, float height) {
    float dx = px - bx;
    float dz = pz - bz;
    float dist_xz = std::sqrt(dx * dx + dz * dz) - radius;
    float dist_y_low = by - py;
    float dist_y_high = py - (by + height);
    float dist_y = std::max(dist_y_low, dist_y_high);
    float outside = std::sqrt(
        std::max(dist_xz, 0.0f) * std::max(dist_xz, 0.0f) +
        std::max(dist_y, 0.0f) * std::max(dist_y, 0.0f));
    float inside = std::min(std::max(dist_xz, dist_y), 0.0f);
    return outside + inside;
}

inline float sdf_sphere(float px, float py, float pz,
                        float cx, float cy, float cz, float r) {
    float dx = px - cx, dy = py - cy, dz = pz - cz;
    return std::sqrt(dx * dx + dy * dy + dz * dz) - r;
}

inline float sdf_ellipsoid(float px, float py, float pz,
                            float cx, float cy, float cz,
                            float rx, float ry, float rz) {
    float dx = (px - cx) / rx;
    float dy = (py - cy) / ry;
    float dz = (pz - cz) / rz;
    float k = std::sqrt(dx * dx + dy * dy + dz * dz);
    float r_min = std::min({rx, ry, rz});
    return (k - 1.0f) * r_min;
}

constexpr float kRoomMaxX = 3.0f;
constexpr float kRoomMaxY = 2.5f;
constexpr float kRoomMaxZ = 3.0f;

inline float sdf_bed_frame(float px, float py, float pz) {
    return sdf_box(px, py, pz, 1.5f, 0.175f, 2.50f, 0.90f, 0.175f, 0.45f);
}
inline float sdf_bed_mattress(float px, float py, float pz) {
    return sdf_box(px, py, pz, 1.5f, 0.45f, 2.50f, 0.85f, 0.10f, 0.42f);
}
inline float sdf_bed_headboard(float px, float py, float pz) {
    return sdf_box(px, py, pz, 1.5f, 0.60f, 2.96f, 0.90f, 0.35f, 0.03f);
}
inline float sdf_blanket(float px, float py, float pz) {
    float wrinkle = std::sin(15.0f * px) * std::sin(12.0f * pz) * 0.015f;
    return sdf_box(px, py - wrinkle, pz, 1.5f, 0.57f, 2.40f, 0.80f, 0.025f, 0.35f);
}
inline float sdf_bed_all(float px, float py, float pz) {
    float d = sdf_bed_frame(px, py, pz);
    d = std::min(d, sdf_bed_mattress(px, py, pz));
    d = std::min(d, sdf_bed_headboard(px, py, pz));
    d = std::min(d, sdf_blanket(px, py, pz));
    return d;
}

inline float sdf_pillow_left(float px, float py, float pz) {
    return sdf_ellipsoid(px, py, pz, 1.0f, 0.62f, 2.70f, 0.18f, 0.08f, 0.12f);
}
inline float sdf_pillow_right(float px, float py, float pz) {
    return sdf_ellipsoid(px, py, pz, 2.0f, 0.62f, 2.70f, 0.18f, 0.08f, 0.12f);
}
inline float sdf_pillows(float px, float py, float pz) {
    return std::min(sdf_pillow_left(px, py, pz), sdf_pillow_right(px, py, pz));
}

inline float sdf_desk_top(float px, float py, float pz) {
    return sdf_box(px, py, pz, 2.50f, 0.75f, 1.00f, 0.40f, 0.02f, 0.30f);
}
inline float sdf_desk_legs(float px, float py, float pz) {
    float d = sdf_cylinder(px, py, pz, 2.15f, 0.0f, 0.75f, 0.02f, 0.73f);
    d = std::min(d, sdf_cylinder(px, py, pz, 2.85f, 0.0f, 0.75f, 0.02f, 0.73f));
    d = std::min(d, sdf_cylinder(px, py, pz, 2.15f, 0.0f, 1.25f, 0.02f, 0.73f));
    d = std::min(d, sdf_cylinder(px, py, pz, 2.85f, 0.0f, 1.25f, 0.02f, 0.73f));
    return d;
}
inline float sdf_desk_all(float px, float py, float pz) {
    return std::min(sdf_desk_top(px, py, pz), sdf_desk_legs(px, py, pz));
}

inline float sdf_chair_seat(float px, float py, float pz) {
    return sdf_box(px, py, pz, 2.30f, 0.45f, 0.65f, 0.20f, 0.02f, 0.20f);
}
inline float sdf_chair_back(float px, float py, float pz) {
    return sdf_box(px, py, pz, 2.30f, 0.70f, 0.83f, 0.18f, 0.20f, 0.015f);
}
inline float sdf_chair_legs(float px, float py, float pz) {
    float d = sdf_cylinder(px, py, pz, 2.13f, 0.0f, 0.48f, 0.015f, 0.43f);
    d = std::min(d, sdf_cylinder(px, py, pz, 2.47f, 0.0f, 0.48f, 0.015f, 0.43f));
    d = std::min(d, sdf_cylinder(px, py, pz, 2.13f, 0.0f, 0.82f, 0.015f, 0.43f));
    d = std::min(d, sdf_cylinder(px, py, pz, 2.47f, 0.0f, 0.82f, 0.015f, 0.43f));
    return d;
}
inline float sdf_chair_all(float px, float py, float pz) {
    float d = sdf_chair_seat(px, py, pz);
    d = std::min(d, sdf_chair_back(px, py, pz));
    d = std::min(d, sdf_chair_legs(px, py, pz));
    return d;
}

inline float sdf_bookshelf_frame(float px, float py, float pz) {
    float outer = sdf_box(px, py, pz, 0.20f, 0.90f, 1.50f, 0.18f, 0.90f, 0.35f);
    float inner = sdf_box(px, py, pz, 0.26f, 0.90f, 1.50f, 0.14f, 0.86f, 0.31f);
    float frame = std::max(outer, -inner);
    float s1 = sdf_box(px, py, pz, 0.20f, 0.45f, 1.50f, 0.17f, 0.015f, 0.34f);
    float s2 = sdf_box(px, py, pz, 0.20f, 0.90f, 1.50f, 0.17f, 0.015f, 0.34f);
    float s3 = sdf_box(px, py, pz, 0.20f, 1.35f, 1.50f, 0.17f, 0.015f, 0.34f);
    frame = std::min(frame, std::min(s1, std::min(s2, s3)));
    return frame;
}
inline float sdf_books(float px, float py, float pz) {
    float d = 1e6f;
    d = std::min(d, sdf_box(px, py, pz, 0.18f, 0.24f, 1.25f, 0.07f, 0.18f, 0.015f));
    d = std::min(d, sdf_box(px, py, pz, 0.18f, 0.22f, 1.38f, 0.07f, 0.16f, 0.015f));
    d = std::min(d, sdf_box(px, py, pz, 0.18f, 0.68f, 1.30f, 0.07f, 0.19f, 0.015f));
    d = std::min(d, sdf_box(px, py, pz, 0.18f, 0.66f, 1.43f, 0.07f, 0.17f, 0.015f));
    d = std::min(d, sdf_box(px, py, pz, 0.18f, 0.68f, 1.58f, 0.07f, 0.19f, 0.015f));
    d = std::min(d, sdf_box(px, py, pz, 0.18f, 1.13f, 1.35f, 0.07f, 0.19f, 0.015f));
    d = std::min(d, sdf_box(px, py, pz, 0.18f, 1.11f, 1.50f, 0.07f, 0.17f, 0.015f));
    d = std::min(d, sdf_box(px, py, pz, 0.18f, 1.13f, 1.65f, 0.07f, 0.20f, 0.015f));
    return d;
}

inline float sdf_lamp_base(float px, float py, float pz) {
    return sdf_cylinder(px, py, pz, 2.65f, 0.77f, 1.20f, 0.025f, 0.30f);
}
inline float sdf_lamp_shade(float px, float py, float pz) {
    return sdf_sphere(px, py, pz, 2.65f, 1.12f, 1.20f, 0.08f);
}
inline float sdf_lamp_all(float px, float py, float pz) {
    return std::min(sdf_lamp_base(px, py, pz), sdf_lamp_shade(px, py, pz));
}

inline float sdf_plant_pot(float px, float py, float pz) {
    return sdf_cylinder(px, py, pz, 1.30f, 0.0f, 1.00f, 0.08f, 0.18f);
}
inline float sdf_plant_leaves(float px, float py, float pz) {
    float d = sdf_sphere(px, py, pz, 1.30f, 0.30f, 1.00f, 0.10f);
    d = std::min(d, sdf_sphere(px, py, pz, 1.20f, 0.28f, 0.92f, 0.07f));
    d = std::min(d, sdf_sphere(px, py, pz, 1.40f, 0.28f, 0.92f, 0.07f));
    d = std::min(d, sdf_sphere(px, py, pz, 1.22f, 0.28f, 1.08f, 0.07f));
    d = std::min(d, sdf_sphere(px, py, pz, 1.38f, 0.28f, 1.08f, 0.07f));
    return d;
}
inline float sdf_plant_all(float px, float py, float pz) {
    return std::min(sdf_plant_pot(px, py, pz), sdf_plant_leaves(px, py, pz));
}

inline float sdf_rug(float px, float py, float pz) {
    float bump = std::sin(8.0f * px) * std::sin(8.0f * pz) * 0.005f;
    return sdf_box(px, py - bump, pz, 1.50f, 0.008f, 1.30f, 0.60f, 0.008f, 0.45f);
}
inline float sdf_picture(float px, float py, float pz) {
    return sdf_box(px, py, pz, 0.015f, 1.50f, 1.00f, 0.015f, 0.20f, 0.25f);
}
inline float sdf_curtain(float px, float py, float pz) {
    float fold = std::sin(20.0f * px) * 0.015f;
    return sdf_box(px, py, pz - fold, 1.50f, 1.40f, 2.97f, 0.70f, 0.60f, 0.02f);
}
inline float sdf_nightstand(float px, float py, float pz) {
    return sdf_box(px, py, pz, 0.40f, 0.25f, 2.50f, 0.20f, 0.25f, 0.20f);
}

inline float scene_sdf(float px, float py, float pz) {
    if (px < -0.3f || px > 3.3f || py < -0.3f || py > 2.8f || pz < -0.3f || pz > 3.3f) {
        return 1.0f;
    }

    float d_floor = py;
    float d_ceiling = kRoomMaxY - py;
    float d_left = px;
    float d_right = kRoomMaxX - px;
    float d_front = pz;
    float d_door = sdf_box(px, py, pz, 1.20f, 1.00f, 0.0f, 0.40f, 1.00f, 0.10f);
    d_front = std::max(d_front, -d_door);
    float d_back = kRoomMaxZ - pz;
    float d_window = sdf_box(px, py, pz, 1.50f, 1.60f, kRoomMaxZ, 0.50f, 0.40f, 0.10f);
    d_back = std::max(d_back, -d_window);

    float d = std::min({d_floor, d_ceiling, d_left, d_right, d_front, d_back});
    d = std::min(d, sdf_bed_all(px, py, pz));
    d = std::min(d, sdf_pillows(px, py, pz));
    d = std::min(d, sdf_desk_all(px, py, pz));
    d = std::min(d, sdf_chair_all(px, py, pz));
    d = std::min(d, sdf_bookshelf_frame(px, py, pz));
    d = std::min(d, sdf_books(px, py, pz));
    d = std::min(d, sdf_lamp_all(px, py, pz));
    d = std::min(d, sdf_plant_all(px, py, pz));
    d = std::min(d, sdf_rug(px, py, pz));
    d = std::min(d, sdf_picture(px, py, pz));
    d = std::min(d, sdf_curtain(px, py, pz));
    d = std::min(d, sdf_nightstand(px, py, pz));
    return d;
}

inline void scene_normal(float px, float py, float pz, float& nx, float& ny, float& nz) {
    constexpr float eps = 0.0005f;
    nx = scene_sdf(px + eps, py, pz) - scene_sdf(px - eps, py, pz);
    ny = scene_sdf(px, py + eps, pz) - scene_sdf(px, py - eps, pz);
    nz = scene_sdf(px, py, pz + eps) - scene_sdf(px, py, pz - eps);
    float len = std::sqrt(nx * nx + ny * ny + nz * nz);
    if (len > 1e-8f) { nx /= len; ny /= len; nz /= len; }
    else { nx = 0; ny = 1; nz = 0; }
}

struct PointLight {
    float pos[3];
    float intensity;
    float color[3];
};

constexpr PointLight kLights[] = {
    {{1.5f, 2.4f, 1.5f},  3.0f, {1.0f, 0.95f, 0.85f}},
    {{2.6f, 1.0f, 1.0f},  1.5f, {1.0f, 0.90f, 0.70f}},
    {{1.5f, 1.5f, -0.3f}, 2.0f, {0.85f, 0.90f, 1.0f}},
    {{0.7f, 0.7f, 2.5f},  0.8f, {1.0f, 0.85f, 0.65f}},
    {{1.5f, 0.5f, 0.5f},  0.5f, {0.95f, 0.95f, 0.95f}},
};
constexpr int kNumLights = sizeof(kLights) / sizeof(kLights[0]);
constexpr float kAmbient = 0.05f;

inline void compute_lighting(float px, float py, float pz,
                             float nx, float ny, float nz,
                             float& light_r, float& light_g, float& light_b) {
    light_r = kAmbient;
    light_g = kAmbient;
    light_b = kAmbient;
    for (int i = 0; i < kNumLights; ++i) {
        float lx = kLights[i].pos[0] - px;
        float ly = kLights[i].pos[1] - py;
        float lz = kLights[i].pos[2] - pz;
        float dist2 = lx * lx + ly * ly + lz * lz;
        float dist = std::sqrt(dist2);
        if (dist < 1e-6f) continue;
        lx /= dist; ly /= dist; lz /= dist;
        float ndotl = std::max(0.0f, nx * lx + ny * ly + nz * lz);
        float atten = kLights[i].intensity / std::max(dist2, 0.01f);
        light_r += ndotl * atten * kLights[i].color[0];
        light_g += ndotl * atten * kLights[i].color[1];
        light_b += ndotl * atten * kLights[i].color[2];
    }
}

struct ColorRegion {
    const char* name;
    float linear_rgb[3];
};

constexpr int kNumRegions = 9;
const ColorRegion kRegions[kNumRegions] = {
    {"Black",   {0.003f, 0.003f, 0.003f}},
    {"White",   {0.871f, 0.871f, 0.871f}},
    {"Red",     {0.710f, 0.003f, 0.003f}},
    {"Orange",  {0.787f, 0.194f, 0.003f}},
    {"Yellow",  {0.820f, 0.716f, 0.003f}},
    {"Green",   {0.005f, 0.521f, 0.005f}},
    {"Cyan",    {0.003f, 0.651f, 0.651f}},
    {"Blue",    {0.003f, 0.005f, 0.716f}},
    {"Purple",  {0.413f, 0.003f, 0.413f}},
};

inline int classify_surface_region(float px, float py, float pz) {
    float min_d = 0.015f;
    int best = -1;
    auto check = [&](int region, float d) {
        if (d < min_d) { min_d = d; best = region; }
    };

    check(0, sdf_chair_all(px, py, pz));
    check(0, sdf_desk_legs(px, py, pz));
    check(0, sdf_nightstand(px, py, pz));
    check(0, sdf_bed_frame(px, py, pz));
    check(0, sdf_bed_headboard(px, py, pz));

    float d_ceiling = kRoomMaxY - py;
    float d_left = px;
    float d_right = kRoomMaxX - px;
    float d_front = pz;
    float d_back = kRoomMaxZ - pz;
    float wall_d = std::min({d_ceiling, d_left, d_right, d_front, d_back});
    check(1, wall_d);
    check(1, sdf_picture(px, py, pz));
    check(1, sdf_bed_mattress(px, py, pz));

    check(2, sdf_blanket(px, py, pz));
    check(3, sdf_bookshelf_frame(px, py, pz));
    check(3, sdf_lamp_all(px, py, pz));
    check(4, py);
    check(4, sdf_desk_top(px, py, pz));
    check(5, sdf_plant_all(px, py, pz));
    check(6, sdf_rug(px, py, pz));
    check(7, sdf_pillows(px, py, pz));
    check(7, sdf_curtain(px, py, pz));
    check(8, sdf_books(px, py, pz));
    return best;
}

inline float fractf(float x) {
    return x - std::floor(x);
}

inline void surface_uv(float px, float py, float pz,
                       float nx, float ny, float nz,
                       float& u, float& v) {
    float ax = std::abs(nx);
    float ay = std::abs(ny);
    float az = std::abs(nz);
    if (ay >= ax && ay >= az) {
        u = px;
        v = pz;
    } else if (ax >= az) {
        u = pz;
        v = py;
    } else {
        u = px;
        v = py;
    }
}

inline void apply_texture_modulation(int region,
                                     float px, float py, float pz,
                                     float nx, float ny, float nz,
                                     float& base_r,
                                     float& base_g,
                                     float& base_b) {
    float u = 0.0f, v = 0.0f;
    surface_uv(px, py, pz, nx, ny, nz, u, v);
    const float wave0 = 0.5f + 0.5f * std::sin(14.0f * u + 3.0f * v);
    const float wave1 = 0.5f + 0.5f * std::sin(23.0f * u - 11.0f * v + 0.7f);
    const float wave2 = 0.5f + 0.5f * std::sin(31.0f * u + 19.0f * v + 1.1f);
    const float checker = (((static_cast<int>(std::floor(u * 6.0f)) +
                             static_cast<int>(std::floor(v * 6.0f))) & 1) == 0) ? 0.0f : 1.0f;
    const float grain = fractf(std::sin(u * 91.3f + v * 37.7f + px * 11.0f + pz * 7.0f) * 43758.5453f);

    float mult_r = 1.0f;
    float mult_g = 1.0f;
    float mult_b = 1.0f;

    switch (region) {
        case 0: { // black metals / legs
            float tone = 0.86f + 0.18f * wave0 + 0.08f * grain;
            mult_r = tone; mult_g = tone; mult_b = tone;
            break;
        }
        case 1: { // walls / mattress / white surfaces
            float tone = 0.82f + 0.20f * wave0 + 0.10f * wave1;
            mult_r = tone;
            mult_g = 0.96f * tone;
            mult_b = 0.92f * tone;
            break;
        }
        case 2: { // blanket
            float tone = 0.78f + 0.28f * wave1;
            mult_r = 1.06f * tone;
            mult_g = 0.92f * tone;
            mult_b = 0.88f * tone;
            break;
        }
        case 3: { // orange wood / lamp
            float tone = checker > 0.5f ? 0.88f : 1.08f;
            tone *= 0.92f + 0.12f * wave2;
            mult_r = 1.03f * tone;
            mult_g = 0.95f * tone;
            mult_b = 0.85f * tone;
            break;
        }
        case 4: { // floor / desk top
            float tone = checker > 0.5f ? 0.84f : 1.12f;
            tone *= 0.94f + 0.08f * grain;
            mult_r = tone;
            mult_g = 0.97f * tone;
            mult_b = 0.84f * tone;
            break;
        }
        case 5: { // plants
            float tone = 0.84f + 0.24f * wave2;
            mult_r = 0.92f * tone;
            mult_g = 1.08f * tone;
            mult_b = 0.90f * tone;
            break;
        }
        case 6: { // rug / cyan
            float tone = 0.76f + 0.30f * wave1;
            mult_r = 0.90f * tone;
            mult_g = 1.02f * tone;
            mult_b = 1.08f * tone;
            break;
        }
        case 7: { // pillows / curtain / blue
            float tone = 0.82f + 0.26f * wave0;
            mult_r = 0.86f * tone;
            mult_g = 0.92f * tone;
            mult_b = 1.12f * tone;
            break;
        }
        case 8: { // books / purple
            float tone = checker > 0.5f ? 0.74f : 1.18f;
            tone *= 0.88f + 0.18f * wave2;
            mult_r = 1.08f * tone;
            mult_g = 0.86f * tone;
            mult_b = 1.06f * tone;
            break;
        }
        default:
            break;
    }

    base_r = std::clamp(base_r * mult_r, 0.0f, 1.0f);
    base_g = std::clamp(base_g * mult_g, 0.0f, 1.0f);
    base_b = std::clamp(base_b * mult_b, 0.0f, 1.0f);
}

inline float sphere_trace(float ox, float oy, float oz,
                          float dx, float dy, float dz,
                          float max_t = 6.0f) {
    float t = 0.05f;
    for (int i = 0; i < 200; ++i) {
        float px = ox + dx * t;
        float py = oy + dy * t;
        float pz = oz + dz * t;
        float d = scene_sdf(px, py, pz);
        if (d < 0.001f) return t;
        t += d * 0.9f;
        if (t > max_t) return 0.0f;
    }
    return 0.0f;
}

void build_cam2world(float ex, float ey, float ez,
                     float tx, float ty, float tz,
                     float cam2world[16]) {
    float fx = tx - ex, fy = ty - ey, fz = tz - ez;
    float flen = std::sqrt(fx * fx + fy * fy + fz * fz);
    if (flen < 1e-6f) flen = 1.0f;
    fx /= flen; fy /= flen; fz /= flen;

    float rx = fz, ry = 0.0f, rz = -fx;
    float rlen = std::sqrt(rx * rx + ry * ry + rz * rz);
    if (rlen < 1e-6f) { rx = 1.0f; rz = 0.0f; rlen = 1.0f; }
    rx /= rlen; ry /= rlen; rz /= rlen;

    float ux = -(ry * fz - rz * fy);
    float uy = -(rz * fx - rx * fz);
    float uz = -(rx * fy - ry * fx);

    cam2world[0]  = rx;   cam2world[1]  = ry;   cam2world[2]  = rz;   cam2world[3]  = 0;
    cam2world[4]  = ux;   cam2world[5]  = uy;   cam2world[6]  = uz;   cam2world[7]  = 0;
    cam2world[8]  = -fx;  cam2world[9]  = -fy;  cam2world[10] = -fz;  cam2world[11] = 0;
    cam2world[12] = ex;   cam2world[13] = ey;   cam2world[14] = ez;   cam2world[15] = 1;
}

struct CameraKeyframe {
    float cam2world[16];
    double timestamp;
    float speed_factor;
};

struct TrajectoryStats {
    double step_mean_m = 0.0;
    double step_p95_m = 0.0;
    double step_max_m = 0.0;
    double total_path_m = 0.0;
    double frames_per_meter = 0.0;
    double max_from_start_m = 0.0;
    double max_from_center_m = 0.0;
};

enum class CaptureProtocol {
    Handheld,
    MonoGSTum,
    WildGSTumStatic,
    WildGSTumScan,
};

std::vector<CameraKeyframe> load_camera_path_file(const fs::path& path) {
    std::ifstream in(path);
    if (!in) {
        throw std::runtime_error("failed opening camera path: " + path.string());
    }

    std::vector<CameraKeyframe> keyframes;
    std::string line;
    std::size_t line_no = 0;
    while (std::getline(in, line)) {
        ++line_no;
        if (line.empty()) {
            continue;
        }

        std::istringstream iss(line);
        std::vector<double> vals;
        double v = 0.0;
        while (iss >> v) {
            vals.push_back(v);
        }

        if (vals.size() != 16 && vals.size() != 17) {
            throw std::runtime_error(
                "camera path line " + std::to_string(line_no) +
                " must contain 16 or 17 scalars, got " + std::to_string(vals.size()));
        }

        CameraKeyframe kf{};
        kf.speed_factor = 1.0f;
        std::size_t offset = 0;
        if (vals.size() == 17) {
            kf.timestamp = vals[0];
            offset = 1;
        } else {
            kf.timestamp = static_cast<double>(keyframes.size());
        }

        for (std::size_t i = 0; i < 16; ++i) {
            kf.cam2world[i] = static_cast<float>(vals[offset + i]);
        }
        keyframes.push_back(kf);
    }

    if (keyframes.empty()) {
        throw std::runtime_error("camera path file is empty: " + path.string());
    }
    return keyframes;
}

TrajectoryStats compute_trajectory_stats(const std::vector<CameraKeyframe>& keyframes) {
    TrajectoryStats stats;
    if (keyframes.size() < 2) {
        return stats;
    }

    std::vector<double> steps;
    steps.reserve(keyframes.size() - 1);
    std::array<double, 3> center{0.0, 0.0, 0.0};
    for (const auto& kf : keyframes) {
        center[0] += kf.cam2world[12];
        center[1] += kf.cam2world[13];
        center[2] += kf.cam2world[14];
    }
    center[0] /= static_cast<double>(keyframes.size());
    center[1] /= static_cast<double>(keyframes.size());
    center[2] /= static_cast<double>(keyframes.size());

    const auto dist3 = [](double ax, double ay, double az,
                          double bx, double by, double bz) -> double {
        const double dx = ax - bx;
        const double dy = ay - by;
        const double dz = az - bz;
        return std::sqrt(dx * dx + dy * dy + dz * dz);
    };

    for (std::size_t i = 1; i < keyframes.size(); ++i) {
        const auto& a = keyframes[i - 1];
        const auto& b = keyframes[i];
        steps.push_back(dist3(
            static_cast<double>(a.cam2world[12]), static_cast<double>(a.cam2world[13]), static_cast<double>(a.cam2world[14]),
            static_cast<double>(b.cam2world[12]), static_cast<double>(b.cam2world[13]), static_cast<double>(b.cam2world[14])));
    }

    std::sort(steps.begin(), steps.end());
    const double sum = std::accumulate(steps.begin(), steps.end(), 0.0);
    const auto pct = [&](double p) -> double {
        if (steps.empty()) return 0.0;
        const double idx = std::clamp(p, 0.0, 1.0) * static_cast<double>(steps.size() - 1);
        const auto lo = static_cast<std::size_t>(std::floor(idx));
        const auto hi = static_cast<std::size_t>(std::ceil(idx));
        const double t = idx - static_cast<double>(lo);
        return steps[lo] * (1.0 - t) + steps[hi] * t;
    };

    stats.step_mean_m = sum / static_cast<double>(steps.size());
    stats.step_p95_m = pct(0.95);
    stats.step_max_m = steps.back();
    stats.total_path_m = sum;
    stats.frames_per_meter = static_cast<double>(keyframes.size()) / std::max(sum, 1e-9);

    const auto& start = keyframes.front();
    for (const auto& kf : keyframes) {
        stats.max_from_start_m = std::max(stats.max_from_start_m, dist3(
            static_cast<double>(kf.cam2world[12]), static_cast<double>(kf.cam2world[13]), static_cast<double>(kf.cam2world[14]),
            static_cast<double>(start.cam2world[12]), static_cast<double>(start.cam2world[13]), static_cast<double>(start.cam2world[14])));
        stats.max_from_center_m = std::max(stats.max_from_center_m, dist3(
            static_cast<double>(kf.cam2world[12]), static_cast<double>(kf.cam2world[13]), static_cast<double>(kf.cam2world[14]),
            center[0], center[1], center[2]));
    }
    return stats;
}

void apply_static_tail(std::vector<CameraKeyframe>& keyframes,
                       float duration_sec,
                       float static_tail_sec,
                       float static_tail_jitter_mm,
                       std::mt19937& rng) {
    if (keyframes.size() < 2 || static_tail_sec <= 0.0f || duration_sec <= static_tail_sec) {
        return;
    }

    std::size_t tail_frames = static_cast<std::size_t>(
        std::round((static_tail_sec / duration_sec) * static_cast<float>(keyframes.size())));
    tail_frames = std::clamp<std::size_t>(tail_frames, 1, keyframes.size() - 1);
    const std::size_t head_frames = keyframes.size() - tail_frames;
    const float head_duration_sec = duration_sec - static_tail_sec;
    const float jitter_m = std::max(0.0f, static_tail_jitter_mm) / 1000.0f;
    std::normal_distribution<float> jitter_pos(0.0f, jitter_m);

    for (std::size_t i = 0; i < head_frames; ++i) {
        const double t_frac = static_cast<double>(i) /
            static_cast<double>(std::max<std::size_t>(1, head_frames - 1));
        keyframes[i].timestamp = t_frac * static_cast<double>(head_duration_sec);
    }

    const CameraKeyframe anchor = keyframes[head_frames - 1];
    for (std::size_t i = head_frames; i < keyframes.size(); ++i) {
        keyframes[i] = anchor;
        keyframes[i].cam2world[12] += jitter_pos(rng);
        keyframes[i].cam2world[13] += jitter_pos(rng);
        keyframes[i].cam2world[14] += jitter_pos(rng);
        const double tail_frac = static_cast<double>(i - head_frames) /
            static_cast<double>(std::max<std::size_t>(1, tail_frames - 1));
        keyframes[i].timestamp = static_cast<double>(head_duration_sec) +
            tail_frac * static_cast<double>(static_tail_sec);
        keyframes[i].speed_factor = std::min(keyframes[i].speed_factor, 0.02f);
    }
}

static inline float sblend(float a, float b, float raw_t) {
    float t = raw_t * raw_t * (3.0f - 2.0f * raw_t);
    return a + (b - a) * t;
}

static void ph0(float s, float& px, float& py, float& pz, float& lx, float& ly, float& lz) {
    constexpr float kPI = 3.14159265f;
    py = 1.38f + 0.20f * std::sin(s * kPI * 4.0f);
    const float wx[6] = {1.5f, 0.3f, 0.3f, 2.7f, 2.7f, 1.5f};
    const float wz[6] = {0.3f, 0.3f, 2.7f, 2.7f, 0.3f, 0.3f};
    float ss = s * 4.9999f;
    int sg = static_cast<int>(ss);
    if (sg > 4) sg = 4;
    float tt = ss - static_cast<float>(sg);
    px = wx[sg] + (wx[sg + 1] - wx[sg]) * tt;
    pz = wz[sg] + (wz[sg + 1] - wz[sg]) * tt;
    lx = 1.5f; ly = 0.85f; lz = 1.5f;
}

static void ph1(float s, float& px, float& py, float& pz, float& lx, float& ly, float& lz) {
    if (s < 0.34f) {
        float u = s / 0.34f;
        px = 0.40f; pz = 0.5f + 1.5f * u; py = 0.70f + 0.75f * u;
        lx = 0.10f; ly = 0.90f; lz = 1.20f;
    } else if (s < 0.67f) {
        float u = (s - 0.34f) / 0.33f;
        px = 0.5f + 2.0f * u; pz = 2.65f; py = 1.15f;
        lx = 1.5f; ly = 0.40f; lz = 2.50f;
    } else {
        float u = (s - 0.67f) / 0.33f;
        px = 2.65f; pz = 1.5f - 0.8f * u; py = 1.30f - 0.35f * u;
        lx = 2.50f; ly = 0.75f; lz = 1.00f;
    }
}

static void ph2(float s, float& px, float& py, float& pz, float& lx, float& ly, float& lz) {
    py = 0.55f;
    if (s < 0.35f) {
        float u = s / 0.35f;
        px = 1.8f - 0.8f * u;
        pz = 0.55f + 0.15f * u;
    } else if (s < 0.70f) {
        float u = (s - 0.35f) / 0.35f;
        float ang = (0.7f - u) * 1.6f;
        px = 1.30f + 0.58f * std::cos(ang);
        pz = 1.00f - 0.58f * std::sin(ang);
    } else {
        float u = (s - 0.70f) / 0.30f;
        px = 1.3f + 1.1f * u;
        pz = 0.70f + 0.90f * u;
    }
    lx = 1.30f; ly = 0.18f; lz = 1.00f;
}

static void ph3(float s, float& px, float& py, float& pz, float& lx, float& ly, float& lz) {
    constexpr float kPI = 3.14159265f;
    float ang = s * 2.0f * kPI;
    px = 1.5f + 0.90f * std::cos(ang);
    py = 2.05f;
    pz = 1.5f + 0.90f * std::sin(ang);
    lx = 1.5f; ly = 0.50f; lz = 1.5f;
}

static void ph4(float s, float& px, float& py, float& pz, float& lx, float& ly, float& lz) {
    px = 1.5f;
    py = 2.05f - 0.67f * s;
    pz = std::max(0.30f, 0.30f + 1.20f * s * (1.0f - s) * 4.0f);
    lx = 1.5f; ly = 1.00f; lz = 1.5f;
}

static void eval_at(float phase, float& px, float& py, float& pz, float& lx, float& ly, float& lz) {
    constexpr float p01 = 0.45f, p12 = 0.60f, p23 = 0.75f, p34 = 0.85f;
    if      (phase < p01) { ph0((phase) / p01, px, py, pz, lx, ly, lz); }
    else if (phase < p12) { ph1((phase - p01) / (p12 - p01), px, py, pz, lx, ly, lz); }
    else if (phase < p23) { ph2((phase - p12) / (p23 - p12), px, py, pz, lx, ly, lz); }
    else if (phase < p34) { ph3((phase - p23) / (p34 - p23), px, py, pz, lx, ly, lz); }
    else                  { ph4((phase - p34) / (1.0f - p34), px, py, pz, lx, ly, lz); }
}

static void scan_path(float phase, float& px, float& py, float& pz, float& lx, float& ly, float& lz) {
    constexpr float p01 = 0.45f, p12 = 0.60f, p23 = 0.75f, p34 = 0.85f;
    constexpr float kB = 0.022f;

    float pb = -1.0f;
    if      (phase >= p01 - kB && phase < p01 + kB) pb = p01;
    else if (phase >= p12 - kB && phase < p12 + kB) pb = p12;
    else if (phase >= p23 - kB && phase < p23 + kB) pb = p23;
    else if (phase >= p34 - kB && phase < p34 + kB) pb = p34;

    if (pb > 0.0f) {
        float bt = (phase - (pb - kB)) / (2.0f * kB);
        float ax, ay, az, alx, aly, alz, bx, by, bz, blx, bly, blz;
        eval_at(pb - kB, ax, ay, az, alx, aly, alz);
        eval_at(pb + kB, bx, by, bz, blx, bly, blz);
        px = sblend(ax, bx, bt);
        py = sblend(ay, by, bt);
        pz = sblend(az, bz, bt);
        lx = sblend(alx, blx, bt);
        ly = sblend(aly, bly, bt);
        lz = sblend(alz, blz, bt);
    } else {
        eval_at(phase, px, py, pz, lx, ly, lz);
    }
}

void generate_handheld_camera_path(std::vector<CameraKeyframe>& keyframes,
                                   std::size_t total_frames,
                                   float duration_sec,
                                   std::mt19937& rng) {
    keyframes.resize(total_frames);
    constexpr float kLoops = 2.5f;
    std::normal_distribution<float> jitter_pos(0.0f, 0.004f);
    std::normal_distribution<float> jitter_rot(0.0f, 0.003f);

    for (std::size_t f = 0; f < total_frames; ++f) {
        float t_frac = static_cast<float>(f) / static_cast<float>(std::max<std::size_t>(1, total_frames - 1));
        double ts = static_cast<double>(duration_sec) * t_frac;
        float speed_mod = 0.7f + 0.3f * std::sin(t_frac * 47.0f);
        if (std::fmod(t_frac * 36.0f, 1.0f) < 0.15f) speed_mod *= 0.1f;
        float phase = std::fmod(t_frac * kLoops, 1.0f);

        float cam_x, cam_y, cam_z, tgt_x, tgt_y, tgt_z;
        scan_path(phase, cam_x, cam_y, cam_z, tgt_x, tgt_y, tgt_z);
        cam_x += jitter_pos(rng);
        cam_y += jitter_pos(rng);
        cam_z += jitter_pos(rng);
        tgt_x += jitter_rot(rng);
        tgt_y += jitter_rot(rng);
        tgt_z += jitter_rot(rng);

        cam_x = std::clamp(cam_x, 0.15f, 2.85f);
        cam_y = std::clamp(cam_y, 0.30f, 2.20f);
        cam_z = std::clamp(cam_z, 0.15f, 2.85f);

        build_cam2world(cam_x, cam_y, cam_z, tgt_x, tgt_y, tgt_z, keyframes[f].cam2world);
        keyframes[f].timestamp = ts;
        keyframes[f].speed_factor = speed_mod;
    }
}

void generate_monogs_tum_camera_path(std::vector<CameraKeyframe>& keyframes,
                                     std::size_t total_frames,
                                     float duration_sec,
                                     std::mt19937& rng) {
    keyframes.resize(total_frames);
    std::normal_distribution<float> jitter_pos(0.0f, 0.0015f);
    std::normal_distribution<float> jitter_rot(0.0f, 0.0010f);

    for (std::size_t f = 0; f < total_frames; ++f) {
        float t_frac = static_cast<float>(f) / static_cast<float>(std::max<std::size_t>(1, total_frames - 1));
        double ts = static_cast<double>(duration_sec) * t_frac;

        float cam_x, cam_y, cam_z, tgt_x, tgt_y, tgt_z;
        if (t_frac < 0.42f) {
            float u = t_frac / 0.42f;
            float ang = (-0.42f + 0.84f * u) * static_cast<float>(M_PI);
            cam_x = 1.45f + 0.42f * std::cos(ang);
            cam_z = 1.25f + 0.32f * std::sin(ang);
            cam_y = 1.34f + 0.05f * std::sin(u * static_cast<float>(M_PI) * 2.0f);
            tgt_x = 1.50f;
            tgt_y = 0.95f;
            tgt_z = 1.45f;
        } else {
            float u = (t_frac - 0.42f) / 0.58f;
            float phase = std::clamp(0.08f + 0.84f * u, 0.0f, 0.999f);
            scan_path(phase, cam_x, cam_y, cam_z, tgt_x, tgt_y, tgt_z);
        }

        cam_x += jitter_pos(rng);
        cam_y += jitter_pos(rng);
        cam_z += jitter_pos(rng);
        tgt_x += jitter_rot(rng);
        tgt_y += jitter_rot(rng);
        tgt_z += jitter_rot(rng);

        cam_x = std::clamp(cam_x, 0.15f, 2.85f);
        cam_y = std::clamp(cam_y, 0.45f, 2.05f);
        cam_z = std::clamp(cam_z, 0.15f, 2.85f);

        build_cam2world(cam_x, cam_y, cam_z, tgt_x, tgt_y, tgt_z, keyframes[f].cam2world);
        keyframes[f].timestamp = ts;
        keyframes[f].speed_factor = (t_frac < 0.42f) ? 0.35f : 0.70f;
    }
}

void generate_wildgs_tum_static_camera_path(std::vector<CameraKeyframe>& keyframes,
                                            std::size_t total_frames,
                                            float duration_sec,
                                            std::mt19937& rng) {
    keyframes.resize(total_frames);
    std::normal_distribution<float> jitter_pos(0.0f, 0.00015f);
    std::normal_distribution<float> jitter_rot(0.0f, 0.00010f);

    for (std::size_t f = 0; f < total_frames; ++f) {
        float t_frac = static_cast<float>(f) / static_cast<float>(std::max<std::size_t>(1, total_frames - 1));
        double ts = static_cast<double>(duration_sec) * t_frac;

        float ang = (0.35f + 0.95f * t_frac) * static_cast<float>(M_PI) * 2.0f;
        float cam_x = 1.50f + 0.080f * std::cos(ang) + 0.015f * std::sin(ang * 0.5f);
        float cam_y = 1.28f + 0.028f * std::sin(ang * 0.7f);
        float cam_z = 1.48f + 0.060f * std::sin(ang) + 0.010f * std::cos(ang * 0.5f);

        float tgt_x = 1.50f;
        float tgt_y = 0.98f;
        float tgt_z = 1.46f;

        cam_x += jitter_pos(rng);
        cam_y += jitter_pos(rng);
        cam_z += jitter_pos(rng);
        tgt_x += jitter_rot(rng);
        tgt_y += jitter_rot(rng);
        tgt_z += jitter_rot(rng);

        cam_x = std::clamp(cam_x, 0.15f, 2.85f);
        cam_y = std::clamp(cam_y, 0.55f, 2.05f);
        cam_z = std::clamp(cam_z, 0.15f, 2.85f);

        build_cam2world(cam_x, cam_y, cam_z, tgt_x, tgt_y, tgt_z, keyframes[f].cam2world);
        keyframes[f].timestamp = ts;
        keyframes[f].speed_factor = 0.10f;
    }
}

void generate_wildgs_tum_scan_camera_path(std::vector<CameraKeyframe>& keyframes,
                                          std::size_t total_frames,
                                          float duration_sec,
                                          std::mt19937& rng) {
    keyframes.resize(total_frames);
    std::normal_distribution<float> jitter_pos(0.0f, 0.0006f);
    std::normal_distribution<float> jitter_rot(0.0f, 0.0004f);

    for (std::size_t f = 0; f < total_frames; ++f) {
        float t_frac = static_cast<float>(f) / static_cast<float>(std::max<std::size_t>(1, total_frames - 1));
        double ts = static_cast<double>(duration_sec) * t_frac;

        float phase = std::clamp(0.03f + 0.94f * t_frac, 0.0f, 0.999f);
        float cam_x, cam_y, cam_z, tgt_x, tgt_y, tgt_z;
        scan_path(phase, cam_x, cam_y, cam_z, tgt_x, tgt_y, tgt_z);

        cam_x += jitter_pos(rng);
        cam_y += jitter_pos(rng);
        cam_z += jitter_pos(rng);
        tgt_x += jitter_rot(rng);
        tgt_y += jitter_rot(rng);
        tgt_z += jitter_rot(rng);

        cam_x = std::clamp(cam_x, 0.15f, 2.85f);
        cam_y = std::clamp(cam_y, 0.45f, 2.10f);
        cam_z = std::clamp(cam_z, 0.15f, 2.85f);

        build_cam2world(cam_x, cam_y, cam_z, tgt_x, tgt_y, tgt_z, keyframes[f].cam2world);
        keyframes[f].timestamp = ts;
        keyframes[f].speed_factor = 0.12f;
    }
}

void render_bgr_lit(const float cam2world[16],
                    float fx, float fy, float cx, float cy,
                    std::uint32_t W, std::uint32_t H,
                    std::vector<std::uint8_t>& bgr_out,
                    std::vector<float>* depth_out = nullptr) {
    bgr_out.resize(static_cast<std::size_t>(W) * H * 3);
    if (depth_out) {
        depth_out->assign(static_cast<std::size_t>(W) * H, 0.0f);
    }
    float ox = cam2world[12], oy = cam2world[13], oz = cam2world[14];

    for (std::uint32_t v = 0; v < H; ++v) {
        for (std::uint32_t u = 0; u < W; ++u) {
            float rx = (static_cast<float>(u) - cx) / fx;
            float ry = -(static_cast<float>(v) - cy) / fy;
            float rz = -1.0f;
            float len = std::sqrt(rx * rx + ry * ry + rz * rz);
            rx /= len; ry /= len; rz /= len;

            float wx = cam2world[0] * rx + cam2world[4] * ry + cam2world[8] * rz;
            float wy = cam2world[1] * rx + cam2world[5] * ry + cam2world[9] * rz;
            float wz = cam2world[2] * rx + cam2world[6] * ry + cam2world[10] * rz;

            float t = sphere_trace(ox, oy, oz, wx, wy, wz);
            std::size_t idx = (static_cast<std::size_t>(v) * W + u) * 3;

            if (t > 0.0f) {
                float hx = ox + wx * t;
                float hy = oy + wy * t;
                float hz = oz + wz * t;
                if (depth_out) {
                    (*depth_out)[static_cast<std::size_t>(v) * W + u] = t;
                }
                float nx, ny, nz;
                scene_normal(hx, hy, hz, nx, ny, nz);
                int region = classify_surface_region(hx, hy, hz);
                float base_r = 0.18f, base_g = 0.18f, base_b = 0.18f;
                if (region >= 0 && region < kNumRegions) {
                    base_r = kRegions[region].linear_rgb[0];
                    base_g = kRegions[region].linear_rgb[1];
                    base_b = kRegions[region].linear_rgb[2];
                    apply_texture_modulation(region, hx, hy, hz, nx, ny, nz, base_r, base_g, base_b);
                }
                float lr, lg, lb;
                compute_lighting(hx, hy, hz, nx, ny, nz, lr, lg, lb);
                float fr = std::min(base_r * lr, 1.0f);
                float fg = std::min(base_g * lg, 1.0f);
                float fb = std::min(base_b * lb, 1.0f);
                auto to_srgb = [](float c) -> std::uint8_t {
                    float s = (c <= 0.0031308f) ? c * 12.92f : 1.055f * std::pow(c, 1.0f / 2.4f) - 0.055f;
                    return static_cast<std::uint8_t>(std::clamp(s * 255.0f + 0.5f, 0.0f, 255.0f));
                };
                bgr_out[idx + 0] = to_srgb(fb);
                bgr_out[idx + 1] = to_srgb(fg);
                bgr_out[idx + 2] = to_srgb(fr);
            } else {
                bgr_out[idx + 0] = 10;
                bgr_out[idx + 1] = 10;
                bgr_out[idx + 2] = 10;
            }
        }
    }
}

bool write_depth_bin(const fs::path& path,
                     const std::vector<float>& depth) {
    std::ofstream out(path, std::ios::binary);
    if (!out) return false;
    out.write(reinterpret_cast<const char*>(depth.data()),
              static_cast<std::streamsize>(depth.size() * sizeof(float)));
    return static_cast<bool>(out);
}

bool write_ppm(const fs::path& path,
               std::uint32_t width,
               std::uint32_t height,
               const std::vector<std::uint8_t>& bgr) {
    std::ofstream out(path, std::ios::binary);
    if (!out) return false;
    out << "P6\n" << width << ' ' << height << "\n255\n";
    for (std::size_t i = 0; i < bgr.size(); i += 3) {
        char rgb[3] = {
            static_cast<char>(bgr[i + 2]),
            static_cast<char>(bgr[i + 1]),
            static_cast<char>(bgr[i + 0])
        };
        out.write(rgb, 3);
    }
    return static_cast<bool>(out);
}

struct Options {
    fs::path output_dir;
    fs::path camera_path;
    std::uint32_t width = 1280;
    std::uint32_t height = 960;
    std::size_t frames = 450;
    float duration_sec = 37.0f;
    unsigned int seed = 7;
    float hfov_deg = 60.0f;
    float fx_override = -1.0f;
    float fy_override = -1.0f;
    float cx_override = -1.0f;
    float cy_override = -1.0f;
    float static_tail_sec = 0.0f;
    float static_tail_jitter_mm = 1.0f;
    CaptureProtocol protocol = CaptureProtocol::Handheld;
    bool write_depth_bin = false;
    float realtime_scale = 0.0f;
};

Options parse_args(int argc, char** argv) {
    Options opts;
    opts.output_dir = fs::path("donor_whitebox/outputs/hislam2_room3x3");
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        auto next = [&](const char* name) -> const char* {
            if (i + 1 >= argc) {
                throw std::runtime_error(std::string("missing value for ") + name);
            }
            return argv[++i];
        };
        if (arg == "--output") opts.output_dir = fs::path(next("--output"));
        else if (arg == "--camera-path") opts.camera_path = fs::path(next("--camera-path"));
        else if (arg == "--width") opts.width = static_cast<std::uint32_t>(std::stoul(next("--width")));
        else if (arg == "--height") opts.height = static_cast<std::uint32_t>(std::stoul(next("--height")));
        else if (arg == "--frames") opts.frames = static_cast<std::size_t>(std::stoul(next("--frames")));
        else if (arg == "--duration") opts.duration_sec = std::stof(next("--duration"));
        else if (arg == "--seed") opts.seed = static_cast<unsigned int>(std::stoul(next("--seed")));
        else if (arg == "--hfov") opts.hfov_deg = std::stof(next("--hfov"));
        else if (arg == "--fx") opts.fx_override = std::stof(next("--fx"));
        else if (arg == "--fy") opts.fy_override = std::stof(next("--fy"));
        else if (arg == "--cx") opts.cx_override = std::stof(next("--cx"));
        else if (arg == "--cy") opts.cy_override = std::stof(next("--cy"));
        else if (arg == "--static-tail-seconds") opts.static_tail_sec = std::stof(next("--static-tail-seconds"));
        else if (arg == "--static-tail-jitter-mm") opts.static_tail_jitter_mm = std::stof(next("--static-tail-jitter-mm"));
        else if (arg == "--realtime-scale") opts.realtime_scale = std::stof(next("--realtime-scale"));
        else if (arg == "--protocol") {
            std::string value = next("--protocol");
            if (value == "handheld") opts.protocol = CaptureProtocol::Handheld;
            else if (value == "monogs_tum") opts.protocol = CaptureProtocol::MonoGSTum;
            else if (value == "wildgs_tum_static") opts.protocol = CaptureProtocol::WildGSTumStatic;
            else if (value == "wildgs_tum_scan") opts.protocol = CaptureProtocol::WildGSTumScan;
            else throw std::runtime_error("unknown protocol: " + value);
        }
        else if (arg == "--write-depth-bin") opts.write_depth_bin = true;
        else if (arg == "--help") {
            std::cout << "Usage: export_room_sequence [--output DIR] [--camera-path FILE] [--width W] [--height H] [--frames N] [--duration SEC] [--seed S] [--hfov DEG] [--fx X --fy Y --cx X0 --cy Y0] [--protocol handheld|monogs_tum|wildgs_tum_static|wildgs_tum_scan] [--static-tail-seconds SEC] [--static-tail-jitter-mm MM] [--write-depth-bin] [--realtime-scale X]\n";
            std::exit(0);
        } else {
            throw std::runtime_error("unknown arg: " + arg);
        }
    }
    return opts;
}

} // namespace

int main(int argc, char** argv) {
    try {
        Options opts = parse_args(argc, argv);
        fs::path images_dir = opts.output_dir / "images";
        fs::create_directories(images_dir);
        fs::path depth_dir = opts.output_dir / "depth_raw";
        if (opts.write_depth_bin) {
            fs::create_directories(depth_dir);
        }

        float fx = 0.5f * static_cast<float>(opts.width) /
            std::tan(0.5f * opts.hfov_deg * static_cast<float>(M_PI) / 180.0f);
        float fy = fx;
        float cx = 0.5f * static_cast<float>(opts.width);
        float cy = 0.5f * static_cast<float>(opts.height);
        if (opts.fx_override > 0.0f) fx = opts.fx_override;
        if (opts.fy_override > 0.0f) fy = opts.fy_override;
        if (opts.cx_override >= 0.0f) cx = opts.cx_override;
        if (opts.cy_override >= 0.0f) cy = opts.cy_override;

        std::vector<CameraKeyframe> keyframes;
        std::mt19937 rng(opts.seed);
        if (!opts.camera_path.empty()) {
            keyframes = load_camera_path_file(opts.camera_path);
            opts.frames = static_cast<int>(keyframes.size());
            if (keyframes.size() >= 2) {
                const double start_ts = keyframes.front().timestamp;
                const double end_ts = keyframes.back().timestamp;
                opts.duration_sec = static_cast<float>(std::max(0.0, end_ts - start_ts));
            } else if (!keyframes.empty()) {
                opts.duration_sec = 0.0f;
            }
        } else {
            switch (opts.protocol) {
                case CaptureProtocol::Handheld:
                    generate_handheld_camera_path(keyframes, opts.frames, opts.duration_sec, rng);
                    break;
                case CaptureProtocol::MonoGSTum:
                    generate_monogs_tum_camera_path(keyframes, opts.frames, opts.duration_sec, rng);
                    break;
                case CaptureProtocol::WildGSTumStatic:
                    generate_wildgs_tum_static_camera_path(keyframes, opts.frames, opts.duration_sec, rng);
                    break;
                case CaptureProtocol::WildGSTumScan:
                    generate_wildgs_tum_scan_camera_path(keyframes, opts.frames, opts.duration_sec, rng);
                    break;
            }
        }
        if (opts.camera_path.empty()) {
            apply_static_tail(
                keyframes,
                opts.duration_sec,
                opts.static_tail_sec,
                opts.static_tail_jitter_mm,
                rng);
        }

        {
            std::ofstream calib(opts.output_dir / "calib.txt");
            calib << std::fixed << std::setprecision(6)
                  << fx << ' ' << fy << ' ' << cx << ' ' << cy << '\n';
        }
        {
            std::ofstream poses(opts.output_dir / "traj_gt.txt");
            poses << std::fixed << std::setprecision(6);
            for (const auto& kf : keyframes) {
                poses << kf.timestamp;
                for (float v : kf.cam2world) poses << ' ' << v;
                poses << '\n';
            }
        }
        {
            std::ofstream manifest(opts.output_dir / "manifest.txt");
            manifest << "generator=export_room_sequence\n";
            manifest << "scene=procedural_room_3x3\n";
            std::string protocol_name = "handheld";
            if (!opts.camera_path.empty()) protocol_name = "external_camera_path";
            else if (opts.protocol == CaptureProtocol::MonoGSTum) protocol_name = "monogs_tum";
            else if (opts.protocol == CaptureProtocol::WildGSTumStatic) protocol_name = "wildgs_tum_static";
            else if (opts.protocol == CaptureProtocol::WildGSTumScan) protocol_name = "wildgs_tum_scan";
            manifest << "protocol=" << protocol_name << '\n';
            manifest << "frames=" << keyframes.size() << '\n';
            manifest << "duration_sec=" << opts.duration_sec << '\n';
            manifest << "width=" << opts.width << '\n';
            manifest << "height=" << opts.height << '\n';
            manifest << "fx=" << fx << '\n';
            manifest << "fy=" << fy << '\n';
            manifest << "cx=" << cx << '\n';
            manifest << "cy=" << cy << '\n';
            manifest << "seed=" << opts.seed << '\n';
            if (!opts.camera_path.empty()) {
                manifest << "camera_path_source=" << opts.camera_path << '\n';
            }
            manifest << "static_tail_sec=" << opts.static_tail_sec << '\n';
            manifest << "static_tail_jitter_mm=" << opts.static_tail_jitter_mm << '\n';
            manifest << "write_depth_bin=" << (opts.write_depth_bin ? "1" : "0") << '\n';
        }
        {
            const TrajectoryStats stats = compute_trajectory_stats(keyframes);
            std::ofstream summary(opts.output_dir / "sequence_summary.json");
            summary << std::fixed << std::setprecision(6);
            summary << "{\n";
            summary << "  \"frames\": " << keyframes.size() << ",\n";
            summary << "  \"duration_sec\": " << opts.duration_sec << ",\n";
            summary << "  \"trajectory\": {\n";
            summary << "    \"step_mean_m\": " << stats.step_mean_m << ",\n";
            summary << "    \"step_p95_m\": " << stats.step_p95_m << ",\n";
            summary << "    \"step_max_m\": " << stats.step_max_m << ",\n";
            summary << "    \"total_path_m\": " << stats.total_path_m << ",\n";
            summary << "    \"frames_per_meter\": " << stats.frames_per_meter << ",\n";
            summary << "    \"max_from_start_m\": " << stats.max_from_start_m << ",\n";
            summary << "    \"max_from_center_m\": " << stats.max_from_center_m << "\n";
            summary << "  }\n";
            summary << "}\n";
        }
        std::vector<std::uint8_t> bgr;
        std::vector<float> depth;
        for (std::size_t i = 0; i < keyframes.size(); ++i) {
            render_bgr_lit(
                keyframes[i].cam2world, fx, fy, cx, cy, opts.width, opts.height, bgr,
                opts.write_depth_bin ? &depth : nullptr);
            std::ostringstream name;
            name << std::setfill('0') << std::setw(6) << i << '_' << std::fixed << std::setprecision(6)
                 << keyframes[i].timestamp << ".ppm";
            fs::path image_path = images_dir / name.str();
            if (!write_ppm(image_path, opts.width, opts.height, bgr)) {
                std::cerr << "failed writing image: " << image_path << '\n';
                return 2;
            }
            if (opts.write_depth_bin) {
                fs::path depth_path = depth_dir / (name.str().substr(0, name.str().size() - 4) + ".bin");
                if (!write_depth_bin(depth_path, depth)) {
                    std::cerr << "failed writing depth: " << depth_path << '\n';
                    return 3;
                }
            }
            if ((i + 1) % 25 == 0 || i + 1 == keyframes.size()) {
                std::cout << "[export_room_sequence] frames=" << (i + 1) << '/' << keyframes.size() << '\n';
            }
            if (opts.realtime_scale > 0.0f && i + 1 < keyframes.size()) {
                const double dt = std::max(0.0, keyframes[i + 1].timestamp - keyframes[i].timestamp);
                if (dt > 0.0) {
                    std::this_thread::sleep_for(std::chrono::duration<double>(dt * opts.realtime_scale));
                }
            }
        }
        {
            std::ofstream complete(opts.output_dir / ".complete");
            complete << "ok\n";
        }

        std::cout << "[export_room_sequence] done output=" << opts.output_dir << " frames=" << keyframes.size()
                  << " size=" << opts.width << 'x' << opts.height << " fx=" << fx << '\n';
        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "export_room_sequence error: " << ex.what() << '\n';
        return 1;
    }
}
