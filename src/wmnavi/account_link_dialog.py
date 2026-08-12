"""Dialog: open tarkov.dev / Tracker and auto-grab ID or token from clipboard."""

from __future__ import annotations

from PySide6.QtCore import QUrl, Qt, QTimer, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .profile_link import (
    fetch_profile_summary,
    fetch_tracker_progress,
    parse_profile_from_text,
    parse_tracker_token,
    tarkov_dev_players_url,
    tarkov_tracker_settings_url,
    tracker_mode_from_token,
)


class AccountLinkDialog(QDialog):
    """Per-user link: tarkov.dev account id + optional Tarkov Tracker token."""

    linked = Signal(str, str, str, str)  # account_id, mode, nickname, tracker_token

    def __init__(
        self,
        parent=None,
        *,
        account_id: str = "",
        game_mode: str = "regular",
        nickname: str = "",
        tracker_token: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("Link Account")
        self.setMinimumWidth(460)
        self._last_clip = ""
        self._account_id = account_id
        self._game_mode = game_mode or "regular"
        self._nickname = nickname
        self._token = tracker_token

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        intro = QLabel(
            "Each person links <b>their own</b> account on this PC.\n"
            "1) Open tarkov.dev, search yourself, open your profile.\n"
            "2) Copy the address bar — we grab the ID automatically.\n"
            "3) For active quests, also link a Tarkov Tracker API token."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(intro)

        layout.addWidget(QLabel("Tarkov.dev profile"))
        row1 = QHBoxLayout()
        self.btn_open_td = QPushButton("Open tarkov.dev players")
        self.btn_open_td.clicked.connect(self._open_tarkov_dev)
        row1.addWidget(self.btn_open_td)
        layout.addLayout(row1)

        self.profile_edit = QLineEdit()
        self.profile_edit.setPlaceholderText("Paste profile URL or account ID…")
        if account_id:
            self.profile_edit.setText(account_id)
        self.profile_edit.textChanged.connect(self._on_profile_typed)
        layout.addWidget(self.profile_edit)

        self.profile_status = QLabel(self._profile_status_text())
        self.profile_status.setObjectName("status")
        self.profile_status.setWordWrap(True)
        layout.addWidget(self.profile_status)

        layout.addWidget(QLabel("Active quests (Tarkov Tracker)"))
        tip = QLabel(
            "tarkov.dev no longer publishes live quest lists. "
            "Create a token at Tracker → Settings (permission: get progression), "
            "click Copy — we detect it from the clipboard."
        )
        tip.setWordWrap(True)
        tip.setObjectName("status")
        layout.addWidget(tip)

        row2 = QHBoxLayout()
        self.btn_open_tt = QPushButton("Open Tracker settings")
        self.btn_open_tt.clicked.connect(self._open_tracker)
        row2.addWidget(self.btn_open_tt)
        layout.addLayout(row2)

        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("Paste PVP_ / PVE_ / SZN_ token…")
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        if tracker_token:
            self.token_edit.setText(tracker_token)
        self.token_edit.textChanged.connect(self._on_token_typed)
        layout.addWidget(self.token_edit)

        self.token_status = QLabel(self._token_status_text())
        self.token_status.setObjectName("status")
        self.token_status.setWordWrap(True)
        layout.addWidget(self.token_status)

        self.watch_label = QLabel("Watching clipboard for profile URL / token…")
        self.watch_label.setObjectName("status")
        layout.addWidget(self.watch_label)

        buttons = QHBoxLayout()
        self.btn_unlink = QPushButton("Unlink")
        self.btn_unlink.clicked.connect(self._unlink)
        buttons.addWidget(self.btn_unlink)
        buttons.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self.btn_cancel)
        self.btn_save = QPushButton("Save & use")
        self.btn_save.clicked.connect(self._save)
        buttons.addWidget(self.btn_save)
        layout.addLayout(buttons)

        self._clip_timer = QTimer(self)
        self._clip_timer.setInterval(700)
        self._clip_timer.timeout.connect(self._poll_clipboard)
        self._clip_timer.start()

    def _profile_status_text(self) -> str:
        if not self._account_id:
            return "Not linked yet."
        nick = f" ({self._nickname})" if self._nickname else ""
        return f"Linked: {self._account_id}{nick} · mode {self._game_mode}"

    def _token_status_text(self) -> str:
        if not self._token:
            return "No Tracker token — quest list stays manual."
        mode = tracker_mode_from_token(self._token)
        return f"Token set ({mode}) · length {len(self._token)}"

    def _open_tarkov_dev(self):
        QDesktopServices.openUrl(QUrl(tarkov_dev_players_url()))
        self.watch_label.setText("Browser opened — copy your profile URL from the address bar.")

    def _open_tracker(self):
        QDesktopServices.openUrl(QUrl(tarkov_tracker_settings_url()))
        self.watch_label.setText("Browser opened — create a token, then click its Copy button.")

    def _poll_clipboard(self):
        clip = QGuiApplication.clipboard().text() or ""
        if not clip or clip == self._last_clip:
            return
        self._last_clip = clip
        token = parse_tracker_token(clip)
        if token and token != self._token:
            self.token_edit.setText(token)
            self.watch_label.setText("Grabbed Tracker token from clipboard.")
            return
        profile = parse_profile_from_text(clip)
        if profile and profile.account_id != self._account_id:
            self._apply_profile(profile.account_id, profile.game_mode)
            self.watch_label.setText("Grabbed tarkov.dev profile ID from clipboard.")

    def _on_profile_typed(self, text: str):
        parsed = parse_profile_from_text(text)
        if parsed:
            self._apply_profile(parsed.account_id, parsed.game_mode, fetch=False)
            self.profile_status.setText(self._profile_status_text())

    def _on_token_typed(self, text: str):
        token = parse_tracker_token(text) or (text.strip() if text.strip().startswith(("PVP_", "PVE_", "SZN_")) else "")
        self._token = token
        self.token_status.setText(self._token_status_text())

    def _apply_profile(self, account_id: str, game_mode: str, fetch: bool = True):
        self._account_id = account_id
        self._game_mode = game_mode
        if self.profile_edit.text().strip() != account_id:
            self.profile_edit.blockSignals(True)
            self.profile_edit.setText(account_id)
            self.profile_edit.blockSignals(False)
        if fetch:
            try:
                summary = fetch_profile_summary(account_id, game_mode)
                self._nickname = summary.nickname
            except Exception:
                pass
        self.profile_status.setText(self._profile_status_text())

    def _unlink(self):
        self._account_id = ""
        self._nickname = ""
        self._token = ""
        self.profile_edit.clear()
        self.token_edit.clear()
        self.profile_status.setText(self._profile_status_text())
        self.token_status.setText(self._token_status_text())

    def _save(self):
        parsed = parse_profile_from_text(self.profile_edit.text()) or (
            parse_profile_from_text(self._account_id) if self._account_id else None
        )
        token = parse_tracker_token(self.token_edit.text()) or self.token_edit.text().strip()
        if token and not parse_tracker_token(token):
            QMessageBox.warning(
                self,
                "Token",
                "Tracker tokens start with PVP_, PVE_, or SZN_. Use the Copy button on the site.",
            )
            return
        if not parsed and not token:
            QMessageBox.warning(self, "Link Account", "Paste a tarkov.dev profile URL and/or a Tracker token.")
            return

        account_id = parsed.account_id if parsed else self._account_id
        mode = parsed.game_mode if parsed else self._game_mode
        nickname = self._nickname

        if account_id:
            try:
                summary = fetch_profile_summary(account_id, mode)
                nickname = summary.nickname or nickname
                mode = summary.game_mode
            except Exception as exc:
                # Still allow save with ID if cache isn't warm yet.
                if not token:
                    QMessageBox.warning(self, "tarkov.dev", str(exc))
                    return

        if token:
            try:
                progress = fetch_tracker_progress(token)
                if progress.display_name and not nickname:
                    nickname = progress.display_name
                mode = progress.game_mode or mode
            except Exception as exc:
                QMessageBox.warning(self, "Tarkov Tracker", str(exc))
                return

        self._clip_timer.stop()
        self.linked.emit(account_id or "", mode or "regular", nickname or "", token or "")
        self.accept()

    def closeEvent(self, event):
        self._clip_timer.stop()
        super().closeEvent(event)
