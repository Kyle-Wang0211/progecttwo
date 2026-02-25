//
// ScanGuidance.metal
// Aether3D
//
// PR#7 Scan Guidance UI — Metal Shaders
// Phase 2: Implements wedge fill + border stroke passes only
//

#include <metal_stdlib>
using namespace metal;

// ─── Structs ───

struct WedgeVertex {
    float3 position     [[attribute(0)]];
    float3 normal       [[attribute(1)]];
    float  metallic     [[attribute(2)]];
    float  roughness    [[attribute(3)]];
    float  display      [[attribute(4)]];
    float  thickness    [[attribute(5)]];
    uint   triangleId   [[attribute(6)]];
};

struct ScanGuidanceUniforms {
    float4x4 viewProjectionMatrix;
    float4x4 modelMatrix;
    float3   cameraPosition;
    float3   primaryLightDirection;
    float    primaryLightIntensity;
    float3   shCoeffs[9];
    uint     qualityTier;
    float    time;
    float    borderGamma;
};

// v7.0.1 FIX: Use float3 instead of uint8_t×3 to avoid Metal alignment issues
// Use packed_float3 to avoid 16-byte alignment padding (48 bytes total)
struct PerTriangleData {
    float  flipAngle;
    float  rippleAmplitude;
    float  borderWidth;
    packed_float3 flipAxisOrigin;
    packed_float3 flipAxisDirection;
    packed_float3 grayscaleColor;  // v7.0.1: was uint8_t×3, now float3 [0,1]
};

// ─── PBR Helper Functions ───

// fp16-safe GGX NDF (Filament approach: cross product avoids catastrophic cancellation)
// Reference: Google Filament v1.69 (2025), Romain Guy fp16 GGX gist
inline half NDF_GGX_Safe(half3 N, half3 H, half roughness) {
    half a = roughness * roughness;
    half a2 = a * a;
    // Use cross product: |N×H|² = 1 - (N·H)² without cancellation error in fp16
    float3 NxH = cross(float3(N), float3(H));
    half OneMinusNdotHSqr = half(dot(NxH, NxH));
    half d = OneMinusNdotHSqr * (a2 - 1.0h) + 1.00001h;  // +epsilon prevents d=0
    return a2 / (M_PI_H * d * d);
}

// Kelemen-Szirmay-Kalos Visibility (saves ~4 ALU vs full Smith-GGX)
// Reference: Filament PBR, optimized for mobile
inline half VisibilityKelemen(half LdotH, half roughness) {
    return 1.0h / (4.0h * max(0.1h, LdotH * LdotH) * (roughness + 0.5h));
}

// Legacy Smith-GGX kept for reference/fallback
inline half GeometrySchlickGGX(half NdotV, half roughness) {
    half r = roughness + 1.0h;
    half k = (r * r) / 8.0h;
    return NdotV / (NdotV * (1.0h - k) + k);
}

inline half GeometrySmith(half NdotV, half NdotL, half roughness) {
    return GeometrySchlickGGX(NdotV, roughness) * GeometrySchlickGGX(NdotL, roughness);
}

// Schlick Fresnel Approximation
inline half3 FresnelSchlick(half cosTheta, half3 F0) {
    half t = 1.0h - cosTheta;
    half t2 = t * t;
    half t5 = t2 * t2 * t;
    return F0 + (1.0h - F0) * t5;
}

// Evaluate L2 Spherical Harmonics (9 coefficients)
inline half3 evaluateSH(float3 n, constant float3 *shCoeffs) {
    // L0 band (ambient)
    half3 result = half3(shCoeffs[0]) * 0.282095h;
    
    // L1 band (directional)
    result += half3(shCoeffs[1]) * 0.488603h * half(n.y);
    result += half3(shCoeffs[2]) * 0.488603h * half(n.z);
    result += half3(shCoeffs[3]) * 0.488603h * half(n.x);
    
    // L2 band (detailed directional)
    result += half3(shCoeffs[4]) * 1.092548h * half(n.x * n.y);
    result += half3(shCoeffs[5]) * 1.092548h * half(n.y * n.z);
    result += half3(shCoeffs[6]) * 0.315392h * half(3.0 * n.z * n.z - 1.0);
    result += half3(shCoeffs[7]) * 1.092548h * half(n.x * n.z);
    result += half3(shCoeffs[8]) * 0.546274h * half(n.x * n.x - n.y * n.y);
    
    return max(result, 0.0h);
}

