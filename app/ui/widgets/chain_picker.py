# app/ui/widgets/chain_picker.py
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QSizePolicy,
)

from app.integrations.lifi_client import DEBANK_TO_CHAIN_ID
from app.i18n import tr, i18n

# DeBank key → human readable display name
_DISPLAY: dict[str, str] = {
    "eth":     "Ethereum",
    "bsc":     "BNB Chain",
    "matic":   "Polygon",
    "avax":    "Avalanche",
    "op":      "Optimism",
    "arb":     "Arbitrum",
    "base":    "Base",
    "era":     "zkSync Era",
    "linea":   "Linea",
    "scrl":    "Scroll",
    "blast":   "Blast",
    "mnt":     "Mantle",
    "mode":    "Mode",
    "frax":    "Fraxtal",
    "opbnb":   "opBNB",
    "taiko":   "Taiko",
    "abs":     "Abstract",
    "ink":     "Ink",
    "soneium": "Soneium",
    "uni":     "Unichain",
    "world":   "World Chain",
    "morph":   "Morph",
    "swell":   "Swellchain",
    "bob":     "BOB",
    "sophon":  "Sophon",
    "xdai":    "Gnosis",
    "celo":    "Celo",
    "cro":     "Cronos",
    "boba":    "Boba",
    "metis":   "Metis",
    "mobm":    "Moonbeam",
    "fuse":    "Fuse",
    "klay":    "Klaytn",
    "rsk":     "RSK",
    "tlos":    "Telos",
    "flr":     "Flare",
    "ron":     "Ronin",
    "sei":     "Sei",
    "gravity": "Gravity",
    "lisk":    "Lisk",
    "ape":     "ApeChain",
    "ethlink": "Etherlink",
    "sonic":   "Sonic",
    "corn":    "Corn",
    "bera":    "Berachain",
    "lens":    "Lens",
    "hyper":   "HyperEVM",
    "hemi":    "Hemi",
    "plume":   "Plume",
    "katana":  "Katana",
    "plasma":  "Plasma",
    "monad":   "Monad",
    "stable":  "Stable",
    "megaeth": "MegaETH",
    "itze":    "Ithaca",
    "ftm":     "Fantom",
    "movr":    "Moonriver",
    "iotx":    "IoTeX",
    "dfk":     "DFK Chain",
    "nova":    "Arbitrum Nova",
    "doge":    "Dogechain",
    "kava":    "Kava",
    "cfx":     "Conflux",
    "core":    "Core",
    "wemix":   "WEMIX",
    "oas":     "Oasys",
    "zora":    "Zora",
    "manta":   "Manta",
    "zeta":    "ZetaChain",
    "merlin":  "Merlin",
    "xlayer":  "X Layer",
    "btr":     "Bitlayer",
    "b2":      "B2 Network",
    "croze":   "Cronos zkEVM",
    "zircuit": "Zircuit",
    "hsk":     "HashKey",
    "story":   "Story",
    "cyber":   "Cyber",
    "chiliz":  "Chiliz",
    "orderly": "Orderly",
    "rari":    "RARI Chain",
    "reya":    "Reya",
    "bb":      "BounceBit",
    "goat":    "GOAT",
    "tac":     "TAC",
    "botanix": "Botanix",
}


def _display_name(key: str) -> str:
    return _DISPLAY.get(key, key.capitalize())


class _ChainPickerPopup(QFrame):
    """Floating popup with search field and checkbox list."""

    def __init__(self, picker: "ChainPickerWidget") -> None:
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint)
        self._picker = picker
        self.setObjectName("chainPickerPopup")
        self.setFixedWidth(260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("chain_search_placeholder"))
        self._search.textChanged.connect(self._filter)
        layout.addWidget(self._search)

        self._list = QListWidget()
        self._list.setFixedHeight(240)
        self._list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._list)

        self._populate()

        i18n.language_changed.connect(self.retranslate_ui)

    def retranslate_ui(self) -> None:
        self._search.setPlaceholderText(tr("chain_search_placeholder"))

    def _populate(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for key, name in self._picker._all_chains:
            item = QListWidgetItem(f"{name}  ({key})")
            item.setData(Qt.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked if key in self._picker._selected else Qt.Unchecked
            )
            self._list.addItem(item)
        self._list.blockSignals(False)

    def refresh_checks(self) -> None:
        self._list.blockSignals(True)
        for i in range(self._list.count()):
            item = self._list.item(i)
            key = item.data(Qt.UserRole)
            item.setCheckState(
                Qt.Checked if key in self._picker._selected else Qt.Unchecked
            )
        self._list.blockSignals(False)

    def _filter(self, text: str) -> None:
        query = text.strip().lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            key = item.data(Qt.UserRole)
            name = _display_name(key).lower()
            item.setHidden(bool(query) and query not in key and query not in name)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            self._picker._selected.add(key)
        else:
            self._picker._selected.discard(key)
        self._picker._update_summary()

    def show_below(self, widget: QWidget) -> None:
        pos = widget.mapToGlobal(QPoint(0, widget.height() + 2))
        self.move(pos)
        self._search.clear()
        self._filter("")
        self.show()
        self._search.setFocus()


class ChainPickerWidget(QWidget):
    """Multi-select chain picker that looks like an input field with a dropdown."""

    selectionChanged = Signal(list)  # list[str] of DeBank keys

    def __init__(self, placeholder: str = None, parent=None) -> None:
        super().__init__(parent)
        self._selected: set[str] = set()
        self._placeholder = placeholder or tr("pick_chains_placeholder")
        self._all_chains: list[tuple[str, str]] = [
            (key, _display_name(key)) for key in DEBANK_TO_CHAIN_ID
        ]

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._btn = QPushButton(self._placeholder)
        self._btn.setObjectName("chainPickerButton")
        self._btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._btn.clicked.connect(self._toggle_popup)
        layout.addWidget(self._btn)

        self._popup = _ChainPickerPopup(self)

        i18n.language_changed.connect(self.retranslate_ui)

    def retranslate_ui(self) -> None:
        self._placeholder = tr("pick_chains_placeholder")
        if not self._selected:
            self._update_summary()

    def _toggle_popup(self) -> None:
        if self._popup.isVisible():
            self._popup.hide()
        else:
            self._popup.refresh_checks()
            self._popup.show_below(self._btn)

    def _update_summary(self) -> None:
        if not self._selected:
            self._btn.setText(self._placeholder)
        else:
            keys = sorted(self._selected)
            # Show as many keys as fit (~40 chars), then "+N more"
            shown: list[str] = []
            total = 0
            for k in keys:
                if total + len(k) + 2 > 38:
                    break
                shown.append(k)
                total += len(k) + 2
            remainder = len(keys) - len(shown)
            text = ", ".join(shown)
            if remainder:
                text += f"  +{remainder}"
            self._btn.setText(text)
        self.selectionChanged.emit(list(self._selected))

    def get_selected(self) -> list[str]:
        return sorted(self._selected)

    def set_selected(self, keys: list[str]) -> None:
        self._selected = set(keys)
        self._update_summary()
        self._popup.refresh_checks()
