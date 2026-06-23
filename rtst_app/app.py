from __future__ import annotations

import os
import time
from html import escape
from dataclasses import replace

from PIL import Image
from PySide6.QtCore import QObject, QPoint, QRect, QRunnable, QSize, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QMouseEvent, QPainter, QPen, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizeGrip,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from rtst_app.capture import ScreenCapturer
from rtst_app.browser_dom import BrowserDomError, BrowserDomSubtitleReader
from rtst_app.codex_oauth import (
    codex_base_url_from_env,
    codex_model_from_env,
    codex_oauth_config_from_env,
)
from rtst_app.config import AppSettings, CaptureRegion, load_settings, save_settings
from rtst_app.frame_change import frame_difference, frame_signature
from rtst_app.logging_utils import clip_text, get_logger
from rtst_app.oauth_client import OAuthConfig, OAuthError, OAuthPkceClient
from rtst_app.ocr import BaseOcr, OcrError, build_ocr_engine
from rtst_app.text_utils import TranslationCache, is_substantial_change
from rtst_app.translator import BaseTranslator, TranslationError, build_translator


log = get_logger("app")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return min(max(value, minimum), maximum)


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return min(max(value, minimum), maximum)


AUTO_SCAN_INTERVAL_MS = _env_int("RTST_SCAN_INTERVAL_MS", 120, 60, 1000)
SUBTITLE_STABLE_MS = _env_int("RTST_SUBTITLE_STABLE_MS", 220, 80, 1500)
SUBTITLE_MAX_WAIT_MS = _env_int("RTST_SUBTITLE_MAX_WAIT_MS", 850, 250, 4000)
VISUAL_CHANGE_THRESHOLD = _env_float("RTST_VISUAL_CHANGE_THRESHOLD", 1.5, 0.1, 40.0)
DOM_POLL_INTERVAL_MS = _env_int("RTST_DOM_POLL_INTERVAL_MS", 250, 100, 2000)


def _env_enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _save_last_capture(image: Image.Image) -> None:
    if not _env_enabled("RTST_SAVE_LAST_CAPTURE", True):
        return

    path = os.getenv("RTST_LAST_CAPTURE_PATH", "rtst_last_capture.png").strip()
    if not path:
        return

    try:
        image.save(path)
    except OSError as exc:
        log.warning("last_capture_save_failed path=%r error=%r", path, str(exc))


class RegionSelector(QWidget):
    region_selected = Signal(CaptureRegion)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._start: QPoint | None = None
        self._end: QPoint | None = None

        geometry = QRect()
        for screen in QGuiApplication.screens():
            geometry = geometry.united(screen.geometry())
        self.setGeometry(geometry)

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 90))

        if self._start is None or self._end is None:
            return

        rect = QRect(self._start, self._end).normalized()
        painter.fillRect(rect, QColor(70, 150, 255, 45))
        painter.setPen(QPen(QColor(90, 180, 255), 2))
        painter.drawRect(rect)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position().toPoint()
            self._end = self._start
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._start is not None:
            self._end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._start is None:
            return

        self._end = event.position().toPoint()
        selected = QRect(self._start, self._end).normalized()
        offset = self.geometry().topLeft()
        region = CaptureRegion(
            left=selected.left() + offset.x(),
            top=selected.top() + offset.y(),
            width=selected.width(),
            height=selected.height(),
        )
        self.hide()
        if region.is_valid():
            self.region_selected.emit(region)
        self.deleteLater()

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.deleteLater()