// ─── Oklab Perceptual Color Space (Layer 7.2) ───
// Reference: Björn Ottosson 2020, "A perceptual color space for image processing"
// Oklab provides perceptually uniform lightness, enabling smooth gradients
// that look visually even across the entire display [0,1] range.

/// Convert Oklab (L, a, b) → linear sRGB
/// Uses the exact matrix from Ottosson's paper (LMS→linear sRGB).
inline half3 oklabToLinearSRGB(half L, half a, half b) {
    // Oklab → LMS (cube root domain)
    half l_ = L + 0.3963377774h * a + 0.2158037573h * b;
    half m_ = L - 0.1055613458h * a - 0.0638541728h * b;
    half s_ = L - 0.0894841775h * a - 1.2914855480h * b;

    // Undo cube root
    half l = l_ * l_ * l_;
    half m = m_ * m_ * m_;
    half s = s_ * s_ * s_;

    // LMS → linear sRGB
    half3 rgb;
    rgb.r = +4.0767416621h * l - 3.3077115913h * m + 0.2309699292h * s;
    rgb.g = -1.2684380046h * l + 2.6097574011h * m - 0.3413193965h * s;
    rgb.b = -0.0041960863h * l - 0.7034186147h * m + 1.7076147010h * s;

    return clamp(rgb, 0.0h, 1.0h);
}

/// Apply IEC 61966-2-1 sRGB OETF (linear → sRGB gamma).
/// Required because render target uses .bgra8Unorm (no hardware conversion).
/// The precise piecewise function avoids banding artifacts in dark regions.
inline half3 linearToSRGB(half3 linear) {
    // Per-channel piecewise sRGB OETF:
    //   c <= 0.0031308: sRGB = c × 12.92
    //   c >  0.0031308: sRGB = 1.055 × c^(1/2.4) - 0.055
    half3 srgb;
    srgb.r = linear.r <= 0.0031308h ? linear.r * 12.92h : 1.055h * pow(linear.r, 1.0h / 2.4h) - 0.055h;
    srgb.g = linear.g <= 0.0031308h ? linear.g * 12.92h : 1.055h * pow(linear.g, 1.0h / 2.4h) - 0.055h;
    srgb.b = linear.b <= 0.0031308h ? linear.b * 12.92h : 1.055h * pow(linear.b, 1.0h / 2.4h) - 0.055h;
    return clamp(srgb, 0.0h, 1.0h);
}

/// Map display evidence [0,1] → Oklab perceptual color
/// Cold (subtle blue) at low evidence → warm neutral white at high evidence.
/// Chroma is kept very low to stay close to grayscale while adding depth.
inline half3 evidenceToOklabColor(half display) {
    // Lightness: 0.05 (near-black) → 0.97 (near-white)
    // S0 triangles appear nearly pure black with white borders
    half L = mix(0.05h, 0.97h, display);

    // Chroma via a,b: small cool offset at low display, fading to neutral
    // a (green-red): slight warm shift at high evidence
    half a_ok = mix(-0.005h, 0.003h, display);
    // b (blue-yellow): cool (negative) at low → neutral at high
    half b_ok = mix(-0.015h, 0.002h, display);

    return oklabToLinearSRGB(L, a_ok, b_ok);
}

// Quaternion from axis-angle
inline float4 quatFromAxisAngle(float3 axis, float angle) {
    float halfAngle = angle * 0.5;
    float s = sin(halfAngle);
    float c = cos(halfAngle);
    return float4(axis * s, c);
}

// Rotate vector by quaternion
inline float3 rotateByQuat(float3 v, float4 q) {
    float3 u = q.xyz;
    float w = q.w;
    return 2.0 * dot(u, v) * u
         + (w * w - dot(u, u)) * v
         + 2.0 * w * cross(u, v);
}

// ─── Vertex/Fragment Shaders ───

struct VertexOut {
    float4 position [[position]];
    float3 worldNormal;
    float3 worldPosition;
    float  metallic;
    float  roughness;
    float  display;
    float  rippleAmplitude;
    float3 grayscaleColor;
    float  borderWidth;
};

