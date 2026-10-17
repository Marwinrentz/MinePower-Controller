import SwiftUI

/// Logo aus `logo.svg`, als SwiftUI-Form nachgebaut (scharf in jeder Größe).
struct BrandMark: View {
    var size: CGFloat = 64

    var body: some View {
        Canvas { context, canvas in
            let s = canvas.width / 100
            let rect = CGRect(origin: .zero, size: canvas)
            context.fill(Path(roundedRect: rect, cornerRadius: 22.5 * s, style: .continuous),
                         with: .linearGradient(Gradient(colors: [Brand.green, Brand.cyan]),
                                               startPoint: .zero, endPoint: CGPoint(x: canvas.width, y: canvas.height)))
            var rays = Path()
            let lines: [(CGFloat, CGFloat, CGFloat, CGFloat)] = [
                (57, 33.9, 69, 33.9), (54.95, 38.85, 63.44, 47.34), (45.05, 38.85, 36.56, 47.34),
                (43, 33.9, 31, 33.9), (45.05, 28.95, 36.56, 20.46), (50, 26.9, 50, 14.9), (54.95, 28.95, 63.44, 20.46),
            ]
            for l in lines {
                rays.move(to: CGPoint(x: l.0 * s, y: l.1 * s))
                rays.addLine(to: CGPoint(x: l.2 * s, y: l.3 * s))
            }
            context.stroke(rays, with: .color(.white), style: StrokeStyle(lineWidth: 5.2 * s, lineCap: .round))
            context.fill(Path(ellipseIn: CGRect(x: (50 - 10.7) * s, y: (33.9 - 10.7) * s,
                                                width: 21.4 * s, height: 21.4 * s)), with: .color(.white))
            var bolt = Path()
            let points: [(CGFloat, CGFloat)] = [(56.3, 45), (41, 68.3), (47.8, 68.3), (43.7, 88), (59, 62.9), (52.3, 62.9)]
            bolt.addLines(points.map { CGPoint(x: $0.0 * s, y: $0.1 * s) })
            bolt.closeSubpath()
            context.fill(bolt, with: .color(.white))
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
