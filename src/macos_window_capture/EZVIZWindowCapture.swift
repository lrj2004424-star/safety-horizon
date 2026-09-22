import AppKit
import CoreMedia
import CoreVideo
import Darwin
import Foundation
import ScreenCaptureKit

private struct Arguments {
    var bundleID = "com.tencent.yybmac.app.com.videogo"
    var titleContains: String?
    var maxWidth = 1280
    var maxHeight = 960
    var fps = 12
    var listWindows = false

    init() throws {
        var index = 1
        let values = CommandLine.arguments
        while index < values.count {
            let key = values[index]
            switch key {
            case "--bundle-id":
                index += 1
                guard index < values.count else { throw CaptureError.arguments("--bundle-id 缺少值") }
                bundleID = values[index]
            case "--title-contains":
                index += 1
                guard index < values.count else { throw CaptureError.arguments("--title-contains 缺少值") }
                titleContains = values[index]
            case "--max-width":
                index += 1
                guard index < values.count, let value = Int(values[index]), value > 0 else {
                    throw CaptureError.arguments("--max-width 必须是正整数")
                }
                maxWidth = value
            case "--max-height":
                index += 1
                guard index < values.count, let value = Int(values[index]), value > 0 else {
                    throw CaptureError.arguments("--max-height 必须是正整数")
                }
                maxHeight = value
            case "--fps":
                index += 1
                guard index < values.count, let value = Int(values[index]), (1...30).contains(value) else {
                    throw CaptureError.arguments("--fps 必须在 1 到 30 之间")
                }
                fps = value
            case "--list-windows":
                listWindows = true
            default:
                throw CaptureError.arguments("未知参数：\(key)")
            }
            index += 1
        }
    }
}

private enum CaptureError: Error, CustomStringConvertible {
    case arguments(String)
    case windowNotFound(String)

    var description: String {
        switch self {
        case .arguments(let message), .windowNotFound(let message): return message
        }
    }
}

private func writeStderr(_ message: String) {
    FileHandle.standardError.write(Data((message + "\n").utf8))
}

private func appendUInt32LE(_ value: Int, to data: inout Data) {
    var encoded = UInt32(value).littleEndian
    withUnsafeBytes(of: &encoded) { data.append(contentsOf: $0) }
}

@available(macOS 13.0, *)
private final class FrameOutput: NSObject, SCStreamOutput, SCStreamDelegate {
    let width: Int
    let height: Int
    private let output = FileHandle.standardOutput
    private let queue = DispatchQueue(label: "com.rj.ezviz-safety-view.frames")
    private var writingEnabled = false

    init(width: Int, height: Int) {
        self.width = width
        self.height = height
    }

    func install(on stream: SCStream) throws {
        try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: queue)
    }

    func beginWriting() {
        queue.sync { writingEnabled = true }
    }

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of outputType: SCStreamOutputType
    ) {
        guard writingEnabled,
              outputType == .screen,
              sampleBuffer.isValid,
              let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer)
        else { return }

        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }
        guard let source = CVPixelBufferGetBaseAddress(pixelBuffer) else { return }

        let sourceWidth = CVPixelBufferGetWidth(pixelBuffer)
        let sourceHeight = CVPixelBufferGetHeight(pixelBuffer)
        let sourceStride = CVPixelBufferGetBytesPerRow(pixelBuffer)
        let copyWidth = min(width, sourceWidth)
        let copyHeight = min(height, sourceHeight)
        let rowBytes = copyWidth * 4
        var frame = Data(count: width * height * 4)
        frame.withUnsafeMutableBytes { destinationRaw in
            guard let destination = destinationRaw.baseAddress else { return }
            for row in 0..<copyHeight {
                memcpy(
                    destination.advanced(by: row * width * 4),
                    source.advanced(by: row * sourceStride),
                    rowBytes
                )
            }
        }
        output.write(frame)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        writeStderr("萤石窗口捕捉已停止：\(error.localizedDescription)")
        Darwin.exit(5)
    }
}