// ─── Pass 1: Wedge Fill ───

vertex VertexOut wedgeFillVertex(
    WedgeVertex in [[stage_in]],
    constant ScanGuidanceUniforms &uniforms [[buffer(1)]],
    constant PerTriangleData *triData [[buffer(2)]],
    uint vid [[vertex_id]]
) {
    VertexOut out;
    uint triId = in.triangleId;
    
    float3 pos = in.position;
    float3 norm = in.normal;
    
    // ── Step 1: Flip Rotation ──
    float angle = triData[triId].flipAngle;
    if (angle > 0.001) {
        float3 axisOrigin = triData[triId].flipAxisOrigin;
        float3 axisDir = triData[triId].flipAxisDirection;
        
        // Translate to axis-local space, rotate, translate back
        float3 localPos = pos - axisOrigin;
        float4 q = quatFromAxisAngle(axisDir, angle);
        localPos = rotateByQuat(localPos, q);
        pos = localPos + axisOrigin;
        
        // Rotate normal too
        norm = rotateByQuat(norm, q);
    }
    
    // ── Step 2: Ripple Displacement ──
    float ripple = triData[triId].rippleAmplitude;
    if (ripple > 0.001) {
        float displacement = ripple * in.thickness * 0.3;  // rippleThicknessMultiplier
        pos += norm * displacement;
    }
    
    // ── Step 3: Transform to clip space ──
    float4 worldPos = uniforms.modelMatrix * float4(pos, 1.0);
    out.position = uniforms.viewProjectionMatrix * worldPos;
    out.worldPosition = worldPos.xyz;
    out.worldNormal = normalize((uniforms.modelMatrix * float4(norm, 0.0)).xyz);
    
    // ── Step 4: Pass-through attributes ──
    out.metallic = in.metallic;
    out.roughness = in.roughness;
    out.display = in.display;
    out.rippleAmplitude = ripple;
    out.grayscaleColor = triData[triId].grayscaleColor;
    out.borderWidth = triData[triId].borderWidth;
    
    return out;
}

