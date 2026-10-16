import Foundation

/// Beschriftungen an einer Skala so verschieben, dass sie sich nicht überlappen
/// (mindestens `gap` Abstand), ohne die Reihenfolge zu ändern – wie `spreadLabels`
/// im Web. Die Markierung bleibt am echten Wert, nur der Text rückt.
public func spreadLabels(_ positions: [Double], gap: Double, limit: Double = .infinity) -> [Double] {
    let order = positions.enumerated().sorted { $0.element < $1.element }
    var out = positions
    var last = -Double.infinity
    for (index, value) in order {
        let placed = min(max(value, last + gap), limit)
        out[index] = placed
        last = placed
    }
    return out
}
