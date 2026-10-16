import SwiftUI

extension Label where Title == Text, Icon == Image {
    /// Beschriftung mit Web-Symbol; als `Image`, damit es auch in Menüs,
    /// Tab-Leiste und Listen (UIKit-gezeichnet) erscheint.
    @MainActor
    init(_ title: LocalizedStringKey, web icon: WebIcon) {
        self.init { Text(title) } icon: { Image(uiImage: icon.image(size: 22)) }
    }

    @MainActor
    init(verbatim title: String, web icon: WebIcon) {
        self.init { Text(verbatim: title) } icon: { Image(uiImage: icon.image(size: 22)) }
    }
}
