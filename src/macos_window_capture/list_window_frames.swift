import CoreGraphics
import Foundation

let windows = CGWindowListCopyWindowInfo(
    [.optionAll, .excludeDesktopElements],
    kCGNullWindowID
) as? [[String: Any]] ?? []

let results: [[String: Any]] = windows.compactMap { window in
    let owner = window[kCGWindowOwnerName as String] as? String ?? ""
    let title = window[kCGWindowName as String] as? String ?? ""
    let ownerLower = owner.lowercased()
    let titleLower = title.lowercased()
    // Match the player window precisely.  Do not accept TouchDesigner output
    // windows that merely mention 萤石安全视界 in their title.
    guard owner.contains("萤石云视频") || ownerLower.contains("ezviz") || ownerLower.contains("videogo") || title.contains("萤石云视频") || titleLower.contains("ezviz") else {
        return nil
    }
    guard let number = window[kCGWindowNumber as String] as? Int,
          let bounds = window[kCGWindowBounds as String] as? [String: Any]
    else { return nil }
    return [
        "window_id": number,
        "owner": owner,
        "title": title,
        "x": bounds["X"] ?? 0,
        "y": bounds["Y"] ?? 0,
        "width": bounds["Width"] ?? 0,
        "height": bounds["Height"] ?? 0,
    ]
}

let data = try JSONSerialization.data(withJSONObject: results, options: [.prettyPrinted])
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write(Data("\n".utf8))
