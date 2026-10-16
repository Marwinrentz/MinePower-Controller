import MinePowerKit
import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

/// SVG-Pfad als SwiftUI-Form, skaliert aus seinem Zeichenraster (`viewBox`).
struct SVGShape: Shape {
    let path: SVGPath
    /// Zeichenraster der Vorlage (Icons 24×24, Garage 400×236 …).
    var viewBox = CGRect(x: 0, y: 0, width: 24, height: 24)
    /// `true`: in den verfügbaren Platz einpassen (Seitenverhältnis bleibt).
    var fit = true

    init(_ data: String, viewBox: CGRect = CGRect(x: 0, y: 0, width: 24, height: 24), fit: Bool = true) {
        path = SVGPathCache.shared.path(data)
        self.viewBox = viewBox
        self.fit = fit
    }

    func path(in rect: CGRect) -> Path {
        let sx = rect.width / viewBox.width, sy = rect.height / viewBox.height
        let s = fit ? min(sx, sy) : 1
        let scaleX = fit ? s : sx, scaleY = fit ? s : sy
        let dx = rect.minX + (rect.width - viewBox.width * scaleX) / 2 - viewBox.minX * scaleX
        let dy = rect.minY + (rect.height - viewBox.height * scaleY) / 2 - viewBox.minY * scaleY
        func p(_ point: SVGPath.Point) -> CGPoint { CGPoint(x: dx + point.x * scaleX, y: dy + point.y * scaleY) }
        var result = Path()
        for command in path.commands {
            switch command {
            case .move(let a): result.move(to: p(a))
            case .line(let a): result.addLine(to: p(a))
            case .cubic(let c1, let c2, let end): result.addCurve(to: p(end), control1: p(c1), control2: p(c2))
            case .quad(let c, let end): result.addQuadCurve(to: p(end), control: p(c))
            case .close: result.closeSubpath()
            }
        }
        return result
    }
}

/// Zerlegte Pfade einmal behalten (Icons werden in Listen oft gezeichnet).
final class SVGPathCache: Sendable {
    static let shared = SVGPathCache()
    private let store = Locked<[String: SVGPath]>([:])

    func path(_ data: String) -> SVGPath {
        if let cached = store.withLock({ $0[data] }) { return cached }
        let parsed = SVGPath(data)
        store.withLock { $0[data] = parsed }
        return parsed
    }
}

/// Web-Symbol in Textfarbe, mit Dynamic Type mitwachsend.
struct Icon: View {
    let icon: WebIcon
    @ScaledMetric private var size: CGFloat
    var strokeWidth: CGFloat = 1.7

    init(_ icon: WebIcon, size: CGFloat = 20, strokeWidth: CGFloat = 1.7) {
        self.icon = icon
        _size = ScaledMetric(wrappedValue: size, relativeTo: .body)
        self.strokeWidth = strokeWidth
    }

    var body: some View {
        let shape = SVGShape(icon.pathData)
        Group {
            if icon.isFilled {
                shape.fill(.foreground)
            } else {
                shape.stroke(.foreground, style: StrokeStyle(lineWidth: (icon.isBold ? 3 : strokeWidth) * size / 24,
                                                             lineCap: .round, lineJoin: .round))
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

#if canImport(UIKit)
extension WebIcon {
    /// Als Vorlagenbild für Stellen, die ein `Image` verlangen (Tab-Leiste, Widgets, Menüs).
    @MainActor
    func image(size: CGFloat = 24, strokeWidth: CGFloat = 1.7) -> UIImage {
        let key = "\(rawValue)-\(size)-\(strokeWidth)" as NSString
        if let cached = WebIconImageCache.shared.object(forKey: key) { return cached }
        let rect = CGRect(x: 0, y: 0, width: size, height: size)
        let path = SVGShape(pathData).path(in: rect)
        let renderer = UIGraphicsImageRenderer(size: rect.size)
        let image = renderer.image { context in
            let cg = context.cgContext
            cg.addPath(path.cgPath)
            UIColor.black.set()
            if isFilled {
                cg.fillPath()
            } else {
                cg.setLineWidth((isBold ? 3 : strokeWidth) * size / 24)
                cg.setLineCap(.round)
                cg.setLineJoin(.round)
                cg.strokePath()
            }
        }.withRenderingMode(.alwaysTemplate)
        WebIconImageCache.shared.setObject(image, forKey: key)
        return image
    }
}

@MainActor
private enum WebIconImageCache {
    static let shared = NSCache<NSString, UIImage>()
}
#endif

extension DeviceCategory {
    /// Symbol je Kategorie wie `CATEGORY_ICON` im Web.
    var webIcon: WebIcon {
        switch self {
        case .inverter: .sun
        case .meter: .grid
        case .wallbox: .car
        case .waterHeater: .water
        case .battery: .battery
        case .unknown: .devices
        }
    }
}
