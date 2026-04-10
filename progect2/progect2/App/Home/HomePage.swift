//
// HomePage.swift
// Aether3D
//
// PR#7 Scan Guidance UI — Home Page
// Apple-platform only (SwiftUI)
//

import Foundation

#if canImport(SwiftUI)
import SwiftUI

#if canImport(UIKit)
import UIKit
#endif

/// Main screen: gallery of completed scans + "开始拍摄" button
///
/// Layout:
///   - ScrollView with LazyVGrid (2 columns, 16pt spacing)
///   - Empty state: centered "尚无扫描作品" + SF Symbol
///   - Bottom: full-width "开始拍摄" button (white bg, black text)
///
/// Navigation:
///   - Tap "开始拍摄" → ScanView (via NavigationStack destination)
///   - Swipe-to-delete on gallery cells
struct HomePage: View {
    @StateObject private var viewModel = HomeViewModel()
    @State private var navigateToScan = false
    @State private var navigateToObjectModeV2 = false
    @State private var selectedRecord: ScanRecord?

    private let columns = [
        GridItem(.flexible(), spacing: 16),
        GridItem(.flexible(), spacing: 16)
    ]

    var body: some View {
        ZStack {
            // Background
            Color.black.ignoresSafeArea()

            VStack(spacing: 0) {
                if viewModel.scanRecords.isEmpty && !viewModel.isLoading {
                    // Empty state
                    emptyStateView
                } else {
                    // Gallery
                    galleryView
                }

                Spacer()

                bottomActionSection
            }
        }
        .navigationTitle("Aether3D")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .onAppear {
            viewModel.loadRecords()
        }
        .sheet(item: $selectedRecord) { record in
            NavigationStack {
                RecordViewerDestinationView(record: record)
            }
            .preferredColorScheme(.dark)
        }
        #if canImport(ARKit)
        .navigationDestination(isPresented: $navigateToScan) {
            ScanView()
        }
        #endif
        .navigationDestination(isPresented: $navigateToObjectModeV2) {
            ObjectModeV2CaptureView()
        }
    }

    // MARK: - Empty State

    private var emptyStateView: some View {
        VStack(spacing: 16) {
            Spacer()

            Image(systemName: "viewfinder.circle")
                .font(.system(size: 64))
                .foregroundColor(.gray)

            Text("尚无扫描作品")
                .font(.system(size: 17))
                .foregroundColor(.gray)

            Spacer()
        }
    }

    // MARK: - Gallery Grid

    private var galleryView: some View {
        ScrollView {
            LazyVGrid(columns: columns, spacing: 20) {
                ForEach(viewModel.scanRecords) { record in
                    Button {
                        selectedRecord = record
                    } label: {
                        ScanRecordCell(
                            record: record,
                            relativeTime: viewModel.relativeTimeString(for: record.createdAt)
                        )
                    }
                    .buttonStyle(.plain)
                    .contextMenu {
                        Button(role: .destructive) {
                            viewModel.deleteRecord(record)
                        } label: {
                            Label("删除", systemImage: "trash")
                        }
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
        }
    }

    // MARK: - Bottom Actions

    private var bottomActionSection: some View {
        VStack(spacing: 14) {
            pipelineQuickAccessRow
            objectModeV2EntryCard
            legacyActionButtons
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 16)
    }

    private var pipelineQuickAccessRow: some View {
        HStack(spacing: 10) {
            quickAccessChip(
                title: "远端",
                subtitle: "旧版",
                systemImage: "cloud.fill",
                foreground: .white,
                background: Color.white.opacity(0.08),
                strokeOpacity: 0.08
            ) {
                navigateToScan = true
            }

            quickAccessChip(
                title: "新远端",
                subtitle: "V2",
                systemImage: "sparkles",
                foreground: .black,
                background: Color(red: 0.82, green: 0.94, blue: 0.55),
                strokeOpacity: 0.18
            ) {
                navigateToObjectModeV2 = true
            }

            quickAccessChip(
                title: "本地",
                subtitle: "旧线",
                systemImage: "iphone.gen3",
                foreground: .white,
                background: Color.white.opacity(0.08),
                strokeOpacity: 0.08
            ) {
                navigateToScan = true
            }
        }
    }

    private var objectModeV2EntryCard: some View {
        Button(action: {
            #if canImport(UIKit)
            let generator = UIImpactFeedbackGenerator(style: .medium)
            generator.impactOccurred()
            #endif
            navigateToObjectModeV2 = true
        }) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 8) {
                    Text("远端新方案")
                        .font(.system(size: 18, weight: .bold))
                        .foregroundColor(.black)

                    Text("BETA")
                        .font(.system(size: 11, weight: .heavy))
                        .foregroundColor(.black.opacity(0.78))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color.white.opacity(0.58))
                        .clipShape(Capsule())

                    Spacer()

                    Image(systemName: "arrow.up.right")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundColor(.black.opacity(0.72))
                }

                Text("对象模式 V2")
                    .font(.system(size: 28, weight: .heavy))
                    .foregroundColor(.black)

                Text("Guided Capture + Preview / Default / HQ 三阶段远端管线")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundColor(.black.opacity(0.72))
                    .lineLimit(2)

                HStack(spacing: 8) {
                    entryFeaturePill("新版拍摄页")
                    entryFeaturePill("三阶段结果")
                    entryFeaturePill("作品级 Viewer")
                }
            }
            .padding(18)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                LinearGradient(
                    colors: [
                        Color(red: 0.82, green: 0.94, blue: 0.55),
                        Color(red: 0.68, green: 0.84, blue: 0.42)
                    ],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
            .overlay(
                RoundedRectangle(cornerRadius: 28)
                    .stroke(Color.white.opacity(0.18), lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 28, style: .continuous))
        }
        .buttonStyle(ScaleButtonStyle())
    }

