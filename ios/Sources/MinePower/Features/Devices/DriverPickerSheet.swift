import MinePowerKit
import SwiftUI

/// Treiberauswahl mit Suche: Hersteller, universelle Treiber, Simulation.
struct DriverPickerSheet: View {
    @Bindable var editor: DeviceEditor
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""

    private var groups: [(DriverMeta.Group, [DriverMeta])] {
        let list = (editor.drivers.value ?? []).filter { $0.matches(query) }
        return DriverMeta.Group.allCases.compactMap { group in
            let items = list.filter { $0.group == group }.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
            return items.isEmpty ? nil : (group, items)
        }
    }

    var body: some View {
        NavigationStack {
            List {
                ForEach(groups, id: \.0) { group, drivers in
                    Section {
                        ForEach(drivers) { driver in
                            Button {
                                editor.select(driver)
                                dismiss()
                            } label: {
                                DriverRow(driver: driver, selected: driver.id == editor.driver?.id)
                            }
                            .foregroundStyle(.primary)
                        }
                    } header: {
                        Text(title(group))
                    } footer: {
                        if group == .simulation {
                            Text("Simuliert ein Gerät ohne Hardware – zum Ausprobieren der Regelung.")
                        }
                    }
                }
            }
            .overlay {
                if groups.isEmpty {
                    ContentUnavailableView.search(text: query)
                }
            }
            .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always),
                        prompt: Text("Hersteller oder Modell"))
            .navigationTitle(editor.draft.category.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Abbrechen") { dismiss() } }
            }
        }
    }

    private func title(_ group: DriverMeta.Group) -> LocalizedStringKey {
        switch group {
        case .vendor: "Hersteller"
        case .generic: "Universell (HTTP, MQTT, Home Assistant, Modbus)"
        case .simulation: "Simulation"
        }
    }
}

private struct DriverRow: View {
    let driver: DriverMeta
    let selected: Bool

    var body: some View {
        HStack(spacing: Space.m) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: Space.s) {
                    Text(driver.name).font(Typo.bodyMedium)
                    switch driver.maturity {
                    case .beta: MaturityTag(text: "Beta", color: Palette.warn)
                    case .experimental: MaturityTag(text: "Experimentell", color: Palette.error)
                    default: EmptyView()
                    }
                }
                if let description = driver.description {
                    Text(description).font(Typo.footnote).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            Spacer(minLength: Space.s)
            if selected {
                Image(systemName: "checkmark").foregroundStyle(Palette.accent).font(.body.weight(.semibold))
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

private struct MaturityTag: View {
    var text: LocalizedStringKey
    var color: DomainColor

    var body: some View {
        Text(text)
            .font(Typo.caption2)
            .foregroundStyle(color.ink)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.soft, in: .capsule)
    }
}