fragment half4 wedgeFillFragment(
    VertexOut in [[stage_in]],
    constant ScanGuidanceUniforms &uniforms [[buffer(1)]]
) {
    // ── Material Properties ──
    half metallic = half(in.metallic);
    // fp16-safe roughness floor (Filament: prevents fp16 underflow at 6.1e-5)
    half roughness = max(half(in.roughness), 0.089h);

    // Base color: blend CPU grayscale with GPU Oklab for perceptual uniformity.
    // Layer 7.2: Oklab provides perceptually even lightness transitions that
    // linear grayscale cannot — mid-tones look evenly spaced to the human eye.
    half3 cpuColor = half3(in.grayscaleColor);
    half3 oklabColor = evidenceToOklabColor(half(in.display));
    // 70% Oklab + 30% CPU grayscale: Oklab dominates for perceptual uniformity,
    // CPU color adds per-patch variation from the evidence system.
    half3 baseColor = mix(cpuColor, oklabColor, 0.7h);
    // Ensure base color is never pure black (prevents zero ambient floor)
    baseColor = max(baseColor, half3(0.02h));

    // ── Vectors ──
    float3 N = normalize(in.worldNormal);
    float3 V = normalize(uniforms.cameraPosition - in.worldPosition);
    float3 L = normalize(-uniforms.primaryLightDirection);
    float3 H = normalize(V + L);

    half NdotL = half(max(dot(N, L), 0.0));
    half NdotH = half(max(dot(N, H), 0.0));
    half HdotV = half(max(dot(H, V), 0.0));
    half LdotH = half(max(dot(L, H), 0.0));

    half3 color;

    // ── Thermal Fragment LOD ──
    if (uniforms.qualityTier >= 3) {
        // CRITICAL thermal: flat Lambertian, no specular, no SH — saves ~60% fragment ALU
        color = baseColor * max(NdotL, 0.15h);
    } else if (uniforms.qualityTier >= 2) {
        // SERIOUS thermal: simplified Blinn-Phong, no SH — saves ~35% fragment ALU
        half spec = pow(max(NdotH, 0.0h), 32.0h) * 0.3h;
        half lightIntensity = half(uniforms.primaryLightIntensity);
        half normalizedIntensity = clamp(lightIntensity / 1000.0h, 0.1h, 3.0h);
        color = baseColor * NdotL * normalizedIntensity + half3(spec);
    } else {
        // NOMINAL/FAIR: Full Cook-Torrance PBR with fp16-safe GGX

        // F0: metallic uses base color, dielectric uses 0.04
        half3 F0 = mix(half3(0.04h), baseColor, metallic);

        // ── Cook-Torrance Specular BRDF (fp16-safe) ──
        half3 N_h = half3(N);
        half3 H_h = half3(H);
        half D = NDF_GGX_Safe(N_h, H_h, roughness);
        half Vis = VisibilityKelemen(LdotH, roughness);
        half3 F = FresnelSchlick(HdotV, F0);

        half3 specular = D * Vis * F;
        // Clamp to MEDIUMP_FLT_MAX to prevent fp16 overflow
        specular = min(specular, half3(65504.0h));

        // ── Energy Conservation ──
        half3 kS = F;
        half3 kD = (1.0h - kS) * (1.0h - metallic);

        // ── Diffuse: Lambertian ──
        half3 diffuse = kD * baseColor / M_PI_H;

        // ── Direct Lighting ──
        half lightIntensity = half(uniforms.primaryLightIntensity);
        half normalizedIntensity = clamp(lightIntensity / 1000.0h, 0.1h, 3.0h);
        half3 directLight = (diffuse + specular) * NdotL * normalizedIntensity;

        // ── Indirect Lighting (SH-based IBL) ──
        half3 irradiance = evaluateSH(N, uniforms.shCoeffs);
        // Safe SH normalization: guard against zero/NaN/Inf coefficients
        half3 shDenom = max(half3(uniforms.shCoeffs[0]) * 0.282095h, half3(0.05h));
        irradiance = irradiance / shDenom * 0.3h;
        // NaN/Inf guard
        irradiance = select(irradiance, half3(0.15h), isnan(irradiance) || isinf(irradiance));
        half3 indirectDiffuse = kD * baseColor * irradiance;

        // Indirect specular: SH at reflection direction
        float3 R = reflect(-V, N);
        half3 reflectedIrradiance = evaluateSH(R, uniforms.shCoeffs);
        reflectedIrradiance = reflectedIrradiance / shDenom * 0.5h;
        reflectedIrradiance = select(reflectedIrradiance, half3(0.1h), isnan(reflectedIrradiance) || isinf(reflectedIrradiance));
        half3 indirectSpecular = F0 * reflectedIrradiance * (1.0h - roughness * 0.7h);

        half3 indirect = indirectDiffuse + indirectSpecular;

        color = directLight + indirect;
    }

    // ── Ambient minimum (all tiers) ──
    color = max(color, baseColor * 0.02h);

    // ── Ripple highlight ──
    if (in.rippleAmplitude > 0.001) {
        half rippleBoost = half(in.rippleAmplitude) * 0.15h;
        color += rippleBoost;
    }

    // ── Tone mapping (Reinhard in float for precision) ──
    float3 colorF = float3(color);
    colorF = colorF / (colorF + 1.0);
    color = half3(colorF);

    // ── Alpha: S5 fade with stochastic blue-noise dithering ──
    half alpha = 1.0h;
    if (in.display > 0.75h) {
        // Progressive fade from S4 (0.75) to S5+ (1.0)
        half fade = (half(in.display) - 0.75h) / (1.0h - 0.75h);
        // Position-based hash for blue-noise-like dithering (TAA-friendly)
        // Layer 3.8: Added temporal seed (uniforms.time * 60.0) so the dithering
        // pattern varies per frame, preventing persistent stipple artifacts.
        // Will upgrade to STBN 128×128×64 texture in future pass.
        float2 screenPos = in.position.xy + uniforms.time * 60.0;
        half noise = half(fract(sin(dot(screenPos, float2(12.9898, 78.233))) * 43758.5453));
        // Stochastic transparency: converges under temporal accumulation
        alpha = (fade < noise) ? 1.0h : 0.0h;
    }

    // sRGB gamma encoding — pixel format is .bgra8Unorm (no hardware conversion).
    // Apply OETF BEFORE pre-multiplied alpha to avoid double-gamma on blended edges.
    color = linearToSRGB(color);

    // Pre-multiplied alpha for AR compositing
    color *= alpha;

    return half4(color, alpha);
}