    private func entryFeaturePill(_ title: String) -> some View {
        Text(title)
            .font(.system(size: 11, weight: .bold))
            .foregroundColor(.black.opacity(0.72))
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(Color.white.opacity(0.52))
            .clipShape(Capsule())
    }

    private func quickAccessChip(
        title: String,
        subtitle: String,
        systemImage: String,
        foreground: Color,
        background: Color,
        strokeOpacity: Double,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: {
            #if canImport(UIKit)
            let generator = UIImpactFeedbackGenerator(style: .light)
            generator.impactOccurred()
            #endif
            action()
        }) {
            VStack(spacing: 5) {
                Image(systemName: systemImage)
                    .font(.system(size: 16, weight: .bold))

                Text(title)
                    .font(.system(size: 13, weight: .bold))

                Text(subtitle)
                    .font(.system(size: 10, weight: .medium))
                    .opacity(0.72)
            }
            .foregroundColor(foreground)
            .frame(maxWidth: .infinity)
            .frame(height: 70)
            .background(background)
            .overlay(
                RoundedRectangle(cornerRadius: 22)
                    .stroke(Color.white.opacity(strokeOpacity), lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
        }
        .buttonStyle(ScaleButtonStyle())
    }

    private var legacyActionButtons: some View {
        VStack(spacing: 10) {
            HStack(spacing: 10) {
                modeSelectionButton(
                    title: "远端",
                    subtitle: "高质量",
                    systemImage: "cloud.fill",
                    foreground: .black,
                    background: .white,
                    strokeOpacity: 0.12
                ) {
                    navigateToScan = true
                }

                modeSelectionButton(
                    title: "新远端",
                    subtitle: "对象模式 V2",
                    systemImage: "sparkles",
                    foreground: .black,
                    background: Color(red: 0.82, green: 0.94, blue: 0.55),
                    strokeOpacity: 0.14
                ) {
                    navigateToObjectModeV2 = true
                }

                modeSelectionButton(
                    title: "本地",
                    subtitle: "旧线",
                    systemImage: "iphone.gen3",
                    foreground: .white,
                    background: Color.white.opacity(0.12),
                    strokeOpacity: 0.08
                ) {
                    navigateToScan = true
                }
            }

            Text("旧版远端链保留给用户测试；新远端走对象模式 V2。")
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(.white.opacity(0.48))
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func modeSelectionButton(
        title: String,
        subtitle: String,
        systemImage: String,
        foreground: Color,
        background: Color,
        strokeOpacity: Double,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: {
            #if canImport(UIKit)
            let generator = UIImpactFeedbackGenerator(style: .light)
            generator.impactOccurred()
            #endif
            action()
        }) {
            VStack(spacing: 6) {
                Image(systemName: systemImage)
                    .font(.system(size: 16, weight: .bold))

                Text(title)
                    .font(.system(size: 15, weight: .bold))

                Text(subtitle)
                    .font(.system(size: 11, weight: .medium))
                    .opacity(0.72)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .minimumScaleFactor(0.8)
            }
            .foregroundColor(foreground)
            .frame(maxWidth: .infinity)
            .frame(height: 84)
            .background(background)
            .overlay(
                RoundedRectangle(cornerRadius: 24)
                    .stroke(Color.white.opacity(strokeOpacity), lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        }
        .buttonStyle(ScaleButtonStyle())
    }
}

/// Scale animation button style (press effect)
private struct ScaleButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.95 : 1.0)
            .animation(.easeInOut(duration: 0.15), value: configuration.isPressed)
    }
}

#endif