@available(macOS 13.0, *)
private func matchingWindows(_ arguments: Arguments) async throws -> [SCWindow] {
    // Keep the named EZVIZ player available while TouchDesigner is the front
    // window.  The desktop-independent filter captures the selected player
    // itself, not the screen behind/above it.
    let content = try await SCShareableContent.excludingDesktopWindows(
        false,
        onScreenWindowsOnly: false
    )
    return content.windows.filter { window in
        let windowBundleID = window.owningApplication?.bundleIdentifier ?? ""
        let applicationName = window.owningApplication?.applicationName ?? ""
        let windowTitle = window.title ?? ""
        let targetMatches = arguments.bundleID == "*"
            || windowBundleID == arguments.bundleID
            || windowBundleID.localizedCaseInsensitiveContains("videogo")
            || applicationName.localizedCaseInsensitiveContains("萤石")
            || applicationName.localizedCaseInsensitiveContains("ezviz")
            || windowTitle.localizedCaseInsensitiveContains("萤石云视频")
        guard targetMatches,
              window.frame.width >= 160,
              window.frame.height >= 120
        else { return false }
        guard let requiredTitle = arguments.titleContains, !requiredTitle.isEmpty else { return true }
        return windowTitle.localizedCaseInsensitiveContains(requiredTitle)
    }.sorted { first, second in
        first.frame.width * first.frame.height > second.frame.width * second.frame.height
    }
}

@available(macOS 13.0, *)
private func listWindows(_ arguments: Arguments) async throws {
    let windows = try await matchingWindows(arguments)
    let payload: [[String: Any]] = windows.map { window in
        [
            "window_id": Int(window.windowID),
            "title": window.title ?? "",
            "app_name": window.owningApplication?.applicationName ?? "",
            "bundle_id": window.owningApplication?.bundleIdentifier ?? "",
            "width": Int(window.frame.width.rounded()),
            "height": Int(window.frame.height.rounded()),
        ]
    }
    let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

@available(macOS 13.0, *)
private func runCapture(_ arguments: Arguments) async throws {
    guard let window = try await matchingWindows(arguments).first else {
        throw CaptureError.windowNotFound(
            "没有找到正在显示的“萤石云视频”窗口。请先打开萤石云视频并进入监控播放页面。"
        )
    }
    let sourceWidth = max(1, Int(window.frame.width.rounded()))
    let sourceHeight = max(1, Int(window.frame.height.rounded()))
    let scale = min(
        1.0,
        Double(arguments.maxWidth) / Double(sourceWidth),
        Double(arguments.maxHeight) / Double(sourceHeight)
    )
    let width = max(2, Int((Double(sourceWidth) * scale).rounded()) / 2 * 2)
    let height = max(2, Int((Double(sourceHeight) * scale).rounded()) / 2 * 2)

    let filter = SCContentFilter(desktopIndependentWindow: window)
    let configuration = SCStreamConfiguration()
    configuration.width = width
    configuration.height = height
    configuration.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(arguments.fps))
    configuration.queueDepth = 2
    configuration.pixelFormat = kCVPixelFormatType_32BGRA
    configuration.showsCursor = false
    configuration.capturesAudio = false

    let frameOutput = FrameOutput(width: width, height: height)
    let stream = SCStream(filter: filter, configuration: configuration, delegate: frameOutput)
    try frameOutput.install(on: stream)

    try await stream.startCapture()
    var header = Data("EZV1".utf8)
    appendUInt32LE(width, to: &header)
    appendUInt32LE(height, to: &header)
    appendUInt32LE(arguments.fps, to: &header)
    FileHandle.standardOutput.write(header)
    frameOutput.beginWriting()
    writeStderr("正在捕捉萤石云视频窗口：\(width)x\(height) @ \(arguments.fps)fps")
    while true {
        try await Task.sleep(nanoseconds: 60_000_000_000)
    }
}

@main
private struct EZVIZWindowCapture {
    static func main() async {
        do {
            _ = NSApplication.shared
            let arguments = try Arguments()
            guard #available(macOS 13.0, *) else {
                writeStderr("窗口捕捉需要 macOS 13 或更高版本。")
                Darwin.exit(2)
            }
            if arguments.listWindows {
                try await listWindows(arguments)
            } else {
                try await runCapture(arguments)
            }
        } catch {
            writeStderr("窗口捕捉启动失败：\(error)")
            Darwin.exit(3)
        }
    }
}