class OverlayWindow(QWidget):
    position_changed = Signal(int, int)
    size_changed = Signal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._drag_start_global: QPoint | None = None
        self._drag_start_top_left: QPoint | None = None
        self._last_reported_size = QSize()
        self._applying_geometry = False

        self.header = QLabel("RTST History")
        self.header.setFixedHeight(24)
        self.header.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.header.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.text_view = QTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setFrameShape(QFrame.Shape.NoFrame)
        self.text_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.text_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 4, 4)
        grip_row.addStretch(1)
        self.size_grip = QSizeGrip(self)
        grip_row.addWidget(self.size_grip)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.text_view)
        layout.addLayout(grip_row)
        self.hide()

    def update_style(self, font_size: int, opacity: float) -> None:
        alpha = int(230 * opacity)
        self.header.setStyleSheet(
            f"""
            QLabel {{
                color: white;
                background-color: rgba(8, 12, 18, {alpha});
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 2px 10px;
                font-size: {max(11, font_size - 12)}px;
                font-weight: 600;
            }}
            """
        )
        self.text_view.setStyleSheet(
            f"""
            QTextEdit {{
                color: white;
                background-color: rgba(8, 12, 18, {alpha});
                border: 1px solid rgba(255, 255, 255, {int(50 * opacity)});
                border-top: 0;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
                padding: 8px 10px;
                font-size: {font_size}px;
            }}
            QScrollBar:vertical {{
                background: rgba(255, 255, 255, 20);
                width: 10px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 90);
                border-radius: 4px;
            }}
            """
        )

    def _resolve_size(
        self,
        settings: AppSettings,
        available: QRect,
        minimum_width: int = 360,
    ) -> tuple[int, int]:
        max_width = max(minimum_width, available.width() - 24)
        width = min(max(settings.overlay_width, minimum_width), max_width)
        max_height = max(80, available.height() - 24)
        height = min(max(settings.overlay_max_height, 80), max_height)
        self.setMinimumSize(320, 120)
        self.setMaximumSize(min(2400, available.width() - 24), min(1200, available.height() - 24))
        self.resize(width, height)
        return width, height

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_start_global = event.globalPosition().toPoint()
        self._drag_start_top_left = self.frameGeometry().topLeft()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_start_global is None or self._drag_start_top_left is None:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            return
        delta = event.globalPosition().toPoint() - self._drag_start_global
        self.move(self._drag_start_top_left + delta)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_start_global = None
        self._drag_start_top_left = None
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.position_changed.emit(self.x(), self.y())
        event.accept()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QPen(QColor(255, 255, 255, 150), 2))
        right = self.width() - 8
        bottom = self.height() - 8
        painter.drawLine(right - 18, bottom, right, bottom - 18)
        painter.drawLine(right - 10, bottom, right, bottom - 10)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if self._applying_geometry or not self.isVisible():
            return
        if self.size() == self._last_reported_size:
            return
        self._last_reported_size = self.size()
        self.size_changed.emit(self.width(), self.height())

    def show_text(self, text: str, region: CaptureRegion, settings: AppSettings) -> None:
        if not text:
            self.hide()
            return

        self.update_style(settings.overlay_font_size, settings.overlay_opacity)
        self.text_view.setHtml(text)
        self.text_view.moveCursor(QTextCursor.MoveOperation.End)

        screen = QGuiApplication.screenAt(QPoint(region.left, region.top))
        available = screen.availableGeometry() if screen else QGuiApplication.primaryScreen().availableGeometry()
        width, height = self._resolve_size(settings, available, minimum_width=360)
        x = min(max(region.left, available.left() + 12), available.right() - width - 12)
        y_above = region.top - height - 14
        y_below = region.bottom + 14
        y = y_above if y_above >= available.top() + 12 else y_below
        y = min(max(y, available.top() + 12), available.bottom() - height - 12)

        self._applying_geometry = True
        self.setGeometry(x, y, width, height)
        self._last_reported_size = QSize(width, height)
        self._applying_geometry = False
        self.show()

    def show_text_positioned(
        self,
        text: str,
        settings: AppSettings,
        position: str,
        offset_x: int = 0,
        offset_y: int = 0,
        custom_region: CaptureRegion | None = None,
        manual_x: int = -1,
        manual_y: int = -1,
    ) -> None:
        if not text:
            self.hide()
            return

        self.update_style(settings.overlay_font_size, settings.overlay_opacity)
        self.text_view.setHtml(text)
        self.text_view.moveCursor(QTextCursor.MoveOperation.End)

        screen = QGuiApplication.primaryScreen()
        if custom_region is not None:
            screen = QGuiApplication.screenAt(QPoint(custom_region.left, custom_region.top)) or screen
        if screen is None:
            return

        available = screen.availableGeometry()
        width, height = self._resolve_size(settings, available, minimum_width=360)

        if position == "manual" and manual_x >= 0 and manual_y >= 0:
            x = manual_x
            y = manual_y
        elif custom_region is not None and position == "custom_region":
            x = custom_region.left
            y = custom_region.top
        else:
            x = available.left() + (available.width() - width) // 2
            if position == "top":
                y = available.top() + 36
            elif position == "center":
                y = available.top() + (available.height() - height) // 2
            else:
                y = available.bottom() - height - 48

        x += offset_x
        y += offset_y
        x = min(max(x, available.left() + 12), available.right() - width - 12)
        y = min(max(y, available.top() + 12), available.bottom() - height - 12)

        self._applying_geometry = True
        self.setGeometry(x, y, width, height)
        self._last_reported_size = QSize(width, height)
        self._applying_geometry = False
        self.show()


class FrameSignals(QObject):
    result = Signal(str, str)
    skipped = Signal(str)
    error = Signal(str, str)


class FrameProcessor(QRunnable):
    def __init__(
        self,
        capturer: ScreenCapturer,
        ocr_engine: BaseOcr,
        translator: BaseTranslator,
        cache: TranslationCache,
        region: CaptureRegion,
        previous_text: str,
        image: Image.Image | None = None,
    ) -> None:
        super().__init__()
        self.signals = FrameSignals()
        self.capturer = capturer
        self.ocr_engine = ocr_engine
        self.translator = translator
        self.cache = cache
        self.region = region
        self.previous_text = previous_text
        self.image = image

    @Slot()
    def run(self) -> None:
        started_at = time.perf_counter()
        try:
            if self.image is None:
                capture_started_at = time.perf_counter()
                image = self.capturer.grab_region(self.region)
                capture_ms = (time.perf_counter() - capture_started_at) * 1000
            else:
                image = self.image
                capture_ms = 0.0

            _save_last_capture(image)
            ocr_started_at = time.perf_counter()
            text = self.ocr_engine.recognize(image)
            ocr_ms = (time.perf_counter() - ocr_started_at) * 1000
            log.info(
                "frame_ocr capture_ms=%.1f ocr_ms=%.1f text=%r",
                capture_ms,
                ocr_ms,
                clip_text(text),
            )
            if not is_substantial_change(self.previous_text, text):
                log.info("frame_skipped reason=no_substantial_change text=%r", clip_text(text))
                self.signals.skipped.emit(text)
                return

            cached = self.cache.get(text)
            if cached is not None:
                log.info("frame_cache_hit text=%r translation=%r", clip_text(text), clip_text(cached))
                self.signals.result.emit(text, cached)
                return

            translate_started_at = time.perf_counter()
            translated = self.translator.translate(text)
            translate_ms = (time.perf_counter() - translate_started_at) * 1000
            self.cache.set(text, translated)
            total_ms = (time.perf_counter() - started_at) * 1000
            log.info(
                "frame_translated translate_ms=%.1f total_ms=%.1f source=%r translation=%r",
                translate_ms,
                total_ms,
                clip_text(text),
                clip_text(translated),
            )
            self.signals.result.emit(text, translated)
        except (OcrError, TranslationError, ValueError) as exc:
            log.warning("frame_error error=%r", str(exc))
            self.signals.error.emit(str(exc), text if "text" in locals() else "")
        except Exception as exc:  # noqa: BLE001
            log.exception("frame_unexpected_error")
            self.signals.error.emit(f"Unexpected error: {exc}", "")