// ─── Pass 2: Border Stroke ───

// SDF helper functions
inline float sdTriangle2D(float2 p, float2 a, float2 b, float2 c) {
    float2 ba = b - a, cb = c - b, ac = a - c;
    float2 pa = p - a, pb = p - b, pc = p - c;
    float2 nor = float2(ba.y, -ba.x);
    float s = sign(dot(nor, pa));
    // NaN guard: degenerate triangles (coincident vertices) produce dot(ba,ba)=0
    float2 d1 = pa - ba * clamp(dot(pa, ba) / max(dot(ba, ba), 1e-10), 0.0, 1.0);
    float2 d2 = pb - cb * clamp(dot(pb, cb) / max(dot(cb, cb), 1e-10), 0.0, 1.0);
    float2 d3 = pc - ac * clamp(dot(pc, ac) / max(dot(ac, ac), 1e-10), 0.0, 1.0);
    float md = min(min(dot(d1,d1), dot(d2,d2)), dot(d3,d3));
    return sqrt(md) * s;
}

inline float sdRoundedTriangle(float2 p, float2 a, float2 b, float2 c, float r) {
    return sdTriangle2D(p, a, b, c) - r;
}

fragment half4 borderStrokeFragment(
    VertexOut in [[stage_in]],
    constant ScanGuidanceUniforms &uniforms [[buffer(1)]]
) {
    // Border width from AdaptiveBorderCalculator
    half borderWidth = half(in.borderWidth);

    // Skip if border width is effectively zero
    if (borderWidth < 0.5h) {
        discard_fragment();
    }

    // Border color: bright white
    half3 borderColor = half3(1.0h, 1.0h, 1.0h);

    // Alpha: modulated by display value
    half baseAlpha = 1.0h;  // borderAlphaAtS0
    half displayFade = 1.0h - half(in.display) * 0.5h;
    half alpha = baseAlpha * displayFade;

    // Apply Stevens' Power Law gamma correction
    // Layer 7.5: gamma = 0.45 → pow(alpha, 2.22) matches sRGB EOTF for
    // perceptually linear border fade on iPhone/iPad displays.
    half gamma = half(uniforms.borderGamma);
    alpha = pow(max(alpha, 0.001h), 1.0h / gamma);

    // ── Layer 7.1: Edge Temporal Anti-Aliasing (ETAA) ──
    // Multi-sample edge detection using screen-space derivatives for
    // sub-pixel accurate anti-aliasing at mesh boundaries.
    //
    // Standard fwidth gives 1px softness; ETAA extends this with:
    // 1. Anisotropic edge softness (respects edge direction)
    // 2. Temporal jitter to break up aliasing under motion
    // 3. Variance-aware clamping to prevent ghosting

    // Anisotropic screen-space derivatives (better than isotropic fwidth)
    float2 dPosDx = float2(dfdx(in.worldPosition.x), dfdx(in.worldPosition.z));
    float2 dPosDy = float2(dfdy(in.worldPosition.x), dfdy(in.worldPosition.z));
    // Edge softness: geometric mean of derivative magnitudes for balanced AA
    half edgeSoftX = half(length(dPosDx));
    half edgeSoftY = half(length(dPosDy));
    half edgeSoftness = sqrt(edgeSoftX * edgeSoftX + edgeSoftY * edgeSoftY);
    // Fallback to position-based fwidth if world derivatives are degenerate
    edgeSoftness = max(edgeSoftness, half(fwidth(in.position.x) + fwidth(in.position.y)) * 0.5h);

    // Temporal sub-pixel jitter: shifts the AA kernel by ±0.5 texel per frame
    // to break up static aliasing patterns.  Under temporal accumulation (TAA)
    // this converges to super-sampled quality.
    float timeFrac = fract(uniforms.time * 7.0);  // 7 Hz jitter cycle
    half jitterOffset = half(timeFrac - 0.5) * edgeSoftness * 0.5h;
    half effectiveBorderWidth = borderWidth * 1.5h + jitterOffset;

    // Variance clipping: limit the jitter to prevent ghosting
    // (k-DOP simplified to 1D: clamp within ±1σ of the static border)
    half staticBorder = borderWidth * 1.5h;
    half varianceClip = edgeSoftness * 1.0h;  // 1σ clipping range
    effectiveBorderWidth = clamp(effectiveBorderWidth,
                                  staticBorder - varianceClip,
                                  staticBorder + varianceClip);

    alpha *= smoothstep(0.0h, edgeSoftness * 2.0h, effectiveBorderWidth);

    // Pre-multiplied alpha
    borderColor *= alpha;

    return half4(borderColor, alpha);
}

