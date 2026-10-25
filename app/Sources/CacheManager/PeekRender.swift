import AppKit
import CoreGraphics
import Foundation
import HelioFITSCore
import ImageIO
import UniformTypeIdentifiers

/// Library files drawn the way HelioFITS Peek draws them in Finder and Quick Look: the same
/// renderer (HelioFITSCore's FITSRenderer), so the same instrument colormap, stretch and
/// header label. The app keeps no rendering of its own; the core's greyscale thumbnail is only
/// the fallback for a file this cannot read.
///
/// One file at a time: grid cells ask in bursts, and the reads are short at thumbnail size.
actor PeekRender {
    static let shared = PeekRender()

    /// Rendered thumbnails, by checksum, so a library file is drawn once per size.
    private let cache: URL = {
        let base = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        return base.appendingPathComponent("space.gilly.hfcachemanager").appendingPathComponent("peek-thumbs")
    }()

    /// A thumbnail at most `size` pixels on its long side, decorated as Finder shows it: the
    /// header's INSTRUME (or TELESCOP) and WAVELNTH, and a stacked edge for several pages.
    func thumbnail(path: String, sha256: String, size: Int) -> CGImage? {
        let file = cache.appendingPathComponent(String(sha256.prefix(2)))
            .appendingPathComponent("\(sha256)-\(size).png")
        if let source = CGImageSourceCreateWithURL(file as CFURL, nil),
           let image = CGImageSourceCreateImageAtIndex(source, 0, nil) {
            return image
        }
        guard let image = Self.draw(path: path, size: size) else { return nil }
        Self.write(image, to: file)
        return image
    }

    /// Everything the frame viewer shows about one file: every image page, its WCS, colorbar,
    /// readout and filters. Built off the main thread; use the result on the main thread only.
    func model(path: String, maxSide: Int) -> FITSPreviewModel? {
        let m = FITSPreviewModel.load(path: path, maxSide: maxSide)
        return m.isEmpty ? nil : m
    }

    static func draw(path: String, size: Int) -> CGImage? {
        guard let r = try? FITSRenderer.render(path: path, maxSide: size) else { return nil }
        let scale = Double(size) / Double(max(r.image.width, r.image.height, 1))
        let w = max(1, Int((Double(r.image.width) * scale).rounded()))
        let h = max(1, Int((Double(r.image.height) * scale).rounded()))
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
              let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: space, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
        else { return r.image }
        ctx.interpolationQuality = .medium
        ctx.draw(r.image, in: CGRect(x: 0, y: 0, width: w, height: h))
        FITSRenderer.decorateThumbnail(ctx, label: FITSRenderer.thumbnailLabel(fromHeader: r.header),
                                       pages: FITSRenderer.pageCount(path: path))
        return ctx.makeImage() ?? r.image
    }

    /// A cut-off file (a crash mid-write) fails to decode next time and is drawn and written again.
    private static func write(_ image: CGImage, to file: URL) {
        try? FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard let dest = CGImageDestinationCreateWithURL(file as CFURL, UTType.png.identifier as CFString, 1, nil)
        else { return }
        CGImageDestinationAddImage(dest, image, nil)
        CGImageDestinationFinalize(dest)
    }
}