class DomSubtitleProcessor(QRunnable):
    def __init__(
        self,
        reader: BrowserDomSubtitleReader,
        translator: BaseTranslator,
        cache: TranslationCache,
        previous_text: str,
    ) -> None:
        super().__init__()
        self.signals = FrameSignals()
        self.reader = reader
        self.translator = translator
        self.cache = cache
        self.previous_text = previous_text

    @Slot()
    def run(self) -> None:
        started_at = time.perf_counter()
        try:
            read_started_at = time.perf_counter()
            text = self.reader.read_text()
            read_ms = (time.perf_counter() - read_started_at) * 1000
            log.info("dom_source_read read_ms=%.1f text=%r", read_ms, clip_text(text))
            if not is_substantial_change(self.previous_text, text):
                log.info("dom_source_skipped reason=no_substantial_change text=%r", clip_text(text))
                self.signals.skipped.emit(text)
                return

            cached = self.cache.get(text)
            if cached is not None:
                log.info("dom_source_cache_hit text=%r translation=%r", clip_text(text), clip_text(cached))
                self.signals.result.emit(text, cached)
                return

            translate_started_at = time.perf_counter()
            translated = self.translator.translate(text)
            translate_ms = (time.perf_counter() - translate_started_at) * 1000
            self.cache.set(text, translated)
            total_ms = (time.perf_counter() - started_at) * 1000
            log.info(
                "dom_source_translated translate_ms=%.1f total_ms=%.1f source=%r translation=%r",
                translate_ms,
                total_ms,
                clip_text(text),
                clip_text(translated),
            )
            self.signals.result.emit(text, translated)
        except (BrowserDomError, TranslationError, ValueError) as exc:
            log.warning("dom_source_error error=%r", str(exc))
            self.signals.error.emit(str(exc), text if "text" in locals() else "")
        except Exception as exc:  # noqa: BLE001
            log.exception("dom_source_unexpected_error")
            self.signals.error.emit(f"Unexpected error: {exc}", "")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RTST Subtitle Translator")
        self.settings = load_settings()
        self.region: CaptureRegion | None = None
        self.capturer = ScreenCapturer()
        self.ocr_engine = build_ocr_engine(self.settings.ocr_engine, self.settings.ocr_language)
        self.translator: BaseTranslator | None = None
        self.browser_reader: BrowserDomSubtitleReader | None = None
        self.oauth_client: OAuthPkceClient | None = None
        self.region_selector: RegionSelector | None = None
        self.cache = TranslationCache()
        self.last_source_text = ""
        self.translation_history: list[tuple[str, str]] = []
        self.worker_running = False
        self._last_seen_signature: Image.Image | None = None
        self._pending_signature: Image.Image | None = None
        self._pending_image: Image.Image | None = None
        self._pending_started_at = 0.0
        self._pending_updated_at = 0.0

        self.overlay = OverlayWindow()
        self.overlay.position_changed.connect(self._handle_overlay_dragged)
        self.overlay.size_changed.connect(self._handle_overlay_resized)
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.process_frame)

        self._build_ui()
        self._load_settings_into_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        toolbar = QHBoxLayout()
        self.select_button = QPushButton("Select subtitle region")
        self.oauth_login_button = QPushButton("OAuth login")
        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        toolbar.addWidget(self.select_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.oauth_login_button)
        toolbar.addWidget(self.start_button)
        toolbar.addWidget(self.stop_button)
        layout.addLayout(toolbar)

        self.region_label = QLabel("Region: none")
        self.status_label = QLabel("Idle")
        layout.addWidget(self.region_label)
        layout.addWidget(self.status_label)

        self.source_combo = QComboBox()
        self.source_combo.addItems(["screen_ocr", "browser_dom"])
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["codex_oauth", "openai"])
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setText(os.getenv("OPENAI_API_KEY", ""))
        self.model_input = QLineEdit(os.getenv("OPENAI_MODEL", self.settings.openai_model))
        self.codex_base_url_input = QLineEdit(codex_base_url_from_env())
        self.oauth_clear_button = QPushButton("Clear OAuth token")
        self.target_input = QLineEdit(self.settings.target_language)
        self.ocr_engine_combo = QComboBox()
        self.ocr_engine_combo.addItems(["windows", "tesseract"])
        self.ocr_lang_input = QLineEdit(self.settings.ocr_language)
        self.browser_debug_url_input = QLineEdit(self.settings.browser_debug_url)
        self.browser_tab_filter_input = QLineEdit(self.settings.browser_tab_filter)
        self.browser_selector_input = QLineEdit(self.settings.browser_subtitle_selector)
        self.browser_selector_input.setPlaceholderText("Optional CSS selector, e.g. .ytp-caption-segment")

        self.font_spin = QSpinBox()
        self.font_spin.setRange(14, 48)
        self.font_spin.setSuffix(" px")

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(30, 100)

        self.overlay_enabled_check = QCheckBox("Show overlay")
        self.overlay_width_spin = QSpinBox()
        self.overlay_width_spin.setRange(320, 2400)
        self.overlay_width_spin.setSingleStep(40)
        self.overlay_width_spin.setSuffix(" px")
        self.overlay_height_spin = QSpinBox()
        self.overlay_height_spin.setRange(80, 1200)
        self.overlay_height_spin.setSingleStep(40)
        self.overlay_height_spin.setSuffix(" px")
        self.overlay_position_combo = QComboBox()
        self.overlay_position_combo.addItems(["auto", "bottom", "top", "center", "custom_region", "manual"])
        self.overlay_offset_x_spin = QSpinBox()
        self.overlay_offset_x_spin.setRange(-1000, 1000)
        self.overlay_offset_x_spin.setSuffix(" px")
        self.overlay_offset_y_spin = QSpinBox()
        self.overlay_offset_y_spin.setRange(-1000, 1000)
        self.overlay_offset_y_spin.setSuffix(" px")
        self.show_original_check = QCheckBox("Show source text")

        self.source_text = QTextEdit()
        self.source_text.setReadOnly(True)
        self.source_text.setMaximumHeight(78)
        self.translation_text = QTextEdit()
        self.translation_text.setReadOnly(True)
        self.translation_text.setMaximumHeight(96)
        self.history_text = QTextEdit()
        self.history_text.setReadOnly(True)
        self.history_text.setStyleSheet(
            """
            QTextEdit {
                background: #f6f8fb;
                border: 1px solid #d7dce3;
                border-radius: 6px;
                padding: 8px;
            }
            """
        )
        self.clear_history_button = QPushButton("Clear history")
        self.translation_history_limit_spin = QSpinBox()
        self.translation_history_limit_spin.setRange(20, 1000)
        self.translation_history_limit_spin.setSingleStep(20)
        self.translation_history_limit_spin.setSuffix(" items")

        tabs = QTabWidget()
        layout.addWidget(tabs)

        run_tab = QWidget()
        run_layout = QVBoxLayout(run_tab)
        run_form = QFormLayout()
        run_form.addRow("Subtitle source", self.source_combo)
        run_form.addRow("Translator", self.provider_combo)
        run_form.addRow("Target language", self.target_input)
        run_layout.addLayout(run_form)
        run_layout.addWidget(QLabel("Latest source"))
        run_layout.addWidget(self.source_text)
        run_layout.addWidget(QLabel("Latest translation"))
        run_layout.addWidget(self.translation_text)
        tabs.addTab(run_tab, "Run")

        history_tab = QWidget()
        history_layout = QVBoxLayout(history_tab)
        history_toolbar = QHBoxLayout()
        history_toolbar.addWidget(QLabel("Translation history"))
        history_toolbar.addStretch(1)
        history_toolbar.addWidget(QLabel("Max"))
        history_toolbar.addWidget(self.translation_history_limit_spin)
        history_toolbar.addWidget(self.clear_history_button)
        history_layout.addLayout(history_toolbar)
        history_layout.addWidget(self.history_text)
        tabs.addTab(history_tab, "History")

        source_tab = QWidget()
        source_form = QFormLayout(source_tab)
        source_form.addRow("OCR engine", self.ocr_engine_combo)
        source_form.addRow("OCR language", self.ocr_lang_input)
        source_form.addRow("Chrome debug URL", self.browser_debug_url_input)
        source_form.addRow("Browser tab filter", self.browser_tab_filter_input)
        source_form.addRow("Subtitle CSS selector", self.browser_selector_input)
        tabs.addTab(source_tab, "Source")

        translation_tab = QWidget()
        translation_form = QFormLayout(translation_tab)
        translation_form.addRow("OpenAI model", self.model_input)
        translation_form.addRow("OpenAI API Key", self.api_key_input)
        translation_form.addRow("", self.oauth_clear_button)
        tabs.addTab(translation_tab, "Translation")

        overlay_tab = QWidget()
        overlay_form = QFormLayout(overlay_tab)
        overlay_form.addRow("", self.overlay_enabled_check)
        overlay_form.addRow("Overlay font", self.font_spin)
        overlay_form.addRow("Overlay opacity", self.opacity_slider)
        overlay_form.addRow("Overlay width", self.overlay_width_spin)
        overlay_form.addRow("Overlay height", self.overlay_height_spin)
        overlay_form.addRow("Overlay position", self.overlay_position_combo)
        overlay_form.addRow("Overlay X offset", self.overlay_offset_x_spin)
        overlay_form.addRow("Overlay Y offset", self.overlay_offset_y_spin)
        overlay_form.addRow("", self.show_original_check)
        tabs.addTab(overlay_tab, "Overlay")

        advanced_tab = QWidget()
        advanced_form = QFormLayout(advanced_tab)
        advanced_form.addRow("Codex base URL", self.codex_base_url_input)
        tabs.addTab(advanced_tab, "Advanced")

        self.select_button.clicked.connect(self.select_region)
        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.stop)
        self.oauth_login_button.clicked.connect(self.oauth_login)
        self.oauth_clear_button.clicked.connect(self.clear_oauth_token)
        self.clear_history_button.clicked.connect(self.clear_translation_history)
        self.translation_history_limit_spin.valueChanged.connect(self._trim_translation_history)
        self.source_combo.currentTextChanged.connect(self._handle_source_changed)
        self.provider_combo.currentTextChanged.connect(self._handle_provider_changed)
        self.overlay_enabled_check.stateChanged.connect(self._handle_overlay_enabled_changed)
        self.opacity_slider.valueChanged.connect(self._preview_overlay_style)
        self.font_spin.valueChanged.connect(self._preview_overlay_style)
        self.overlay_width_spin.valueChanged.connect(self._preview_overlay_position)
        self.overlay_height_spin.valueChanged.connect(self._preview_overlay_position)
        self.overlay_position_combo.currentTextChanged.connect(self._preview_overlay_position)
        self.overlay_offset_x_spin.valueChanged.connect(self._preview_overlay_position)
        self.overlay_offset_y_spin.valueChanged.connect(self._preview_overlay_position)

        self.resize(680, 560)

    def _load_settings_into_ui(self) -> None:
        self.source_combo.setCurrentText(self.settings.subtitle_source)
        self.provider_combo.setCurrentText(self.settings.translator_provider)
        self.model_input.setText(os.getenv("OPENAI_MODEL", self.settings.openai_model))
        self.codex_base_url_input.setText(os.getenv("RTST_CODEX_BASE_URL", self.settings.codex_base_url))
        self.target_input.setText(self.settings.target_language)
        self.ocr_engine_combo.setCurrentText(self.settings.ocr_engine)
        self.ocr_lang_input.setText(self.settings.ocr_language)
        self.browser_debug_url_input.setText(os.getenv("RTST_BROWSER_DEBUG_URL", self.settings.browser_debug_url))
        self.browser_tab_filter_input.setText(os.getenv("RTST_BROWSER_TAB_FILTER", self.settings.browser_tab_filter))
        self.browser_selector_input.setText(
            os.getenv("RTST_BROWSER_SUBTITLE_SELECTOR", self.settings.browser_subtitle_selector)
        )
        self.font_spin.setValue(self.settings.overlay_font_size)
        self.opacity_slider.setValue(round(self.settings.overlay_opacity * 100))
        self.overlay_enabled_check.setChecked(self.settings.overlay_enabled)
        self.overlay_width_spin.setValue(self.settings.overlay_width)
        self.overlay_height_spin.setValue(self.settings.overlay_max_height)
        self.overlay_position_combo.setCurrentText(self.settings.overlay_position)
        self.overlay_offset_x_spin.setValue(self.settings.overlay_offset_x)
        self.overlay_offset_y_spin.setValue(self.settings.overlay_offset_y)
        self.translation_history_limit_spin.setValue(self.settings.translation_history_limit)
        self.show_original_check.setChecked(self.settings.show_original)
        self._handle_source_changed(self.source_combo.currentText())
        self._handle_provider_changed(self.provider_combo.currentText())

    def _settings_from_ui(self) -> AppSettings:
        return replace(
            self.settings,
            subtitle_source=self.source_combo.currentText(),
            target_language=self.target_input.text().strip() or "Korean",
            ocr_engine=self.ocr_engine_combo.currentText(),
            ocr_language=self.ocr_lang_input.text().strip() or "en",
            browser_debug_url=self.browser_debug_url_input.text().strip() or "http://127.0.0.1:9222",
            browser_tab_filter=self.browser_tab_filter_input.text().strip(),
            browser_subtitle_selector=self.browser_selector_input.text().strip(),
            translator_provider=self.provider_combo.currentText(),
            openai_model=self.model_input.text().strip() or "gpt-5-mini",
            codex_base_url=self.codex_base_url_input.text().strip() or "https://chatgpt.com/backend-api",
            oauth_proxy_url=os.getenv("RTST_PROXY_URL", self.settings.oauth_proxy_url),
            oauth_authorization_url=os.getenv("RTST_OAUTH_AUTH_URL", self.settings.oauth_authorization_url),
            oauth_token_url=os.getenv("RTST_OAUTH_TOKEN_URL", self.settings.oauth_token_url),
            oauth_client_id=os.getenv("RTST_OAUTH_CLIENT_ID", self.settings.oauth_client_id),
            oauth_scope=os.getenv("RTST_OAUTH_SCOPE", self.settings.oauth_scope),
            overlay_enabled=self.overlay_enabled_check.isChecked(),
            overlay_font_size=self.font_spin.value(),
            overlay_opacity=self.opacity_slider.value() / 100,
            overlay_width=self.overlay_width_spin.value(),
            overlay_max_height=self.overlay_height_spin.value(),
            overlay_position=self.overlay_position_combo.currentText(),
            overlay_offset_x=self.overlay_offset_x_spin.value(),
            overlay_offset_y=self.overlay_offset_y_spin.value(),
            overlay_accumulate=True,
            translation_history_limit=self.translation_history_limit_spin.value(),
            show_original=self.show_original_check.isChecked(),
        )

    def _handle_source_changed(self, source: str) -> None:
        using_dom = source == "browser_dom"
        self.select_button.setText("Select overlay region" if using_dom else "Select subtitle region")
        if using_dom and self.region is None:
            self.region_label.setText("Region: auto overlay position")
        elif not using_dom and self.region is None:
            self.region_label.setText("Region: none")

    def _handle_provider_changed(self, provider: str) -> None:
        if provider != "codex_oauth":
            return
        current_model = self.model_input.text().strip()
        if not current_model or current_model == "gpt-5-mini":
            self.model_input.setText(codex_model_from_env())
        self.codex_base_url_input.setText(codex_base_url_from_env())

    def select_region(self) -> None:
        if self.region_selector is not None:
            self.region_selector.close()
            self.region_selector = None

        self.region_selector = RegionSelector()
        self.region_selector.region_selected.connect(self.set_region)
        self.region_selector.destroyed.connect(self._clear_region_selector)
        self.region_selector.show()
        self.region_selector.raise_()
        self.region_selector.activateWindow()
        if self.source_combo.currentText() == "browser_dom":
            self.status_label.setText("Drag over the overlay position")
        else:
            self.status_label.setText("Drag over the subtitle region")

    def _clear_region_selector(self, *_args: object) -> None:
        self.region_selector = None

    def set_region(self, region: CaptureRegion) -> None:
        self.region = region
        self._reset_detection_state()
        if self.source_combo.currentText() == "browser_dom":
            self.overlay_position_combo.setCurrentText("custom_region")
        self.region_label.setText(
            f"Region: x={region.left}, y={region.top}, w={region.width}, h={region.height}"
        )
        if self.source_combo.currentText() == "browser_dom":
            self.status_label.setText("Overlay region selected")
        else:
            self.status_label.setText("Region selected")

    def _reset_detection_state(self) -> None:
        self._last_seen_signature = None
        self._pending_signature = None
        self._pending_image = None
        self._pending_started_at = 0.0
        self._pending_updated_at = 0.0

    def build_oauth_client(self) -> OAuthPkceClient:
        settings = self._settings_from_ui()
        if settings.translator_provider == "codex_oauth":
            return OAuthPkceClient(codex_oauth_config_from_env())
        return OAuthPkceClient(
            OAuthConfig(
                authorization_url=settings.oauth_authorization_url,
                token_url=settings.oauth_token_url,
                client_id=settings.oauth_client_id,
                scope=settings.oauth_scope,
            )
        )

    def oauth_login(self) -> None:
        self.settings = self._settings_from_ui()
        self.oauth_client = self.build_oauth_client()
        self.status_label.setText("Waiting for OAuth login in browser")
        QApplication.processEvents()
        try:
            self.oauth_client.login()
        except OAuthError as exc:
            QMessageBox.warning(self, "OAuth login failed", str(exc))
            self.status_label.setText("OAuth login failed")
            return

        save_settings(self.settings)
        self.status_label.setText("OAuth login complete")

    def clear_oauth_token(self) -> None:
        client = self.oauth_client or self.build_oauth_client()
        client.clear_token()
        self.oauth_client = client
        self.status_label.setText("OAuth token cleared")

    def start(self) -> None:
        self.settings = self._settings_from_ui()
        using_dom = self.settings.subtitle_source == "browser_dom"
        if not using_dom and self.region is None:
            QMessageBox.warning(self, "Region required", "Select the subtitle region first.")
            return

        try:
            if using_dom:
                self.browser_reader = BrowserDomSubtitleReader(
                    debug_url=self.settings.browser_debug_url,
                    tab_filter=self.settings.browser_tab_filter,
                    subtitle_selector=self.settings.browser_subtitle_selector,
                )
                self.browser_reader.ensure_ready()
            else:
                self.ocr_engine = build_ocr_engine(self.settings.ocr_engine, self.settings.ocr_language)
                self.ocr_engine.ensure_ready()
            oauth_client = None
            if self.settings.translator_provider in {"oauth_proxy", "codex_oauth"}:
                self.oauth_client = self.build_oauth_client()
                self.status_label.setText("Checking OAuth token")
                QApplication.processEvents()
                self.oauth_client.get_access_token()
                oauth_client = self.oauth_client
            self.translator = build_translator(
                provider=self.settings.translator_provider,
                target_language=self.settings.target_language,
                source_language=self.settings.source_language,
                model=self.settings.openai_model,
                api_key=self.api_key_input.text().strip(),
                oauth_proxy_url=self.settings.oauth_proxy_url,
                oauth_client=oauth_client,
                codex_base_url=self.settings.codex_base_url,
            )
            if self.settings.translator_provider == "openai" and not self.api_key_input.text().strip():
                raise TranslationError("OpenAI mode requires an API key.")
        except (OcrError, BrowserDomError, OAuthError, TranslationError) as exc:
            QMessageBox.warning(self, "Start failed", str(exc))
            return

        save_settings(self.settings)
        self.cache.clear()
        self.last_source_text = ""
        self._reset_detection_state()
        timer_interval_ms = DOM_POLL_INTERVAL_MS if using_dom else AUTO_SCAN_INTERVAL_MS
        self.timer.start(timer_interval_ms)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        if using_dom:
            log.info(
                "app_started source=browser_dom provider=%s model=%s poll_interval_ms=%s debug_url=%s tab_filter=%r selector=%r",
                self.settings.translator_provider,
                self.settings.openai_model,
                timer_interval_ms,
                self.settings.browser_debug_url,
                self.settings.browser_tab_filter,
                self.settings.browser_subtitle_selector,
            )
            self.status_label.setText("Watching browser subtitles")
        else:
            log.info(
                "app_started source=screen_ocr provider=%s model=%s scan_interval_ms=%s stable_ms=%s max_wait_ms=%s visual_threshold=%.2f ocr=%s:%s region=%s,%s,%s,%s",
                self.settings.translator_provider,
                self.settings.openai_model,
                AUTO_SCAN_INTERVAL_MS,
                SUBTITLE_STABLE_MS,
                SUBTITLE_MAX_WAIT_MS,
                VISUAL_CHANGE_THRESHOLD,
                self.settings.ocr_engine,
                self.settings.ocr_language,
                self.region.left,
                self.region.top,
                self.region.width,
                self.region.height,
            )
            self.status_label.setText("Watching subtitle region")

    def stop(self) -> None:
        self.timer.stop()
        if self.browser_reader is not None:
            self.browser_reader.close()
            self.browser_reader = None
        self.overlay.hide()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        log.info("app_stopped")
        self.status_label.setText("Stopped")

    def process_frame(self) -> None:
        if self.settings.subtitle_source == "browser_dom":
            self.process_dom_subtitle()
            return
        self.process_screen_frame()

    def process_dom_subtitle(self) -> None:
        if self.worker_running or self.translator is None or self.browser_reader is None:
            return

        self.worker_running = True
        worker = DomSubtitleProcessor(
            reader=self.browser_reader,
            translator=self.translator,
            cache=self.cache,
            previous_text=self.last_source_text,
        )
        worker.signals.result.connect(self._handle_result)
        worker.signals.skipped.connect(self._handle_skipped)
        worker.signals.error.connect(self._handle_error)
        self.thread_pool.start(worker)

    def process_screen_frame(self) -> None:
        if self.region is None or self.translator is None:
            return

        try:
            capture_started_at = time.perf_counter()
            image = self.capturer.grab_region(self.region)
            capture_ms = (time.perf_counter() - capture_started_at) * 1000
            signature = frame_signature(image)
        except (OcrError, ValueError) as exc:
            log.warning("frame_capture_error error=%r", str(exc))
            self.status_label.setText(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("frame_capture_unexpected_error")
            self.status_label.setText(f"Capture error: {exc}")
            return

        now = time.perf_counter()
        previous_signature = self._last_seen_signature
        diff = None if previous_signature is None else frame_difference(previous_signature, signature)
        changed = previous_signature is None or diff >= VISUAL_CHANGE_THRESHOLD
        pending_was_empty = self._pending_signature is None

        if changed:
            if pending_was_empty:
                self._pending_started_at = now
                reason = "initial" if diff is None else "visual_change"
                log.info(
                    "frame_visual_pending reason=%s capture_ms=%.1f diff=%s threshold=%.2f",
                    reason,
                    capture_ms,
                    "initial" if diff is None else f"{diff:.2f}",
                    VISUAL_CHANGE_THRESHOLD,
                )
                if self.worker_running:
                    self.status_label.setText("Subtitle change queued")
                else:
                    self.status_label.setText("Subtitle change detected")

            self._pending_signature = signature
            self._pending_image = image
            self._pending_updated_at = now

        self._last_seen_signature = signature

        if self._pending_image is None or self._pending_signature is None:
            return

        stable_ms = (now - self._pending_updated_at) * 1000
        pending_age_ms = (now - self._pending_started_at) * 1000
        if stable_ms < SUBTITLE_STABLE_MS and pending_age_ms < SUBTITLE_MAX_WAIT_MS:
            return

        if self.worker_running:
            return

        worker_image = self._pending_image
        ready_reason = "stable" if stable_ms >= SUBTITLE_STABLE_MS else "max_wait"
        log.info(
            "frame_detection_ready reason=%s stable_ms=%.1f pending_age_ms=%.1f",
            ready_reason,
            stable_ms,
            pending_age_ms,
        )
        self._pending_signature = None
        self._pending_image = None

        self.worker_running = True
        worker = FrameProcessor(
            capturer=self.capturer,
            ocr_engine=self.ocr_engine,
            translator=self.translator,
            cache=self.cache,
            region=self.region,
            previous_text=self.last_source_text,
            image=worker_image,
        )
        worker.signals.result.connect(self._handle_result)
        worker.signals.skipped.connect(self._handle_skipped)
        worker.signals.error.connect(self._handle_error)
        self.thread_pool.start(worker)

    def _handle_result(self, source: str, translation: str) -> None:
        self.worker_running = False
        self.last_source_text = source
        self.source_text.setPlainText(source)
        self.translation_text.setPlainText(translation)
        self._append_translation_history(source, translation)

        self._show_overlay_history()
        self.status_label.setText("Translation updated")

    def _handle_skipped(self, text: str) -> None:
        self.worker_running = False
        if text:
            self.source_text.setPlainText(text)
            self.status_label.setText("No text change")
        else:
            self.last_source_text = ""
            self.source_text.setPlainText("")
            if self.settings.subtitle_source == "browser_dom":
                self.status_label.setText("No DOM subtitle text - check tab or selector")
            else:
                self.status_label.setText("OCR found no text - check selected region")

    def _handle_error(self, message: str, source: str = "") -> None:
        self.worker_running = False
        if source:
            self.last_source_text = source
            self.source_text.setPlainText(source)
        self.status_label.setText(message)

    def _append_translation_history(self, source: str, translation: str) -> None:
        if self.translation_history and self.translation_history[-1] == (source, translation):
            return
        self.translation_history.append((source, translation))
        self._trim_translation_history(render=False)
        self._render_translation_history()

    def clear_translation_history(self) -> None:
        self.translation_history.clear()
        self.history_text.clear()
        self.overlay.hide()
        self.status_label.setText("History cleared")

    def _trim_translation_history(self, *_args: object, render: bool = True) -> None:
        limit = self.translation_history_limit_spin.value()
        if len(self.translation_history) > limit:
            self.translation_history = self.translation_history[-limit:]
        if render:
            self._render_translation_history()
            self._show_overlay_history()

    def _render_translation_history(self) -> None:
        if not self.translation_history:
            self.history_text.clear()
            return

        blocks: list[str] = []
        for index, (source, translation) in enumerate(self.translation_history, start=1):
            source_html = escape(source).replace("\n", "<br>")
            translation_html = escape(translation).replace("\n", "<br>")
            blocks.append(
                f"""
                <div style="margin: 10px 0 14px 0;">
                  <div style="color: #6b7280; font-size: 11px;">#{index} Source</div>
                  <div style="background: #eef2ff; color: #111827; border-radius: 8px; padding: 8px 10px; margin: 3px 36px 6px 0;">
                    {source_html}
                  </div>
                  <div style="color: #6b7280; font-size: 11px; text-align: right;">Translation</div>
                  <div style="background: #dcfce7; color: #052e16; border-radius: 8px; padding: 8px 10px; margin: 3px 0 0 36px;">
                    {translation_html}
                  </div>
                </div>
                """
            )
        self.history_text.setHtml("".join(blocks))
        self.history_text.moveCursor(QTextCursor.MoveOperation.End)

    def _overlay_history_html(self, entries: list[tuple[str, str]] | None = None) -> str:
        history = entries if entries is not None else self.translation_history
        if not history:
            return ""

        blocks: list[str] = []
        for index, (source, translation) in enumerate(history, start=1):
            source_html = escape(source).replace("\n", "<br>")
            translation_html = escape(translation).replace("\n", "<br>")
            source_block = ""
            if self.settings.show_original and source.strip():
                source_block = (
                    "<div style='color:#bfdbfe; font-size:0.78em; line-height:1.25; "
                    f"margin-bottom:4px;'>{source_html}</div>"
                )
            blocks.append(
                "<div style='margin-bottom:10px;'>"
                f"<div style='color:#9ca3af; font-size:0.66em;'>#{index}</div>"
                f"{source_block}"
                "<div style='color:#ffffff; font-weight:600; line-height:1.3;'>"
                f"{translation_html}</div>"
                "</div>"
            )
        return "".join(blocks)

    def _show_overlay_history(self) -> None:
        self.settings = self._settings_from_ui()
        if not self.settings.overlay_enabled:
            self.overlay.hide()
            return
        html = self._overlay_history_html()
        if not html:
            self.overlay.hide()
            return
        self._show_overlay_text(html)

    def _overlay_region(self) -> CaptureRegion | None:
        if self.region is not None:
            return self.region

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return None
        available = screen.availableGeometry()
        width = min(max(int(available.width() * 0.72), 560), max(560, available.width() - 48))
        height = 120
        left = available.left() + max(24, (available.width() - width) // 2)
        top = available.bottom() - height - 84
        return CaptureRegion(left=left, top=top, width=width, height=height)

    def _show_overlay_text(self, display_text: str) -> None:
        if not self.settings.overlay_enabled:
            self.overlay.hide()
            return
        position = self.settings.overlay_position
        if position == "auto":
            overlay_region = self._overlay_region()
            if overlay_region is not None:
                self.overlay.show_text(display_text, overlay_region, self.settings)
            return

        custom_region = self.region if position == "custom_region" else None
        self.overlay.show_text_positioned(
            display_text,
            self.settings,
            position=position,
            offset_x=self.settings.overlay_offset_x,
            offset_y=self.settings.overlay_offset_y,
            custom_region=custom_region,
            manual_x=self.settings.overlay_manual_x,
            manual_y=self.settings.overlay_manual_y,
        )

    def _refresh_overlay_history(self) -> None:
        self.settings = self._settings_from_ui()
        self._show_overlay_history()

    def _handle_overlay_enabled_changed(self, *_args: object) -> None:
        self.settings = self._settings_from_ui()
        save_settings(self.settings)
        if not self.settings.overlay_enabled:
            self.overlay.hide()
            self.status_label.setText("Overlay hidden")
            return
        self._show_overlay_history()
        self.status_label.setText("Overlay enabled")

    def _handle_overlay_dragged(self, x: int, y: int) -> None:
        self.settings = replace(
            self._settings_from_ui(),
            overlay_position="manual",
            overlay_manual_x=x,
            overlay_manual_y=y,
        )
        self.overlay_position_combo.setCurrentText("manual")
        save_settings(self.settings)
        self.status_label.setText(f"Overlay moved: x={x}, y={y}")

    def _handle_overlay_resized(self, width: int, height: int) -> None:
        self.overlay_width_spin.blockSignals(True)
        self.overlay_height_spin.blockSignals(True)
        self.overlay_width_spin.setValue(width)
        self.overlay_height_spin.setValue(height)
        self.overlay_width_spin.blockSignals(False)
        self.overlay_height_spin.blockSignals(False)
        self.settings = replace(
            self._settings_from_ui(),
            overlay_width=width,
            overlay_max_height=height,
        )
        save_settings(self.settings)
        self.status_label.setText(f"Overlay resized: w={width}, h={height}")

    def _preview_overlay_style(self) -> None:
        self.settings = self._settings_from_ui()
        self.overlay.update_style(self.settings.overlay_font_size, self.settings.overlay_opacity)
        self._preview_overlay_position()

    def _preview_overlay_position(self) -> None:
        self.settings = self._settings_from_ui()
        if not self.settings.overlay_enabled:
            self.overlay.hide()
            return
        if self.translation_history:
            self._show_overlay_history()
            return

        current_text = self.translation_text.toPlainText().strip()
        if not current_text:
            return
        source = self.source_text.toPlainText().strip()
        self._show_overlay_text(self._overlay_history_html([(source, current_text)]))

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.stop()
        if self.region_selector is not None:
            self.region_selector.close()
        self.thread_pool.waitForDone(1500)
        save_settings(self._settings_from_ui())
        event.accept()


def run_app(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setApplicationName("RTST")
    window = MainWindow()
    window.show()
    if "--browser-dom" in argv:
        window.source_combo.setCurrentText("browser_dom")
    if "--proxy-oauth-login" in argv:
        def trigger_oauth_login() -> None:
            window.provider_combo.setCurrentText("oauth_proxy")
            window.oauth_login()

        QTimer.singleShot(400, trigger_oauth_login)
    elif "--oauth-login" in argv or "--codex-oauth-login" in argv:
        def trigger_codex_oauth_login() -> None:
            window.provider_combo.setCurrentText("codex_oauth")
            window.model_input.setText(codex_model_from_env())
            window.oauth_login()

        QTimer.singleShot(400, trigger_codex_oauth_login)
    return app.exec()