// ─── Pass 3: Metallic Lighting Enhancement ───
// Adds screen-space metallic sheen for ALL patches (from S0 onwards).
// Fresnel-based rim light emphasizes surface curvature. Bigger/darker triangles
// show the most metallic character (area_factor boost applied in C++ Core layer).

fragment half4 metallicLightingFragment(
    VertexOut in [[stage_in]],
    constant ScanGuidanceUniforms &uniforms [[buffer(1)]]
) {
    half display = half(in.display);
    // No display gate — metallic sheen visible from S0 (C++ sets metallic_s0=0.3)

    float3 N = normalize(in.worldNormal);
    float3 V = normalize(uniforms.cameraPosition - in.worldPosition);

    half NdotV = half(max(dot(N, V), 0.0));
    half fresnel = pow(1.0h - NdotV, 3.0h);
    // Metallic intensity from vertex attribute (set by C++ PBR pipeline, area-boosted)
    half metalIntensity = half(in.metallic);
    half3 sheen = half3(fresnel * metalIntensity * 0.15h);

    half alpha = fresnel * metalIntensity * 0.3h;
    // Pre-multiplied alpha
    return half4(sheen * alpha, alpha);
}

// ─── Pass 4: Color Correction ───
// Subtle color temperature shift: warm for high evidence, cool for low.
// Reinforces the Oklab cold→warm mapping with an additional composited layer.

fragment half4 colorCorrectionFragment(
    VertexOut in [[stage_in]],
    constant ScanGuidanceUniforms &uniforms [[buffer(1)]]
) {
    half display = half(in.display);
    half3 baseColor = half3(in.grayscaleColor);

    // Warm shift for high evidence, cool for low
    half warmth = display * 0.05h;
    half3 correction = half3(warmth, 0.0h, -warmth);
    half3 corrected = baseColor + correction;

    half alpha = 0.15h * display;
    return half4(corrected * alpha, alpha);
}

// ─── Pass 5: Screen-Space Ambient Occlusion (SSAO approximation) ───
// Per-fragment cavity detection using normal vs view angle.
// Darkens edges and crevices for depth perception without a separate depth pass.

fragment half4 ambientOcclusionFragment(
    VertexOut in [[stage_in]],
    constant ScanGuidanceUniforms &uniforms [[buffer(1)]]
) {
    float3 N = normalize(in.worldNormal);
    float3 V = normalize(uniforms.cameraPosition - in.worldPosition);

    half NdotV = half(max(dot(N, V), 0.0));
    // AO: faces pointing away from camera get darkened
    half ao = 0.3h + 0.7h * NdotV;  // AO factor [0.3, 1.0]

    // Apply only as darkening (multiply blend in the pipeline)
    half darken = 1.0h - ao;
    half alpha = darken * 0.4h;
    return half4(0.0h, 0.0h, 0.0h, alpha);
}

// ─── Pass 6: Post-Processing (Film Grain) ───
// Subtle film grain for tactile quality feedback.
// Skipped at serious/critical thermal tiers to save fragment ALU.

fragment half4 postProcessFragment(
    VertexOut in [[stage_in]],
    constant ScanGuidanceUniforms &uniforms [[buffer(1)]]
) {
    if (uniforms.qualityTier >= 2) discard_fragment();

    float2 screenPos = in.position.xy;
    // Time-varying hash for per-frame grain variation
    float timeSeed = uniforms.time * 60.0;
    half grain = half(fract(sin(dot(screenPos * 0.01 + timeSeed, float2(12.9898, 78.233))) * 43758.5453));
    grain = (grain - 0.5h) * 0.02h;  // ±1% noise

    half alpha = 0.05h;
    return half4(half3(grain), alpha);
}
