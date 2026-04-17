import sys, os, json, glob, shutil, base64
import html
import time
import ctypes
import traceback
import faulthandler
import threading
import re
from urllib.parse import quote_plus
from urllib.parse import urlparse, parse_qs, urlencode, urljoin
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
    QLineEdit, QPushButton, QTabWidget, QListWidget, QFileDialog,
    QMenu, QMessageBox, QInputDialog, QListWidgetItem, QColorDialog, QDialog, QCheckBox, QComboBox, QStatusBar, QCompleter, QLabel, QFrame, QProgressBar, QAbstractItemView
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEngineProfile, QWebEnginePage, QWebEngineUrlRequestInterceptor, QWebEngineScript
try:
    from PyQt6.QtWebEngineCore import qWebEngineChromiumVersion
except Exception:
    def qWebEngineChromiumVersion():
        return ""
from PyQt6.QtCore import QUrl, Qt, QRect, QStringListModel, QTimer, pyqtSignal, QPoint, QEvent, QSize, QProcess, qInstallMessageHandler, QtMsgType
from PyQt6.QtGui import QPixmap, QPainter, QColor, QBrush, QKeySequence, QIcon, QDesktopServices, QAction, QShortcut
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QPlaybackOptions
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog, QPrinterInfo
try:
    from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest as QWebEngineDownloadItem
except Exception:
    from PyQt6.QtWebEngineCore import QWebEngineDownloadItem
try:
    import requests
except Exception:
    requests = None
try:
    import pygame
except Exception:
    pygame = None
import asyncio
import platform
import random

# Keep Chromium flags conservative, but allow smoother media playback and less
# background throttling on long-running tabs.

def _find_widevine_cdm():
    """
    Auto-detect widevinecdm.dll from common locations.
    Priority: next to this script > Chrome install > user's AppData.
    Returns the path string if found, else None.
    """
    candidates = []
    # 1. Same folder as this script (user can just drop the DLL next to the .py)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(script_dir, "widevinecdm.dll"))
    # 2. Standard Chrome install paths on Windows
    for base in [
        os.environ.get("PROGRAMFILES", "C:\\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application"),
    ]:
        if not base:
            continue
        chrome_base = os.path.join(base, "Google", "Chrome", "Application")
        if os.path.isdir(chrome_base):
            try:
                versions = sorted(
                    [d for d in os.listdir(chrome_base) if d[0].isdigit()],
                    reverse=True,
                )
                for ver in versions:
                    p = os.path.join(chrome_base, ver, "WidevineCdm", "_platform_specific",
                                     "win_x64", "widevinecdm.dll")
                    candidates.append(p)
            except Exception:
                pass
        # Flat fallback
        candidates.append(os.path.join(base, "Google", "Chrome", "Application",
                                        "WidevineCdm", "_platform_specific", "win_x64", "widevinecdm.dll"))
    # 3. Aurora browser config folder
    aurora_dir = os.path.join(os.path.expanduser("~"), ".aurora_browser")
    candidates.append(os.path.join(aurora_dir, "widevinecdm.dll"))

    for p in candidates:
        if os.path.isfile(p):
            return p
    return None

_WIDEVINE_CDM_PATH = _find_widevine_cdm()


def _detect_chromium_version():
    try:
        version = (qWebEngineChromiumVersion() or "").strip()
    except Exception:
        version = ""
    return version or "134.0.6998.208"


CHROMIUM_VERSION = _detect_chromium_version()
CHROMIUM_MAJOR_VERSION = (CHROMIUM_VERSION.split(".", 1)[0] or "134").strip()


def _build_default_user_agent():
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{CHROMIUM_VERSION} Safari/537.36"
    )


def is_google_auth_host(hostname):
    host = (hostname or "").lower()
    return host in {"accounts.google.com", "signin.google.com"}

CHROMIUM_FLAGS = [
    "--autoplay-policy=no-user-gesture-required",
    "--disable-background-media-suspend",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    # Widevine DRM — required for encrypted anime/streaming video (fixes Error 102630)
    "--enable-widevine",
    "--disable-features=MediaEngagementBypassAutoplayPolicies",
    "--enable-features=PlatformHEVCDecoderSupport,HardwareMediaKeyHandling",
]
# Append Widevine CDM path flag only when the DLL is actually present
if _WIDEVINE_CDM_PATH:
    CHROMIUM_FLAGS.append(f"--widevine-path={_WIDEVINE_CDM_PATH}")

DEFAULT_USER_AGENT = _build_default_user_agent()
ENABLE_BROWSER_WIDE_COMPAT_SCRIPTS = True   # was False — needed for streaming site compatibility
ENABLE_RUNTIME_PAGE_PROTECTIONS = True
ENABLE_CUSTOM_REQUEST_HEADER_PATCHES = True
GOOGLE_CLIENT_HINT_HEADERS = {
    b"Sec-CH-UA": f'"Chromium";v="{CHROMIUM_MAJOR_VERSION}", "Google Chrome";v="{CHROMIUM_MAJOR_VERSION}", "Not(A:Brand";v="24"'.encode("utf-8"),
    b"Sec-CH-UA-Mobile": b"?0",
    b"Sec-CH-UA-Platform": b'"Windows"',
    b"Sec-CH-UA-Platform-Version": b'"15.0.0"',
    b"Sec-CH-UA-Arch": b'"x86"',
    b"Sec-CH-UA-Bitness": b'"64"',
    b"Sec-CH-UA-Full-Version": f'"{CHROMIUM_VERSION}"'.encode("utf-8"),
}
MEDIA_COMPATIBILITY_HOST_FRAGMENTS = (
    "anicrush",
    "anicrush.to",
    "anicrush.tv",
    "southcloud",
    "vidstream",
    "rabbitstream",
)
POPUP_COMPATIBILITY_HOST_FRAGMENTS = MEDIA_COMPATIBILITY_HOST_FRAGMENTS + (
    "megacloud",
    "megaf",
    "streamsb",
    "streamtape",
    "filemoon",
    "vidsrc",
    "mcloud",
    "mp4upload",
)
existing_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
if CHROMIUM_FLAGS:
    flag_set = set(existing_flags.split()) if existing_flags else set()
    for chromium_flag in CHROMIUM_FLAGS:
        if chromium_flag not in flag_set:
            existing_flags = f"{existing_flags} {chromium_flag}".strip()
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = existing_flags

LOG_DIR = os.path.join(os.path.expanduser("~"), ".aurora_browser")
LOG_FILE = os.path.join(LOG_DIR, "qt6_debug.log")

def log_debug(message):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class AuroraMediaProxyHandler(BaseHTTPRequestHandler):
    server_version = "AuroraMediaProxy/1.0"

    def do_HEAD(self):
        self.server.owner.handle_proxy_request(self, head_only=True)

    def do_GET(self):
        self.server.owner.handle_proxy_request(self, head_only=False)

    def log_message(self, format_string, *args):
        log_debug("MediaProxy: " + (format_string % args))


class AuroraMediaProxyServer:
    _shared_instance = None

    def __init__(self):
        self.httpd = ThreadedHTTPServer(("127.0.0.1", 0), AuroraMediaProxyHandler)
        self.httpd.owner = self
        self.host, self.port = self.httpd.server_address
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="AuroraMediaProxy", daemon=True)
        self._thread.start()

    @classmethod
    def shared(cls):
        if cls._shared_instance is None:
            cls._shared_instance = cls()
        return cls._shared_instance

    def build_proxy_url(self, target_url, referer="", origin="", user_agent=""):
        params = {"url": target_url}
        if referer:
            params["referer"] = referer
        if origin:
            params["origin"] = origin
        if user_agent:
            params["ua"] = user_agent
        return f"http://127.0.0.1:{self.port}/proxy?{urlencode(params)}"

    def _rewrite_playlist(self, text, base_url, referer="", origin="", user_agent=""):
        def proxify(raw_value):
            if not raw_value or raw_value.startswith("#"):
                return raw_value
            absolute = urljoin(base_url, raw_value)
            return self.build_proxy_url(absolute, referer=referer, origin=origin, user_agent=user_agent)

        rewritten_lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                rewritten_lines.append(line)
                continue
            if stripped.startswith("#"):
                line = re.sub(
                    r'URI="([^"]+)"',
                    lambda match: f'URI="{proxify(match.group(1))}"',
                    line,
                )
                rewritten_lines.append(line)
                continue
            rewritten_lines.append(proxify(stripped))
        return "\n".join(rewritten_lines)

    def _copy_upstream_headers(self, handler, upstream, content_length=None, content_type_override=None):
        skip_headers = {
            "transfer-encoding",
            "connection",
            "keep-alive",
            "content-encoding",
            "content-length",
        }
        handler.send_response(upstream.status_code)
        sent_content_type = False
        for key, value in upstream.headers.items():
            lowered = key.lower()
            if lowered in skip_headers:
                continue
            if lowered == "content-type":
                sent_content_type = True
                if content_type_override:
                    handler.send_header("Content-Type", content_type_override)
                else:
                    handler.send_header(key, value)
                continue
            handler.send_header(key, value)
        if content_type_override and not sent_content_type:
            handler.send_header("Content-Type", content_type_override)
        if content_length is not None:
            handler.send_header("Content-Length", str(content_length))
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.end_headers()

    def handle_proxy_request(self, handler, head_only=False):
        parsed = urlparse(handler.path)
        query = parse_qs(parsed.query)
        target_url = (query.get("url", [""])[0] or "").strip()
        referer = (query.get("referer", [""])[0] or "").strip()
        origin = (query.get("origin", [""])[0] or "").strip()
        user_agent = (query.get("ua", [""])[0] or DEFAULT_USER_AGENT).strip() or DEFAULT_USER_AGENT
        if not target_url:
            handler.send_error(400, "Missing target URL")
            return
        if requests is None:
            handler.send_error(500, "requests library is unavailable")
            return
        upstream = None
        try:
            headers = {
                "User-Agent": user_agent,
                "Accept": "*/*",
            }
            if referer:
                headers["Referer"] = referer
            if origin:
                headers["Origin"] = origin
            range_header = handler.headers.get("Range")
            if range_header:
                headers["Range"] = range_header
            upstream = requests.get(target_url, headers=headers, timeout=20, stream=True, allow_redirects=True)
            content_type = (upstream.headers.get("Content-Type") or "").lower()
            final_url = upstream.url or target_url
            is_playlist = ".m3u8" in final_url.lower() or "mpegurl" in content_type
            if is_playlist:
                text = upstream.text
                rewritten = self._rewrite_playlist(text, final_url, referer=referer, origin=origin, user_agent=user_agent)
                payload = rewritten.encode("utf-8")
                self._copy_upstream_headers(
                    handler,
                    upstream,
                    content_length=len(payload),
                    content_type_override="application/vnd.apple.mpegurl",
                )
                if not head_only:
                    handler.wfile.write(payload)
                return
            self._copy_upstream_headers(handler, upstream)
            if head_only:
                return
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    handler.wfile.write(chunk)
        except Exception as exc:
            log_debug(f"MediaProxy failure for {target_url}: {exc}")
            handler.send_error(502, f"Proxy error: {exc}")
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except Exception:
                    pass


class NativeMediaPlayerDialog(QDialog):
    def __init__(self, source_url, title_text="", referer="", origin="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title_text or "Aurora Native Player")
        style_aux_window(self)
        self.resize(1080, 720)
        self.source_url = source_url
        self.proxy_server = AuroraMediaProxyServer.shared()
        proxied_url = self.proxy_server.build_proxy_url(
            source_url,
            referer=referer,
            origin=origin,
            user_agent=DEFAULT_USER_AGENT,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.status_label = QLabel("Loading stream in Aurora Native Player...")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #cfe3ff;")
        layout.addWidget(self.status_label)

        self.video_widget = QVideoWidget(self)
        self.video_widget.setStyleSheet("background: #000; border-radius: 12px;")
        layout.addWidget(self.video_widget, 1)

        controls = QHBoxLayout()
        self.play_pause_button = QPushButton("Pause")
        self.play_pause_button.clicked.connect(self.toggle_playback)
        controls.addWidget(self.play_pause_button)

        retry_button = QPushButton("Retry Stream")
        retry_button.clicked.connect(self.retry_stream)
        controls.addWidget(retry_button)

        source_button = QPushButton("Copy Stream URL")
        source_button.clicked.connect(lambda: QApplication.clipboard().setText(self.source_url))
        controls.addWidget(source_button)
        controls.addStretch()
        layout.addLayout(controls)

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(1.0)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        try:
            playback_options = QPlaybackOptions()
            playback_options.setNetworkTimeout(20000)
            self.player.setPlaybackOptions(playback_options)
        except Exception:
            pass
        self.player.errorOccurred.connect(self.on_player_error)
        self.player.mediaStatusChanged.connect(self.on_media_status_changed)
        self.player.playbackStateChanged.connect(self.on_playback_state_changed)
        self.player.setSource(QUrl(proxied_url))
        self.player.play()

    def retry_stream(self):
        self.status_label.setText("Retrying stream...")
        self.player.stop()
        self.player.play()

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def on_player_error(self, error, error_string):
        message = error_string or "Aurora could not play this stream in the native player."
        self.status_label.setText(f"Playback failed: {message}")

    def on_media_status_changed(self, status):
        status_map = {
            QMediaPlayer.MediaStatus.NoMedia: "No media loaded.",
            QMediaPlayer.MediaStatus.LoadingMedia: "Loading stream...",
            QMediaPlayer.MediaStatus.LoadedMedia: "Stream loaded.",
            QMediaPlayer.MediaStatus.BufferingMedia: "Buffering...",
            QMediaPlayer.MediaStatus.BufferedMedia: "Playback ready.",
            QMediaPlayer.MediaStatus.EndOfMedia: "Playback finished.",
            QMediaPlayer.MediaStatus.InvalidMedia: "Qt marked this media source as invalid.",
        }
        text = status_map.get(status)
        if text:
            self.status_label.setText(text)

    def on_playback_state_changed(self, state):
        is_playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_pause_button.setText("Pause" if is_playing else "Play")

    def closeEvent(self, event):
        try:
            self.player.stop()
        except Exception:
            pass
        super().closeEvent(event)

def _qt_message_handler(mode, context, message):
    try:
        mode_name = {
            QtMsgType.QtDebugMsg: "DEBUG",
            QtMsgType.QtInfoMsg: "INFO",
            QtMsgType.QtWarningMsg: "WARN",
            QtMsgType.QtCriticalMsg: "CRIT",
            QtMsgType.QtFatalMsg: "FATAL",
        }.get(mode, "LOG")
    except Exception:
        mode_name = "LOG"
    log_debug(f"Qt[{mode_name}]: {message}")

def _install_debug_hooks():
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        fh = open(LOG_FILE, "a", encoding="utf-8")
        faulthandler.enable(file=fh, all_threads=True)
    except Exception:
        pass
    try:
        qInstallMessageHandler(_qt_message_handler)
    except Exception:
        pass
    def _excepthook(exc_type, exc, tb):
        log_debug("Unhandled exception:")
        log_debug("".join(traceback.format_exception(exc_type, exc, tb)))
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = _excepthook

# ---- PyQt6 compatibility shims (keep legacy names used across the codebase) ----
if hasattr(Qt, "Key"):
    Qt.Key_F11 = int(Qt.Key.Key_F11)
    Qt.Key_F12 = int(Qt.Key.Key_F12)
    Qt.Key_F6 = int(Qt.Key.Key_F6)
    Qt.Key_P = int(Qt.Key.Key_P)
    Qt.Key_Plus = int(Qt.Key.Key_Plus)
    Qt.Key_Minus = int(Qt.Key.Key_Minus)
    Qt.Key_0 = int(Qt.Key.Key_0)
    Qt.Key_L = int(Qt.Key.Key_L)
    Qt.ToolTip = Qt.WindowType.ToolTip
    Qt.Window = Qt.WindowType.Window
    Qt.Popup = Qt.WindowType.Popup
    Qt.FramelessWindowHint = Qt.WindowType.FramelessWindowHint
    Qt.NoPen = Qt.PenStyle.NoPen
    Qt.NoFocus = Qt.FocusPolicy.NoFocus
    Qt.NoItemFlags = Qt.ItemFlag.NoItemFlags
    Qt.ItemIsUserCheckable = Qt.ItemFlag.ItemIsUserCheckable
    Qt.LeftButton = Qt.MouseButton.LeftButton
    Qt.CustomContextMenu = Qt.ContextMenuPolicy.CustomContextMenu
    Qt.ActionsContextMenu = Qt.ContextMenuPolicy.ActionsContextMenu
    Qt.AlignLeft = Qt.AlignmentFlag.AlignLeft
    Qt.AlignRight = Qt.AlignmentFlag.AlignRight
    Qt.AlignCenter = Qt.AlignmentFlag.AlignCenter
    Qt.AlignVCenter = Qt.AlignmentFlag.AlignVCenter
    Qt.MatchContains = Qt.MatchFlag.MatchContains
    Qt.CaseInsensitive = Qt.CaseSensitivity.CaseInsensitive
    Qt.RichText = Qt.TextFormat.RichText
    Qt.TextBrowserInteraction = Qt.TextInteractionFlag.TextBrowserInteraction
    Qt.TextSelectableByMouse = Qt.TextInteractionFlag.TextSelectableByMouse
    Qt.CTRL = int(Qt.KeyboardModifier.ControlModifier.value)
    Qt.KeepAspectRatio = Qt.AspectRatioMode.KeepAspectRatio
    Qt.KeepAspectRatioByExpanding = Qt.AspectRatioMode.KeepAspectRatioByExpanding
    Qt.SmoothTransformation = Qt.TransformationMode.SmoothTransformation
    Qt.WA_ShowWithoutActivating = Qt.WidgetAttribute.WA_ShowWithoutActivating
    Qt.WA_StyledBackground = Qt.WidgetAttribute.WA_StyledBackground
    Qt.ScrollBarAlwaysOff = Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    Qt.transparent = Qt.GlobalColor.transparent
    Qt.UserRole = Qt.ItemDataRole.UserRole

if hasattr(QPainter, "RenderHint") and not hasattr(QPainter, "Antialiasing"):
    QPainter.Antialiasing = QPainter.RenderHint.Antialiasing

if not hasattr(QListWidget, "ExtendedSelection"):
    QListWidget.ExtendedSelection = QAbstractItemView.SelectionMode.ExtendedSelection
    QListWidget.SingleSelection = QAbstractItemView.SelectionMode.SingleSelection

if not hasattr(QFrame, "StyledPanel"):
    QFrame.StyledPanel = QFrame.Shape.StyledPanel

if not hasattr(QListWidget, "ScrollPerPixel"):
    QListWidget.ScrollPerPixel = QAbstractItemView.ScrollMode.ScrollPerPixel
    QListWidget.ScrollPerItem = QAbstractItemView.ScrollMode.ScrollPerItem

if not hasattr(QDialog, "exec_"):
    def _exec_dialog(self):
        return QDialog.exec(self)
    QDialog.exec_ = _exec_dialog
if not hasattr(QMessageBox, "exec_"):
    def _exec_msgbox(self):
        return QMessageBox.exec(self)
    QMessageBox.exec_ = _exec_msgbox
if not hasattr(QMenu, "exec_"):
    def _exec_menu(self, *args, **kwargs):
        return QMenu.exec(self, *args, **kwargs)
    QMenu.exec_ = _exec_menu
if not hasattr(QMessageBox, "Yes"):
    QMessageBox.Yes = QMessageBox.StandardButton.Yes
    QMessageBox.No = QMessageBox.StandardButton.No
    QMessageBox.Cancel = QMessageBox.StandardButton.Cancel
if not hasattr(QDialog, "Accepted"):
    QDialog.Accepted = QDialog.DialogCode.Accepted
    QDialog.Rejected = QDialog.DialogCode.Rejected
if not hasattr(QApplication, "exec_"):
    def _exec_app(self):
        return QApplication.exec()
    QApplication.exec_ = _exec_app
if not hasattr(QPrinter, "HighResolution"):
    QPrinter.HighResolution = QPrinter.PrinterMode.HighResolution
    QPrinter.NativeFormat = QPrinter.OutputFormat.NativeFormat
    QPrinter.PdfFormat = QPrinter.OutputFormat.PdfFormat

if hasattr(QEvent, "Type"):
    QEvent.HoverMove = QEvent.Type.HoverMove
    QEvent.HoverEnter = QEvent.Type.HoverEnter
    QEvent.HoverLeave = QEvent.Type.HoverLeave

if hasattr(QWebEngineSettings, "WebAttribute"):
    if not hasattr(QWebEngineSettings, "JavascriptEnabled"):
        QWebEngineSettings.JavascriptEnabled = QWebEngineSettings.WebAttribute.JavascriptEnabled
        QWebEngineSettings.LocalStorageEnabled = QWebEngineSettings.WebAttribute.LocalStorageEnabled
        QWebEngineSettings.WebGLEnabled = QWebEngineSettings.WebAttribute.WebGLEnabled
        QWebEngineSettings.Accelerated2dCanvasEnabled = QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled

if hasattr(QWebEngineProfile, "PersistentCookiesPolicy"):
    if not hasattr(QWebEngineProfile, "ForcePersistentCookies"):
        QWebEngineProfile.ForcePersistentCookies = QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
    if not hasattr(QWebEngineProfile, "AllowPersistentCookies"):
        QWebEngineProfile.AllowPersistentCookies = QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
    if not hasattr(QWebEngineProfile, "NoPersistentCookies"):
        QWebEngineProfile.NoPersistentCookies = QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies

if hasattr(QWebEngineProfile, "HttpCacheType"):
    if not hasattr(QWebEngineProfile, "DiskHttpCache"):
        QWebEngineProfile.DiskHttpCache = QWebEngineProfile.HttpCacheType.DiskHttpCache
    if not hasattr(QWebEngineProfile, "MemoryHttpCache"):
        QWebEngineProfile.MemoryHttpCache = QWebEngineProfile.HttpCacheType.MemoryHttpCache
    if not hasattr(QWebEngineProfile, "NoCache"):
        QWebEngineProfile.NoCache = QWebEngineProfile.HttpCacheType.NoCache

if hasattr(QWebEngineDownloadItem, "DownloadState") and not hasattr(QWebEngineDownloadItem, "DownloadInProgress"):
    QWebEngineDownloadItem.DownloadRequested = QWebEngineDownloadItem.DownloadState.DownloadRequested
    QWebEngineDownloadItem.DownloadInProgress = QWebEngineDownloadItem.DownloadState.DownloadInProgress
    QWebEngineDownloadItem.DownloadCompleted = QWebEngineDownloadItem.DownloadState.DownloadCompleted
    QWebEngineDownloadItem.DownloadCancelled = QWebEngineDownloadItem.DownloadState.DownloadCancelled
    QWebEngineDownloadItem.DownloadInterrupted = QWebEngineDownloadItem.DownloadState.DownloadInterrupted
    QWebEngineDownloadItem.DownloadPaused = getattr(QWebEngineDownloadItem.DownloadState, "DownloadPaused", QWebEngineDownloadItem.DownloadState.DownloadInProgress)


def set_webengine_attr(settings_obj, attr_name, enabled):
    attr = getattr(QWebEngineSettings, attr_name, None)
    if attr is None and hasattr(QWebEngineSettings, "WebAttribute"):
        attr = getattr(QWebEngineSettings.WebAttribute, attr_name, None)
    if attr is not None:
        settings_obj.setAttribute(attr, enabled)


def download_get_path(download):
    if download is None:
        return ""
    if hasattr(download, "path"):
        try:
            return download.path()
        except Exception:
            return ""
    try:
        directory = download.downloadDirectory()
        filename = download.downloadFileName()
        if directory:
            return os.path.join(directory, filename)
    except Exception:
        return ""
    return ""


def download_set_path(download, file_path):
    if download is None or not file_path:
        return
    if hasattr(download, "setPath"):
        try:
            download.setPath(file_path)
            return
        except Exception:
            pass
    try:
        download.setDownloadDirectory(os.path.dirname(file_path))
        download.setDownloadFileName(os.path.basename(file_path))
    except Exception:
        pass


def download_get_filename(download):
    path = download_get_path(download)
    if path:
        return os.path.basename(path)
    try:
        return download.downloadFileName() or "download"
    except Exception:
        return "download"


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _bytes_to_blob(data):
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def protect_bytes(data, description="Aurora Password Vault"):
    if os.name != "nt":
        return data
    if not isinstance(data, (bytes, bytearray)):
        data = bytes(data)
    in_blob, in_buffer = _bytes_to_blob(data)
    out_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        description,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def unprotect_bytes(data):
    if os.name != "nt":
        return data
    if not isinstance(data, (bytes, bytearray)):
        data = bytes(data)
    in_blob, in_buffer = _bytes_to_blob(data)
    out_blob = DATA_BLOB()
    description = wintypes.LPWSTR()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        ctypes.byref(description),
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        if description:
            ctypes.windll.kernel32.LocalFree(description)
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def is_youtube_url(url_value):
    raw = url_value.toString() if hasattr(url_value, "toString") else str(url_value)
    try:
        host = (urlparse(raw).hostname or "").lower()
    except Exception:
        return False
    return host == "youtube.com" or host.endswith(".youtube.com")


def prettify_host_label(url_value):
    try:
        host = (urlparse(url_value).hostname or "").lower()
    except Exception:
        host = ""
    if not host:
        return "Quick Link"
    if host.startswith("www."):
        host = host[4:]
    base = host.split(".")[0]
    replacements = {
        "youtube": "YouTube",
        "gmail": "Gmail",
        "google": "Google",
        "github": "GitHub",
        "stackoverflow": "Stack Overflow",
        "chatgpt": "ChatGPT",
        "reddit": "Reddit",
        "linkedin": "LinkedIn",
        "x": "X",
    }
    return replacements.get(base, base.replace("-", " ").replace("_", " ").title())


def normalize_quick_access_title(title, url_value):
    if not title:
        return prettify_host_label(url_value)
    stripped = title.strip()
    lowered = stripped.lower()
    try:
        parsed = urlparse(url_value)
        host = (parsed.hostname or "").lower()
    except Exception:
        host = ""
    host_variants = {host, f"www.{host}" if host else ""}
    if lowered.startswith(("http://", "https://")) or lowered in host_variants:
        return prettify_host_label(url_value)
    return stripped


def aux_window_stylesheet():
    return """
        QWidget, QDialog, QMainWindow {
            background-color: #091323;
            color: #edf4ff;
            font-family: "Segoe UI";
            font-size: 13px;
        }
        QListWidget, QLineEdit, QComboBox {
            background-color: #0d1a30;
            color: #edf4ff;
            border: 1px solid #24395f;
            border-radius: 14px;
            padding: 8px 10px;
            selection-color: #06101d;
            selection-background-color: #7bd2ff;
        }
        QLineEdit {
            min-height: 22px;
            font-size: 14px;
            font-weight: 600;
        }
        QLineEdit[echoMode="0"] {
            color: #edf4ff;
        }
        QLineEdit::placeholder {
            color: #b7c9e6;
        }
        QListWidget::item {
            color: #edf4ff;
            padding: 8px 10px;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }
        QListWidget::item:selected {
            background-color: rgba(121, 216, 255, 0.18);
            color: #ffffff;
            border-radius: 10px;
        }
        QAbstractItemView {
            color: #edf4ff;
            alternate-background-color: #10203a;
            selection-color: #06101d;
            selection-background-color: #7bd2ff;
        }
        QPushButton {
            background-color: #13233f;
            color: #edf4ff;
            border: 1px solid #26426f;
            border-radius: 14px;
            padding: 8px 14px;
            font-weight: 700;
            min-height: 34px;
        }
        QPushButton:hover {
            background-color: #193055;
            border-color: #3e6db2;
        }
        QPushButton:pressed {
            background-color: #10203a;
        }
        QLabel, QCheckBox {
            color: #edf4ff;
        }
    """


def style_aux_window(widget):
    widget.setStyleSheet(aux_window_stylesheet())


def show_styled_question(parent, title, text):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setModal(True)
    dialog.setMinimumWidth(460)
    dialog.setStyleSheet("""
        QDialog {
            background: #08111f;
            color: #edf4ff;
        }
        QLabel {
            background: transparent;
            color: #edf4ff;
        }
        QLabel#titleLabel {
            font-size: 15px;
            font-weight: 700;
        }
        QLabel#bodyLabel {
            font-size: 13px;
            color: #d9e7ff;
        }
        QFrame#panel {
            background: #091728;
            border: 1px solid rgba(112, 160, 255, 0.24);
            border-radius: 16px;
        }
        QPushButton {
            min-width: 88px;
            min-height: 34px;
            background-color: #13233f;
            color: #edf4ff;
            border: 1px solid #26426f;
            border-radius: 10px;
            padding: 4px 10px;
            font-weight: 700;
        }
        QPushButton:hover {
            background-color: #193055;
        }
        QPushButton#danger {
            background-color: #324fbd;
            border-color: #5d80ff;
        }
    """)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 18, 18, 18)
    panel = QFrame()
    panel.setObjectName("panel")
    panel_layout = QVBoxLayout(panel)
    panel_layout.setContentsMargins(18, 16, 18, 16)
    panel_layout.setSpacing(12)
    title_label = QLabel(title)
    title_label.setObjectName("titleLabel")
    body_label = QLabel(text)
    body_label.setObjectName("bodyLabel")
    body_label.setWordWrap(True)
    button_row = QHBoxLayout()
    button_row.addStretch()
    no_button = QPushButton("No")
    yes_button = QPushButton("Yes")
    yes_button.setObjectName("danger")
    no_button.clicked.connect(dialog.reject)
    yes_button.clicked.connect(dialog.accept)
    button_row.addWidget(no_button)
    button_row.addWidget(yes_button)
    panel_layout.addWidget(title_label)
    panel_layout.addWidget(body_label)
    panel_layout.addLayout(button_row)
    layout.addWidget(panel)
    return QMessageBox.Yes if dialog.exec_() == QDialog.Accepted else QMessageBox.No


def show_styled_text_input(parent, title, label, text=""):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setModal(True)
    dialog.setMinimumWidth(420)
    dialog.setStyleSheet("""
        QDialog { background: #08111f; color: #edf4ff; }
        QLabel { background: transparent; color: #edf4ff; }
        QLineEdit {
            min-height: 38px;
            padding: 0 12px;
            border-radius: 12px;
            border: 1px solid rgba(112, 160, 255, 0.24);
            background: rgba(5, 13, 28, 0.96);
            color: #edf4ff;
        }
        QPushButton {
            min-width: 88px;
            min-height: 34px;
            background-color: #13233f;
            color: #edf4ff;
            border: 1px solid #26426f;
            border-radius: 10px;
            padding: 4px 10px;
            font-weight: 700;
        }
        QPushButton:hover { background-color: #193055; }
    """)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 18, 18, 18)
    layout.setSpacing(12)
    layout.addWidget(QLabel(label))
    line_edit = QLineEdit()
    line_edit.setText(text)
    line_edit.selectAll()
    layout.addWidget(line_edit)
    buttons = QHBoxLayout()
    buttons.addStretch()
    cancel_button = QPushButton("Cancel")
    ok_button = QPushButton("OK")
    cancel_button.clicked.connect(dialog.reject)
    ok_button.clicked.connect(dialog.accept)
    buttons.addWidget(cancel_button)
    buttons.addWidget(ok_button)
    layout.addLayout(buttons)
    if dialog.exec_() == QDialog.Accepted:
        return line_edit.text(), True
    return text, False


def show_styled_item_picker(parent, title, label, items, current_index=0, editable=False):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setModal(True)
    dialog.setMinimumWidth(420)
    dialog.setStyleSheet("""
        QDialog { background: #08111f; color: #edf4ff; }
        QLabel { background: transparent; color: #edf4ff; }
        QComboBox, QLineEdit {
            min-height: 38px;
            padding: 0 12px;
            border-radius: 12px;
            border: 1px solid rgba(112, 160, 255, 0.24);
            background: rgba(5, 13, 28, 0.96);
            color: #edf4ff;
        }
        QPushButton {
            min-width: 88px;
            min-height: 34px;
            background-color: #13233f;
            color: #edf4ff;
            border: 1px solid #26426f;
            border-radius: 10px;
            padding: 4px 10px;
            font-weight: 700;
        }
        QPushButton:hover { background-color: #193055; }
    """)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 18, 18, 18)
    layout.setSpacing(12)
    layout.addWidget(QLabel(label))
    combo = QComboBox()
    combo.setEditable(editable)
    combo.addItems(items)
    combo.setCurrentIndex(max(0, min(current_index, len(items) - 1))) if items else None
    layout.addWidget(combo)
    buttons = QHBoxLayout()
    buttons.addStretch()
    cancel_button = QPushButton("Cancel")
    ok_button = QPushButton("OK")
    cancel_button.clicked.connect(dialog.reject)
    ok_button.clicked.connect(dialog.accept)
    buttons.addWidget(cancel_button)
    buttons.addWidget(ok_button)
    layout.addLayout(buttons)
    if dialog.exec_() == QDialog.Accepted:
        return combo.currentText(), True
    return combo.currentText(), False


def restart_application(extra_args=None):
    app = QApplication.instance()
    executable = sys.executable
    args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
    if extra_args:
        args = list(args) + list(extra_args)
    started = QProcess.startDetached(executable, args)
    if app is not None and started:
        QTimer.singleShot(0, app.quit)
    return started


def ensure_profile_dir(profile_name):
    profile_dir = os.path.join(os.path.expanduser("~"), ".aurora_browser", profile_name)
    os.makedirs(profile_dir, exist_ok=True)
    return profile_dir


def get_profile_avatar_path(profile_name):
    profile_dir = ensure_profile_dir(profile_name)
    for extension in ("png", "jpg", "jpeg", "bmp", "webp"):
        candidate = os.path.join(profile_dir, f"avatar.{extension}")
        if os.path.exists(candidate):
            return candidate
    return ""


def set_profile_avatar(profile_name, source_path):
    if not profile_name:
        return False, "Choose a profile first."
    source_path = (source_path or "").strip()
    if not source_path or not os.path.exists(source_path):
        return False, "Selected image was not found."
    profile_dir = ensure_profile_dir(profile_name)
    for extension in ("png", "jpg", "jpeg", "bmp", "webp"):
        existing = os.path.join(profile_dir, f"avatar.{extension}")
        if os.path.exists(existing):
            try:
                os.remove(existing)
            except Exception:
                pass
    ext = os.path.splitext(source_path)[1].lower().lstrip(".") or "png"
    if ext not in {"png", "jpg", "jpeg", "bmp", "webp"}:
        ext = "png"
    destination = os.path.join(profile_dir, f"avatar.{ext}")
    try:
        shutil.copy2(source_path, destination)
        return True, destination
    except Exception as exc:
        return False, str(exc)


def build_profile_avatar_pixmap(profile_name, size=76):
    custom_path = get_profile_avatar_path(profile_name)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    if custom_path:
        loaded = QPixmap(custom_path)
        if not loaded.isNull():
            scaled = loaded.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            painter.setBrush(QBrush(scaled))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(pixmap.rect(), 18, 18)
            painter.end()
            return pixmap
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#16374b"))
    painter.drawRoundedRect(pixmap.rect(), 18, 18)
    painter.setPen(QColor("#edf4ff"))
    font = painter.font()
    font.setBold(True)
    font.setPointSize(max(12, int(size / 3.2)))
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, (profile_name[:2] or "AU").upper())
    painter.end()
    return pixmap


def get_app_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resolve_app_resource(*relative_parts):
    candidates = []
    base_dir = get_app_base_dir()
    candidates.append(os.path.join(base_dir, *relative_parts))
    candidates.append(os.path.join(base_dir, "_internal", *relative_parts))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, *relative_parts))
    candidates.append(os.path.join(os.path.expanduser("~"), *relative_parts))
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


def configure_windows_app_id():
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AarushPandey.AuroraBrowser")
    except Exception:
        pass


def sanitize_weather_label(city):
    return (city or "Weather").strip()


def delete_profile_data(profile_name):
    profile_name = (profile_name or "").strip()
    if not profile_name or profile_name.lower() == "default":
        return False, "The default profile cannot be deleted."
    home_dir = os.path.expanduser("~")
    profile_dir = os.path.join(home_dir, ".aurora_browser", profile_name)
    history_file = os.path.join(home_dir, f"my_browser_history_{profile_name}.json")
    profile_file = os.path.join(home_dir, "profile.txt")
    try:
        if os.path.isdir(profile_dir):
            shutil.rmtree(profile_dir, ignore_errors=False)
        if os.path.exists(history_file):
            os.remove(history_file)
        if os.path.exists(profile_file):
            with open(profile_file, "r", encoding="utf-8") as f:
                stored_profile = f.read().strip()
            if stored_profile == profile_name:
                with open(profile_file, "w", encoding="utf-8") as f:
                    f.write("default")
        return True, ""
    except Exception as exc:
        return False, str(exc)


def rename_profile_data(old_name, new_name):
    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()
    if not old_name or not new_name:
        return False, "Profile names cannot be empty."
    if old_name.lower() == "default":
        return False, "The default profile cannot be renamed."
    if new_name.lower() == "default":
        return False, "That profile name is reserved."
    if old_name == new_name:
        return False, "Choose a different profile name."
    existing = set(discover_profiles())
    if new_name in existing:
        return False, "A profile with that name already exists."
    home_dir = os.path.expanduser("~")
    old_dir = os.path.join(home_dir, ".aurora_browser", old_name)
    new_dir = os.path.join(home_dir, ".aurora_browser", new_name)
    old_history = os.path.join(home_dir, f"my_browser_history_{old_name}.json")
    new_history = os.path.join(home_dir, f"my_browser_history_{new_name}.json")
    profile_file = os.path.join(home_dir, "profile.txt")
    try:
        if os.path.isdir(old_dir):
            os.rename(old_dir, new_dir)
        else:
            os.makedirs(new_dir, exist_ok=True)
        if os.path.exists(old_history):
            os.rename(old_history, new_history)
        if os.path.exists(profile_file):
            with open(profile_file, "r", encoding="utf-8") as f:
                stored_profile = f.read().strip()
            if stored_profile == old_name:
                with open(profile_file, "w", encoding="utf-8") as f:
                    f.write(new_name)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def duplicate_profile_data(source_name, target_name):
    source_name = (source_name or "").strip()
    target_name = (target_name or "").strip()
    if not source_name or not target_name:
        return False, "Profile names cannot be empty."
    if target_name.lower() == "default":
        return False, "That profile name is reserved."
    existing = set(discover_profiles())
    if target_name in existing:
        return False, "A profile with that name already exists."
    home_dir = os.path.expanduser("~")
    source_dir = os.path.join(home_dir, ".aurora_browser", source_name)
    target_dir = os.path.join(home_dir, ".aurora_browser", target_name)
    source_history = os.path.join(home_dir, f"my_browser_history_{source_name}.json")
    target_history = os.path.join(home_dir, f"my_browser_history_{target_name}.json")
    try:
        os.makedirs(target_dir, exist_ok=True)
        if os.path.isdir(source_dir):
            for item_name in os.listdir(source_dir):
                src = os.path.join(source_dir, item_name)
                dst = os.path.join(target_dir, item_name)
                if os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
        if os.path.exists(source_history):
            shutil.copy2(source_history, target_history)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def stop_and_dispose_webview(webview, replace_page=True):
    if webview is None:
        return
    old_page = None
    try:
        webview.setAudioMuted(True)
    except Exception:
        pass
    try:
        if hasattr(webview, "stop"):
            webview.stop()
    except Exception:
        pass
    try:
        old_page = webview.page()
    except Exception:
        old_page = None
    try:
        if old_page is not None:
            old_page.triggerAction(QWebEnginePage.WebAction.Stop)
    except Exception:
        pass
    try:
        webview.setHtml("", QUrl("about:blank"))
    except Exception:
        try:
            webview.setUrl(QUrl("about:blank"))
        except Exception:
            pass
    if replace_page:
        try:
            if old_page is not None:
                replacement_page = QWebEnginePage(old_page.profile(), webview)
                webview.setPage(replacement_page)
        except Exception:
            pass
    try:
        if old_page is not None:
            old_page.deleteLater()
    except Exception:
        pass
    try:
        webview.deleteLater()
    except Exception:
        pass


def iter_window_audio_browsers(window):
    seen = set()
    tab_widget = getattr(window, "tab_widget", None)
    if tab_widget is not None:
        try:
            for index in range(tab_widget.count()):
                browser = tab_widget.widget(index)
                if browser is None or id(browser) in seen:
                    continue
                seen.add(id(browser))
                yield browser
        except Exception:
            pass
    for child in list(getattr(window, "child_windows", []) or []):
        try:
            browser = child.current_browser()
        except Exception:
            browser = None
        if browser is None or id(browser) in seen:
            continue
        seen.add(id(browser))
        yield browser


def sync_window_soundscape_with_browser_audio(window):
    any_audible = False
    for browser in iter_window_audio_browsers(window):
        try:
            recently_audible = getattr(browser.page(), "recentlyAudible", None)
            if callable(recently_audible) and recently_audible():
                any_audible = True
                break
        except Exception:
            continue
    setattr(window, "browser_audio_active", any_audible)
    if pygame is None or not getattr(window, "soundscape_enabled", False):
        return
    try:
        if not pygame.mixer.get_init():
            return
        if any_audible:
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.pause()
                setattr(window, "soundscape_paused_for_browser_audio", True)
        elif getattr(window, "soundscape_paused_for_browser_audio", False):
            pygame.mixer.music.unpause()
            setattr(window, "soundscape_paused_for_browser_audio", False)
            if not pygame.mixer.music.get_busy() and hasattr(window, "update_soundscape"):
                window.update_soundscape()
    except Exception:
        pass

# ----------------- SecurityInterceptor -----------------
DEFAULT_BLOCKED_AD_HOSTS = {
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "adservice.google.com",
    "adservice.google.co.in",
    "adnxs.com",
    "adroll.com",
    "adskeeper.co.uk",
    "adskeeper.com",
    "criteo.com",
    "criteo.net",
    "exoclick.com",
    "mgid.com",
    "outbrain.com",
    "popads.net",
    "popcash.net",
    "propellerads.com",
    "rtbhouse.com",
    "taboola.com",
}

DEFAULT_BLOCKED_URL_KEYWORDS = (
    "doubleclick",
    "googlesyndication",
    "googleadservices",
    "adservice",
    "adskeeper",
    "adroll",
    "adnxs",
    "criteo",
    "exoclick",
    "mgid",
    "outbrain",
    "popads",
    "popcash",
    "popunder",
    "propellerads",
    "redirect-to",
    "tracking",
)


def host_matches_rule(host, rule):
    host = (host or "").lower().strip(".")
    rule = (rule or "").lower().strip(".")
    if not host or not rule:
        return False
    return host == rule or host.endswith("." + rule)

def host_matches_fragment(host, fragments):
    normalized = (host or "").lower()
    if not normalized:
        return False
    return any(fragment and fragment in normalized for fragment in fragments)


class SecurityInterceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, media_request_callback=None):
        super().__init__()
        self.do_not_track = False
        self.ad_blocker_enabled = True
        self.popup_blocker_enabled = True
        self.blocked_request_count = 0
        self.allowlisted_hosts = set()
        self.blocked_ad_hosts = set(DEFAULT_BLOCKED_AD_HOSTS)
        self.blocked_url_keywords = tuple(DEFAULT_BLOCKED_URL_KEYWORDS)
        self.media_request_callback = media_request_callback

    def set_allowlist(self, hosts):
        cleaned = set()
        for host in hosts or []:
            normalized = (host or "").strip().lower()
            if normalized:
                cleaned.add(normalized)
        self.allowlisted_hosts = cleaned

    def is_allowlisted(self, host):
        normalized = (host or "").strip().lower()
        if not normalized:
            return False
        return any(host_matches_rule(normalized, rule) for rule in self.allowlisted_hosts)

    def should_block_url(self, request_url, first_party_url=None):
        try:
            target = request_url if isinstance(request_url, QUrl) else QUrl(str(request_url))
            first_party = first_party_url if isinstance(first_party_url, QUrl) else QUrl(str(first_party_url or ""))
            target_host = (target.host() or "").lower()
            first_party_host = (first_party.host() or "").lower()
            if not target_host:
                return False
            media_sensitive_site = host_matches_fragment(first_party_host, MEDIA_COMPATIBILITY_HOST_FRAGMENTS)
            media_sensitive_target = host_matches_fragment(target_host, MEDIA_COMPATIBILITY_HOST_FRAGMENTS)
            if media_sensitive_site or media_sensitive_target:
                return False
            if self.is_allowlisted(target_host) or self.is_allowlisted(first_party_host):
                return False
            if any(host_matches_rule(target_host, rule) for rule in self.blocked_ad_hosts):
                return True
            target_text = target.toString().lower()
            media_hints = (
                ".m3u8", ".mpd", ".mp4", ".m4s", ".webm", ".mp3", ".aac",
                "cloudflarestream.com", "videodelivery.net", "manifest", "playlist",
            )
            if any(hint in target_text for hint in media_hints):
                return False
            if first_party_host and target_host == first_party_host:
                return False
            return any(keyword in target_text for keyword in self.blocked_url_keywords)
        except Exception:
            return False

    def interceptRequest(self, info):
        if self.do_not_track:
            info.setHttpHeader(b"DNT", b"1")
        # Never block requests from Google auth/login pages
        try:
            first_party_for_block = info.firstPartyUrl() if hasattr(info, "firstPartyUrl") else QUrl()
            fp_host = (first_party_for_block.host() or "").lower()
            if is_google_auth_host(fp_host) or fp_host.endswith(".accounts.google.com"):
                return
        except Exception:
            pass
        if ENABLE_CUSTOM_REQUEST_HEADER_PATCHES:
            try:
                host = (info.requestUrl().host() or "").lower()
                first_party = info.firstPartyUrl() if hasattr(info, "firstPartyUrl") else QUrl()
                first_party_host = (first_party.host() or "").lower()
                target_text = info.requestUrl().toString().lower()
                media_sensitive_request = (
                    host_matches_fragment(host, MEDIA_COMPATIBILITY_HOST_FRAGMENTS)
                    or host_matches_fragment(first_party_host, MEDIA_COMPATIBILITY_HOST_FRAGMENTS)
                    or any(hint in target_text for hint in (".m3u8", ".mpd", ".m4s", ".mp4", ".webm", "playlist", "manifest"))
                )
                google_auth_request = is_google_auth_host(host) or is_google_auth_host(first_party_host)
                if host.endswith("google.com") and not google_auth_request:
                    info.setHttpHeader(b"X-Requested-With", b"")
                    info.setHttpHeader(
                        b"User-Agent",
                        DEFAULT_USER_AGENT.encode("utf-8"),
                    )
                    info.setHttpHeader(b"Accept-Language", b"en-US,en;q=0.9")
                    for header_name, header_value in GOOGLE_CLIENT_HINT_HEADERS.items():
                        info.setHttpHeader(header_name, header_value)
                if media_sensitive_request:
                    should_capture_media = (
                        self.media_request_callback is not None
                        and any(marker in target_text for marker in (".m3u8", ".mpd", ".mp4", ".webm", "playlist", "manifest"))
                        and not any(marker in target_text for marker in (".m4s", ".ts", ".aac", ".vtt"))
                    )
                    if should_capture_media:
                        try:
                            self.media_request_callback({
                                "url": info.requestUrl().toString(),
                                "first_party_url": first_party.toString() if first_party.isValid() else "",
                                "host": host,
                                "first_party_host": first_party_host,
                                "referer": first_party.toString() if first_party.isValid() else "",
                                "origin": f"{first_party.scheme()}://{first_party.host()}" if first_party.isValid() else "",
                                "captured_at": time.time(),
                            })
                        except Exception:
                            pass
                    info.setHttpHeader(b"User-Agent", DEFAULT_USER_AGENT.encode("utf-8"))
                    info.setHttpHeader(b"Accept-Language", b"en-US,en;q=0.9")
                    info.setHttpHeader(
                        b"Accept",
                        b"*/*,application/vnd.apple.mpegurl,application/x-mpegURL,application/dash+xml,video/mp4"
                    )
                    if first_party.isValid():
                        first_party_text = first_party.toString().encode("utf-8", errors="ignore")
                        origin_text = f"{first_party.scheme()}://{first_party.host()}".encode("utf-8", errors="ignore")
                        info.setHttpHeader(b"Referer", first_party_text)
                        info.setHttpHeader(b"Origin", origin_text)
            except Exception:
                pass
        if self.ad_blocker_enabled:
            try:
                first_party = info.firstPartyUrl() if hasattr(info, "firstPartyUrl") else QUrl()
            except Exception:
                first_party = QUrl()
            if self.should_block_url(info.requestUrl(), first_party):
                self.blocked_request_count += 1
                info.block(True)

# ----------------- CustomTabBar -----------------
class CustomTabBar(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setUsesScrollButtons(True)
        self.preview_pixmap = None
        self.preview_label = QLabel(self)
        self.preview_label.setWindowFlags(Qt.ToolTip)
        self.preview_label.setVisible(False)
        self.hover_timer = QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.timeout.connect(self.show_preview)
        self.current_hover_index = -1

    def event(self, event):
        if event.type() == QEvent.HoverMove or event.type() == QEvent.HoverEnter:
            index = self.tabBar().tabAt(event.pos())
            if index >= 0 and index != self.current_hover_index:
                self.current_hover_index = index
                self.hover_timer.start(500)
            return True
        elif event.type() == QEvent.HoverLeave:
            self.hide_preview()
            self.current_hover_index = -1
            return True
        return super().event(event)

    def show_preview(self):
        if self.current_hover_index >= 0 and self.current_hover_index < self.count():
            tab = self.widget(self.current_hover_index)
            if isinstance(tab, QWebEngineView):
                pixmap = tab.grab()
                self.preview_pixmap = pixmap.scaled(200, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview_label.setPixmap(self.preview_pixmap)
                pos = self.mapToGlobal(self.tabBar().tabRect(self.current_hover_index).topRight()) + QPoint(10, 10)
                self.preview_label.move(pos)
                self.preview_label.adjustSize()
                self.preview_label.show()
        else:
            self.hide_preview()

    def hide_preview(self):
        if self.preview_label.isVisible():
            self.preview_label.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.preview_label.isVisible():
            self.show_preview()


def discover_profiles():
    home_dir = os.path.expanduser("~")
    profile_root = os.path.join(home_dir, ".aurora_browser")
    profiles = []
    if os.path.isdir(profile_root):
        for name in os.listdir(profile_root):
            full_path = os.path.join(profile_root, name)
            if os.path.isdir(full_path):
                profiles.append(name)
    legacy_history_files = glob.glob(os.path.join(home_dir, "my_browser_history_*.json"))
    for file_path in legacy_history_files:
        base = os.path.basename(file_path)
        if base.startswith("my_browser_history_") and base.endswith(".json"):
            profiles.append(base[len("my_browser_history_"):-len(".json")])
    if "default" not in profiles:
        profiles.append("default")
    if "Guest" not in profiles:
        profiles.append("Guest")
    return sorted(set(filter(None, profiles)))


class ProfilePickerDialog(QDialog):
    def __init__(self, profiles, current_profile="default", parent=None, active_profile=None):
        super().__init__(parent)
        self.active_profile = (active_profile or "").strip()
        self.setWindowTitle("Launch Aurora")
        self.setModal(True)
        self.setMinimumSize(520, 360)
        self.setStyleSheet("""
            QDialog {
                background: #08111f;
                color: #edf4ff;
            }
            QLabel {
                background: transparent;
            }
            QLabel#heroTitle {
                font-size: 28px;
                font-weight: 800;
                color: #f5f9ff;
            }
            QLabel#heroSub {
                color: #99afd1;
                font-size: 13px;
            }
            QLabel#sectionTitle {
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 1px;
                color: #88b9ff;
            }
            QFrame#heroPanel, QFrame#cardPanel {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(16, 31, 58, 0.95), stop:1 rgba(7, 16, 32, 0.98));
                border: 1px solid rgba(112, 160, 255, 0.22);
                border-radius: 22px;
            }
            QComboBox, QLineEdit {
                min-height: 42px;
                padding: 0 14px;
                border-radius: 14px;
                border: 1px solid rgba(112, 160, 255, 0.24);
                background: rgba(5, 13, 28, 0.96);
                color: #edf4ff;
            }
            QPushButton {
                min-height: 40px;
                border-radius: 14px;
                padding: 0 18px;
                font-weight: 700;
            }
            QPushButton#openBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ffd96a, stop:1 #63e5d0);
                color: #061120;
                border: none;
            }
            QPushButton#cancelBtn {
                background: rgba(255,255,255,0.05);
                color: #d6e4ff;
                border: 1px solid rgba(255,255,255,0.12);
            }
            QPushButton#dangerBtn {
                background: rgba(220, 87, 104, 0.12);
                color: #ffdbe1;
                border: 1px solid rgba(220, 87, 104, 0.34);
            }
            QPushButton#utilityBtn {
                background: rgba(255,255,255,0.05);
                color: #d6e4ff;
                border: 1px solid rgba(255,255,255,0.12);
            }
            QLabel#avatarFrame {
                min-width: 78px;
                min-height: 78px;
                max-width: 78px;
                max-height: 78px;
                border-radius: 18px;
                border: 1px solid rgba(112, 160, 255, 0.24);
                background: rgba(5, 13, 28, 0.96);
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        hero_panel = QFrame()
        hero_panel.setObjectName("heroPanel")
        hero_layout = QVBoxLayout(hero_panel)
        hero_layout.setContentsMargins(24, 22, 24, 22)
        hero_layout.setSpacing(10)
        eyebrow = QLabel("AURORA")
        eyebrow.setObjectName("sectionTitle")
        title = QLabel("Open the browser with the right profile")
        title.setObjectName("heroTitle")
        subtitle = QLabel("Each profile keeps its own history, bookmarks, session, homepage, quick access, and browser storage.")
        subtitle.setObjectName("heroSub")
        subtitle.setWordWrap(True)
        hero_layout.addWidget(eyebrow)
        hero_layout.addWidget(title)
        hero_layout.addWidget(subtitle)

        card_panel = QFrame()
        card_panel.setObjectName("cardPanel")
        card_layout = QVBoxLayout(card_panel)
        card_layout.setContentsMargins(24, 22, 24, 22)
        card_layout.setSpacing(12)
        pick_label = QLabel("PROFILE")
        pick_label.setObjectName("sectionTitle")
        avatar_row = QHBoxLayout()
        avatar_row.setSpacing(12)
        self.avatar_label = QLabel()
        self.avatar_label.setObjectName("avatarFrame")
        self.avatar_label.setAlignment(Qt.AlignCenter)
        avatar_meta = QVBoxLayout()
        avatar_meta.setSpacing(6)
        avatar_title = QLabel("Profile picture")
        avatar_title.setStyleSheet("font-size: 14px; font-weight: 700; color: #edf4ff;")
        avatar_hint = QLabel("Add a custom image for the selected profile.")
        avatar_hint.setObjectName("heroSub")
        self.avatar_button = QPushButton("Change Picture")
        self.avatar_button.setObjectName("utilityBtn")
        self.avatar_button.clicked.connect(self.choose_profile_picture)
        avatar_meta.addWidget(avatar_title)
        avatar_meta.addWidget(avatar_hint)
        avatar_meta.addWidget(self.avatar_button, 0, Qt.AlignLeft)
        avatar_row.addWidget(self.avatar_label)
        avatar_row.addLayout(avatar_meta, 1)
        self.profile_combo = QComboBox()
        self.profile_combo.addItems(profiles + ["New Profile..."])
        if current_profile in profiles:
            self.profile_combo.setCurrentText(current_profile)
        self.new_profile_input = QLineEdit()
        self.new_profile_input.setPlaceholderText("Enter new profile name")
        self.new_profile_input.hide()
        tips = QLabel("Tip: use different profiles for separate school, work, and personal sessions.")
        tips.setObjectName("heroSub")
        card_layout.addWidget(pick_label)
        card_layout.addLayout(avatar_row)
        card_layout.addWidget(self.profile_combo)
        card_layout.addWidget(self.new_profile_input)
        card_layout.addWidget(tips)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        delete_button = QPushButton("Delete Profile")
        delete_button.setObjectName("dangerBtn")
        rename_button = QPushButton("Rename")
        rename_button.setObjectName("utilityBtn")
        duplicate_button = QPushButton("Duplicate")
        duplicate_button.setObjectName("utilityBtn")
        open_button = QPushButton("Open")
        open_button.setObjectName("openBtn")
        cancel_button = QPushButton("Cancel")
        cancel_button.setObjectName("cancelBtn")
        button_row.addWidget(delete_button)
        button_row.addWidget(rename_button)
        button_row.addWidget(duplicate_button)
        button_row.addStretch()
        button_row.addWidget(cancel_button)
        button_row.addWidget(open_button)

        layout.addWidget(hero_panel)
        layout.addWidget(card_panel)
        layout.addLayout(button_row)
        self.profile_combo.currentTextChanged.connect(self._toggle_new_profile_input)
        self.new_profile_input.textChanged.connect(lambda _text: self.refresh_avatar_preview())
        delete_button.clicked.connect(self.delete_selected_profile)
        rename_button.clicked.connect(self.rename_selected_profile)
        duplicate_button.clicked.connect(self.duplicate_selected_profile)
        open_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        self.refresh_avatar_preview()

    def _toggle_new_profile_input(self, value):
        is_new = value == "New Profile..."
        self.new_profile_input.setVisible(is_new)
        self.refresh_avatar_preview()
        if is_new:
            self.new_profile_input.setFocus()

    def selected_profile(self):
        selected = self.profile_combo.currentText()
        if selected == "New Profile...":
            return self.new_profile_input.text().strip()
        return selected.strip()

    def avatar_target_profile(self):
        selected = self.profile_combo.currentText().strip()
        if selected == "New Profile...":
            return self.new_profile_input.text().strip()
        return selected

    def refresh_avatar_preview(self):
        profile_name = self.avatar_target_profile() or self.profile_combo.currentText().strip() or "AU"
        self.avatar_label.setPixmap(build_profile_avatar_pixmap(profile_name))
        is_existing = bool(profile_name) and self.profile_combo.currentText().strip() != "New Profile..."
        self.avatar_button.setEnabled(is_existing)
        self.avatar_button.setText("Change Picture" if is_existing else "Create profile first")

    def choose_profile_picture(self):
        profile_name = self.avatar_target_profile()
        if not profile_name or self.profile_combo.currentText().strip() == "New Profile...":
            QMessageBox.information(self, "Profile Picture", "Create the profile first, then add a picture.")
            return
        file_path, _ = QFileDialog.getOpenFileName(self, "Choose Profile Picture", "", "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not file_path:
            return
        success, result = set_profile_avatar(profile_name, file_path)
        if not success:
            QMessageBox.warning(self, "Profile Picture", f"Could not save picture: {result}")
            return
        self.refresh_avatar_preview()
        QMessageBox.information(self, "Profile Picture", f"Updated picture for '{profile_name}'.")

    def delete_selected_profile(self):
        selected = self.profile_combo.currentText().strip()
        if not selected or selected == "New Profile...":
            QMessageBox.information(self, "Delete Profile", "Select an existing profile first.")
            return
        if selected.lower() == "default":
            QMessageBox.information(self, "Delete Profile", "The default profile cannot be deleted.")
            return
        if self.active_profile and selected == self.active_profile:
            QMessageBox.information(self, "Delete Profile", "Switch away from the active profile before deleting it.")
            return
        confirm = show_styled_question(
            self,
            "Delete Profile",
            f"Delete profile '{selected}' and its saved data?\n\nThis removes bookmarks, session, homepage, quick access, extensions, and browser storage for that profile.",
        )
        if confirm != QMessageBox.Yes:
            return
        success, error_message = delete_profile_data(selected)
        if not success:
            QMessageBox.warning(self, "Delete Profile", f"Could not delete '{selected}': {error_message}")
            return
        next_profile = self.active_profile if self.active_profile and self.active_profile != selected else "default"
        self.reload_profiles(next_profile)
        QMessageBox.information(self, "Delete Profile", f"Profile '{selected}' was deleted.")

    def rename_selected_profile(self):
        selected = self.profile_combo.currentText().strip()
        if not selected or selected == "New Profile...":
            QMessageBox.information(self, "Rename Profile", "Select an existing profile first.")
            return
        if selected.lower() == "default":
            QMessageBox.information(self, "Rename Profile", "The default profile cannot be renamed.")
            return
        new_name, ok = show_styled_text_input(self, "Rename Profile", "New profile name:", selected)
        if not ok or not new_name.strip():
            return
        success, error_message = rename_profile_data(selected, new_name.strip())
        if not success:
            QMessageBox.warning(self, "Rename Profile", f"Could not rename '{selected}': {error_message}")
            return
        self.active_profile = new_name.strip() if self.active_profile == selected else self.active_profile
        self.reload_profiles(new_name.strip())
        QMessageBox.information(self, "Rename Profile", f"Profile '{selected}' was renamed to '{new_name.strip()}'.")

    def duplicate_selected_profile(self):
        selected = self.profile_combo.currentText().strip()
        if not selected or selected == "New Profile...":
            QMessageBox.information(self, "Duplicate Profile", "Select an existing profile first.")
            return
        new_name, ok = show_styled_text_input(self, "Duplicate Profile", "New profile name:")
        if not ok or not new_name.strip():
            return
        success, error_message = duplicate_profile_data(selected, new_name.strip())
        if not success:
            QMessageBox.warning(self, "Duplicate Profile", f"Could not duplicate '{selected}': {error_message}")
            return
        self.reload_profiles(new_name.strip())
        QMessageBox.information(self, "Duplicate Profile", f"Profile '{selected}' was copied to '{new_name.strip()}'.")

    def reload_profiles(self, selected_profile=None):
        profiles = discover_profiles()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems(profiles + ["New Profile..."])
        target = selected_profile or self.active_profile or "default"
        if target in profiles:
            self.profile_combo.setCurrentText(target)
        self.profile_combo.blockSignals(False)
        self._toggle_new_profile_input(self.profile_combo.currentText())

class AuroraPage(QWebEnginePage):
    def __init__(self, profile, view=None):
        super().__init__(profile, view)
        self._view = view

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        try:
            text = str(message or "")
            if text.startswith("AURORA_MEDIA_CANDIDATE:"):
                payload = text.split("AURORA_MEDIA_CANDIDATE:", 1)[1].strip()
                view = self._view if self._view is not None else self.parent()
                browser_window = view.window() if view else None
                if browser_window and hasattr(browser_window, "handle_media_console_payload"):
                    browser_window.handle_media_console_payload(payload, view)
                return
        except Exception:
            pass
        try:
            super().javaScriptConsoleMessage(level, message, line_number, source_id)
        except Exception:
            pass

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        url_text = url.toString()
        lowered = url_text.lower()
        host = (url.host() or "").lower()
        if lowered.startswith("aurora://") or host in ("aurora.settings", "aurora.home"):
            log_debug(f"Internal navigation intercepted: {url_text}")
            view = self._view if self._view is not None else self.parent()
            browser_window = view.window() if view else None
            if browser_window and hasattr(browser_window, "handle_aurora_navigation"):
                QTimer.singleShot(0, lambda u=url_text, w=browser_window: w.handle_aurora_navigation(u))
                return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

class BrowserTab(QWebEngineView):
    def __init__(self, history_manager, download_manager, incognito=False, qprofile=None, initial_url="https://www.google.com"):
        super().__init__()
        self.history_manager = history_manager
        self.download_manager = download_manager
        self.incognito = incognito
        self._last_user_gesture_at = 0.0
        self._popup_gesture_used = False
        self._popup_open_times = []

        if qprofile is None:
            qprofile = QWebEngineProfile.defaultProfile()
        self.setPage(AuroraPage(qprofile, self))
        self.install_document_start_scripts()

        self.page().profile().setHttpUserAgent(DEFAULT_USER_AGENT)

        settings = self.settings()
        set_webengine_attr(settings, "PdfViewerEnabled", True)
        set_webengine_attr(settings, "PluginsEnabled", True)
        set_webengine_attr(settings, "FullScreenSupportEnabled", True)
        set_webengine_attr(settings, "PlaybackRequiresUserGesture", False)
        set_webengine_attr(settings, "JavascriptEnabled", True)
        set_webengine_attr(settings, "LocalStorageEnabled", True)
        set_webengine_attr(settings, "WebGLEnabled", True)
        set_webengine_attr(settings, "Accelerated2dCanvasEnabled", True)
        set_webengine_attr(settings, "DnsPrefetchEnabled", True)
        set_webengine_attr(settings, "AutoLoadImages", True)
        set_webengine_attr(settings, "JavascriptCanOpenWindows", True)
        set_webengine_attr(settings, "JavascriptCanAccessClipboard", True)
        set_webengine_attr(settings, "LocalContentCanAccessRemoteUrls", True)
        set_webengine_attr(settings, "LocalContentCanAccessFileUrls", True)
        # Allow mixed HTTP/HTTPS content — many anime CDN video segments are served over
        # plain HTTP even when the page is HTTPS, which causes silent playback failures.
        set_webengine_attr(settings, "AllowRunningInsecureContent", True)
        try:
            settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
            settings.setAttribute(QWebEngineSettings.LocalStorageEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebGLEnabled, True)
            settings.setAttribute(QWebEngineSettings.Accelerated2dCanvasEnabled, True)
            settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
            settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        except Exception:
            pass

        self.page().fullScreenRequested.connect(self.handle_fullscreen)
        if initial_url:
            self.setUrl(QUrl(initial_url))

        if self.incognito:
            self.history_manager = None

        self.urlChanged.connect(self.update_history)
        # Avoid running DOM mutation scripts on arbitrary pages.
        self.urlChanged.connect(self.inject_fullscreen_fix)
        self.loadFinished.connect(self.apply_runtime_page_protections)

    def install_document_start_scripts(self):
        if not ENABLE_BROWSER_WIDE_COMPAT_SCRIPTS:
            return
        try:
            scripts = self.page().scripts()
        except Exception:
            return
        existing_names = set()
        try:
            for script in scripts.toList():
                existing_names.add(script.name())
        except Exception:
            pass
        for script in (self.build_compatibility_script(), self.build_passkey_guard_script()):
            if script.name() not in existing_names:
                scripts.insert(script)

    def build_compatibility_script(self):
        script = QWebEngineScript()
        script.setName("AuroraCompatibility")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        script.setSourceCode("""
        (() => {
            try {
                const host = String((location && location.hostname) || '').toLowerCase();
                if (host === 'accounts.google.com' || host === 'signin.google.com') {
                    return;
                }
                const defineGetter = (target, key, getter) => {
                    try {
                        Object.defineProperty(target, key, {
                            configurable: true,
                            enumerable: true,
                            get: getter
                        });
                    } catch (e) {}
                };
                defineGetter(navigator, 'webdriver', () => undefined);
                if (!window.chrome) {
                    window.chrome = { runtime: {}, app: {}, webstore: {} };
                } else {
                    window.chrome.runtime = window.chrome.runtime || {};
                }
                defineGetter(navigator, 'languages', () => ['en-US', 'en']);
                defineGetter(navigator, 'platform', () => 'Win32');
                if (!navigator.plugins || navigator.plugins.length === 0) {
                    defineGetter(navigator, 'plugins', () => [1, 2, 3, 4, 5]);
                }
                if (!navigator.userAgentData) {
                    const uaData = {
                        brands: [
                            { brand: 'Chromium', version: '__AURORA_CHROME_MAJOR__' },
                            { brand: 'Google Chrome', version: '__AURORA_CHROME_MAJOR__' },
                            { brand: 'Not(A:Brand', version: '24' }
                        ],
                        mobile: false,
                        platform: 'Windows',
                        getHighEntropyValues: async () => ({
                            architecture: 'x86',
                            bitness: '64',
                            model: '',
                            platform: 'Windows',
                            platformVersion: '15.0.0',
                            uaFullVersion: '__AURORA_CHROME_FULL__'
                        })
                    };
                    defineGetter(navigator, 'userAgentData', () => uaData);
                }
                if (navigator.permissions && navigator.permissions.query) {
                    const originalQuery = navigator.permissions.query.bind(navigator.permissions);
                    navigator.permissions.query = (parameters) => {
                        if (parameters && parameters.name === 'notifications') {
                            return Promise.resolve({ state: Notification.permission });
                        }
                        return originalQuery(parameters);
                    };
                }
                const mediaSensitive = !host.endsWith('google.com') && host !== 'accounts.google.com' && host !== 'signin.google.com';
                if (mediaSensitive && !window.__auroraNativeMediaFallbackInstalled) {
                    window.__auroraNativeMediaFallbackInstalled = true;
                    const emitCandidate = (url, kind) => {
                        try {
                            const raw = String(url || '').trim();
                            if (!raw || raw.startsWith('data:')) {
                                return;
                            }
                            const absolute = new URL(raw, location.href).toString();
                            const lower = absolute.toLowerCase();
                            const mediaLike = (
                                lower.includes('.m3u8') ||
                                lower.includes('.mp4') ||
                                lower.includes('.mpd') ||
                                lower.includes('.webm') ||
                                lower.includes('.mkv') ||
                                lower.includes('videodelivery.net') ||
                                lower.includes('googlevideo.com/videoplayback') ||
                                lower.includes('megacloud') ||
                                lower.includes('megaf') ||
                                lower.includes('streamsb') ||
                                lower.includes('streamtape') ||
                                lower.includes('filemoon') ||
                                lower.includes('jwplayer') ||
                                lower.includes('manifest') ||
                                lower.includes('playlist') ||
                                lower.includes('master.m3u8') ||
                                lower.includes('index.m3u8')
                            );
                            if (!mediaLike) {
                                return;
                            }
                            console.error('AURORA_MEDIA_CANDIDATE:' + JSON.stringify({
                                url: absolute,
                                kind: kind || 'unknown',
                                page_url: location.href,
                                host: location.hostname || '',
                                origin: location.origin || ''
                            }));
                        } catch (e) {}
                    };
                    try {
                        const originalFetch = window.fetch ? window.fetch.bind(window) : null;
                        if (originalFetch) {
                            window.fetch = function(resource, init) {
                                try {
                                    const candidate = typeof resource === 'string' ? resource : (resource && resource.url) || '';
                                    emitCandidate(candidate, 'fetch');
                                } catch (e) {}
                                return originalFetch(resource, init);
                            };
                        }
                    } catch (e) {}
                    try {
                        const xhrOpen = XMLHttpRequest && XMLHttpRequest.prototype && XMLHttpRequest.prototype.open;
                        if (xhrOpen) {
                            XMLHttpRequest.prototype.open = function(method, url) {
                                try {
                                    emitCandidate(url, 'xhr');
                                } catch (e) {}
                                return xhrOpen.apply(this, arguments);
                            };
                        }
                    } catch (e) {}
                    try {
                        const mediaProto = HTMLMediaElement && HTMLMediaElement.prototype;
                        const srcDescriptor = mediaProto ? Object.getOwnPropertyDescriptor(mediaProto, 'src') : null;
                        if (srcDescriptor && srcDescriptor.set) {
                            Object.defineProperty(mediaProto, 'src', {
                                configurable: true,
                                enumerable: srcDescriptor.enumerable,
                                get: srcDescriptor.get ? function() { return srcDescriptor.get.call(this); } : undefined,
                                set: function(value) {
                                    emitCandidate(value, 'media-src');
                                    return srcDescriptor.set.call(this, value);
                                }
                            });
                        }
                        const originalSetAttribute = Element.prototype.setAttribute;
                        Element.prototype.setAttribute = function(name, value) {
                            try {
                                if (this instanceof HTMLMediaElement && String(name || '').toLowerCase() === 'src') {
                                    emitCandidate(value, 'media-attr');
                                }
                            } catch (e) {}
                            return originalSetAttribute.apply(this, arguments);
                        };
                    } catch (e) {}
                    try {
                        setInterval(() => {
                            try {
                                document.querySelectorAll('video, audio, source').forEach((element) => {
                                    emitCandidate(element.currentSrc || element.src || element.getAttribute('src') || '', 'media-scan');
                                });
                                document.querySelectorAll('iframe').forEach((element) => {
                                    emitCandidate(element.src || element.getAttribute('src') || '', 'iframe-src');
                                });
                            } catch (e) {}
                        }, 1200);
                    } catch (e) {}
                    const openNativeFallback = () => {
                        try {
                            if (window.__auroraNativeMediaFallbackPending) {
                                return;
                            }
                            window.__auroraNativeMediaFallbackPending = true;
                            location.href = 'aurora://action/open-native-media?target=' + encodeURIComponent(location.href);
                            setTimeout(() => {
                                window.__auroraNativeMediaFallbackPending = false;
                            }, 2500);
                        } catch (e) {}
                    };
                    const attachJwplayerFallback = () => {
                        try {
                            if (typeof window.jwplayer !== 'function') {
                                return;
                            }
                            const maybeAttach = (player) => {
                                if (!player || player.__auroraNativeMediaHooked) {
                                    return;
                                }
                                player.__auroraNativeMediaHooked = true;
                                player.on('error', (event) => {
                                    if (event && String(event.code || '') === '102630') {
                                        openNativeFallback();
                                    }
                                });
                            };
                            for (let index = 0; index < 5; index += 1) {
                                try {
                                    maybeAttach(window.jwplayer(index));
                                } catch (e) {}
                            }
                            try {
                                maybeAttach(window.jwplayer());
                            } catch (e) {}
                        } catch (e) {}
                    };
                    attachJwplayerFallback();
                    setInterval(attachJwplayerFallback, 1500);
                }
            } catch (e) {}
        })();
        """.replace("__AURORA_CHROME_MAJOR__", CHROMIUM_MAJOR_VERSION).replace("__AURORA_CHROME_FULL__", CHROMIUM_VERSION))
        return script

    def build_passkey_guard_script(self):
        script = QWebEngineScript()
        script.setName("AuroraPasskeyGuard")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        script.setSourceCode("""
        (() => {
            try {
                if (window.__auroraPasskeyGuardInstalled) {
                    return;
                }
                window.__auroraPasskeyGuardInstalled = true;
                let lastGestureAt = 0;
                let passkeyBudget = 0;
                const noteGesture = () => {
                    lastGestureAt = Date.now();
                    passkeyBudget = 1;
                };
                ['pointerdown', 'mousedown', 'keydown', 'touchstart'].forEach((eventName) => {
                    document.addEventListener(eventName, noteGesture, true);
                });
                if (!navigator.credentials || !navigator.credentials.get) {
                    return;
                }
                const originalGet = navigator.credentials.get.bind(navigator.credentials);
                navigator.credentials.get = function(options) {
                    try {
                        const isPublicKey = !!(options && options.publicKey);
                        const mediation = options && options.mediation ? String(options.mediation).toLowerCase() : '';
                        // Only suppress fully silent/conditional background probes, not user-triggered prompts
                        const silentProbe = mediation === 'silent';
                        if (isPublicKey && silentProbe) {
                            return Promise.reject(new DOMException('Automatic passkey prompt suppressed by Aurora.', 'NotAllowedError'));
                        }
                        if (isPublicKey && passkeyBudget > 0) {
                            passkeyBudget -= 1;
                        }
                    } catch (e) {}
                    return originalGet(options);
                };
            } catch (e) {}
        })();
        """)
        return script

    def register_user_gesture(self):
        self._last_user_gesture_at = time.monotonic()
        self._popup_gesture_used = False

    def has_recent_user_gesture(self, max_age=8.0):
        try:
            return (time.monotonic() - self._last_user_gesture_at) <= max_age
        except Exception:
            return False

    def consume_popup_gesture(self):
        if self.has_recent_user_gesture() and not self._popup_gesture_used:
            self._popup_gesture_used = True
            return True
        return False

    def prune_popup_open_times(self, max_age=10.0):
        now = time.monotonic()
        self._popup_open_times = [stamp for stamp in self._popup_open_times if (now - stamp) <= max_age]

    def consume_popup_allowance(self):
        if self.consume_popup_gesture():
            self._popup_open_times.append(time.monotonic())
            self.prune_popup_open_times()
            return True
        if self.has_recent_user_gesture(max_age=8.0):
            self.prune_popup_open_times()
            if len(self._popup_open_times) < 4:
                self._popup_open_times.append(time.monotonic())
                return True
        return False

    def mousePressEvent(self, event):
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self.register_user_gesture()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.register_user_gesture()
        super().keyPressEvent(event)

    def apply_runtime_page_protections(self, ok):
        if not ok:
            return
        if not ENABLE_RUNTIME_PAGE_PROTECTIONS:
            return
        main_window = self.window()
        if main_window is None:
            return
        current_url = self.url().toString()
        if current_url.startswith("aurora://") or "aurora.home" in current_url or "aurora.settings" in current_url:
            return
        current_host = (urlparse(current_url).hostname or "").lower()
        if host_matches_fragment(current_host, POPUP_COMPATIBILITY_HOST_FRAGMENTS):
            return
        if getattr(main_window, "ad_blocker_enabled", True):
            cosmetic_cleanup_js = """
            try {
                const selectors = [
                    '.adsbygoogle', '.ad-container', '.ad-slot', '.ad-wrapper',
                    '.banner-ad', '.google-auto-placed', '.promo-banner',
                    '[id^="google_ads"]', '[id*="taboola"]', '[class*="taboola"]',
                    '[id*="outbrain"]', '[class*="outbrain"]', '[class*="sponsored"]'
                ];
                selectors.forEach((selector) => {
                    document.querySelectorAll(selector).forEach((element) => {
                        element.style.display = 'none';
                        element.style.visibility = 'hidden';
                        element.style.height = '0';
                        element.style.minHeight = '0';
                    });
                });
            } catch (e) {
                console.debug('Aurora cosmetic cleanup error', e);
            }
            """
            self.page().runJavaScript(cosmetic_cleanup_js)

    def update_history(self, url):
        if self.history_manager:
            url_text = url.toString()
            if url_text.startswith("aurora://") or "aurora.home" in url_text or "aurora.settings" in url_text:
                return
            self.history_manager.add_history(url_text)
            main_window = self.window()
            if main_window and hasattr(main_window, "invalidate_internal_cache"):
                main_window.invalidate_internal_cache()

    def handle_fullscreen(self, request):
        request.accept()
        main_window = self.window()
        video_tweaks = bool(getattr(main_window, "video_page_tweaks_enabled", False))
        if request.toggleOn():
            if hasattr(main_window, "stabilize_media_mode_for_fullscreen"):
                main_window.stabilize_media_mode_for_fullscreen()
            if hasattr(main_window, "enter_app_fullscreen"):
                main_window.enter_app_fullscreen()
            else:
                if hasattr(main_window, "set_browser_chrome_visible"):
                    main_window.set_browser_chrome_visible(False)
                main_window.showFullScreen()
            screen_size = QApplication.primaryScreen().size()
            self.setGeometry(0, 0, screen_size.width(), screen_size.height())
            self.setVisible(True)
            if video_tweaks and is_youtube_url(self.url()):
                js = """
                try {
                    function tryWebGL() {
                        var canvas = document.createElement('canvas');
                        var contexts = ['webgl', 'webgl2', 'experimental-webgl'];
                        for (var i = 0; i < contexts.length; i++) {
                            var ctx = canvas.getContext(contexts[i], { failIfMajorPerformanceCaveat: true });
                            if (ctx) {
                                console.log('WebGL initialized: ' + contexts[i]);
                                return true;
                            }
                        }
                        console.log('WebGL not supported or blacklisted, forcing software');
                        var ctx = canvas.getContext('webgl', { failIfMajorPerformanceCaveat: false });
                        if (ctx) {
                            console.log('Software WebGL initialized');
                            return true;
                        }
                        return false;
                    }
                    document.addEventListener('DOMContentLoaded', function() {
                        tryWebGL();
                        var videoProgress = 0;
                        function maximizeVideo() {
                            var video = document.querySelector('video, .video-stream, .html5-main-video, .ytp-video');
                            if (video) {
                                videoProgress = video.currentTime;
                                var player = document.querySelector('#movie_player, .html5-video-player, #player, .ytd-player, .ytp-player-content');
                                if (player) {
                                    player.style.position = 'fixed';
                                    player.style.top = '0';
                                    player.style.left = '0';
                                    player.style.width = '100%';
                                    player.style.height = '100%';
                                    player.style.background = 'black';
                                    player.style.margin = '0';
                                    player.style.padding = '0';
                                    video.style.width = '100%';
                                    video.style.height = '100%';
                                    video.style.objectFit = 'contain';
                                    video.style.zIndex = '9999';
                                    video.controls = false;
                                    document.querySelectorAll('#masthead-container, #container, .ytp-chrome-top, .ytp-chrome-bottom, .ytp-gradient-bottom, .ytp-gradient-top, .ytp-title, .ytp-watermark, .annotation, #ytd-player, #movie_player > *:not(video), .html5-video-player > *:not(video), .ytp-player-content > *:not(video), .ad-container, .ytp-ad-module, .ytp-ad-overlay, .ytp-ad-text').forEach(el => el.style.display = 'none');
                                    document.body.style.overflow = 'hidden';
                                    document.documentElement.style.overflow = 'hidden';
                                    video.currentTime = videoProgress;
                                    video.play();
                                    console.log('Video maximized successfully at ' + videoProgress + 's');
                                    return true;
                                } else {
                                    console.log('Player not found, retrying...');
                                    setTimeout(maximizeVideo, 1000);
                                    return false;
                                }
                            } else {
                                console.log('Video element not found, retrying...');
                                setTimeout(maximizeVideo, 1000);
                                return false;
                            }
                        }
                        maximizeVideo();
                    });
                } catch (e) {
                    console.error('Error in maximizeVideo:', e);
                }
                """
                self.page().runJavaScript(js)
                QTimer.singleShot(1000, self.update)
        else:
            if hasattr(main_window, "exit_app_fullscreen"):
                main_window.exit_app_fullscreen()
            else:
                if hasattr(main_window, "set_browser_chrome_visible"):
                    main_window.set_browser_chrome_visible(True)
                main_window.showNormal()
            self.setGeometry(main_window.centralWidget().geometry())
            if video_tweaks and is_youtube_url(self.url()):
                js = """
                try {
                    var video = document.querySelector('video, .video-stream, .html5-main-video');
                    var videoProgress = video ? video.currentTime : 0;
                    document.querySelectorAll('#masthead-container, #container, .ytp-chrome-top, .ytp-chrome-bottom, .ytp-gradient-bottom, .ytp-gradient-top, .ytp-title, .ytp-watermark, .annotation, #ytd-player').forEach(el => el.style.display = '');
                    var player = document.querySelector('#movie_player, .html5-video-player, #player, .ytd-player, .ytp-player-content');
                    if (player) {
                        player.style.position = '';
                        player.style.top = '';
                        player.style.left = '';
                        player.style.width = '';
                        player.style.height = '';
                        player.style.background = '';
                        player.style.margin = '';
                        player.style.padding = '';
                        player.style.zIndex = '';
                    }
                    document.body.style.overflow = '';
                    document.documentElement.style.overflow = '';
                    if (video) {
                        video.style.width = '';
                        video.style.height = '';
                        video.style.objectFit = '';
                        video.style.zIndex = '';
                        video.currentTime = videoProgress;
                        video.play();
                        console.log('Restored video at ' + videoProgress + 's');
                    }
                } catch (e) {
                    console.error('Error restoring page:', e);
                }
                """
                self.page().runJavaScript(js)
                QTimer.singleShot(1000, self.update)
            if hasattr(main_window, "finalize_media_mode_after_fullscreen"):
                QTimer.singleShot(0, main_window.finalize_media_mode_after_fullscreen)
            main_window.is_fullscreen = False

    def inject_fullscreen_fix(self, url):
        main_window = self.window()
        if not bool(getattr(main_window, "youtube_fullscreen_fix_enabled", True)):
            return
        if is_youtube_url(url):
            js = """
            try {
                const requestPlayerFullscreen = () => {
                    const player = document.querySelector('#movie_player, .html5-video-player, #player');
                    if (player && player.requestFullscreen) {
                        player.requestFullscreen().catch(() => {});
                        return true;
                    }
                    const video = document.querySelector('video');
                    if (video && video.requestFullscreen) {
                        video.requestFullscreen().catch(() => {});
                        return true;
                    }
                    return false;
                };
                const installFsFix = () => {
                    const fsButton = document.querySelector('.ytp-fullscreen-button');
                    if (fsButton && !fsButton.dataset.auroraBound) {
                        fsButton.dataset.auroraBound = '1';
                        fsButton.addEventListener('click', () => {
                            setTimeout(requestPlayerFullscreen, 10);
                        });
                    }
                };
                installFsFix();
                document.addEventListener('yt-navigate-finish', installFsFix);
            } catch (e) {
                console.error('Error in fullscreen fix:', e);
            }
            """
            self.page().runJavaScript(js)

    def createWindow(self, _type):
        main_window = self.window()
        if hasattr(main_window, "should_block_popup_request") and main_window.should_block_popup_request(self):
            return None
        try:
            log_debug(f"BrowserTab.createWindow requested: type={int(_type)}")
        except Exception:
            pass
        new_tab = BrowserTab(self.history_manager, self.download_manager, self.incognito, qprofile=self.page().profile())
        if hasattr(main_window, 'tab_widget'):
            if hasattr(main_window, 'on_tab_load_finished'):
                new_tab.loadFinished.connect(lambda ok, tab=new_tab: main_window.on_tab_load_finished(ok, tab))
            if hasattr(main_window, 'on_url_changed'):
                new_tab.urlChanged.connect(main_window.on_url_changed)
            main_window.tab_widget.addTab(new_tab, "New Tab")
            if hasattr(main_window, "bind_tab_signals"):
                main_window.bind_tab_signals(new_tab)
            main_window.tab_widget.setCurrentWidget(new_tab)
        return new_tab

# ----------------- HistoryManager -----------------
class HistoryManager(QWidget):
    historyClicked = pyqtSignal(str)
    
    def __init__(self, profile="default"):
        super().__init__()
        self.setWindowTitle("History")
        self.setGeometry(200, 200, 560, 620)
        self.history_file = os.path.join(os.path.expanduser("~"), f"my_browser_history_{profile}.json")
        self.history_data = self.load_history()
        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(18, 18, 18, 18)
        self.layout.setSpacing(12)
        title = QLabel("Browsing History")
        title.setStyleSheet("font-size: 22px; font-weight: 800;")
        subtitle = QLabel("Review, reopen, copy, or clear the pages visited in this profile.")
        subtitle.setStyleSheet("color: #94abd1;")
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Filter history by name or URL")
        self.search_bar.textChanged.connect(self.refresh_list)
        self.history_list = QListWidget()
        self.history_list.setContextMenuPolicy(Qt.ActionsContextMenu)
        self.history_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.layout.addWidget(self.history_list)
        self.layout.insertWidget(0, title)
        self.layout.insertWidget(1, subtitle)
        self.layout.insertWidget(2, self.search_bar)
        self.delete_selected_action = QAction("Delete Selected", self)
        self.delete_selected_action.triggered.connect(self.delete_selected_history)
        self.clear_all_action = QAction("Clear All History", self)
        self.clear_all_action.triggered.connect(self.clear_history)
        self.history_list.addAction(self.delete_selected_action)
        self.history_list.addAction(self.clear_all_action)
        self.open_button = QPushButton("Open Selected")
        self.open_button.clicked.connect(self.open_selected_history)
        self.copy_button = QPushButton("Copy URL")
        self.copy_button.clicked.connect(self.copy_selected_history_url)
        self.delete_selected_button = QPushButton("Delete Selected")
        self.delete_selected_button.clicked.connect(self.delete_selected_history)
        self.clear_history_button = QPushButton("Clear History")
        self.clear_history_button.clicked.connect(self.clear_history)
        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        action_row.addWidget(self.open_button)
        action_row.addWidget(self.copy_button)
        action_row.addStretch()
        action_row.addWidget(self.delete_selected_button)
        action_row.addWidget(self.clear_history_button)
        self.layout.addLayout(action_row)
        self.setLayout(self.layout)
        self.history_list.itemDoubleClicked.connect(self.itemClicked)
        style_aux_window(self)
        self.refresh_list()

    def itemClicked(self, item):
        url = item.data(Qt.UserRole) or item.text()
        self.historyClicked.emit(url)

    def load_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r") as file:
                    data = json.load(file)
                    if isinstance(data, list):
                        return data
            except Exception as e:
                print("Error loading history:", e)
                return []
        return []

    def save_history(self):
        try:
            with open(self.history_file, "w") as file:
                json.dump(self.history_data, file, indent=4)
        except Exception as e:
            QMessageBox.warning(self, "History Save Error", f"Could not save history: {e}")

    def add_history(self, url):
        if url not in self.history_data:
            self.history_data.append(url)
            self.save_history()
        self.refresh_list()

    def clear_history(self):
        self.history_list.clear()
        self.history_data = []
        self.save_history()

    def delete_selected_history(self):
        selected_items = self.history_list.selectedItems()
        if not selected_items:
            return
        selected_urls = {item.data(Qt.UserRole) or item.text() for item in selected_items}
        self.history_data = [url for url in self.history_data if url not in selected_urls]
        self.save_history()
        self.refresh_list()

    def refresh_list(self):
        filter_text = self.search_bar.text().strip().lower() if hasattr(self, "search_bar") else ""
        self.history_list.clear()
        for url in reversed(self.history_data):
            if filter_text and filter_text not in url.lower() and filter_text not in prettify_host_label(url).lower():
                continue
            host = (urlparse(url).hostname or url).replace("www.", "")
            item = QListWidgetItem()
            item.setData(Qt.UserRole, url)
            item.setToolTip(url)
            item.setSizeHint(QSize(0, 64))
            self.history_list.addItem(item)
            row_widget = QWidget()
            row_layout = QVBoxLayout(row_widget)
            row_layout.setContentsMargins(10, 8, 10, 8)
            row_layout.setSpacing(2)
            title_label = QLabel(prettify_host_label(url))
            title_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #edf4ff;")
            meta_label = QLabel(host)
            meta_label.setStyleSheet("font-size: 12px; color: #8fb0da;")
            meta_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row_layout.addWidget(title_label)
            row_layout.addWidget(meta_label)
            self.history_list.setItemWidget(item, row_widget)

    def open_selected_history(self):
        item = self.history_list.currentItem()
        if item:
            self.itemClicked(item)

    def copy_selected_history_url(self):
        item = self.history_list.currentItem()
        if item:
            QApplication.clipboard().setText(item.data(Qt.UserRole) or "")

# ----------------- DownloadManager -----------------
class DownloadManager(QWidget):
    def __init__(self, browser_window=None, history_file=None, popup_only=False):
        super().__init__()
        self.browser_window = browser_window
        self.popup_only = popup_only
        self.connected_profiles = set()
        self.download_entries = {}
        self.active_downloads = {}
        self.download_key_lookup = {}
        self.dropdown_button = None
        self.default_download_dir = os.path.join(os.path.expanduser("~"), "Downloads", "Aurora Downloads")
        os.makedirs(self.default_download_dir, exist_ok=True)
        self.history_file = history_file
        self.setWindowTitle("Downloads")
        self.setGeometry(200, 200, 620, 520)
        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(18, 18, 18, 18)
        self.layout.setSpacing(12)
        title = QLabel("Downloads")
        title.setStyleSheet("font-size: 22px; font-weight: 800;")
        subtitle = QLabel("Track active downloads, reopen finished files, and jump straight to the download folder.")
        subtitle.setStyleSheet("color: #94abd1;")
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("color: #b7c8e8; font-weight: 600;")
        self.download_list = QListWidget()
        self.download_list.setContextMenuPolicy(Qt.ActionsContextMenu)
        self.download_list.setSelectionMode(QListWidget.SingleSelection)
        self.layout.addWidget(title)
        self.layout.addWidget(subtitle)
        self.layout.addWidget(self.summary_label)
        self.layout.addWidget(self.download_list)
        self.selection_label = QLabel("Selected: none")
        self.selection_label.setStyleSheet("color: #d9e7ff; font-weight: 600;")
        self.layout.addWidget(self.selection_label)
        self.setLayout(self.layout)
        self.download_list.itemDoubleClicked.connect(self.activate_download_item)
        self.download_list.setStyleSheet("""
            QListWidget {
                background: rgba(9, 19, 36, 0.68);
                color: #edf4ff;
                border: 1px solid rgba(120, 168, 255, 0.18);
                border-radius: 12px;
                outline: none;
            }
            QListWidget::item {
                padding: 2px;
                margin: 3px 0;
                border: none;
            }
            QListWidget::item:selected {
                background: rgba(124, 217, 255, 0.14);
                border: 1px solid rgba(124, 217, 255, 0.28);
                border-radius: 10px;
            }
        """)
        self.download_list.currentItemChanged.connect(lambda _cur, _prev: self.update_manager_action_state())
        self.dropdown = QFrame(None)
        self.dropdown.hide()
        self.dropdown.setFrameShape(QFrame.StyledPanel)
        self.dropdown.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.dropdown.setAttribute(Qt.WA_ShowWithoutActivating, True)
        popup_layout = QVBoxLayout()
        popup_layout.setContentsMargins(12, 12, 12, 12)
        popup_layout.setSpacing(8)
        header_layout = QHBoxLayout()
        self.popup_title = QLabel("Downloads")
        self.popup_title.setStyleSheet("font-weight: 700; color: white;")
        self.popup_close_button = QPushButton("Hide")
        self.popup_close_button.setFixedHeight(26)
        self.popup_close_button.clicked.connect(self.hide_dropdown)
        header_layout.addWidget(self.popup_title)
        header_layout.addStretch()
        header_layout.addWidget(self.popup_close_button)
        self.popup_list = QListWidget()
        self.popup_list.setMaximumHeight(180)
        self.popup_list.setMinimumWidth(360)
        self.popup_list.setSpacing(6)
        self.popup_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.popup_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.popup_list.setSelectionMode(QListWidget.SingleSelection)
        self.popup_list.setFocusPolicy(Qt.NoFocus)
        self.popup_list.itemDoubleClicked.connect(self.activate_download_item)
        popup_layout.addLayout(header_layout)
        popup_layout.addWidget(self.popup_list)
        self.popup_selection_label = QLabel("Selected: none")
        self.popup_selection_label.setStyleSheet("color: #d9e7ff; font-weight: 600;")
        popup_layout.addWidget(self.popup_selection_label)
        popup_actions_layout = QHBoxLayout()
        popup_actions_layout.setSpacing(8)
        self.popup_open_button = QPushButton("Open")
        self.popup_folder_button = QPushButton("Folder")
        self.popup_pause_button = QPushButton("Pause / Resume")
        self.popup_retry_button = QPushButton("Retry")
        self.popup_cancel_button = QPushButton("Cancel / Remove")
        self.popup_clear_button = QPushButton("Clear Finished")
        self.popup_show_all_button = QPushButton("Show All")
        popup_actions_layout.addWidget(self.popup_open_button)
        popup_actions_layout.addWidget(self.popup_folder_button)
        popup_actions_layout.addWidget(self.popup_pause_button)
        popup_actions_layout.addWidget(self.popup_retry_button)
        popup_actions_layout.addWidget(self.popup_cancel_button)
        popup_actions_layout.addStretch()
        popup_actions_layout.addWidget(self.popup_clear_button)
        popup_actions_layout.addWidget(self.popup_show_all_button)
        popup_layout.addLayout(popup_actions_layout)
        self.dropdown.setLayout(popup_layout)
        self.dropdown.setStyleSheet("""
            QFrame {
                background-color: rgba(12, 18, 32, 235);
                border: 1px solid rgba(120, 168, 255, 120);
                border-radius: 12px;
            }
            QListWidget {
                background: transparent;
                color: white;
                border: none;
                outline: none;
            }
            QListWidget::item {
                padding: 2px;
                border-bottom: 1px solid rgba(255,255,255,0.08);
            }
            QListWidget::item:selected {
                background: rgba(124, 217, 255, 0.12);
                border: 1px solid rgba(124, 217, 255, 0.22);
                border-radius: 10px;
            }
            QPushButton {
                background-color: rgba(255,255,255,0.08);
                color: white;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                padding: 0 10px;
            }
        """)
        self.open_file_action = QAction("Open File", self)
        self.open_file_action.triggered.connect(lambda: self.open_selected_download(self.download_list))
        self.open_folder_action = QAction("Show In Folder", self)
        self.open_folder_action.triggered.connect(lambda: self.show_selected_in_folder(self.download_list))
        self.clear_finished_action = QAction("Clear Finished", self)
        self.clear_finished_action.triggered.connect(self.clear_finished_downloads)
        self.download_list.addAction(self.open_file_action)
        self.download_list.addAction(self.open_folder_action)
        self.download_list.addAction(self.clear_finished_action)
        self.open_downloads_folder_button = QPushButton("Open Downloads Folder")
        self.open_downloads_folder_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.default_download_dir)))
        self.pause_resume_button = QPushButton("Pause / Resume")
        self.pause_resume_button.clicked.connect(lambda: self.toggle_pause_resume(self.download_list.currentItem()) if self.download_list.currentItem() else None)
        self.retry_button = QPushButton("Retry")
        self.retry_button.clicked.connect(lambda: self.retry_selected_download(self.download_list))
        self.cancel_button = QPushButton("Cancel / Remove")
        self.cancel_button.clicked.connect(lambda: self.cancel_or_remove_selected_download(self.download_list))
        self.clear_manager_button = QPushButton("Clear Finished")
        self.clear_manager_button.clicked.connect(self.clear_finished_downloads)
        manager_actions = QHBoxLayout()
        manager_actions.setSpacing(10)
        manager_actions.addWidget(self.open_downloads_folder_button)
        manager_actions.addWidget(self.pause_resume_button)
        manager_actions.addWidget(self.retry_button)
        manager_actions.addWidget(self.cancel_button)
        manager_actions.addStretch()
        manager_actions.addWidget(self.clear_manager_button)
        self.layout.addLayout(manager_actions)
        self.update_manager_action_state()
        self.popup_list.currentItemChanged.connect(self.update_popup_action_state)
        self.popup_open_button.clicked.connect(lambda: self.open_selected_download(self.popup_list))
        self.popup_folder_button.clicked.connect(lambda: self.show_selected_in_folder(self.popup_list))
        self.popup_pause_button.clicked.connect(lambda: self.toggle_pause_resume(self.popup_list.currentItem()) if self.popup_list.currentItem() else None)
        self.popup_retry_button.clicked.connect(lambda: self.retry_selected_download(self.popup_list))
        self.popup_cancel_button.clicked.connect(lambda: self.cancel_or_remove_selected_download(self.popup_list))
        self.popup_clear_button.clicked.connect(self.clear_finished_downloads)
        self.popup_show_all_button.clicked.connect(self.show_full_manager_from_popup)
        self.load_download_history()
        self.update_popup_action_state()
        style_aux_window(self)
        self.update_summary()
        if self.popup_only:
            self.hide()
            self.popup_show_all_button.hide()
        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(1000)
        self.progress_timer.timeout.connect(self.refresh_active_downloads)
        self.progress_timer.start()
        self.retry_action = QAction("Retry Download", self)
        self.retry_action.triggered.connect(lambda: self.retry_selected_download(self.download_list))
        self.download_list.addAction(self.retry_action)
        self.cancel_action = QAction("Cancel / Remove", self)
        self.cancel_action.triggered.connect(lambda: self.cancel_or_remove_selected_download(self.download_list))
        self.download_list.addAction(self.cancel_action)

    def get_entry_for_item(self, item):
        if item is None:
            return None
        entry_key = item.data(Qt.UserRole)
        if entry_key is None:
            return None
        return self.download_entries.get(entry_key)

    def get_selected_entry(self, list_widget):
        if list_widget is None:
            return None
        return self.get_entry_for_item(list_widget.currentItem())

    def format_entry_name(self, entry):
        name = (entry or {}).get("name", "") if isinstance(entry, dict) else ""
        name = str(name or "").strip()
        return name if len(name) <= 36 else name[:33] + "..."

    def download_is_paused(self, download):
        if download is None:
            return False
        try:
            is_paused_method = getattr(download, "isPaused", None)
            if callable(is_paused_method):
                return bool(is_paused_method())
            paused_state = getattr(QWebEngineDownloadItem, "DownloadPaused", None)
            return paused_state is not None and download.state() == paused_state
        except Exception:
            return False

    def resolve_download_status(self, download, entry=None):
        if download is None:
            return entry.get("status", "Interrupted") if entry else "Interrupted"
        try:
            state = download.state()
        except Exception:
            return entry.get("status", "Interrupted") if entry else "Interrupted"
        interrupted_state = getattr(QWebEngineDownloadItem, "DownloadInterrupted", None)
        if self.download_is_paused(download):
            return "Paused"
        if state == getattr(QWebEngineDownloadItem, "DownloadCompleted", None):
            return "Completed"
        if interrupted_state is not None and state == interrupted_state:
            return "Interrupted"
        if state == getattr(QWebEngineDownloadItem, "DownloadCancelled", None):
            return "Cancelled"
        if state == getattr(QWebEngineDownloadItem, "DownloadInProgress", None):
            return "Downloading"
        return entry.get("status", "Starting") if entry else "Starting"

    def can_pause_entry(self, entry):
        return bool(entry and entry.get("download") is not None and entry["status"] in ("Downloading", "Paused", "Starting"))

    def can_retry_entry(self, entry):
        return bool(entry and entry.get("url") and entry["status"] in ("Failed", "Interrupted", "Cancelled"))

    def can_cancel_entry(self, entry):
        return bool(entry and entry.get("download") is not None and entry["status"] in ("Starting", "Downloading", "Paused"))

    def can_remove_entry(self, entry):
        return bool(entry and entry["status"] in ("Completed", "Cancelled", "Failed", "Interrupted", "Retrying"))

    def load_download_history(self):
        if not self.history_file or not os.path.exists(self.history_file):
            return
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        if not isinstance(data, list):
            return
        for record in data[-100:]:
            if not isinstance(record, dict) or not record.get("name") or not record.get("path"):
                continue
            self.add_entry_from_record(record)
        self.update_button_state()

    def save_download_history(self):
        if not self.history_file:
            return
        serializable = []
        for entry in self.download_entries.values():
            serializable.append({
                "key": entry["key"],
                "name": entry["name"],
                "path": entry["path"],
                "progress": entry["progress"],
                "status": entry["status"],
                "received": int(entry.get("received", 0)),
                "total": int(entry.get("total", 0)),
                "url": entry.get("url", ""),
                "created_at": float(entry.get("created_at", 0) or 0),
                "finished_at": float(entry.get("finished_at", 0) or 0),
            })
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(serializable[-150:], f, indent=2)
        except Exception as e:
            print(f"Download history save error: {e}")

    def add_entry_from_record(self, record):
        entry_key = record.get("key") or f"history::{record['path']}"
        stored_status = record.get("status", "Completed")
        stored_received = int(record.get("received", 0) or 0)
        stored_total = int(record.get("total", 0) or 0)
        stored_progress = int(record.get("progress", 0) or 0)
        file_exists = os.path.exists(record["path"])
        if stored_status in ("Starting", "Downloading", "Paused"):
            if file_exists and stored_total > 0 and stored_received >= stored_total:
                resolved_status = "Completed"
                resolved_progress = 100
            else:
                resolved_status = "Interrupted"
                resolved_progress = min(max(stored_progress, 0), 99 if stored_received > 0 else 0)
        elif stored_status == "Completed" and not file_exists:
            resolved_status = "Failed"
            resolved_progress = stored_progress
        else:
            resolved_status = stored_status
            resolved_progress = stored_progress
        item = QListWidgetItem()
        item.setData(Qt.UserRole, entry_key)
        item.setText("")
        item.setSizeHint(QSize(420, 76))
        self.download_list.addItem(item)
        entry = {
            "key": entry_key,
            "download": None,
            "item": item,
            "name": record["name"],
            "path": record["path"],
            "url": record.get("url", ""),
            "progress": resolved_progress,
            "status": resolved_status,
            "received": stored_received,
            "total": stored_total,
            "speed_bps": 0.0,
            "eta_seconds": None,
            "last_sample_bytes": stored_received,
            "last_sample_time": time.time(),
            "created_at": float(record.get("created_at", 0) or 0),
            "finished_at": float(record.get("finished_at", 0) or 0),
        }
        self.download_entries[entry_key] = entry
        self.download_list.setItemWidget(item, self.create_download_item_widget(entry))
        self.sync_item_text(entry)

    def format_bytes(self, byte_count):
        try:
            value = float(max(byte_count, 0))
        except Exception:
            value = 0.0
        units = ["B", "KB", "MB", "GB", "TB"]
        unit_index = 0
        while value >= 1024 and unit_index < len(units) - 1:
            value /= 1024.0
            unit_index += 1
        if unit_index == 0:
            return f"{int(value)} {units[unit_index]}"
        return f"{value:.1f} {units[unit_index]}"

    def format_eta(self, seconds_remaining):
        if seconds_remaining is None or seconds_remaining < 0:
            return "--"
        seconds_remaining = int(round(seconds_remaining))
        if seconds_remaining <= 1:
            return "1s"
        if seconds_remaining < 60:
            return f"{seconds_remaining}s"
        minutes, seconds = divmod(seconds_remaining, 60)
        if minutes < 60:
            return f"{minutes}m" if seconds == 0 else f"{minutes}m {seconds}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h" if minutes == 0 else f"{hours}h {minutes}m"

    def format_rate(self, bytes_per_second):
        speed_value = float(bytes_per_second or 0.0)
        if speed_value <= 0:
            return "--/s"
        return f"{self.format_bytes(speed_value)}/s"

    def format_timestamp(self, timestamp_value):
        if not timestamp_value:
            return ""
        try:
            dt = time.localtime(float(timestamp_value))
            return time.strftime("%d %b %I:%M %p", dt).lstrip("0")
        except Exception:
            return ""

    def update_transfer_metrics(self, entry, received, total):
        now = time.time()
        received = max(int(received or 0), 0)
        total = max(int(total or 0), 0)
        previous_bytes = int(entry.get("last_sample_bytes", received))
        previous_time = float(entry.get("last_sample_time", now))
        elapsed = max(now - previous_time, 0.001)
        delta_bytes = max(received - previous_bytes, 0)
        previous_speed = float(entry.get("speed_bps", 0.0) or 0.0)
        if delta_bytes > 0:
            instant_speed = delta_bytes / elapsed
            if previous_speed > 0:
                entry["speed_bps"] = (previous_speed * 0.68) + (instant_speed * 0.32)
            else:
                entry["speed_bps"] = instant_speed
        elif entry.get("status") in ("Downloading", "Starting") and previous_speed > 0:
            # Decay gently instead of snapping to zero so the UI feels stable.
            entry["speed_bps"] = previous_speed * 0.82
        elif entry.get("status") not in ("Downloading", "Starting"):
            entry["speed_bps"] = 0.0
        entry["last_sample_bytes"] = received
        entry["last_sample_time"] = now
        entry["received"] = received
        entry["total"] = total
        if total > 0:
            entry["progress"] = max(0, min(100, int((received / total) * 100)))
            if received >= total:
                entry["progress"] = 100
                if entry.get("status") in ("Starting", "Downloading", "Paused"):
                    entry["status"] = "Completed"
                    entry["finished_at"] = now
        elif entry.get("status") == "Completed":
            entry["progress"] = 100
        elif received > 0 and entry.get("progress", 0) <= 0:
            entry["progress"] = 1
        if total > 0 and entry.get("speed_bps", 0) > 0:
            remaining_bytes = max(total - received, 0)
            raw_eta = remaining_bytes / entry["speed_bps"] if remaining_bytes > 0 else 0
            previous_eta = entry.get("eta_seconds")
            if previous_eta is not None and raw_eta > 0:
                entry["eta_seconds"] = (float(previous_eta) * 0.72) + (raw_eta * 0.28)
            else:
                entry["eta_seconds"] = raw_eta
        else:
            entry["eta_seconds"] = None

    def build_meta_text(self, entry):
        status = entry["status"]
        received = int(entry.get("received", 0) or 0)
        total = int(entry.get("total", 0) or 0)
        speed_bps = float(entry.get("speed_bps", 0.0) or 0.0)
        if total > 0:
            size_text = f"{self.format_bytes(received)} / {self.format_bytes(total)}"
            percent_text = f"{entry['progress']}%"
        else:
            size_text = self.format_bytes(received) if received > 0 else "0 B"
            percent_text = "calculating"
        if status in ("Downloading", "Starting", "Paused"):
            speed_text = self.format_rate(speed_bps)
            eta_text = self.format_eta(entry.get("eta_seconds"))
            if status == "Starting" and total <= 0 and received <= 0:
                return "Starting | Preparing download..."
            if status == "Paused":
                if total > 0:
                    return f"Paused | {percent_text} | {size_text}"
                return f"Paused | {size_text}"
            if total > 0:
                return f"{percent_text} | {size_text} | {speed_text} | {eta_text} left"
            return f"{status} | {size_text} | {speed_text}"
        if status == "Retrying":
            return "Retrying | Opening source page..."
        if status == "Completed":
            finished_size = self.format_bytes(total or received)
            finished_at = self.format_timestamp(entry.get("finished_at"))
            return f"Completed | {finished_size}" + (f" | {finished_at}" if finished_at else "")
        if status == "Interrupted":
            return f"Interrupted | {size_text} | Retry available"
        if status in ("Cancelled", "Failed"):
            finished_at = self.format_timestamp(entry.get("finished_at"))
            suffix = " | Retry available" if entry.get("url") else ""
            if finished_at:
                suffix = f" | {finished_at}" + suffix
            return f"{status} | {size_text}{suffix}"
        return f"{status} | {percent_text}"

    def refresh_active_downloads(self):
        dirty = False
        for entry in list(self.download_entries.values()):
            if entry["status"] not in ("Starting", "Downloading", "Paused"):
                continue
            live_download = entry.get("download")
            if live_download is not None:
                try:
                    self.update_transfer_metrics(entry, live_download.receivedBytes(), live_download.totalBytes())
                    current_status = self.resolve_download_status(live_download, entry)
                    if entry["status"] == "Completed":
                        pass
                    elif current_status in ("Downloading", "Paused", "Completed"):
                        entry["status"] = current_status
                except Exception:
                    pass
            elif os.path.exists(entry["path"]):
                try:
                    current_size = os.path.getsize(entry["path"])
                    self.update_transfer_metrics(entry, current_size, entry.get("total", 0))
                    if entry["status"] == "Completed":
                        pass
                    elif entry["status"] == "Starting" and current_size > 0:
                        entry["status"] = "Downloading"
                except Exception:
                    pass
            self.sync_item_text(entry)
            dirty = True
        if dirty:
            self.save_download_history()
            self.update_button_state()
            if self.dropdown.isVisible():
                self.refresh_dropdown()

    def attach_window(self, browser_window):
        self.browser_window = browser_window

    def attach_button(self, button):
        self.dropdown_button = button
        try:
            self.dropdown_button.clicked.disconnect(self.toggle_dropdown)
        except Exception:
            pass
        self.dropdown_button.clicked.connect(self.toggle_dropdown)

    def connect_profile(self, profile):
        if profile is None:
            return
        profile_key = id(profile)
        if profile_key in self.connected_profiles:
            return
        profile.downloadRequested.connect(self.handle_download)
        self.connected_profiles.add(profile_key)

    def refresh_dropdown(self):
        if self.dropdown is None:
            return
        status_priority = {
            "Starting": 0,
            "Downloading": 0,
            "Paused": 1,
            "Failed": 2,
            "Interrupted": 2,
            "Cancelled": 3,
            "Completed": 4,
        }
        recent_entries = list(self.download_entries.values())[-10:]
        recent_entries = sorted(
            recent_entries,
            key=lambda entry: (
                status_priority.get(entry["status"], 5),
                -list(self.download_entries).index(entry["key"]),
            ),
        )[:8]
        self.popup_list.clear()
        if recent_entries:
            for entry in recent_entries:
                list_item = QListWidgetItem()
                list_item.setData(Qt.UserRole, entry["key"])
                list_item.setSizeHint(QSize(340, 62))
                self.popup_list.addItem(list_item)
                self.popup_list.setItemWidget(list_item, self.create_download_item_widget(entry, compact=True))
        else:
            self.popup_list.addItem(QListWidgetItem("No downloads yet"))
            self.popup_list.item(0).setFlags(Qt.NoItemFlags)
        if self.popup_list.count() > 0 and self.popup_list.item(0).flags() != Qt.NoItemFlags:
            self.popup_list.setCurrentRow(0)
        self.dropdown.adjustSize()
        self.reposition_popup()
        self.dropdown.show()
        self.dropdown.raise_()
        self.update_button_state()
        self.update_popup_action_state()

    def reposition_popup(self):
        if not self.browser_window or not self.dropdown:
            return
        popup_width = max(460, self.dropdown.sizeHint().width())
        popup_height = max(220, min(420, self.dropdown.sizeHint().height()))
        if self.dropdown_button and self.dropdown_button.isVisible():
            button_pos = self.dropdown_button.mapToGlobal(self.dropdown_button.rect().bottomRight())
            x = button_pos.x() - popup_width + self.dropdown_button.width()
            y = button_pos.y() + 8
        else:
            top_right = self.browser_window.mapToGlobal(self.browser_window.rect().topRight())
            x = top_right.x() - popup_width - 18
            y = top_right.y() + 48
        self.dropdown.setGeometry(x, y, popup_width, popup_height)

    def toggle_dropdown(self):
        if self.dropdown.isVisible():
            self.hide_dropdown()
        else:
            self.refresh_dropdown()

    def hide_dropdown(self):
        if self.dropdown is not None:
            self.dropdown.hide()

    def update_popup_action_state(self):
        entry = self.get_selected_entry(self.popup_list)
        can_pause = self.can_pause_entry(entry)
        can_open = bool(entry and entry["status"] == "Completed" and os.path.exists(entry["path"]))
        can_show_folder = bool(entry and os.path.exists(entry["path"]))
        can_retry = self.can_retry_entry(entry)
        can_cancel = self.can_cancel_entry(entry)
        can_remove = self.can_remove_entry(entry)
        has_finished = any(
            entry["status"] in ("Completed", "Cancelled", "Failed", "Interrupted")
            for entry in self.download_entries.values()
        )
        self.popup_selection_label.setText(f"Selected: {self.format_entry_name(entry) if entry else 'none'}")
        self.popup_open_button.setEnabled(can_open)
        self.popup_folder_button.setEnabled(can_show_folder)
        self.popup_pause_button.setEnabled(can_pause)
        self.popup_clear_button.setEnabled(has_finished)
        self.popup_retry_button.setEnabled(can_retry)
        self.popup_cancel_button.setEnabled(can_cancel or can_remove)
        self.popup_pause_button.setText("Resume Selected" if entry and entry["status"] == "Paused" and can_pause else "Pause Selected")
        self.popup_retry_button.setText("Retry Selected")
        self.popup_cancel_button.setText("Cancel Selected" if can_cancel else "Remove Selected")

    def update_manager_action_state(self):
        entry = self.get_selected_entry(self.download_list)
        can_pause = self.can_pause_entry(entry)
        can_retry = self.can_retry_entry(entry)
        can_cancel = self.can_cancel_entry(entry)
        can_remove = self.can_remove_entry(entry)
        self.selection_label.setText(f"Selected: {self.format_entry_name(entry) if entry else 'none'}")
        self.pause_resume_button.setEnabled(can_pause)
        self.retry_button.setEnabled(can_retry)
        self.cancel_button.setEnabled(can_cancel or can_remove)
        self.pause_resume_button.setText("Resume Selected" if entry and entry["status"] == "Paused" and can_pause else "Pause Selected")
        self.retry_button.setText("Retry Selected")
        self.cancel_button.setText("Cancel Selected" if can_cancel else "Remove Selected")

    def show_full_manager_from_popup(self):
        if self.popup_only:
            return
        self.hide_dropdown()
        self.show()
        self.raise_()
        self.activateWindow()

    def update_button_state(self):
        if not self.dropdown_button:
            return
        active_count = sum(
            1 for entry in self.download_entries.values()
            if entry["status"] in ("Starting", "Downloading", "Paused")
        )
        if active_count > 0:
            self.dropdown_button.setText(f"Downloads ({active_count})")
        else:
            self.dropdown_button.setText("Downloads")
        self.update_summary()
        self.update_manager_action_state()

    def update_summary(self):
        active_count = sum(1 for entry in self.download_entries.values() if entry["status"] in ("Starting", "Downloading", "Paused"))
        finished_count = sum(1 for entry in self.download_entries.values() if entry["status"] in ("Completed", "Cancelled", "Failed", "Interrupted"))
        self.summary_label.setText(f"Active: {active_count}   |   Finished: {finished_count}   |   Folder: {self.default_download_dir}")

    def build_download_path(self, suggested_name):
        candidate_name = suggested_name or "download"
        root, ext = os.path.splitext(candidate_name)
        if not root:
            root = "download"
        candidate_path = os.path.join(self.default_download_dir, candidate_name)
        counter = 1
        while os.path.exists(candidate_path):
            candidate_path = os.path.join(self.default_download_dir, f"{root} ({counter}){ext}")
            counter += 1
        return candidate_path

    def create_download_item_widget(self, entry, compact=False):
        wrapper = QWidget()
        wrapper.setAttribute(Qt.WA_StyledBackground, True)
        wrapper.setStyleSheet("background: transparent; border: none;")
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(3 if compact else 4)

        title = QLabel(entry["name"])
        title.setWordWrap(False)
        title.setToolTip(entry["name"])
        title.setStyleSheet("font-weight: 700; color: #edf4ff; background: transparent; border: none;" if not compact else "font-weight: 700; color: #f6fbff; background: transparent; border: none;")
        total_bytes = int(entry.get("total", 0) or 0)
        meta_text = self.build_meta_text(entry)
        meta = QLabel(meta_text)
        meta.setWordWrap(False)
        meta.setToolTip(meta_text)
        meta.setStyleSheet("color: #bfd0ee; background: transparent; border: none;" if not compact else "color: rgba(237,244,255,0.86); background: transparent; border: none;")
        progress = QProgressBar()
        if total_bytes > 0:
            progress.setRange(0, 100)
            progress.setValue(entry["progress"])
        elif entry["status"] in ("Starting", "Downloading"):
            progress.setRange(0, 0)
        else:
            progress.setRange(0, 100)
            progress.setValue(entry["progress"])
        progress.setTextVisible(False)
        progress.setFixedHeight(8 if compact else 12)
        progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid rgba(120, 168, 255, 0.18);
                border-radius: 6px;
                background: rgba(130, 146, 176, 0.12);
            }
            QProgressBar::chunk {
                border-radius: 5px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #77c2ff, stop:1 #7af0ca);
            }
        """)

        layout.addWidget(title)
        layout.addWidget(meta)
        layout.addWidget(progress)

        if not compact:
            entry["title_label"] = title
            entry["meta_label"] = meta
            entry["progress_bar"] = progress
        return wrapper

    def set_refresh_interval(self, interval_ms):
        try:
            self.progress_timer.setInterval(max(500, int(interval_ms)))
        except Exception:
            pass

    def handle_download(self, download: QWebEngineDownloadItem):
        if download.state() == QWebEngineDownloadItem.DownloadRequested:
            try:
                suggested_name = download_get_filename(download) or "download"
                file_path = self.build_download_path(suggested_name)
                download_set_path(download, file_path)
                download.accept()
            except Exception as e:
                dialog_parent = self.browser_window if self.browser_window is not None else self
                QMessageBox.warning(dialog_parent, "Download Error", f"Could not start download: {e}")
                try:
                    download.cancel()
                except Exception:
                    pass
                return

            name = os.path.basename(file_path)
            entry_key = f"download::{file_path}"
            item = QListWidgetItem(f"{name} - 0% (Starting)")
            item.setData(Qt.UserRole, entry_key)
            item.setText("")
            item.setSizeHint(QSize(420, 76))
            self.download_list.addItem(item)
            self.download_entries[entry_key] = {
                "key": entry_key,
                "download": download,
                "item": item,
                "name": name,
                "path": file_path,
                "url": download.url().toString() if hasattr(download, "url") else "",
                "progress": 0,
                "status": "Starting",
                "received": 0,
                "total": 0,
                "speed_bps": 0.0,
                "eta_seconds": None,
                "last_sample_bytes": 0,
                "last_sample_time": time.time(),
                "created_at": time.time(),
                "finished_at": 0.0,
            }
            self.download_key_lookup[id(download)] = entry_key
            self.download_list.setItemWidget(item, self.create_download_item_widget(self.download_entries[entry_key]))
            self.active_downloads[id(download)] = download
            if hasattr(download, "downloadProgress"):
                try:
                    download.downloadProgress.connect(lambda rec, tot, item=item, d=download: self.update_progress(item, d, rec, tot))
                except Exception:
                    pass
            else:
                for signal_name in ("receivedBytesChanged", "totalBytesChanged"):
                    try:
                        signal = getattr(download, signal_name, None)
                        if signal is not None:
                            signal.connect(lambda *_args, d=download: self.refresh_download_snapshot(d))
                    except Exception:
                        pass
            try:
                download.stateChanged.connect(lambda _state, d=download: self.update_download_state(d))
            except Exception:
                pass
            try:
                download.finished.connect(lambda d=download: self.mark_download_finished(d))
            except Exception:
                pass
            self.save_download_history()
            self.refresh_dropdown()

    def refresh_download_snapshot(self, download):
        try:
            entry = self.download_entries.get(self.download_key_lookup.get(id(download)))
            if not entry:
                return
            self.update_progress(entry["item"], download, download.receivedBytes(), download.totalBytes())
        except RuntimeError:
            self.active_downloads.pop(id(download), None)
            self.download_key_lookup.pop(id(download), None)
        except Exception:
            pass

    def sync_item_text(self, entry):
        if not entry:
            return
        entry["item"].setText("")
        title_label = entry.get("title_label")
        meta_label = entry.get("meta_label")
        progress_bar = entry.get("progress_bar")
        if title_label is not None:
            title_label.setText(entry["name"])
            title_label.setToolTip(entry["name"])
        if meta_label is not None:
            total_bytes = int(entry.get("total", 0) or 0)
            meta_text = self.build_meta_text(entry)
            meta_label.setText(meta_text)
            meta_label.setToolTip(meta_text)
        if progress_bar is not None:
            if total_bytes > 0:
                progress_bar.setRange(0, 100)
                progress_bar.setValue(max(0, min(100, entry["progress"])))
            elif entry["status"] in ("Starting", "Downloading"):
                progress_bar.setRange(0, 0)
            else:
                progress_bar.setRange(0, 100)
                progress_bar.setValue(max(0, min(100, entry["progress"])))

    def mark_download_finished(self, download):
        try:
            entry = self.download_entries.get(self.download_key_lookup.get(id(download)))
            if not entry:
                return
            try:
                self.update_transfer_metrics(entry, download.receivedBytes(), download.totalBytes())
            except Exception:
                pass
            if os.path.exists(entry["path"]):
                entry["progress"] = 100
                entry["status"] = "Completed"
                entry["finished_at"] = time.time()
            else:
                entry["status"] = "Failed"
                entry["finished_at"] = time.time()
            self.sync_item_text(entry)
            self.active_downloads.pop(id(download), None)
            self.download_key_lookup.pop(id(download), None)
            self.save_download_history()
            self.update_button_state()
            if self.dropdown.isVisible():
                self.refresh_dropdown()
        except Exception:
            self.active_downloads.pop(id(download), None)

    def update_progress(self, item, download, received, total):
        try:
            entry = self.download_entries.get(self.download_key_lookup.get(id(download)))
            current_status = self.resolve_download_status(download, entry)
            if entry:
                self.update_transfer_metrics(entry, received, total)
            if total > 0:
                percent = (received / total) * 100
                state = current_status if current_status in ("Paused", "Downloading") else "Downloading"
                if entry:
                    if entry["status"] != "Completed":
                        entry["status"] = "Completed" if received >= total else state
                    self.sync_item_text(entry)
                else:
                    item.setText(f"{download_get_filename(download)} - {percent:.0f}% ({state})")
            else:
                if entry:
                    if entry["status"] != "Completed":
                        entry["status"] = current_status if current_status in ("Paused", "Downloading") else "Downloading"
                    self.sync_item_text(entry)
                else:
                    item.setText(f"{download_get_filename(download)} - 0% (Downloading)")
            if current_status == "Downloading":
                item.setForeground(QBrush(Qt.green))
            elif current_status == "Paused":
                item.setForeground(QBrush(Qt.red))
            else:
                item.setForeground(QBrush(Qt.black))
            if entry and entry["status"] == "Completed":
                self.active_downloads.pop(id(download), None)
                self.download_key_lookup.pop(id(download), None)
            if entry:
                self.save_download_history()
            self.update_button_state()
            if self.dropdown.isVisible():
                self.refresh_dropdown()
        except RuntimeError:
            self.active_downloads.pop(id(download), None)
            self.download_key_lookup.pop(id(download), None)
        except Exception:
            self.active_downloads.pop(id(download), None)
            self.download_key_lookup.pop(id(download), None)

    def update_download_state(self, download):
        try:
            entry = self.download_entries.get(self.download_key_lookup.get(id(download)))
            if not entry:
                return
            entry["status"] = self.resolve_download_status(download, entry)
            try:
                self.update_transfer_metrics(entry, download.receivedBytes(), download.totalBytes())
            except Exception:
                pass
            if entry["status"] == "Completed":
                entry["progress"] = 100
                entry["finished_at"] = time.time()
            elif entry["status"] in ("Cancelled", "Failed", "Interrupted"):
                entry["finished_at"] = time.time()
            else:
                entry["finished_at"] = 0.0
            self.sync_item_text(entry)
            if entry["status"] in ("Completed", "Cancelled", "Failed", "Interrupted"):
                self.active_downloads.pop(id(download), None)
                self.download_key_lookup.pop(id(download), None)
            self.save_download_history()
            self.update_button_state()
            if self.dropdown.isVisible() or entry["status"] in ("Completed", "Cancelled", "Failed", "Interrupted"):
                self.refresh_dropdown()
        except RuntimeError:
            self.active_downloads.pop(id(download), None)
            self.download_key_lookup.pop(id(download), None)
        except Exception:
            self.active_downloads.pop(id(download), None)
            self.download_key_lookup.pop(id(download), None)

    def activate_download_item(self, item):
        entry = self.download_entries.get(item.data(Qt.UserRole))
        if entry and entry["status"] == "Completed" and os.path.exists(entry["path"]):
            QDesktopServices.openUrl(QUrl.fromLocalFile(entry["path"]))
            return
        if entry and entry.get("download") is not None and entry["status"] in ("Starting", "Downloading", "Paused"):
            self.toggle_pause_resume(item)

    def toggle_pause_resume(self, item):
        entry = self.get_entry_for_item(item)
        download = entry.get("download") if entry else None
        if download is None:
            return
        try:
            is_paused = self.download_is_paused(download)
        except Exception:
            return
        if is_paused:
            download.resume()
            entry["status"] = "Downloading"
        else:
            download.pause()
            entry["status"] = "Paused"
        self.sync_item_text(entry)
        self.save_download_history()
        self.update_button_state()
        if self.dropdown.isVisible():
            self.refresh_dropdown()

    def open_selected_download(self, list_widget):
        item = list_widget.currentItem()
        if item is None:
            return
        entry = self.download_entries.get(item.data(Qt.UserRole))
        if entry and entry["status"] == "Completed" and os.path.exists(entry["path"]):
            QDesktopServices.openUrl(QUrl.fromLocalFile(entry["path"]))

    def show_selected_in_folder(self, list_widget):
        item = list_widget.currentItem()
        if item is None:
            return
        entry = self.download_entries.get(item.data(Qt.UserRole))
        if not entry:
            return
        folder_path = os.path.dirname(entry["path"])
        if os.path.isdir(folder_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder_path))

    def clear_finished_downloads(self):
        rows_to_remove = []
        for row in range(self.download_list.count()):
            item = self.download_list.item(row)
            entry_key = item.data(Qt.UserRole)
            entry = self.download_entries.get(entry_key)
            if entry and entry["status"] in ("Completed", "Cancelled", "Failed", "Interrupted"):
                rows_to_remove.append(row)
                live_download = entry.get("download")
                if live_download is not None:
                    self.active_downloads.pop(id(live_download), None)
                    self.download_key_lookup.pop(id(live_download), None)
                self.download_entries.pop(entry_key, None)
        for row in reversed(rows_to_remove):
            self.download_list.takeItem(row)
        self.save_download_history()
        self.update_button_state()
        if self.dropdown.isVisible():
            self.refresh_dropdown()
        else:
            self.update_popup_action_state()

    def remove_entry(self, entry):
        if not entry:
            return
        live_download = entry.get("download")
        if live_download is not None:
            self.active_downloads.pop(id(live_download), None)
            self.download_key_lookup.pop(id(live_download), None)
        item = entry.get("item")
        if item is not None:
            row = self.download_list.row(item)
            if row >= 0:
                self.download_list.takeItem(row)
        self.download_entries.pop(entry["key"], None)
        self.save_download_history()
        self.update_button_state()
        if self.dropdown.isVisible():
            self.refresh_dropdown()
        else:
            self.update_popup_action_state()

    def cancel_or_remove_selected_download(self, list_widget):
        entry = self.get_selected_entry(list_widget)
        if not entry:
            return
        download = entry.get("download")
        if self.can_cancel_entry(entry) and download is not None:
            try:
                download.cancel()
            except Exception:
                pass
            entry["status"] = "Cancelled"
            entry["speed_bps"] = 0.0
            entry["eta_seconds"] = None
            entry["finished_at"] = time.time()
            self.active_downloads.pop(id(download), None)
            self.download_key_lookup.pop(id(download), None)
            entry["download"] = None
            self.sync_item_text(entry)
            self.save_download_history()
            self.update_button_state()
            if self.dropdown.isVisible():
                self.refresh_dropdown()
            return
        if self.can_remove_entry(entry):
            self.remove_entry(entry)

    def finalize_active_downloads_on_shutdown(self):
        dirty = False
        for entry in self.download_entries.values():
            if entry["status"] not in ("Starting", "Downloading", "Paused"):
                continue
            live_download = entry.get("download")
            if live_download is not None:
                try:
                    live_download.cancel()
                except Exception:
                    pass
            if os.path.exists(entry["path"]) and entry.get("total", 0) > 0 and entry.get("received", 0) >= entry.get("total", 0):
                entry["status"] = "Completed"
                entry["progress"] = 100
            else:
                entry["status"] = "Interrupted"
            entry["download"] = None
            entry["speed_bps"] = 0.0
            entry["eta_seconds"] = None
            self.sync_item_text(entry)
            dirty = True
        self.active_downloads.clear()
        self.download_key_lookup.clear()
        if dirty:
            self.save_download_history()
            self.update_button_state()

    def retry_selected_download(self, list_widget):
        entry = self.get_selected_entry(list_widget)
        if not entry:
            return
        url = (entry.get("url") or "").strip()
        if not url:
            return
        if self.browser_window and hasattr(self.browser_window, "open_target_in_browser"):
            entry["status"] = "Retrying"
            entry["speed_bps"] = 0.0
            entry["eta_seconds"] = None
            entry["finished_at"] = 0.0
            self.sync_item_text(entry)
            self.save_download_history()
            self.browser_window.open_target_in_browser(self.browser_window.current_browser(), url)


class DetachedBrowserWindow(QMainWindow):
    def __init__(self, history_manager, download_manager, qprofile=None, theme_snapshot=None, parent_window=None, incognito=False):
        super().__init__(parent_window)
        theme_snapshot = theme_snapshot or {}
        self.history_manager = history_manager
        self.download_manager = download_manager
        self.parent_window = parent_window
        self.incognito = incognito
        if qprofile is not None:
            self.qprofile = qprofile
        elif parent_window is not None and hasattr(parent_window, "qprofile"):
            self.qprofile = parent_window.qprofile
        else:
            self.qprofile = QWebEngineProfile(self)
        self.setWindowTitle("Aurora Browser")
        self.setGeometry(160, 120, 980, 700)
        self.url_bar = QLineEdit()
        self.url_bar.setPlaceholderText("Enter URL or search")
        self.url_bar.returnPressed.connect(self.load_url)
        self.back_button = QPushButton("Back")
        self.forward_button = QPushButton("Forward")
        self.refresh_button = QPushButton("Refresh")
        self.home_button = QPushButton("Home")
        self.back_button.clicked.connect(lambda: self.current_browser().back())
        self.forward_button.clicked.connect(lambda: self.current_browser().forward())
        self.refresh_button.clicked.connect(lambda: self.current_browser().reload())
        self.home_button.clicked.connect(self.go_home)
        controls = QHBoxLayout()
        controls.setContentsMargins(10, 10, 10, 0)
        controls.setSpacing(8)
        controls.addWidget(self.back_button)
        controls.addWidget(self.forward_button)
        controls.addWidget(self.home_button)
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.url_bar, 1)
        self.browser = BrowserTab(self.history_manager, self.download_manager, self.incognito, qprofile=self.qprofile)
        self.browser.urlChanged.connect(self.on_url_changed)
        self.browser.titleChanged.connect(self.on_title_changed)
        try:
            self.browser.page().recentlyAudibleChanged.connect(lambda *_args: sync_window_soundscape_with_browser_audio(self.parent_window))
        except Exception:
            pass
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addLayout(controls)
        layout.addWidget(self.browser, 1)
        self.setCentralWidget(container)
        stylesheet = theme_snapshot.get("stylesheet")
        if isinstance(stylesheet, str) and stylesheet.strip():
            self.setStyleSheet(stylesheet)
        window_icon = theme_snapshot.get("window_icon")
        if isinstance(window_icon, QIcon):
            self.setWindowIcon(window_icon)

    def current_browser(self):
        return self.browser

    def go_home(self):
        if self.parent_window and hasattr(self.parent_window, "open_target_in_browser"):
            target = getattr(self.parent_window, "homepage", "aurora://home")
            self.parent_window.open_target_in_browser(self.browser, target)
        else:
            self.browser.setUrl(QUrl("https://www.google.com"))

    def load_url(self):
        url = self.url_bar.text().strip()
        if not url:
            return
        if self.parent_window and hasattr(self.parent_window, "open_target_in_browser"):
            if not url.startswith("http") and not url.lower().startswith("aurora:"):
                if " " in url or "." not in url:
                    url = self.parent_window.search_url_for_query(url) if hasattr(self.parent_window, "search_url_for_query") else ("https://www.google.com/search?q=" + quote_plus(url))
                else:
                    url = "https://" + url
            self.parent_window.open_target_in_browser(self.browser, url)
            return
        if not url.startswith("http"):
            if " " in url or "." not in url:
                url = "https://www.google.com/search?q=" + quote_plus(url)
            else:
                url = "https://" + url
        self.browser.setUrl(QUrl(url))

    def on_url_changed(self, url):
        self.url_bar.setText(url.toString())

    def on_title_changed(self, title):
        cleaned = (title or "").strip()
        self.setWindowTitle(cleaned or "Aurora Browser")

    def open_external_browser_window(self, source_tab=None):
        if self.parent_window and hasattr(self.parent_window, "open_external_browser_window"):
            return self.parent_window.open_external_browser_window(source_tab=source_tab or self.browser)
        return None

    def closeEvent(self, event):
        stop_and_dispose_webview(self.browser)
        if self.parent_window is not None:
            sync_window_soundscape_with_browser_audio(self.parent_window)
        super().closeEvent(event)

# ----------------- BookmarkManager -----------------
class BookmarkManager(QWidget):
    bookmarkClicked = pyqtSignal(str)
    bookmarksChanged = pyqtSignal()

    def __init__(self, profile="default"):
        super().__init__()
        self.setWindowTitle("Bookmarks")
        self.setGeometry(200, 200, 560, 620)
        profile_base_dir = os.path.join(os.path.expanduser("~"), ".aurora_browser", profile)
        os.makedirs(profile_base_dir, exist_ok=True)
        self.bookmarks_file = os.path.join(profile_base_dir, "bookmarks.json")
        self.bookmarks = self.load_bookmarks()
        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(18, 18, 18, 18)
        self.layout.setSpacing(12)
        title = QLabel("Bookmarks")
        title.setStyleSheet("font-size: 22px; font-weight: 800;")
        subtitle = QLabel("Save important pages for this profile and reopen them quickly.")
        subtitle.setStyleSheet("color: #94abd1;")
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Filter bookmarks by title or URL")
        self.search_bar.textChanged.connect(self.refresh_list)
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("color: #b7c8e8; font-weight: 600;")
        self.bookmark_list = QListWidget()
        self.bookmark_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.layout.addWidget(self.bookmark_list)
        self.layout.insertWidget(0, title)
        self.layout.insertWidget(1, subtitle)
        self.layout.insertWidget(2, self.search_bar)
        self.layout.insertWidget(3, self.summary_label)
        self.open_button = QPushButton("Open Selected")
        self.open_button.clicked.connect(self.open_selected_bookmark)
        self.copy_button = QPushButton("Copy URL")
        self.copy_button.clicked.connect(self.copy_selected_bookmark_url)
        self.add_button = QPushButton("Add Bookmark")
        self.add_button.clicked.connect(self.add_bookmark)
        self.edit_button = QPushButton("Edit Selected")
        self.edit_button.clicked.connect(self.edit_selected_bookmark)
        self.delete_button = QPushButton("Delete Bookmark")
        self.delete_button.clicked.connect(self.delete_bookmark)
        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        action_row.addWidget(self.open_button)
        action_row.addWidget(self.copy_button)
        action_row.addWidget(self.edit_button)
        action_row.addStretch()
        action_row.addWidget(self.add_button)
        action_row.addWidget(self.delete_button)
        self.layout.addLayout(action_row)
        self.setLayout(self.layout)
        self.bookmark_list.itemDoubleClicked.connect(self.open_bookmark)
        style_aux_window(self)
        self.refresh_list()

    def normalize_bookmarks(self, bookmarks):
        normalized = []
        seen_urls = set()
        for bookmark in bookmarks:
            if not isinstance(bookmark, dict):
                continue
            title = (bookmark.get("title") or "").strip()
            url = (bookmark.get("url") or "").strip()
            if not url:
                continue
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            url_key = url.lower()
            if url_key in seen_urls:
                continue
            seen_urls.add(url_key)
            normalized.append({
                "title": title or prettify_host_label(url),
                "url": url,
            })
        return normalized

    def load_bookmarks(self):
        if os.path.exists(self.bookmarks_file):
            try:
                with open(self.bookmarks_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return self.normalize_bookmarks(data)
            except Exception as e:
                print(f"Error loading bookmarks: {e}")
                return []
        return []

    def save_bookmarks(self):
        try:
            self.bookmarks = self.normalize_bookmarks(self.bookmarks)
            with open(self.bookmarks_file, "w", encoding="utf-8") as f:
                json.dump(self.bookmarks, f, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Bookmark Save Error", f"Could not save bookmarks: {e}")

    def add_bookmark(self):
        title, ok1 = QInputDialog.getText(self, "Add Bookmark", "Title:")
        if not ok1 or not title.strip():
            return
        url, ok2 = QInputDialog.getText(self, "Add Bookmark", "URL:")  # Fixed string literal
        if not ok2 or not url.strip():  # Enhanced check with strip() for whitespace
            QMessageBox.warning(self, "Invalid Input", "URL cannot be empty.")
            return
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        # Validate URL format
        try:
            QUrl(url).isValid()
        except:
            QMessageBox.warning(self, "Invalid URL", "Please enter a valid URL.")
            return
        self.bookmarks.append({"title": title.strip(), "url": url})
        self.save_bookmarks()
        self.refresh_list()
        self.bookmarksChanged.emit()

    def selected_bookmark_item(self):
        item = self.bookmark_list.currentItem()
        return item.data(Qt.UserRole) if item and item.data(Qt.UserRole) else None

    def edit_selected_bookmark(self):
        bookmark = self.selected_bookmark_item()
        if not bookmark:
            QMessageBox.information(self, "Edit Bookmark", "Select a bookmark first.")
            return
        new_title, ok1 = QInputDialog.getText(self, "Edit Bookmark", "Title:", text=bookmark.get("title", ""))
        if not ok1 or not new_title.strip():
            return
        new_url, ok2 = QInputDialog.getText(self, "Edit Bookmark", "URL:", text=bookmark.get("url", ""))
        if not ok2 or not new_url.strip():
            return
        new_url = new_url.strip()
        if not new_url.startswith(("http://", "https://")):
            new_url = "https://" + new_url
        for entry in self.bookmarks:
            if entry.get("url", "").strip() == bookmark.get("url", "").strip():
                entry["title"] = new_title.strip()
                entry["url"] = new_url
                break
        self.save_bookmarks()
        self.refresh_list()
        self.bookmarksChanged.emit()

    def delete_bookmark(self):
        selected_items = self.bookmark_list.selectedItems()
        if not selected_items:
            return
        selected_pairs = {
            (item.data(Qt.UserRole)["title"], item.data(Qt.UserRole)["url"])
            for item in selected_items if item.data(Qt.UserRole)
        }
        self.bookmarks = [
            b for b in self.bookmarks
            if (b["title"], b["url"]) not in selected_pairs
        ]
        self.save_bookmarks()
        self.refresh_list()
        self.bookmarksChanged.emit()

    def open_bookmark(self, item):
        data = item.data(Qt.UserRole)
        if not data:
            return
        self.bookmarkClicked.emit(data["url"])

    def refresh_list(self):
        filter_text = self.search_bar.text().strip().lower() if hasattr(self, "search_bar") else ""
        self.bookmark_list.clear()
        for bookmark in self.bookmarks:
            haystack = f"{bookmark['title']} {bookmark['url']}".lower()
            if filter_text and filter_text not in haystack:
                continue
            host = (urlparse(bookmark["url"]).hostname or bookmark["url"]).replace("www.", "")
            item = QListWidgetItem()
            item.setData(Qt.UserRole, bookmark)
            item.setToolTip(bookmark["url"])
            item.setSizeHint(QSize(0, 62))
            self.bookmark_list.addItem(item)
            row_widget = QWidget()
            row_layout = QVBoxLayout(row_widget)
            row_layout.setContentsMargins(10, 8, 10, 8)
            row_layout.setSpacing(2)
            title_label = QLabel(bookmark["title"])
            title_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #edf4ff;")
            meta_label = QLabel(host)
            meta_label.setStyleSheet("font-size: 12px; color: #8fb0da;")
            meta_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row_layout.addWidget(title_label)
            row_layout.addWidget(meta_label)
            self.bookmark_list.setItemWidget(item, row_widget)
        visible_count = self.bookmark_list.count()
        self.summary_label.setText(f"Stored bookmarks: {len(self.bookmarks)}   |   Showing: {visible_count}")

    def open_selected_bookmark(self):
        item = self.bookmark_list.currentItem()
        if item:
            self.open_bookmark(item)

    def copy_selected_bookmark_url(self):
        item = self.bookmark_list.currentItem()
        if item and item.data(Qt.UserRole):
            QApplication.clipboard().setText(item.data(Qt.UserRole)["url"])

# ----------------- IncognitoWindow -----------------
class IncognitoWindow(QMainWindow):
    def __init__(self, theme_snapshot=None):
        super().__init__()
        theme_snapshot = theme_snapshot or {}
        self.current_theme = (theme_snapshot.get("current_theme") or "dark").strip().lower()
        self.custom_color = theme_snapshot.get("custom_color")
        self.wallpaper_path = theme_snapshot.get("wallpaper_path")
        self.setWindowTitle("Incognito Window")
        self.setGeometry(100, 100, 900, 600)
        self.youtube_fullscreen_fix_enabled = False
        self.video_page_tweaks_enabled = False
        self.is_fullscreen = False
        self.was_maximized_before_fullscreen = False
        self.child_windows = []
        self.browser_audio_active = False
        self.soundscape_paused_for_browser_audio = False
        self.qprofile = QWebEngineProfile(self)
        self.qprofile.setHttpUserAgent(DEFAULT_USER_AGENT)
        if hasattr(self.qprofile, "setHttpAcceptLanguage"):
            self.qprofile.setHttpAcceptLanguage("en-US,en;q=0.9")
        self.history_manager = HistoryManager()
        self.download_manager = DownloadManager(self, popup_only=True)
        self.download_manager.connect_profile(self.qprofile)
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self.add_new_tab_button = QPushButton("+")
        self.add_new_tab_button.clicked.connect(lambda: self.add_new_tab(QUrl("https://www.google.com"), "New Tab", incognito=True))
        self.add_new_tab_button.setFixedWidth(40)
        self.url_bar = QLineEdit()
        self.url_bar.setPlaceholderText("Enter URL or search")
        self.url_bar.returnPressed.connect(self.load_url)
        self.back_button = QPushButton("Back")
        self.forward_button = QPushButton("Forward")
        self.home_button = QPushButton("Home")
        self.refresh_button = QPushButton("Refresh")
        self.downloads_button = QPushButton("Downloads")
        self.downloads_button.setFixedHeight(30)
        self.downloads_button.setMinimumWidth(100)
        self.downloads_button.setMaximumWidth(118)
        self.back_button.clicked.connect(lambda: self.current_browser().back())
        self.forward_button.clicked.connect(lambda: self.current_browser().forward())
        self.home_button.clicked.connect(lambda: self.current_browser().setUrl(QUrl("https://www.google.com")))
        self.refresh_button.clicked.connect(lambda: self.current_browser().reload())
        button_layout = QHBoxLayout()
        button_layout.addWidget(self.back_button)
        button_layout.addWidget(self.forward_button)
        button_layout.addWidget(self.home_button)
        button_layout.addWidget(self.refresh_button)
        button_layout.addWidget(self.downloads_button)
        main_layout = QVBoxLayout()
        main_layout.addWidget(self.url_bar)
        main_layout.addLayout(button_layout)
        tab_layout = QVBoxLayout()
        tab_layout.addWidget(self.add_new_tab_button)
        tab_layout.addWidget(self.tab_widget)
        main_layout.addLayout(tab_layout)
        main_layout.setContentsMargins(0, 0, 0, 0)
        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)
        self.download_manager.attach_button(self.downloads_button)
        self.create_menu()
        self.fullscreen_shortcut = QShortcut(QKeySequence(Qt.Key_F11), self)
        self.fullscreen_shortcut.activated.connect(self.toggle_app_fullscreen)
        inherited_stylesheet = theme_snapshot.get("stylesheet")
        if isinstance(inherited_stylesheet, str) and inherited_stylesheet.strip():
            self.setStyleSheet(inherited_stylesheet)
        self.add_new_tab(QUrl("https://www.google.com"), "New Tab", incognito=True)

    def create_menu(self):
        menubar = self.menuBar()
        file_menu = QMenu("File", self)
        new_tab_action = QAction("New Tab", self)
        new_tab_action.triggered.connect(lambda: self.add_new_tab(QUrl("https://www.google.com"), "New Tab", incognito=True))
        file_menu.addAction(new_tab_action)
        menubar.addMenu(file_menu)

    def add_new_tab(self, url, label="New Tab", incognito=False):
        new_tab = BrowserTab(self.history_manager, self.download_manager, incognito, qprofile=self.qprofile)
        self.bind_tab_signals(new_tab)
        new_tab.setUrl(url)
        index = self.tab_widget.addTab(new_tab, label)
        self.tab_widget.setCurrentIndex(index)

    def bind_tab_signals(self, tab):
        tab.titleChanged.connect(lambda title, current_tab=tab: self.update_tab_title(current_tab, title))
        tab.urlChanged.connect(lambda _url, current_tab=tab: self.update_bookmark_button_state(current_tab))
        try:
            tab.page().recentlyAudibleChanged.connect(lambda *_args: sync_window_soundscape_with_browser_audio(self))
        except Exception:
            pass

    def update_bookmark_button_state(self, browser=None):
        return

    def update_tab_title(self, tab, title):
        index = self.tab_widget.indexOf(tab)
        if index == -1:
            return
        cleaned = (title or "").strip()
        if not cleaned:
            cleaned = "New Tab"
        self.tab_widget.setTabText(index, cleaned[:28])

    def close_tab(self, index):
        if self.tab_widget.count() > 1:
            closed_tab = self.tab_widget.widget(index)
            self.tab_widget.removeTab(index)
            stop_and_dispose_webview(closed_tab)
            sync_window_soundscape_with_browser_audio(self)

    def load_url(self):
        url = self.url_bar.text().strip()
        if not url:
            return
        if not url.startswith("http"):
            if " " in url or "." not in url:
                url = "https://www.google.com/search?udm=14&q=" + quote_plus(url)
            else:
                url = "https://" + url
        self.current_browser().setUrl(QUrl(url))

    def current_browser(self):
        return self.tab_widget.currentWidget()

    def open_external_browser_window(self, source_tab=None):
        profile = None
        if source_tab is not None:
            try:
                profile = source_tab.page().profile()
            except Exception:
                profile = None
        child_window = DetachedBrowserWindow(
            self.history_manager,
            self.download_manager,
            qprofile=profile or self.qprofile,
            theme_snapshot={
                "stylesheet": self.styleSheet(),
                "window_icon": self.windowIcon(),
            },
            parent_window=self,
            incognito=True,
        )
        self.child_windows.append(child_window)
        child_window.destroyed.connect(lambda *_args, win=child_window: self.child_windows.remove(win) if win in self.child_windows else None)
        child_window.show()
        child_window.raise_()
        child_window.activateWindow()
        return child_window.current_browser()

    def set_browser_chrome_visible(self, visible):
        widgets = [
            self.url_bar,
            self.back_button,
            self.forward_button,
            self.home_button,
            self.refresh_button,
            self.downloads_button,
            self.add_new_tab_button,
        ]
        for widget in widgets:
            if widget:
                widget.setVisible(visible)
        self.tab_widget.tabBar().setVisible(visible)
        self.menuBar().setVisible(visible)
        if hasattr(self, "bookmark_bar"):
            self.bookmark_bar.setVisible(visible and getattr(self, "bookmark_bar_visible", True))

    def refresh_active_browser_geometry(self):
        browser = self.current_browser()
        if browser:
            browser.setGeometry(self.centralWidget().geometry())
            browser.update()

    def stabilize_media_mode_for_fullscreen(self):
        self.refresh_active_browser_geometry()

    def finalize_media_mode_after_fullscreen(self):
        self.refresh_active_browser_geometry()

    def enter_app_fullscreen(self):
        self.was_maximized_before_fullscreen = self.isMaximized()
        self.set_browser_chrome_visible(False)
        if hasattr(self, "bookmark_bar"):
            self.bookmark_bar.setVisible(False)
        self.showFullScreen()
        self.is_fullscreen = True
        browser = self.current_browser()
        if browser:
            screen_size = QApplication.primaryScreen().size()
            browser.setGeometry(0, 0, screen_size.width(), screen_size.height())
            browser.setVisible(True)
        QTimer.singleShot(0, self.refresh_active_browser_geometry)

    def exit_app_fullscreen(self):
        self.set_browser_chrome_visible(True)
        if hasattr(self, "bookmark_bar"):
            self.bookmark_bar.setVisible(getattr(self, "bookmark_bar_visible", True))
        if self.was_maximized_before_fullscreen:
            self.showMaximized()
        else:
            self.showNormal()
        self.is_fullscreen = False
        QTimer.singleShot(0, self.refresh_active_browser_geometry)

    def toggle_app_fullscreen(self):
        if self.isFullScreen():
            self.exit_app_fullscreen()
        else:
            self.enter_app_fullscreen()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F11:
            self.toggle_app_fullscreen()
            event.accept()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.download_manager.reposition_popup()

    def closeEvent(self, event):
        self.download_manager.finalize_active_downloads_on_shutdown()
        for child_window in list(getattr(self, "child_windows", []) or []):
            try:
                child_window.close()
            except Exception:
                pass
        for i in range(self.tab_widget.count()):
            stop_and_dispose_webview(self.tab_widget.widget(i), replace_page=False)
        event.accept()

# ----------------- PasswordManagerWindow -----------------
class PasswordManagerWindow(QMainWindow):
    def __init__(self, browser_window=None):
        super().__init__()
        self.browser_window = browser_window
        self.setWindowTitle("Password Manager")
        self.setGeometry(150, 150, 620, 560)
        profile_dir = getattr(browser_window, "profile_base_dir", os.path.join(os.path.expanduser("~"), ".aurora_browser", "default"))
        os.makedirs(profile_dir, exist_ok=True)
        self.credentials_file = os.path.join(profile_dir, "passwords.secure.json")
        self.legacy_credentials_file = os.path.join(os.path.expanduser("~"), "my_browser_passwords.json")
        self.autofill_rules_file = os.path.join(profile_dir, "autofill_rules.json")
        self.autofill_rules = self.load_autofill_rules()
        self.credentials = self.load_credentials()
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.ExtendedSelection)
        title = QLabel("Password Manager")
        title.setStyleSheet("font-size: 22px; font-weight: 800;")
        subtitle = QLabel("Store credentials with Windows account protection and use them on the current page.")
        subtitle.setStyleSheet("color: #94abd1;")
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Filter credentials by site or username")
        self.search_bar.textChanged.connect(self.populate_list)
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("color: #b7c8e8; font-weight: 600;")
        self.site_policy_label = QLabel("")
        self.site_policy_label.setStyleSheet("color: #94abd1; font-weight: 600;")
        self.populate_list()
        add_btn = QPushButton("Add Credential")
        add_btn.clicked.connect(self.add_credential)
        edit_btn = QPushButton("Edit Selected")
        edit_btn.clicked.connect(self.edit_selected_credential)
        delete_btn = QPushButton("Delete Selected")
        delete_btn.clicked.connect(self.delete_selected_credentials)
        copy_user_btn = QPushButton("Copy Username")
        copy_user_btn.clicked.connect(self.copy_selected_username)
        copy_pass_btn = QPushButton("Copy Password")
        copy_pass_btn.clicked.connect(self.copy_selected_password)
        reveal_btn = QPushButton("Show Password")
        reveal_btn.clicked.connect(self.show_selected_password)
        autofill_btn = QPushButton("Autofill Current Page")
        autofill_btn.clicked.connect(self.autofill)
        layout = QVBoxLayout()
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self.search_bar)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.site_policy_label)
        site_rule_row = QHBoxLayout()
        site_rule_row.setSpacing(10)
        allow_site_btn = QPushButton("Allow This Site")
        ask_site_btn = QPushButton("Ask For This Site")
        block_site_btn = QPushButton("Block This Site")
        allow_site_btn.clicked.connect(lambda: self.set_current_site_autofill_mode("allow"))
        ask_site_btn.clicked.connect(lambda: self.set_current_site_autofill_mode("ask"))
        block_site_btn.clicked.connect(lambda: self.set_current_site_autofill_mode("deny"))
        site_rule_row.addWidget(allow_site_btn)
        site_rule_row.addWidget(ask_site_btn)
        site_rule_row.addWidget(block_site_btn)
        site_rule_row.addStretch()
        layout.addLayout(site_rule_row)
        layout.addWidget(self.list_widget)
        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        action_row.addWidget(add_btn)
        action_row.addWidget(edit_btn)
        action_row.addWidget(delete_btn)
        action_row.addWidget(copy_user_btn)
        action_row.addWidget(copy_pass_btn)
        action_row.addWidget(reveal_btn)
        action_row.addStretch()
        action_row.addWidget(autofill_btn)
        layout.addLayout(action_row)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        style_aux_window(self)

    def prompt_password_value(self, title, label, text=""):
        dialog = QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setTextEchoMode(QLineEdit.Password)
        dialog.setTextValue(text or "")
        style_aux_window(dialog)
        if dialog.exec() == QDialog.Accepted:
            value = dialog.textValue()
            if value:
                return value, True
        return "", False

    def encrypt_password_value(self, password):
        raw = (password or "").encode("utf-8")
        return base64.b64encode(protect_bytes(raw)).decode("ascii")

    def decrypt_password_value(self, credential):
        if not isinstance(credential, dict):
            return ""
        if credential.get("password"):
            return credential.get("password", "")
        password_blob = credential.get("password_blob", "")
        if not password_blob:
            return ""
        try:
            decrypted = unprotect_bytes(base64.b64decode(password_blob))
            return decrypted.decode("utf-8")
        except Exception:
            return ""

    def build_vault_entry(self, website, username, password=None, password_blob=None, created_at=None, updated_at=None):
        website_value = (website or "").strip()
        username_value = (username or "").strip()
        if not website_value or not username_value:
            return None
        secret_blob = password_blob or ""
        if password is not None:
            if not password:
                return None
            secret_blob = self.encrypt_password_value(password)
        if not secret_blob:
            return None
        now = int(time.time())
        return {
            "website": website_value,
            "username": username_value,
            "password_blob": secret_blob,
            "created_at": int(created_at or now),
            "updated_at": int(updated_at or now),
        }

    def normalize_site_key(self, website):
        raw_value = (website or "").strip().lower()
        if not raw_value:
            return ""
        parsed = urlparse(raw_value if "://" in raw_value else "https://" + raw_value)
        host = (parsed.hostname or raw_value).replace("www.", "")
        return host

    def load_autofill_rules(self):
        if not os.path.exists(self.autofill_rules_file):
            return {}
        try:
            with open(self.autofill_rules_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}
            return {
                self.normalize_site_key(host): mode
                for host, mode in data.items()
                if self.normalize_site_key(host) and mode in {"allow", "ask", "deny"}
            }
        except Exception:
            return {}

    def save_autofill_rules(self):
        try:
            with open(self.autofill_rules_file, "w", encoding="utf-8") as f:
                json.dump(self.autofill_rules, f, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Autofill Rules", f"Could not save autofill site rules: {e}")

    def current_autofill_mode(self):
        site = self.current_site_key()
        if not site:
            return "ask"
        return self.autofill_rules.get(site, "ask")

    def set_current_site_autofill_mode(self, mode):
        site = self.current_site_key()
        if not site:
            QMessageBox.information(self, "Autofill Rules", "Open a login site first, then set its autofill rule.")
            return
        if mode not in {"allow", "ask", "deny"}:
            return
        self.autofill_rules[site] = mode
        self.save_autofill_rules()
        self.populate_list()
        labels = {
            "allow": "Autofill allowed for this site.",
            "ask": "Aurora will ask before autofill on this site.",
            "deny": "Autofill blocked for this site.",
        }
        QMessageBox.information(self, "Autofill Rules", labels[mode])

    def credential_matches_current_site(self, credential, current_site):
        if not credential or not current_site:
            return False
        credential_site = self.normalize_site_key(credential.get("website", ""))
        return bool(credential_site) and host_matches_rule(current_site, credential_site)

    def current_site_key(self):
        if not self.browser_window or not self.browser_window.current_browser():
            return ""
        try:
            return self.normalize_site_key(self.browser_window.current_browser().url().toString())
        except Exception:
            return ""

    def selected_credential(self):
        selected = self.list_widget.currentItem()
        return selected.data(Qt.UserRole) if selected and selected.data(Qt.UserRole) else None

    def find_credential_index(self, website, username):
        site_key = self.normalize_site_key(website)
        user_key = (username or "").strip().lower()
        for index, cred in enumerate(self.credentials):
            if self.normalize_site_key(cred.get("website", "")) == site_key and (cred.get("username", "").strip().lower() == user_key):
                return index
        return -1

    def load_credentials(self):
        if os.path.exists(self.credentials_file):
            try:
                with open(self.credentials_file, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                if isinstance(payload, dict) and payload.get("kind") == "dpapi-vault":
                    entries = payload.get("entries", [])
                    loaded = []
                    for cred in entries:
                        if not isinstance(cred, dict):
                            continue
                        entry = self.build_vault_entry(
                            cred.get("website", ""),
                            cred.get("username", ""),
                            password_blob=cred.get("password_blob", ""),
                            created_at=cred.get("created_at"),
                            updated_at=cred.get("updated_at"),
                        )
                        if entry:
                            loaded.append(entry)
                    return loaded
                if isinstance(payload, list):
                    converted = []
                    for cred in payload:
                        if not isinstance(cred, dict):
                            continue
                        entry = self.build_vault_entry(
                            cred.get("website", ""),
                            cred.get("username", ""),
                            password=cred.get("password", ""),
                        )
                        if entry:
                            converted.append(entry)
                    self.credentials = converted
                    self.save_credentials()
                    return converted
                if isinstance(payload, dict) and payload.get("kind") == "dpapi":
                    encrypted = base64.b64decode(payload.get("payload", ""))
                    raw = unprotect_bytes(encrypted).decode("utf-8")
                    data = json.loads(raw)
                    if isinstance(data, list):
                        converted = []
                        for cred in data:
                            if not isinstance(cred, dict):
                                continue
                            entry = self.build_vault_entry(
                                cred.get("website", ""),
                                cred.get("username", ""),
                                password=cred.get("password", ""),
                            )
                            if entry:
                                converted.append(entry)
                        self.credentials = converted
                        self.save_credentials()
                        return converted
            except Exception as e:
                print("Error loading secure credentials:", e)
                return []
        if os.path.exists(self.legacy_credentials_file):
            try:
                with open(self.legacy_credentials_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    converted = []
                    for cred in data:
                        if not isinstance(cred, dict):
                            continue
                        entry = self.build_vault_entry(
                            cred.get("website", ""),
                            cred.get("username", ""),
                            password=cred.get("password", ""),
                        )
                        if entry:
                            converted.append(entry)
                    self.credentials = converted
                    self.save_credentials()
                    return converted
            except Exception as e:
                print("Error loading legacy credentials:", e)
        return []

    def save_credentials(self):
        try:
            normalized = []
            seen = set()
            for cred in self.credentials:
                if not isinstance(cred, dict):
                    continue
                website = (cred.get("website") or "").strip()
                username = (cred.get("username") or "").strip()
                if not website or not username:
                    continue
                password_blob = cred.get("password_blob") or ""
                password = cred.get("password")
                if password is not None and not password:
                    continue
                entry = self.build_vault_entry(
                    website,
                    username,
                    password=password,
                    password_blob=password_blob,
                    created_at=cred.get("created_at"),
                    updated_at=cred.get("updated_at") or int(time.time()),
                )
                if not entry:
                    continue
                key = (self.normalize_site_key(website), username.lower())
                if key in seen:
                    continue
                seen.add(key)
                normalized.append(entry)
            self.credentials = normalized
            payload = {
                "version": 3,
                "kind": "dpapi-vault",
                "entries": self.credentials,
            }
            with open(self.credentials_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            if os.path.exists(self.legacy_credentials_file):
                try:
                    os.remove(self.legacy_credentials_file)
                except Exception:
                    pass
        except Exception as e:
            QMessageBox.warning(self, "Save Error", f"Could not save credentials: {e}")

    def populate_list(self):
        self.list_widget.clear()
        filter_text = self.search_bar.text().strip().lower() if hasattr(self, "search_bar") else ""
        current_site = self.current_site_key()
        ordered_credentials = sorted(
            self.credentials,
            key=lambda cred: (
                0 if current_site and self.credential_matches_current_site(cred, current_site) else 1,
                (cred.get("website") or "").lower(),
                (cred.get("username") or "").lower(),
            ),
        )
        best_index = -1
        for cred in ordered_credentials:
            haystack = f"{cred['website']} {cred['username']}".lower()
            if filter_text and filter_text not in haystack:
                continue
            website_label = cred["website"]
            if current_site and self.credential_matches_current_site(cred, current_site):
                website_label += "  [Current site]"
            item = QListWidgetItem(f"{website_label}\n{cred['username']}")
            item.setData(Qt.UserRole, cred)
            item.setSizeHint(QSize(0, 48))
            self.list_widget.addItem(item)
            if best_index == -1 and current_site and self.credential_matches_current_site(cred, current_site):
                best_index = self.list_widget.count() - 1
        if hasattr(self, "summary_label"):
            site_hint = f"   |   Current site match: {current_site}" if current_site else ""
            self.summary_label.setText(f"Stored credentials: {len(self.credentials)}{site_hint}")
        if hasattr(self, "site_policy_label"):
            mode = self.current_autofill_mode()
            mode_label = {
                "allow": "Allowed",
                "ask": "Ask every time",
                "deny": "Blocked",
            }.get(mode, "Ask every time")
            self.site_policy_label.setText(
                f"Current site rule: {current_site or 'No active site'}  |  {mode_label}"
            )
        if best_index >= 0 and self.list_widget.count() > best_index:
            self.list_widget.setCurrentRow(best_index)

    def add_credential(self):
        suggested_site = self.current_site_key()
        website, ok1 = QInputDialog.getText(self, "Add Credential", "Website:", text=suggested_site)
        if not ok1 or not website:
            return
        username, ok2 = QInputDialog.getText(self, "Add Credential", "Username:")
        if not ok2 or not username:
            return
        password, ok3 = self.prompt_password_value("Add Credential", "Password:")
        if not ok3 or not password:
            return
        index = self.find_credential_index(website, username)
        cred = self.build_vault_entry(website.strip(), username.strip(), password=password)
        if not cred:
            QMessageBox.warning(self, "Add Credential", "A valid website, username, and password are required.")
            return
        if index >= 0:
            self.credentials[index] = cred
        else:
            self.credentials.append(cred)
        self.save_credentials()
        self.populate_list()

    def edit_selected_credential(self):
        cred = self.selected_credential()
        if not cred:
            QMessageBox.information(self, "Edit Credential", "Select a credential first.")
            return
        website, ok1 = QInputDialog.getText(self, "Edit Credential", "Website:", text=cred.get("website", ""))
        if not ok1 or not website.strip():
            return
        username, ok2 = QInputDialog.getText(self, "Edit Credential", "Username:", text=cred.get("username", ""))
        if not ok2 or not username.strip():
            return
        current_password = self.decrypt_password_value(cred)
        password, ok3 = self.prompt_password_value("Edit Credential", "Password:", text=current_password)
        if not ok3 or not password:
            return
        original_index = self.find_credential_index(cred.get("website", ""), cred.get("username", ""))
        updated = self.build_vault_entry(
            website.strip(),
            username.strip(),
            password=password,
            created_at=cred.get("created_at"),
            updated_at=int(time.time()),
        )
        if not updated:
            QMessageBox.warning(self, "Edit Credential", "A valid website, username, and password are required.")
            return
        replacement_index = self.find_credential_index(website, username)
        if original_index >= 0:
            self.credentials[original_index] = updated
        elif replacement_index >= 0:
            self.credentials[replacement_index] = updated
        else:
            self.credentials.append(updated)
        self.save_credentials()
        self.populate_list()

    def autofill(self):
        current_site = self.current_site_key()
        if not current_site:
            QMessageBox.information(self, "Autofill", "Open the target website first, then try autofill again.")
            return
        site_mode = self.current_autofill_mode()
        if site_mode == "deny":
            QMessageBox.warning(self, "Autofill", f"Autofill is blocked for {current_site}.")
            return
        cred = self.selected_credential()
        if not cred:
            if current_site:
                for item_index in range(self.list_widget.count()):
                    item = self.list_widget.item(item_index)
                    item_cred = item.data(Qt.UserRole)
                    if item_cred and self.credential_matches_current_site(item_cred, current_site):
                        self.list_widget.setCurrentRow(item_index)
                        cred = item_cred
                        break
        if not cred:
            QMessageBox.information(self, "Autofill", "Select a credential or open the matching site first.")
            return
        if not self.credential_matches_current_site(cred, current_site):
            QMessageBox.warning(
                self,
                "Autofill",
                f"This credential is saved for {self.normalize_site_key(cred.get('website', '')) or cred.get('website', 'another site')}, not {current_site}.",
            )
            return
        if site_mode == "ask":
            decision = show_styled_question(
                self,
                "Autofill Password",
                f"Fill the saved credential for {cred.get('username', '')} on {current_site}?",
            )
            if decision != QMessageBox.Yes:
                return
        if cred:
            if not self.browser_window or not self.browser_window.current_browser():
                QMessageBox.information(self, "Autofill", "No active browser tab is available.")
                return
            secret_password = self.decrypt_password_value(cred)
            if not secret_password:
                QMessageBox.warning(self, "Autofill", "This credential could not be unlocked.")
                return
            username = json.dumps(cred["username"])
            password = json.dumps(secret_password)
            js_code = f"""
            (() => {{
                const usernameValue = {username};
                const passwordValue = {password};
                const dispatch = (el) => {{
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }};
                const isVisible = (el) => !!(el && el.offsetParent !== null);
                const candidates = Array.from(document.querySelectorAll('input'));
                const usernameField = candidates.find(el =>
                    isVisible(el) &&
                    !el.disabled &&
                    ['text', 'email', 'search', 'tel', 'url', ''].includes((el.type || '').toLowerCase()) &&
                    /(user|email|login|identifier|account|name)/i.test(`${{el.name}} ${{el.id}} ${{el.placeholder}}`)
                ) || candidates.find(el =>
                    isVisible(el) &&
                    !el.disabled &&
                    ['text', 'email', 'search', 'tel', 'url', ''].includes((el.type || '').toLowerCase())
                );
                const passwordField = candidates.find(el =>
                    isVisible(el) &&
                    !el.disabled &&
                    (el.type || '').toLowerCase() === 'password'
                );
                if (usernameField) {{
                    usernameField.focus();
                    usernameField.value = usernameValue;
                    dispatch(usernameField);
                }}
                if (passwordField) {{
                    passwordField.focus();
                    passwordField.value = passwordValue;
                    dispatch(passwordField);
                }}
                return {{ user: !!usernameField, pass: !!passwordField }};
            }})()
            """
            self.browser_window.current_browser().page().runJavaScript(
                js_code,
                lambda result: QMessageBox.information(
                    self,
                    "Autofill",
                    "Autofill applied." if result and (result.get("user") or result.get("pass")) else "No compatible login fields were found on this page."
                )
            )
        else:
            QMessageBox.warning(self, "Autofill", "Credential not found.")

    def delete_selected_credentials(self):
        selected_items = self.list_widget.selectedItems()
        if not selected_items:
            return
        if QMessageBox.question(self, "Delete Credentials", f"Delete {len(selected_items)} selected credential(s)?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        selected_pairs = {
            (item.data(Qt.UserRole)["website"], item.data(Qt.UserRole)["username"])
            for item in selected_items if item.data(Qt.UserRole)
        }
        self.credentials = [
            cred for cred in self.credentials
            if (cred["website"], cred["username"]) not in selected_pairs
        ]
        self.save_credentials()
        self.populate_list()

    def copy_selected_username(self):
        selected = self.list_widget.currentItem()
        if selected and selected.data(Qt.UserRole):
            QApplication.clipboard().setText(selected.data(Qt.UserRole)["username"])

    def copy_selected_password(self):
        cred = self.selected_credential()
        if cred:
            secret_password = self.decrypt_password_value(cred)
            if not secret_password:
                QMessageBox.warning(self, "Password Manager", "This credential could not be unlocked.")
                return
            clipboard = QApplication.clipboard()
            clipboard.setText(secret_password)
            QTimer.singleShot(45000, lambda expected=secret_password: self.clear_password_clipboard(expected))
            QMessageBox.information(self, "Password Manager", "Password copied to clipboard for 45 seconds.")

    def show_selected_password(self):
        cred = self.selected_credential()
        if cred:
            secret_password = self.decrypt_password_value(cred)
            if not secret_password:
                QMessageBox.warning(self, "Stored Password", "This credential could not be unlocked.")
                return
            QMessageBox.information(self, "Stored Password", f"{cred['website']}\n\nUsername: {cred['username']}\nPassword: {secret_password}")

    def clear_password_clipboard(self, expected_value):
        clipboard = QApplication.clipboard()
        try:
            if clipboard.text() == expected_value:
                clipboard.clear()
        except Exception:
            pass

# ----------------- ExtensionManagerDialog -----------------
class ExtensionManagerDialog(QDialog):
    def __init__(self, browser_window):
        super().__init__(browser_window)
        self.browser_window = browser_window
        self._is_refreshing = False
        self.setWindowTitle("Script Extensions (Beta)")
        self.setGeometry(220, 220, 640, 500)

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        title = QLabel("Script Extensions (Beta)")
        title.setStyleSheet("font-size: 22px; font-weight: 800;")
        subtitle = QLabel("Add JavaScript-based Aurora extensions, toggle them, and apply them to all open tabs.")
        subtitle.setStyleSheet("color: #94abd1;")
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Filter extensions by file name")
        self.search_bar.textChanged.connect(self.refresh_list)
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("color: #b7c8e8; font-weight: 600;")
        self.extension_list = QListWidget()
        self.extension_list.itemChanged.connect(self.on_item_changed)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self.search_bar)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.extension_list)

        action_row = QHBoxLayout()
        add_btn = QPushButton("Add Extension")
        remove_btn = QPushButton("Remove Selected")
        reload_btn = QPushButton("Apply Enabled")
        close_btn = QPushButton("Close")
        add_btn.clicked.connect(self.add_extensions)
        remove_btn.clicked.connect(self.remove_selected)
        reload_btn.clicked.connect(self.apply_enabled_extensions)
        close_btn.clicked.connect(self.accept)
        action_row.addWidget(add_btn)
        action_row.addWidget(remove_btn)
        action_row.addWidget(reload_btn)
        action_row.addStretch()
        action_row.addWidget(close_btn)
        layout.addLayout(action_row)

        self.setLayout(layout)
        self.refresh_list()
        style_aux_window(self)

    def refresh_list(self):
        self._is_refreshing = True
        self.extension_list.clear()
        filter_text = self.search_bar.text().strip().lower() if hasattr(self, "search_bar") else ""
        for ext in self.browser_window.extensions:
            file_path = ext.get("path", "")
            enabled = bool(ext.get("enabled", True))
            name = os.path.basename(file_path) if file_path else "Unnamed Extension"
            if not os.path.exists(file_path):
                name = f"{name} (missing file)"
            if filter_text and filter_text not in name.lower():
                continue
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, file_path)
            item.setToolTip(file_path)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
            self.extension_list.addItem(item)
        self._is_refreshing = False
        enabled_count = sum(1 for ext in self.browser_window.extensions if ext.get("enabled", True))
        self.summary_label.setText(f"Installed: {len(self.browser_window.extensions)}   |   Enabled: {enabled_count}")

    def add_extensions(self):
        file_paths, _ = QFileDialog.getOpenFileNames(self, "Select Extension(s)", "", "JavaScript Files (*.js)")
        if not file_paths:
            return
        existing = {ext.get("path") for ext in self.browser_window.extensions}
        for path in file_paths:
            abs_path = os.path.abspath(path)
            if abs_path not in existing:
                self.browser_window.extensions.append({"path": abs_path, "enabled": True})
                existing.add(abs_path)
        self.browser_window.save_extensions_config()
        self.browser_window.apply_extensions_to_all_tabs()
        self.refresh_list()

    def remove_selected(self):
        item = self.extension_list.currentItem()
        if not item:
            return
        file_path = item.data(Qt.UserRole)
        self.browser_window.extensions = [
            ext for ext in self.browser_window.extensions if ext.get("path") != file_path
        ]
        self.browser_window.save_extensions_config()
        self.browser_window.apply_extensions_to_all_tabs()
        self.refresh_list()

    def on_item_changed(self, item):
        if self._is_refreshing:
            return
        file_path = item.data(Qt.UserRole)
        enabled = item.checkState() == Qt.Checked
        for ext in self.browser_window.extensions:
            if ext.get("path") == file_path:
                ext["enabled"] = enabled
                break
        self.browser_window.save_extensions_config()
        self.browser_window.apply_extensions_to_all_tabs()

    def apply_enabled_extensions(self):
        self.browser_window.apply_extensions_to_all_tabs()
        QMessageBox.information(self, "Extension Manager", "Enabled extensions were applied to all open tabs.")

# ----------------- MyBrowser -----------------
class MyBrowser(QMainWindow):
    def __init__(self, settings_variant="modern"):
        super().__init__()
        self.settings_variant = settings_variant if settings_variant in ("modern", "legacy") else "modern"
        self.use_internal_settings_page = self.settings_variant == "modern"
        self.setWindowTitle("Aurora Browser")
        self.setGeometry(100, 100, 900, 600)
        icon_path = resolve_app_resource("Browser Icon 1.ico")
        icon = QIcon()
        app_instance = QApplication.instance()
        if app_instance:
            app_icon = app_instance.windowIcon()
            if not app_icon.isNull():
                icon = app_icon
        if icon.isNull():
            icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        if icon.isNull():
            icon = QIcon(sys.executable)
        if not icon.isNull():
            self.setWindowIcon(icon)
        else:
            print(f"Icon file not found at: {icon_path}")

        self.wallpaper = None
        self.custom_color = None
        self.dev_tools_window = None
        self.child_windows = []
        self.is_dragging = False
        self.drag_position = QPoint()

        # Music folder for soundscape
        self.music_folder = os.path.join(os.path.expanduser("~"), "Music")
        self.current_music_track = None

        self.profile = "default"
        profile_file = os.path.join(os.path.expanduser("~"), "profile.txt")
        if os.path.exists(profile_file):
            with open(profile_file, "r") as f:
                self.profile = f.read().strip() or "default"

        if self.profile.lower() == "default":
            self.qprofile = QWebEngineProfile.defaultProfile()
        else:
            self.qprofile = QWebEngineProfile(self.profile, self)

        self.profile_base_dir = os.path.join(os.path.expanduser("~"), ".aurora_browser", self.profile)
        os.makedirs(self.profile_base_dir, exist_ok=True)
        self.extensions_file = os.path.join(self.profile_base_dir, "extensions.json")
        self.homepage_file = os.path.join(self.profile_base_dir, "homepage.txt")
        self.quick_access_file = os.path.join(self.profile_base_dir, "quick_access.json")
        self.session_file = os.path.join(self.profile_base_dir, "session.txt")
        self.theme_file = os.path.join(self.profile_base_dir, "theme.json")
        self.weather_file = os.path.join(self.profile_base_dir, "weather.json")
        self.time_capsule_file = os.path.join(self.profile_base_dir, "time_capsule.json")
        self.download_history_file = os.path.join(self.profile_base_dir, "downloads.json")
        self.pinned_tabs_file = os.path.join(self.profile_base_dir, "pinned_tabs.json")
        self.browser_settings_file = os.path.join(self.profile_base_dir, "settings.json")
        self._internal_cache = {}
        self._internal_cache_version = 0
        self.media_request_history = []
        self.native_media_windows = []
        self.native_media_autoplay_tokens = set()

        if hasattr(self.qprofile, "setPersistentCookiesPolicy"):
            self.qprofile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        if hasattr(self.qprofile, "setHttpCacheType"):
            self.qprofile.setHttpCacheType(QWebEngineProfile.DiskHttpCache)
        if hasattr(self.qprofile, "setCachePath"):
            self.qprofile.setCachePath(os.path.join(self.profile_base_dir, "cache"))
        if hasattr(self.qprofile, "setPersistentStoragePath"):
            self.qprofile.setPersistentStoragePath(os.path.join(self.profile_base_dir, "storage"))
        if hasattr(self.qprofile, "setHttpCacheMaximumSize"):
            self.qprofile.setHttpCacheMaximumSize(256 * 1024 * 1024)

        qprofile_settings = self.qprofile.settings()
        set_webengine_attr(qprofile_settings, "JavascriptEnabled", True)
        set_webengine_attr(qprofile_settings, "LocalStorageEnabled", True)
        set_webengine_attr(qprofile_settings, "WebGLEnabled", True)
        set_webengine_attr(qprofile_settings, "Accelerated2dCanvasEnabled", True)
        set_webengine_attr(qprofile_settings, "DnsPrefetchEnabled", True)
        set_webengine_attr(qprofile_settings, "PluginsEnabled", True)
        set_webengine_attr(qprofile_settings, "FullScreenSupportEnabled", True)
        set_webengine_attr(qprofile_settings, "PlaybackRequiresUserGesture", False)
        set_webengine_attr(qprofile_settings, "JavascriptCanOpenWindows", True)
        set_webengine_attr(qprofile_settings, "AllowRunningInsecureContent", True)
        self.qprofile.setHttpUserAgent(DEFAULT_USER_AGENT)
        if hasattr(self.qprofile, "setHttpAcceptLanguage"):
            self.qprofile.setHttpAcceptLanguage("en-US,en;q=0.9")

        self.closed_tabs = []
        self.history_manager = HistoryManager(self.profile)
        self.history_manager.historyClicked.connect(self.open_history_url)
        self.download_manager = DownloadManager(self, history_file=self.download_history_file)
        self.download_manager.connect_profile(self.qprofile)
        self.current_theme = "dark"
        self.homepage = self.load_homepage_preference()
        self.extensions = self.load_extensions_config()
        self.quick_access_links = self.load_quick_access_links()
        weather_state = self.load_weather_preferences()
        self.weather_city = weather_state.get("city") or "London"
        self.time_capsule_data = self.load_time_capsule()
        self.pinned_tabs = self.load_pinned_tabs()
        settings_state = self.load_browser_settings()
        self.extension_blocked_hosts = {
            "google.com", "www.google.com", "bing.com", "www.bing.com",
            "duckduckgo.com", "www.duckduckgo.com", "search.yahoo.com",
            "captcha.com", "www.recaptcha.net", "recaptcha.net",
        }
        self.weather_cache = {
            "timestamp": float(weather_state.get("timestamp", 0) or 0),
            "value": (weather_state.get("last_status") or "").strip() or "Weather disabled",
        }
        self.tab_widget = CustomTabBar()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setMovable(True)
        self.tab_widget.setDocumentMode(True)
        self.tab_widget.tabBar().setIconSize(QSize(16, 16))
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self.add_new_tab_button = QPushButton("+")
        self.add_new_tab_button.setObjectName("newTabBtn")
        self.add_new_tab_button.clicked.connect(lambda: self.add_new_tab(QUrl(self.homepage), "New Tab"))
        self.add_new_tab_button.setFixedSize(32, 32)
        self.add_new_tab_button.setToolTip("New Tab")
        self.url_bar = QLineEdit()
        self.url_bar.setPlaceholderText("Enter URL or search")
        self.url_bar.setClearButtonEnabled(True)
        self.url_bar.setMinimumHeight(34)
        self.url_bar.setObjectName("addressBar")
        self.bookmark_button = QPushButton("\u2606")
        self.bookmark_button.setObjectName("bookmarkBtn")
        self.bookmark_button.setToolTip("Add bookmark")
        self.bookmark_button.setFlat(True)
        self.bookmark_button.setFixedSize(34, 34)
        self.search_engine_combo = QComboBox()
        self.search_engines = {
            "Google": "https://www.google.com/search?q=",
            "Bing": "https://www.bing.com/search?q=",
            "DuckDuckGo": "https://duckduckgo.com/?q=",
            "Yahoo": "https://search.yahoo.com/search?p="
        }
        self.search_engine_combo.addItems(self.search_engines.keys())
        saved_search_engine = settings_state.get("search_engine", "Google")
        self.search_engine_combo.setCurrentText(saved_search_engine if saved_search_engine in self.search_engines else "Google")
        self.search_engine_combo.setMinimumHeight(34)
        self.search_engine_combo.setFixedWidth(135)
        self.search_engine_combo.setObjectName("engineBox")
        self.search_engine_combo.currentTextChanged.connect(self.on_search_engine_changed)
        self.url_bar_layout = QHBoxLayout()
        self.url_bar_layout.addWidget(self.url_bar)
        self.url_bar_layout.addWidget(self.bookmark_button)
        self.url_bar_layout.addWidget(self.search_engine_combo)
        self.url_bar.returnPressed.connect(self.load_url)
        self.bookmark_button.clicked.connect(self.toggle_current_page_bookmark)
        self.back_button = QPushButton("\u25C0")
        self.forward_button = QPushButton("\u25B6")
        self.home_button = QPushButton("\u2302")
        self.refresh_button = QPushButton("\u21BB")
        self.downloads_button = QPushButton("Downloads")
        self.theme_button = QPushButton("Theme")
        self.theme_button.setObjectName("themeBtn")
        self.back_button.setToolTip("Back")
        self.forward_button.setToolTip("Forward")
        self.home_button.setToolTip("Home")
        self.refresh_button.setToolTip("Refresh")
        self.downloads_button.setToolTip("Downloads")
        self.theme_button.setToolTip("Toggle Theme")
        for btn in [self.back_button, self.forward_button, self.home_button, self.refresh_button, self.add_new_tab_button]:
            btn.setObjectName("navBtn")
            btn.setFlat(True)
            btn.setIconSize(QSize(16, 16))
            btn.setFixedSize(32, 32)
        self.downloads_button.setFlat(True)
        self.downloads_button.setFixedHeight(32)
        self.downloads_button.setMinimumWidth(96)
        self.downloads_button.setObjectName("themeBtn")
        self.theme_button.setFlat(True)
        self.theme_button.setFixedHeight(32)
        self.theme_button.setMinimumWidth(78)
        self.back_button.clicked.connect(lambda: self.current_browser().back())
        self.forward_button.clicked.connect(lambda: self.current_browser().forward())
        self.home_button.clicked.connect(lambda: self.open_target_in_browser(self.current_browser(), self.homepage))
        self.refresh_button.clicked.connect(lambda: self.current_browser().reload())
        self.theme_button.clicked.connect(self.toggle_theme)
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)
        button_layout.addWidget(self.back_button)
        button_layout.addWidget(self.forward_button)
        button_layout.addWidget(self.home_button)
        button_layout.addWidget(self.refresh_button)
        button_layout.addWidget(self.add_new_tab_button)
        button_layout.addStretch()
        button_layout.addWidget(self.downloads_button)
        button_layout.addWidget(self.theme_button)
        self.bookmark_bar = QFrame()
        self.bookmark_bar.setObjectName("bookmarkBar")
        self.bookmark_bar_layout = QHBoxLayout(self.bookmark_bar)
        self.bookmark_bar_layout.setContentsMargins(8, 2, 8, 2)
        self.bookmark_bar_layout.setSpacing(6)
        main_layout = QVBoxLayout()
        main_layout.addLayout(self.url_bar_layout)
        main_layout.addLayout(button_layout)
        main_layout.addWidget(self.bookmark_bar, 0, Qt.AlignLeft)
        main_layout.addWidget(self.tab_widget)
        main_layout.setContentsMargins(8, 8, 8, 0)
        main_layout.setSpacing(8)
        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)
        self.setStyleSheet("")
        self.download_manager.attach_button(self.downloads_button)
        self.tab_widget.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tab_widget.tabBar().customContextMenuRequested.connect(self.show_tab_context_menu)
        
        self.status = QStatusBar(self)
        self.setStatusBar(self.status)
        self.status.setSizeGripEnabled(False)
        self.status_runtime_label = QLabel("")
        self.status_profile_label = QLabel("")
        self.status_weather_label = QLabel("")
        self._runtime_status_message = ""
        self.status.addWidget(self.status_runtime_label, 1)
        self.status.addPermanentWidget(self.status_profile_label)
        self.status.addPermanentWidget(self.status_weather_label)

        # Initialize feature flags here
        self.reading_mode_enabled = bool(settings_state.get("reading_mode_enabled", False))
        self.weather_enabled = bool(settings_state.get("weather_enabled", False))
        self.time_capsule_enabled = bool(settings_state.get("time_capsule_enabled", False))
        self.soundscape_enabled = bool(settings_state.get("soundscape_enabled", False))
        self.stable_google_results = False
        self.restore_session_enabled = bool(settings_state.get("restore_session_enabled", True))
        self.low_power_mode = bool(settings_state.get("low_power_mode", False))
        self.ad_blocker_enabled = bool(settings_state.get("ad_blocker_enabled", True))
        self.popup_blocker_enabled = bool(settings_state.get("popup_blocker_enabled", True))
        self._popup_request_log = {}
        self.last_content_browser = None
        self.site_security_allowlist = sorted({
            (host or "").strip().lower()
            for host in settings_state.get("site_security_allowlist", [])
            if isinstance(host, str) and host.strip()
        })
        self.video_page_tweaks_enabled = False
        self.youtube_fullscreen_fix_enabled = False
        self.music_folder = settings_state.get("music_folder") or os.path.join(os.path.expanduser("~"), "Music")
        self.bookmark_bar_visible = bool(settings_state.get("bookmark_bar_visible", True))
        self.updateStatusBar()
        
        self.create_menu()
        self.apply_saved_theme()
        self.tab_widget.currentChanged.connect(self.update_url_bar)
        self.completer_model = QStringListModel(self.history_manager.history_data)
        self.completer = QCompleter(self.completer_model, self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.url_bar.setCompleter(self.completer)
        
        restorable_urls = self.load_restorable_session_urls() if self.restore_session_enabled else []
        startup_urls = []
        for url in self.pinned_tabs:
            if url not in startup_urls:
                startup_urls.append(url)
        for url in restorable_urls:
            if url not in startup_urls:
                startup_urls.append(url)
        if not self.restore_session_enabled and os.path.exists(self.session_file):
            try:
                os.remove(self.session_file)
            except Exception:
                pass
        if not restorable_urls and os.path.exists(self.session_file):
            try:
                os.remove(self.session_file)
            except Exception:
                pass
        if restorable_urls:
            reply = QMessageBox.question(self, "Restore Session",
                                         "Do you want to restore your last session?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.Yes:
                for url in startup_urls:
                    self.add_new_tab(QUrl(url), "Pinned Tab" if url in self.pinned_tabs else "Restored Tab", pinned=(url in self.pinned_tabs))
            else:
                try:
                    if os.path.exists(self.session_file):
                        os.remove(self.session_file)
                except Exception:
                    pass
                self.add_new_tab(QUrl(self.homepage), "New Tab")
        else:
            if self.pinned_tabs:
                for url in self.pinned_tabs:
                    self.add_new_tab(QUrl(url), "Pinned Tab", pinned=True)
                self.add_new_tab(QUrl(self.homepage), "New Tab")
            else:
                self.add_new_tab(QUrl(self.homepage), "New Tab")
        
        self.interceptor = SecurityInterceptor(media_request_callback=self.register_observed_media_request)
        self.interceptor.do_not_track = False
        self.interceptor.ad_blocker_enabled = self.ad_blocker_enabled
        self.interceptor.popup_blocker_enabled = self.popup_blocker_enabled
        self.interceptor.set_allowlist(self.site_security_allowlist)
        self.qprofile.setUrlRequestInterceptor(self.interceptor)
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.updateStatusBar)
        self.timer.start(5000)
        self.apply_power_mode()
        QTimer.singleShot(2500, self.check_due_time_capsules)

        self.dev_tools_shortcut = QShortcut(QKeySequence(Qt.Key_F12), self)
        self.dev_tools_shortcut.activated.connect(self.open_dev_tools)
        self.mini_player_shortcut = QShortcut(QKeySequence("F6"), self)
        self.mini_player_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.mini_player_shortcut.activated.connect(self.toggle_mini_player)
        self.print_shortcut = QShortcut(QKeySequence(Qt.CTRL + Qt.Key_P), self)
        self.print_shortcut.activated.connect(self.print_page)
        self.zoom_in_shortcut = QShortcut(QKeySequence(Qt.CTRL + Qt.Key_Plus), self)
        self.zoom_in_shortcut.activated.connect(lambda: self.adjust_zoom(0.1))
        self.zoom_out_shortcut = QShortcut(QKeySequence(Qt.CTRL + Qt.Key_Minus), self)
        self.zoom_out_shortcut.activated.connect(lambda: self.adjust_zoom(-0.1))
        self.zoom_reset_shortcut = QShortcut(QKeySequence(Qt.CTRL + Qt.Key_0), self)
        self.zoom_reset_shortcut.activated.connect(lambda: self.adjust_zoom(0, reset=True))
        self.focus_address_bar_shortcut = QShortcut(QKeySequence(Qt.CTRL + Qt.Key_L), self)
        self.focus_address_bar_shortcut.activated.connect(lambda: (self.url_bar.setFocus(), self.url_bar.selectAll()))
        self.incognito_shortcut = QShortcut(QKeySequence("Ctrl+Shift+N"), self)
        self.incognito_shortcut.activated.connect(self.open_incognito_window)
        self.is_mini_player = False
        self.is_fullscreen = False
        self.was_maximized_before_fullscreen = False
        self.normal_geometry = QRect(100, 100, 900, 600)
        self.zoom_factor = 1.0
        self.browser_audio_active = False
        self.soundscape_paused_for_browser_audio = False

        self.soundscape_timer = QTimer(self)
        self.soundscape_timer.timeout.connect(self.update_soundscape)
        self.current_soundscape_url = None
        QTimer.singleShot(200, self.finish_startup_tasks)

    def mousePressEvent(self, event):
        if self.is_mini_player and event.button() == Qt.LeftButton:
            self.is_dragging = True
            try:
                gpos = event.globalPosition().toPoint()
            except AttributeError:
                gpos = event.globalPos()
            self.drag_position = gpos - self.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.is_dragging and self.is_mini_player:
            try:
                gpos = event.globalPosition().toPoint()
            except AttributeError:
                gpos = event.globalPos()
            self.move(gpos - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False
            event.accept()

    def refresh_active_browser_geometry(self):
        browser = self.current_browser()
        if browser:
            browser.setGeometry(self.centralWidget().geometry())
            browser.update()

    def restore_window_from_mini_player(self):
        if not self.is_mini_player:
            return
        self.set_browser_chrome_visible(True)
        self.setWindowFlags(Qt.Window)
        self.setGeometry(self.normal_geometry)
        self.is_mini_player = False
        self.show()
        self.refresh_active_browser_geometry()

    def stabilize_media_mode_for_fullscreen(self):
        if self.is_mini_player:
            self.restore_window_from_mini_player()

    def finalize_media_mode_after_fullscreen(self):
        self.refresh_active_browser_geometry()

    def updateStatusBar(self):
        self.status_profile_label.setText(f"Profile: {self.profile}")
        if self.weather_enabled:
            self.status_weather_label.setText(f"Weather: {self.get_weather_status()}")
            self.status_weather_label.setVisible(True)
        else:
            self.status_weather_label.setVisible(False)

    def show_runtime_status(self, message, duration_ms=2000):
        self._runtime_status_message = message
        self.status_runtime_label.setText(message)
        if duration_ms > 0:
            QTimer.singleShot(duration_ms, lambda: self._clear_runtime_status(message))

    def _clear_runtime_status(self, message):
        if self._runtime_status_message == message:
            self._runtime_status_message = ""
            self.status_runtime_label.setText("")

    def current_site_host(self, browser=None):
        target_browser = browser or self.current_browser()
        if target_browser is None:
            return ""
        try:
            return (target_browser.url().host() or "").strip().lower()
        except Exception:
            return ""

    def should_block_popup_request(self, source_tab):
        if not getattr(self, "popup_blocker_enabled", True):
            return False
        source_host = self.current_site_host(source_tab)
        if self.interceptor.is_allowlisted(source_host):
            return False
        if host_matches_fragment(source_host, POPUP_COMPATIBILITY_HOST_FRAGMENTS):
            return False
        # Let explicit user-driven new tabs/windows work. Only background popup bursts
        # should be blocked, not normal link opens from the active page.
        if source_tab is self.current_browser():
            return False
        if source_tab is not None and hasattr(source_tab, "consume_popup_allowance") and source_tab.consume_popup_allowance():
            return False
        if source_tab is self.current_browser() and hasattr(source_tab, "has_recent_user_gesture") and source_tab.has_recent_user_gesture(8.0):
            return False
        host_label = source_host or "this site"
        self.show_runtime_status(f"Blocked a popup from {host_label}", 3500)
        return True

    def get_security_target_browser(self):
        browser = self.current_browser()
        if browser is not None:
            current_url = browser.url().toString()
            if not self.is_internal_browser_url(current_url):
                return browser
        candidate = getattr(self, "last_content_browser", None)
        if candidate is not None:
            try:
                if self.tab_widget.indexOf(candidate) != -1:
                    candidate_url = candidate.url().toString()
                    if not self.is_internal_browser_url(candidate_url):
                        return candidate
            except Exception:
                pass
        return None

    def get_media_target_browser(self):
        browser = self.current_browser()
        if browser is not None:
            current_url = browser.url().toString()
            if not self.is_internal_browser_url(current_url):
                return browser
        candidate = getattr(self, "last_content_browser", None)
        if candidate is not None:
            try:
                if self.tab_widget.indexOf(candidate) != -1:
                    candidate_url = candidate.url().toString()
                    if not self.is_internal_browser_url(candidate_url):
                        return candidate
            except Exception:
                pass
        return None

    def add_current_site_to_security_allowlist(self):
        host = self.current_site_host(self.get_security_target_browser())
        if not host:
            QMessageBox.information(self, "Security Options", "Open a website first, then trust it from here.")
            return False
        if host not in self.site_security_allowlist:
            self.site_security_allowlist.append(host)
            self.site_security_allowlist = sorted(set(self.site_security_allowlist))
            self.interceptor.set_allowlist(self.site_security_allowlist)
            self.save_browser_settings()
            self.show_runtime_status(f"Trusted site: {host}", 3000)
        return True

    def remove_sites_from_security_allowlist(self, hosts):
        targets = {(host or "").strip().lower() for host in hosts if (host or "").strip()}
        if not targets:
            return
        self.site_security_allowlist = [host for host in self.site_security_allowlist if host not in targets]
        self.interceptor.set_allowlist(self.site_security_allowlist)
        self.save_browser_settings()

    def set_browser_chrome_visible(self, visible):
        widgets = [
            self.url_bar,
            self.search_engine_combo,
            self.back_button,
            self.forward_button,
            self.home_button,
            self.refresh_button,
            self.downloads_button,
            self.theme_button,
            self.add_new_tab_button,
            self.bookmark_button,
        ]
        for widget in widgets:
            widget.setVisible(visible)
        self.tab_widget.tabBar().setVisible(visible)
        self.status.setVisible(visible)
        self.menuBar().setVisible(visible)
        if hasattr(self, "bookmark_bar"):
            self.bookmark_bar.setVisible(visible and getattr(self, "bookmark_bar_visible", True))

    def enter_app_fullscreen(self):
        self.was_maximized_before_fullscreen = self.isMaximized()
        self.set_browser_chrome_visible(False)
        self.showFullScreen()
        self.is_fullscreen = True
        QTimer.singleShot(0, self.refresh_active_browser_geometry)

    def exit_app_fullscreen(self):
        self.set_browser_chrome_visible(True)
        if self.was_maximized_before_fullscreen:
            self.showMaximized()
        else:
            self.showNormal()
        self.is_fullscreen = False
        QTimer.singleShot(0, self.refresh_active_browser_geometry)

    def open_history_url(self, url):
        self.current_browser().setUrl(QUrl(url))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F11:
            if self.isFullScreen():
                self.exit_app_fullscreen()
                if self.video_page_tweaks_enabled and is_youtube_url(self.current_browser().url()):
                    js = """
                    try {
                        var video = document.querySelector('video, .video-stream, .html5-main-video');
                        var videoProgress = video ? video.currentTime : 0;
                        document.querySelectorAll('#masthead-container, #container, .ytp-chrome-top, .ytp-chrome-bottom, .ytp-gradient-bottom, .ytp-gradient-top, .ytp-title, .ytp-watermark, .annotation, #ytd-player').forEach(el => el.style.display = '');
                        var player = document.querySelector('#movie_player, .html5-video-player, #player, .ytd-player, .ytp-player-content');
                        if (player) {
                            player.style.position = '';
                            player.style.top = '';
                            player.style.left = '';
                            player.style.width = '';
                            player.style.height = '';
                            player.style.background = '';
                            player.style.margin = '';
                            player.style.padding = '';
                            player.style.zIndex = '';
                        }
                        document.body.style.overflow = '';
                        document.documentElement.style.overflow = '';
                        if (video) {
                            video.style.width = '';
                            video.style.height = '';
                            video.style.objectFit = '';
                            video.style.zIndex = '';
                            video.currentTime = videoProgress;
                            video.play();
                            console.log('Restored video at ' + videoProgress + 's');
                        }
                    } catch (e) {
                        console.error('Error restoring page:', e);
                    }
                    """
                    self.current_browser().page().runJavaScript(js)
                    QTimer.singleShot(1000, self.current_browser().update)
            else:
                self.enter_app_fullscreen()
                screen_size = QApplication.primaryScreen().size()
                self.current_browser().setGeometry(0, 0, screen_size.width(), screen_size.height())
                self.current_browser().setVisible(True)
                if self.video_page_tweaks_enabled and is_youtube_url(self.current_browser().url()):
                    js = """
                    try {
                        function tryWebGL() {
                            var canvas = document.createElement('canvas');
                            var contexts = ['webgl', 'webgl2', 'experimental-webgl'];
                            for (var i = 0; i < contexts.length; i++) {
                                var ctx = canvas.getContext(contexts[i], { failIfMajorPerformanceCaveat: true });
                                if (ctx) {
                                    console.log('WebGL initialized: ' + contexts[i]);
                                    return true;
                                }
                            }
                            console.log('WebGL not supported or blacklisted, forcing software');
                            var ctx = canvas.getContext('webgl', { failIfMajorPerformanceCaveat: false });
                            if (ctx) {
                                console.log('Software WebGL initialized');
                                return true;
                            }
                            return false;
                        }
                        document.addEventListener('DOMContentLoaded', function() {
                            tryWebGL();
                            var videoProgress = 0;
                            function maximizeVideo() {
                                var video = document.querySelector('video, .video-stream, .html5-main-video, .ytp-video');
                                if (video) {
                                    videoProgress = video.currentTime;
                                    var player = document.querySelector('#movie_player, .html5-video-player, #player, .ytd-player, .ytp-player-content');
                                    if (player) {
                                        player.style.position = 'fixed';
                                        player.style.top = '0';
                                        player.style.left = '0';
                                        player.style.width = '100%';
                                        player.style.height = '100%';
                                        player.style.background = 'black';
                                        player.style.margin = '0';
                                        player.style.padding = '0';
                                        video.style.width = '100%';
                                        video.style.height = '100%';
                                        video.style.objectFit = 'contain';
                                        video.style.zIndex = '9999';
                                        video.controls = false;
                                        document.querySelectorAll('#masthead-container, #container, .ytp-chrome-top, .ytp-chrome-bottom, .ytp-gradient-bottom, .ytp-gradient-top, .ytp-title, .ytp-watermark, .annotation, #ytd-player, #movie_player > *:not(video), .html5-video-player > *:not(video), .ytp-player-content > *:not(video), .ad-container, .ytp-ad-module, .ytp-ad-overlay, .ytp-ad-text').forEach(el => el.style.display = 'none');
                                        document.body.style.overflow = 'hidden';
                                        document.documentElement.style.overflow = 'hidden';
                                        video.currentTime = videoProgress;
                                        video.play();
                                        console.log('Video maximized successfully at ' + videoProgress + 's');
                                        return true;
                                    } else {
                                        console.log('Player not found, retrying...');
                                        setTimeout(maximizeVideo, 1000);
                                        return false;
                                    }
                                } else {
                                    console.log('Video element not found, retrying...');
                                    setTimeout(maximizeVideo, 1000);
                                    return false;
                                }
                            }
                            maximizeVideo();
                        });
                    } catch (e) {
                        console.error('Error in maximizeVideo:', e);
                    }
                    """
                    self.current_browser().page().runJavaScript(js)
                    QTimer.singleShot(1000, self.current_browser().update)
            self.show()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.wallpaper is not None:
            painter.drawPixmap(self.rect(), self.wallpaper)
        elif self.custom_color is not None:
            painter.fillRect(self.rect(), self.custom_color)
        super().paintEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.download_manager.reposition_popup()

    def update_url_bar(self, index):
        browser = self.current_browser()
        if browser:
            url_text = browser.url().toString()
            lowered = url_text.lower()
            if "aurora.settings" in lowered:
                self.url_bar.setText(f"aurora://settings/{getattr(self, 'current_settings_section', 'profiles')}")
            elif "aurora.home" in lowered:
                self.url_bar.setText("aurora://home")
            else:
                self.url_bar.setText(url_text)
            self.completer_model.setStringList(self.history_manager.history_data)
            self.update_bookmark_button_state(browser)

    def finish_startup_tasks(self):
        self.refresh_bookmark_bar()
        self.apply_bookmark_bar_visibility()
        if self.soundscape_enabled:
            self.toggle_soundscape(True)

    def apply_bookmark_bar_visibility(self, url_value=None):
        if not hasattr(self, "bookmark_bar"):
            return
        current_url = url_value
        if current_url is None and self.current_browser():
            try:
                current_url = self.current_browser().url().toString()
            except Exception:
                current_url = ""
        lowered = (current_url or "").lower()
        should_show = bool(self.bookmark_bar_visible)
        if "aurora.settings" in lowered or lowered.startswith("aurora://settings") or lowered.startswith("aurora:settings"):
            should_show = False
        self.bookmark_bar.setVisible(should_show)

    def refresh_bookmark_bar(self):
        if not hasattr(self, "bookmark_bar_layout"):
            return
        while self.bookmark_bar_layout.count():
            item = self.bookmark_bar_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        bookmarks = self.load_profile_bookmarks()
        visible_bookmarks = bookmarks[:8]
        overflow_bookmarks = bookmarks[8:]
        for bookmark in visible_bookmarks:
            button = QPushButton(bookmark["title"])
            button.setObjectName("bookmarkChip")
            button.setToolTip(bookmark["url"])
            button.clicked.connect(lambda _checked=False, url=bookmark["url"]: self.open_target_in_browser(self.current_browser(), url))
            self.bookmark_bar_layout.addWidget(button)
        if overflow_bookmarks:
            more_button = QPushButton("More")
            more_button.setObjectName("bookmarkOverflowBtn")
            overflow_menu = QMenu(more_button)
            for bookmark in overflow_bookmarks:
                action = QAction(bookmark["title"], overflow_menu)
                action.setToolTip(bookmark["url"])
                action.triggered.connect(lambda _checked=False, url=bookmark["url"]: self.open_target_in_browser(self.current_browser(), url))
                overflow_menu.addAction(action)
            more_button.setMenu(overflow_menu)
            self.bookmark_bar_layout.addWidget(more_button)
        manage_button = QPushButton("Bookmarks")
        manage_button.setObjectName("bookmarkManageBtn")
        manage_button.clicked.connect(self.show_bookmark_manager)
        self.bookmark_bar_layout.addWidget(manage_button)

    def show_tab_context_menu(self, pos):
        index = self.tab_widget.tabBar().tabAt(pos)
        if index < 0:
            return
        tab = self.tab_widget.widget(index)
        menu = QMenu(self)
        pinned = bool(tab.property("pinned"))
        pin_action = QAction("Unpin Tab" if pinned else "Pin Tab", self)
        pin_action.triggered.connect(lambda: self.set_tab_pinned(tab, not pinned))
        menu.addAction(pin_action)
        duplicate_action = QAction("Duplicate Tab", self)
        duplicate_action.triggered.connect(lambda: self.duplicate_tab(tab))
        menu.addAction(duplicate_action)
        menu.exec_(self.tab_widget.tabBar().mapToGlobal(pos))

    def set_tab_pinned(self, tab, pinned):
        if tab is None:
            return
        tab.setProperty("pinned", bool(pinned))
        index = self.tab_widget.indexOf(tab)
        if index >= 0:
            self.apply_tab_visual_state(tab, tab.title())
        self.save_pinned_tabs()

    def duplicate_tab(self, tab=None):
        source_tab = tab or self.current_browser()
        if source_tab is None:
            return
        url_value = source_tab.url().toString().strip() or self.homepage
        title = (source_tab.title() or "").strip() or prettify_host_label(url_value) or "New Tab"
        self.add_new_tab(url_value, title, pinned=bool(source_tab.property("pinned")))

    def create_menu(self):
        menubar = self.menuBar()
        menubar.clear()

        file_menu = QMenu("File", self)
        new_tab_action = QAction("New Tab", self)
        new_tab_action.triggered.connect(lambda: self.add_new_tab(QUrl(self.homepage), "New Tab"))
        file_menu.addAction(new_tab_action)
        reopen_tab_action = QAction("Reopen Closed Tab", self)
        reopen_tab_action.triggered.connect(self.reopen_closed_tab)
        file_menu.addAction(reopen_tab_action)
        file_menu.addSeparator()
        print_action = QAction("Print Page", self)
        print_action.triggered.connect(self.print_page)
        file_menu.addAction(print_action)
        file_menu.addSeparator()
        set_homepage_action = QAction("Set Homepage", self)
        set_homepage_action.triggered.connect(self.set_homepage)
        file_menu.addAction(set_homepage_action)
        switch_profile_action = QAction("Manage Profiles", self)
        switch_profile_action.triggered.connect(self.switch_profile)
        file_menu.addAction(switch_profile_action)
        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        menubar.addMenu(file_menu)

        navigate_menu = QMenu("Navigate", self)
        go_home_action = QAction("Go Home", self)
        go_home_action.triggered.connect(lambda: self.open_target_in_browser(self.current_browser(), self.homepage))
        navigate_menu.addAction(go_home_action)
        back_action = QAction("Back", self)
        back_action.triggered.connect(lambda: self.current_browser().back())
        navigate_menu.addAction(back_action)
        forward_action = QAction("Forward", self)
        forward_action.triggered.connect(lambda: self.current_browser().forward())
        navigate_menu.addAction(forward_action)
        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(lambda: self.current_browser().reload())
        navigate_menu.addAction(refresh_action)
        menubar.addMenu(navigate_menu)

        view_menu = QMenu("View", self)
        zoom_menu = QMenu("Zoom", self)
        zoom_in_action = QAction("Zoom In", self)
        zoom_in_action.triggered.connect(lambda: self.adjust_zoom(0.1))
        zoom_menu.addAction(zoom_in_action)
        zoom_out_action = QAction("Zoom Out", self)
        zoom_out_action.triggered.connect(lambda: self.adjust_zoom(-0.1))
        zoom_menu.addAction(zoom_out_action)
        zoom_reset_action = QAction("Reset Zoom", self)
        zoom_reset_action.triggered.connect(lambda: self.adjust_zoom(0, reset=True))
        zoom_menu.addAction(zoom_reset_action)
        view_menu.addMenu(zoom_menu)
        view_menu.addSeparator()
        theme_action = QAction("Toggle Theme", self)
        theme_action.triggered.connect(self.toggle_theme)
        view_menu.addAction(theme_action)
        customize_ui_action = QAction("Customize UI", self)
        customize_ui_action.triggered.connect(self.customize_ui)
        view_menu.addAction(customize_ui_action)
        view_menu.addSeparator()
        bookmark_bar_action = QAction("Toggle Bookmark Bar", self)
        bookmark_bar_action.triggered.connect(lambda: (setattr(self, "bookmark_bar_visible", not self.bookmark_bar_visible), self.apply_bookmark_bar_visibility(), self.save_browser_settings()))
        view_menu.addAction(bookmark_bar_action)
        reading_mode_action = QAction("Toggle Reading Mode For Current Tab", self)
        reading_mode_action.triggered.connect(lambda _checked=False: self.toggle_current_page_reading_mode())
        view_menu.addAction(reading_mode_action)
        menubar.addMenu(view_menu)

        library_menu = QMenu("Library", self)
        history_action = QAction("History", self)
        history_action.triggered.connect(self.show_history)
        library_menu.addAction(history_action)
        download_action = QAction("Downloads", self)
        download_action.triggered.connect(self.show_download_manager)
        library_menu.addAction(download_action)
        bookmark_action = QAction("Bookmarks", self)
        bookmark_action.triggered.connect(self.show_bookmark_manager)
        library_menu.addAction(bookmark_action)
        password_manager_action = QAction("Password Manager", self)
        password_manager_action.triggered.connect(self.open_password_manager)
        library_menu.addAction(password_manager_action)
        menubar.addMenu(library_menu)

        privacy_menu = QMenu("Privacy", self)
        incognito_action = QAction("Incognito Mode", self)
        incognito_action.triggered.connect(self.open_incognito_window)
        privacy_menu.addAction(incognito_action)
        security_options_action = QAction("Security Options", self)
        security_options_action.triggered.connect(self.open_security_options)
        privacy_menu.addAction(security_options_action)
        menubar.addMenu(privacy_menu)

        tools_menu = QMenu("Tools", self)
        screenshot_action = QAction("Take Screenshot", self)
        screenshot_action.triggered.connect(self.take_screenshot)
        tools_menu.addAction(screenshot_action)
        add_quick_access_action = QAction("Add Quick Access Link", self)
        add_quick_access_action.triggered.connect(self.add_quick_access_link)
        tools_menu.addAction(add_quick_access_action)
        remove_quick_access_action = QAction("Remove Quick Access Link", self)
        remove_quick_access_action.triggered.connect(self.remove_quick_access_link)
        tools_menu.addAction(remove_quick_access_action)
        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self.open_settings_window_action)
        tools_menu.addAction(settings_action)
        menubar.addMenu(tools_menu)

        developer_menu = QMenu("Developer", self)
        dev_tools_action = QAction("Developer Tools", self)
        dev_tools_action.triggered.connect(self.open_dev_tools)
        developer_menu.addAction(dev_tools_action)
        extension_manager_action = QAction("Script Extensions (Beta)", self)
        extension_manager_action.triggered.connect(self.open_extension_manager)
        developer_menu.addAction(extension_manager_action)
        menubar.addMenu(developer_menu)

        help_menu = QMenu("Help", self)
        shortcuts_action = QAction("Keyboard Shortcuts", self)
        shortcuts_action.triggered.connect(self.show_shortcuts_dialog)
        help_menu.addAction(shortcuts_action)
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)
        menubar.addMenu(help_menu)

    def show_about_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("About Aurora Browser")
        dialog.setModal(True)
        dialog.setMinimumWidth(520)
        style_aux_window(dialog)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        title = QLabel("Aurora Browser")
        title.setStyleSheet("font-size: 22px; font-weight: 800; color: #f5f9ff;")
        body = QLabel(
            "Welcome to Aurora Browser, your gateway to a seamless and secure web experience.\n\n"
            "Developed by Aarush\n\n"
            "If you encounter any issues or have suggestions, please reach out to:\n"
            "<a style='color:#8ed8ff; text-decoration:none; font-weight:700;' href='mailto:Aarushpandey.op@gmail.com'>Aarushpandey.op@gmail.com</a>\n\n"
            "Your feedback is invaluable in helping us enhance your browsing experience. "
            "Thank you for choosing Aurora Browser!"
        )
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        body.setTextInteractionFlags(Qt.TextBrowserInteraction)
        body.setOpenExternalLinks(True)
        body.setStyleSheet("color: #edf4ff; font-size: 13px; line-height: 1.5;")
        ok_button = QPushButton("OK")
        ok_button.setMinimumWidth(96)
        ok_button.clicked.connect(dialog.accept)
        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(ok_button)
        layout.addWidget(title)
        layout.addWidget(body)
        layout.addLayout(button_row)
        dialog.exec_()

    def show_shortcuts_dialog(self):
        shortcuts = [
            ("F11", "Toggle app fullscreen"),
            ("F12", "Open Developer Tools"),
            ("F6", "Toggle Mini Player"),
            ("Ctrl+Shift+N", "Open incognito window"),
            ("Ctrl+P", "Print page"),
            ("Ctrl+L", "Focus address bar"),
            ("Ctrl++", "Zoom in"),
            ("Ctrl+-", "Zoom out"),
            ("Ctrl+0", "Reset zoom"),
            ("Enter (Address Bar)", "Open URL or search"),
            ("Tools -> Add Quick Access Link", "Add custom homepage quick access"),
        ]
        rows = "".join(
            f"<tr><td style='padding:6px 14px;'><b>{key}</b></td><td style='padding:6px 14px;'>{desc}</td></tr>"
            for key, desc in shortcuts
        )
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Keyboard Shortcuts")
        msg_box.setTextFormat(Qt.RichText)
        msg_box.setText(
            "<div style='min-width:560px;'>"
            "<h3 style='margin:0 0 10px 0;'>Aurora Browser Keyboard Shortcuts</h3>"
            "<table style='border-collapse:collapse;'>"
            f"{rows}"
            "</table>"
            "</div>"
        )
        if self.current_theme == "dark":
            msg_box.setStyleSheet(
                "QMessageBox { background-color: #232323; color: #f2f6ff; }"
                "QLabel { color: #f2f6ff; }"
                "QPushButton { min-width: 90px; }"
            )
        msg_box.exec_()

    def load_homepage_preference(self):
        default_homepage = "aurora://home"
        if os.path.exists(self.homepage_file):
            try:
                with open(self.homepage_file, "r", encoding="utf-8") as f:
                    value = f.read().strip()
                    if value:
                        return value
            except Exception as e:
                print(f"Homepage load error: {e}")
        return default_homepage

    def is_default_home_url(self, url_value):
        lowered = (url_value or "").strip().lower()
        return lowered in {
            "",
            "aurora://home",
            "aurora:home",
            "about:aurora",
            "about:newtab",
            "https://aurora.home/",
            "https://aurora.home",
        }

    def is_internal_aurora_url(self, url_value):
        lowered = (url_value or "").strip().lower()
        return (
            lowered.startswith("aurora://")
            or lowered.startswith("aurora:")
            or "aurora.home" in lowered
            or "aurora.settings" in lowered
            or self.is_internal_settings_file_url(lowered)
        )

    def is_internal_settings_file_url(self, lowered_url):
        if not lowered_url.startswith("file:"):
            return False
        try:
            local_path = QUrl(lowered_url).toLocalFile()
        except Exception:
            return False
        if not local_path:
            return False
        base = os.path.basename(local_path).lower()
        return base.startswith("settings_page") and base.endswith(".html")

    def load_restorable_session_urls(self):
        if not os.path.exists(self.session_file):
            return []
        try:
            with open(self.session_file, "r", encoding="utf-8") as f:
                urls = [line.strip() for line in f.read().splitlines() if line.strip()]
        except Exception as e:
            print(f"Session load error: {e}")
            return []
        filtered = []
        seen = set()
        for url in urls:
            if self.is_default_home_url(url) or self.is_internal_aurora_url(url):
                continue
            if url not in seen:
                filtered.append(url)
                seen.add(url)
        return filtered

    def save_session_state(self):
        if not getattr(self, "restore_session_enabled", True):
            if os.path.exists(self.session_file):
                try:
                    os.remove(self.session_file)
                except Exception:
                    pass
            return
        urls = []
        seen = set()
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if tab is None:
                continue
            url = tab.url().toString().strip()
            if not url or self.is_default_home_url(url) or self.is_internal_aurora_url(url):
                continue
            if url not in seen:
                urls.append(url)
                seen.add(url)
        try:
            if urls:
                with open(self.session_file, "w", encoding="utf-8") as f:
                    f.write("\n".join(urls) + "\n")
            elif os.path.exists(self.session_file):
                os.remove(self.session_file)
        except Exception as e:
            print(f"Session save error: {e}")

    def save_homepage_preference(self):
        try:
            with open(self.homepage_file, "w", encoding="utf-8") as f:
                f.write(self.homepage)
            self.invalidate_internal_cache()
        except Exception as e:
            QMessageBox.warning(self, "Homepage Save Error", f"Could not save homepage: {e}")

    def load_quick_access_links(self):
        if not os.path.exists(self.quick_access_file):
            return []
        try:
            with open(self.quick_access_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                links = []
                for item in data:
                    if isinstance(item, dict) and item.get("title") and item.get("url"):
                        links.append({
                            "title": normalize_quick_access_title(item["title"], item["url"]),
                            "url": item["url"],
                        })
                return links
        except Exception as e:
            print(f"Quick access load error: {e}")
        return []

    def save_quick_access_links(self):
        try:
            with open(self.quick_access_file, "w", encoding="utf-8") as f:
                json.dump(self.quick_access_links, f, indent=4)
            self.invalidate_internal_cache()
        except Exception as e:
            QMessageBox.warning(self, "Quick Access Save Error", f"Could not save quick access links: {e}")

    def add_quick_access_link(self):
        title, ok = QInputDialog.getText(self, "Add Quick Access", "Title:")
        if not ok:
            return
        url, ok = QInputDialog.getText(self, "Add Quick Access", "URL:")
        if not ok or not url.strip():
            return
        cleaned_url = url.strip()
        if not cleaned_url.startswith(("http://", "https://")):
            cleaned_url = "https://" + cleaned_url
        normalized_title = normalize_quick_access_title(title.strip(), cleaned_url)
        self.quick_access_links.append({"title": normalized_title, "url": cleaned_url})
        self.save_quick_access_links()
        if self.current_browser() and self.current_browser().url().toString().startswith("https://aurora.home/"):
            self.open_target_in_browser(self.current_browser(), "aurora://home")

    def remove_quick_access_link(self):
        if not self.quick_access_links:
            QMessageBox.information(self, "Quick Access", "No quick access links to remove.")
            return
        options = [f"{item['title']} - {item['url']}" for item in self.quick_access_links]
        selected, ok = QInputDialog.getItem(self, "Remove Quick Access", "Select link:", options, 0, False)
        if not ok or not selected:
            return
        self.quick_access_links = [
            item for item in self.quick_access_links
            if f"{item['title']} - {item['url']}" != selected
        ]
        self.save_quick_access_links()
        if self.current_browser() and self.current_browser().url().toString().startswith("https://aurora.home/"):
            self.open_target_in_browser(self.current_browser(), "aurora://home")

    def load_extensions_config(self):
        if not os.path.exists(self.extensions_file):
            return []
        try:
            with open(self.extensions_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                parsed = []
                for ext in data:
                    if isinstance(ext, dict) and ext.get("path"):
                        parsed.append({
                            "path": os.path.abspath(ext["path"]),
                            "enabled": bool(ext.get("enabled", True))
                        })
                return parsed
        except Exception as e:
            print(f"Extension config load error: {e}")
        return []

    def save_extensions_config(self):
        try:
            with open(self.extensions_file, "w", encoding="utf-8") as f:
                json.dump(self.extensions, f, indent=4)
        except Exception as e:
            QMessageBox.warning(self, "Extension Save Error", f"Could not save extension config: {e}")

    def load_theme_preference(self):
        default_theme = {"theme": "dark", "accent_color": None, "wallpaper": None}
        if not os.path.exists(self.theme_file):
            return default_theme
        try:
            with open(self.theme_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                accent_color = data.get("accent_color", data.get("custom_color"))
                theme_name = data.get("theme", "light")
                if theme_name == "custom":
                    theme_name = "accent"
                return {
                    "theme": theme_name,
                    "accent_color": accent_color,
                    "wallpaper": data.get("wallpaper"),
                }
        except Exception as e:
            print(f"Theme preference load error: {e}")
        return default_theme

    def save_theme_preference(self):
        wallpaper_path = getattr(self, "wallpaper_path", None)
        accent_color_name = self.custom_color.name() if isinstance(self.custom_color, QColor) else None
        try:
            with open(self.theme_file, "w", encoding="utf-8") as f:
                json.dump({
                    "theme": self.current_theme,
                    "accent_color": accent_color_name,
                    "wallpaper": wallpaper_path,
                }, f, indent=2)
            self.invalidate_internal_cache()
        except Exception as e:
            QMessageBox.warning(self, "Theme Save Error", f"Could not save theme preference: {e}")

    def apply_saved_theme(self):
        theme_data = self.load_theme_preference()
        theme_name = theme_data.get("theme", "dark")
        if theme_name == "dark":
            self.set_dark_theme()
        elif theme_name == "blue":
            self.set_blue_theme()
        elif theme_name == "graphite":
            self.set_graphite_theme()
        elif theme_name == "forest":
            self.set_forest_theme()
        elif theme_name == "sunset":
            self.set_sunset_theme()
        elif theme_name == "accent" and theme_data.get("accent_color"):
            self.apply_accent_color_theme(theme_data["accent_color"])
        elif theme_name == "wallpaper" and theme_data.get("wallpaper") and os.path.exists(theme_data["wallpaper"]):
            self.apply_wallpaper_theme(theme_data["wallpaper"])
        else:
            self.set_dark_theme()

    def invalidate_internal_cache(self):
        self._internal_cache_version += 1
        self._internal_cache.clear()

    def get_cached_homepage_html(self):
        cache_key = ("home",)
        cached = self._internal_cache.get(cache_key)
        if cached and cached.get("version") == self._internal_cache_version:
            return cached["html"]
        html = self.build_custom_homepage_html()
        self._internal_cache[cache_key] = {"version": self._internal_cache_version, "html": html}
        return html

    def get_cached_settings_html(self, section):
        cache_key = ("settings", section)
        cached = self._internal_cache.get(cache_key)
        if cached and cached.get("version") == self._internal_cache_version:
            return cached["html"]
        html = self.build_settings_page_html(section)
        self._internal_cache[cache_key] = {"version": self._internal_cache_version, "html": html}
        return html

    def apply_power_mode(self):
        try:
            if hasattr(self, "timer") and self.timer:
                self.timer.setInterval(15000 if self.low_power_mode else 5000)
        except Exception:
            pass
        try:
            if hasattr(self, "download_manager") and self.download_manager:
                self.download_manager.set_refresh_interval(2500 if self.low_power_mode else 1000)
        except Exception:
            pass

    def toggle_low_power_mode(self):
        self.low_power_mode = not self.low_power_mode
        self.save_browser_settings()
        self.apply_power_mode()
        self.invalidate_internal_cache()
        self.refresh_internal_pages()

    def toggle_session_restore(self):
        self.restore_session_enabled = not self.restore_session_enabled
        self.save_browser_settings()
        if not self.restore_session_enabled and os.path.exists(self.session_file):
            try:
                os.remove(self.session_file)
            except Exception:
                pass

    def reset_theme_surface_overrides(self):
        central = self.centralWidget()
        if central is not None:
            central.setStyleSheet("")

    def css_rgba(self, color_value, alpha):
        color = color_value if isinstance(color_value, QColor) else QColor(color_value)
        if not color.isValid():
            color = QColor("#5f84ff")
        return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"

    def internal_theme_palette(self):
        accent_themes = {"accent", "graphite", "forest", "sunset"}
        accent = self.custom_color if self.current_theme in accent_themes and isinstance(self.custom_color, QColor) and self.custom_color.isValid() else None
        if accent is None:
            if self.current_theme == "blue":
                accent = QColor("#26a8da")
            elif self.current_theme == "light":
                accent = QColor("#376bff")
            else:
                accent = QColor("#79d8ff")
        accent_2 = accent.lighter(135)
        accent_3 = accent.lighter(160)
        common = {
            "accent": accent.name(),
            "accent_2": accent_2.name(),
            "accent_3": accent_3.name(),
            "accent_soft": self.css_rgba(accent, 0.12),
            "accent_line": self.css_rgba(accent, 0.24),
            "accent_glow": self.css_rgba(accent, 0.22),
            "danger": "#e46d86",
        }
        if self.current_theme == "light":
            return {
                **common,
                "page_background": "radial-gradient(620px circle at 10% 8%, rgba(71, 117, 255, 0.10), transparent 42%), radial-gradient(500px circle at 92% 12%, rgba(54, 198, 172, 0.10), transparent 34%), linear-gradient(180deg, #f6f8fc 0%, #ecf1f9 100%)",
                "panel": "rgba(255,255,255,0.92)",
                "panel_alt": "rgba(248,250,255,0.98)",
                "card": "rgba(255,255,255,0.98)",
                "chip": "rgba(239,244,255,0.96)",
                "input": "#ffffff",
                "text": "#18263d",
                "muted": "#60738e",
                "border": "rgba(84, 110, 158, 0.18)",
                "line": "rgba(23, 47, 85, 0.08)",
                "shadow": "0 22px 60px rgba(98, 120, 156, 0.14)",
                "primary_bg": f"linear-gradient(135deg, {accent.name()}, {accent_2.name()})",
                "primary_text": "#ffffff",
                "nav_bg": "rgba(235, 240, 250, 0.78)",
                "nav_hover": "rgba(225, 233, 248, 0.96)",
                "nav_active": self.css_rgba(accent, 0.14),
                "nav_text": "#20314e",
                "nav_active_text": "#17325e",
                "badge_bg": "rgba(243,247,255,0.95)",
                "badge_live_bg": self.css_rgba(accent, 0.10),
                "badge_live_text": "#17325e",
                "hero_glow": self.css_rgba(accent_3, 0.18),
            }
        if self.current_theme == "blue":
            return {
                **common,
                "page_background": "radial-gradient(620px circle at 12% 10%, rgba(38, 168, 218, 0.16), transparent 46%), radial-gradient(540px circle at 88% 14%, rgba(114, 239, 210, 0.12), transparent 36%), linear-gradient(180deg, #edf9fc 0%, #dff4f8 100%)",
                "panel": "rgba(242,251,253,0.90)",
                "panel_alt": "rgba(248,253,255,0.98)",
                "card": "rgba(255,255,255,0.98)",
                "chip": "rgba(224,247,251,0.96)",
                "input": "#ffffff",
                "text": "#11394a",
                "muted": "#4d7281",
                "border": "rgba(31, 129, 154, 0.18)",
                "line": "rgba(17, 68, 86, 0.10)",
                "shadow": "0 22px 60px rgba(25, 102, 127, 0.12)",
                "primary_bg": f"linear-gradient(135deg, {accent.name()}, {accent_2.name()})",
                "primary_text": "#08313d",
                "nav_bg": "rgba(226, 248, 251, 0.85)",
                "nav_hover": "rgba(214, 242, 247, 0.98)",
                "nav_active": self.css_rgba(accent, 0.14),
                "nav_text": "#12526a",
                "nav_active_text": "#0f485d",
                "badge_bg": "rgba(240,251,253,0.98)",
                "badge_live_bg": self.css_rgba(accent, 0.12),
                "badge_live_text": "#0f485d",
                "hero_glow": self.css_rgba(accent_3, 0.18),
            }
        if self.current_theme == "wallpaper" and getattr(self, "wallpaper_path", None) and os.path.exists(self.wallpaper_path):
            wallpaper_url = QUrl.fromLocalFile(self.wallpaper_path).toString()
            page_background = (
                f"linear-gradient(180deg, rgba(4, 8, 18, 0.74), rgba(4, 8, 18, 0.84)), "
                f"radial-gradient(620px circle at 12% 10%, {self.css_rgba(accent, 0.18)}, transparent 46%), "
                f"url('{wallpaper_url}') center / cover fixed no-repeat"
            )
        else:
            page_background = "radial-gradient(620px circle at 12% 12%, rgba(70, 115, 255, 0.30), transparent 50%), radial-gradient(460px circle at 88% 18%, rgba(255, 140, 176, 0.12), transparent 42%), radial-gradient(720px circle at 52% 120%, rgba(76, 223, 209, 0.18), transparent 45%), linear-gradient(180deg, #081220 0%, #040911 100%)"
        return {
            **common,
            "page_background": page_background,
            "panel": "rgba(9, 20, 39, 0.84)",
            "panel_alt": "rgba(5, 13, 26, 0.92)",
            "card": "rgba(5, 13, 26, 0.84)",
            "chip": "rgba(7, 16, 32, 0.62)",
            "input": "rgba(5, 13, 26, 0.92)",
            "text": "#eef5ff",
            "muted": "#97aecd",
            "border": "rgba(121, 166, 255, 0.22)",
            "line": "rgba(255,255,255,0.08)",
            "shadow": "0 24px 70px rgba(1, 7, 20, 0.45)",
            "primary_bg": f"linear-gradient(135deg, {accent_3.name()}, {accent_2.name()})",
            "primary_text": "#07101f",
            "nav_bg": "rgba(255,255,255,0.03)",
            "nav_hover": "rgba(255,255,255,0.05)",
            "nav_active": self.css_rgba(accent, 0.16),
            "nav_text": "#dce9ff",
            "nav_active_text": "#ffffff",
            "badge_bg": "rgba(255,255,255,0.05)",
            "badge_live_bg": self.css_rgba(accent_2, 0.12),
            "badge_live_text": "#eefcff",
            "hero_glow": self.css_rgba(accent_3, 0.18),
        }

    def load_browser_settings(self):
        defaults = {
            "reading_mode_enabled": False,
            "weather_enabled": False,
            "time_capsule_enabled": False,
            "soundscape_enabled": False,
            "music_folder": os.path.join(os.path.expanduser("~"), "Music"),
            "bookmark_bar_visible": True,
            "search_engine": "Google",
            "restore_session_enabled": True,
            "low_power_mode": False,
            "ad_blocker_enabled": True,
            "popup_blocker_enabled": True,
            "site_security_allowlist": [],
        }
        if not os.path.exists(self.browser_settings_file):
            return defaults
        try:
            with open(self.browser_settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                defaults.update({k: data.get(k, v) for k, v in defaults.items()})
        except Exception as e:
            print(f"Settings load error: {e}")
        return defaults

    def save_browser_settings(self):
        try:
            with open(self.browser_settings_file, "w", encoding="utf-8") as f:
                json.dump({
                    "reading_mode_enabled": self.reading_mode_enabled,
                    "weather_enabled": self.weather_enabled,
                    "time_capsule_enabled": self.time_capsule_enabled,
                    "soundscape_enabled": self.soundscape_enabled,
                    "music_folder": self.music_folder,
                    "bookmark_bar_visible": getattr(self, "bookmark_bar_visible", True),
                    "search_engine": self.search_engine_combo.currentText() if hasattr(self, "search_engine_combo") else "Google",
                    "restore_session_enabled": getattr(self, "restore_session_enabled", True),
                    "low_power_mode": getattr(self, "low_power_mode", False),
                    "ad_blocker_enabled": getattr(self, "ad_blocker_enabled", True),
                    "popup_blocker_enabled": getattr(self, "popup_blocker_enabled", True),
                    "site_security_allowlist": list(getattr(self, "site_security_allowlist", [])),
                }, f, indent=2)
            self.invalidate_internal_cache()
        except Exception as e:
            print(f"Settings save error: {e}")

    def load_pinned_tabs(self):
        if not os.path.exists(self.pinned_tabs_file):
            return []
        try:
            with open(self.pinned_tabs_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [url for url in data if isinstance(url, str) and url.strip()]
        except Exception as e:
            print(f"Pinned tab load error: {e}")
        return []

    def save_pinned_tabs(self):
        pinned_urls = []
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if tab is None or not tab.property("pinned"):
                continue
            url = tab.url().toString().strip()
            if url and not self.is_default_home_url(url):
                pinned_urls.append(url)
        self.pinned_tabs = pinned_urls
        try:
            with open(self.pinned_tabs_file, "w", encoding="utf-8") as f:
                json.dump(self.pinned_tabs, f, indent=2)
        except Exception as e:
            print(f"Pinned tab save error: {e}")

    def load_weather_preferences(self):
        defaults = {"city": "London", "last_status": "", "timestamp": 0}
        if not os.path.exists(self.weather_file):
            return defaults
        try:
            with open(self.weather_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                defaults["city"] = (data.get("city") or defaults["city"]).strip() or "London"
                defaults["last_status"] = (data.get("last_status") or "").strip()
                defaults["timestamp"] = float(data.get("timestamp", 0) or 0)
        except Exception as e:
            print(f"Weather settings load error: {e}")
        return defaults

    def save_weather_preferences(self):
        try:
            with open(self.weather_file, "w", encoding="utf-8") as f:
                json.dump({
                    "city": self.weather_city,
                    "last_status": (self.weather_cache.get("value") or "").strip(),
                    "timestamp": int(self.weather_cache.get("timestamp", 0) or 0),
                }, f, indent=2)
            self.invalidate_internal_cache()
        except Exception as e:
            QMessageBox.warning(self, "Weather Save Error", f"Could not save weather city: {e}")

    def load_weather_city(self):
        return self.load_weather_preferences().get("city", "London")

    def save_weather_city(self):
        self.save_weather_preferences()

    def set_weather_city(self):
        common_cities = [
            "Bangalore",
            "Delhi",
            "Mumbai",
            "London",
            "New York",
            "Singapore",
            "Dubai",
            "Tokyo",
            "Sydney",
            "Toronto",
        ]
        choices = []
        current_city = (self.weather_city or "").strip()
        if current_city:
            choices.append(current_city)
        for city_name in common_cities:
            if city_name not in choices:
                choices.append(city_name)
        city, ok = QInputDialog.getItem(
            self,
            "Weather City",
            "Choose or type a city:",
            choices,
            0,
            True,
        )
        city = (city or "").strip()
        if ok and city:
            self.weather_city = city
            self.save_weather_city()
            self.weather_cache = {"timestamp": 0, "value": ""}
            self.updateStatusBar()
            self.show_runtime_status(f"Weather city set to {city}", 2500)
            return True
        return False

    def load_time_capsule(self):
        if not os.path.exists(self.time_capsule_file):
            return []
        try:
            with open(self.time_capsule_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict) and item.get("url") and item.get("unlock_date")]
        except Exception as e:
            print(f"Time capsule load error: {e}")
        return []

    def check_due_time_capsules(self):
        if not self.time_capsule_data:
            return
        from datetime import datetime
        due_capsules = []
        remaining_capsules = []
        for capsule in self.time_capsule_data:
            try:
                unlock_date = datetime.strptime(capsule["unlock_date"], "%Y-%m-%d")
            except Exception:
                remaining_capsules.append(capsule)
                continue
            if unlock_date <= datetime.now():
                due_capsules.append(capsule)
            else:
                remaining_capsules.append(capsule)
        if due_capsules:
            self.time_capsule_data = remaining_capsules
            self.save_time_capsule()
            for capsule in due_capsules:
                self.add_new_tab(QUrl(capsule["url"]), "Time Capsule")
            self.show_runtime_status(f"Opened {len(due_capsules)} due time capsule(s)", 3500)

    def collect_homepage_suggestions(self, recent_urls, bookmark_links):
        suggestions = []
        seen = set()

        def add_suggestion(value, meta):
            cleaned_value = (value or "").strip()
            cleaned_meta = (meta or "").strip()
            if not cleaned_value:
                return
            key = cleaned_value.lower()
            if key in seen:
                return
            seen.add(key)
            suggestions.append({"value": cleaned_value, "meta": cleaned_meta})

        for item in self.quick_access_links[:12]:
            title = normalize_quick_access_title(item.get("title"), item.get("url", ""))
            add_suggestion(item.get("url", ""), f"{title} · Quick access")
            add_suggestion(title, "Quick access title")

        for item in bookmark_links[:18]:
            title = normalize_quick_access_title(item.get("title"), item.get("url", ""))
            add_suggestion(item.get("url", ""), f"{title} · Bookmark")
            add_suggestion(title, "Bookmark title")

        for url_value in recent_urls[:24]:
            add_suggestion(url_value, f"{prettify_host_label(url_value)} · Recent")

        return suggestions[:28]

    def build_custom_homepage_html(self):
        recent_urls = []
        seen_recent = set()
        for url in reversed(self.history_manager.history_data):
            cleaned = url.strip()
            if cleaned and cleaned.startswith("http") and cleaned not in seen_recent:
                recent_urls.append(cleaned)
                seen_recent.add(cleaned)
            if len(recent_urls) >= 12:
                break

        bookmarks_file = os.path.join(self.profile_base_dir, "bookmarks.json")
        bookmark_links = []
        if os.path.exists(bookmarks_file):
            try:
                with open(bookmarks_file, "r", encoding="utf-8") as f:
                    bookmark_data = json.load(f)
                if isinstance(bookmark_data, list):
                    for item in bookmark_data:
                        if isinstance(item, dict) and item.get("url"):
                            bookmark_links.append({
                                "title": normalize_quick_access_title(item.get("title"), item["url"]),
                                "url": item["url"],
                            })
            except Exception as e:
                print(f"Bookmark load error: {e}")

        def make_site_card(title, url_value, meta):
            title = normalize_quick_access_title(title, url_value)
            host = (urlparse(url_value).hostname or url_value).replace("www.", "")
            accent = "gold" if meta == "Pinned" else "cool"
            return (
                f"<a class='siteCard {accent}' href='{html.escape(url_value)}'>"
                f"<div class='siteTop'>"
                f"<div class='siteBadge'>{html.escape(title[:2].upper())}</div>"
                f"<div class='siteHost'>{html.escape(host)}</div>"
                "</div>"
                f"<div class='siteTitle'>{html.escape(title)}</div>"
                f"<div class='siteMeta'>{html.escape(meta)}</div>"
                "</a>"
            )

        quick_cards = []
        for item in self.quick_access_links[:6]:
            quick_cards.append(make_site_card(item["title"], item["url"], "Pinned"))
        if not quick_cards:
            defaults = [
                ("Google", "https://www.google.com", "Search"),
                ("YouTube", "https://www.youtube.com", "Watch"),
                ("Gmail", "https://mail.google.com", "Inbox"),
                ("GitHub", "https://github.com", "Code"),
                ("Reddit", "https://www.reddit.com", "Communities"),
                ("LinkedIn", "https://www.linkedin.com", "Network"),
            ]
            quick_cards = [make_site_card(title, url_value, meta) for title, url_value, meta in defaults]

        spotlight_items = []
        seen_hosts = set()
        for item in bookmark_links:
            host = (urlparse(item["url"]).hostname or "").lower()
            if host and host not in seen_hosts:
                spotlight_items.append(make_site_card(item["title"], item["url"], "Bookmark"))
                seen_hosts.add(host)
            if len(spotlight_items) >= 3:
                break
        for url_value in recent_urls:
            host = (urlparse(url_value).hostname or "").lower()
            if host and host not in seen_hosts:
                spotlight_items.append(make_site_card(prettify_host_label(url_value), url_value, "Recent"))
                seen_hosts.add(host)
            if len(spotlight_items) >= 6:
                break

        recent_rows = []
        for url_value in recent_urls[:6]:
            host = (urlparse(url_value).hostname or "").replace("www.", "")
            recent_rows.append(
                f"<a class='recentRow' href='{html.escape(url_value)}'>"
                f"<span class='recentName'>{html.escape(prettify_host_label(url_value))}</span>"
                f"<span class='recentHost'>{html.escape(host)}</span>"
                "</a>"
            )
        if not recent_rows:
            recent_rows.append("<div class='recentEmpty'>Your latest pages will appear here.</div>")

        bookmark_count = len(bookmark_links)
        history_count = len(self.history_manager.history_data)
        quick_count = len(self.quick_access_links)
        profile_label = html.escape(self.profile)
        suggestion_items = self.collect_homepage_suggestions(recent_urls, bookmark_links)
        quick_cards_html = "\n".join(quick_cards)
        spotlight_html = "\n".join(spotlight_items) if spotlight_items else "<div class='emptyPanel'>Add bookmarks or browse a bit more to populate this area.</div>"
        recent_rows_html = "\n".join(recent_rows)

        search_engine_name = html.escape(self.current_search_engine_name())
        search_base = json.dumps(self.search_url_for_query("").rstrip("+"))
        suggestions_json = json.dumps(suggestion_items)
        body_class = "lowPower" if self.low_power_mode else ""
        return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Aurora Home</title>
  <style>
    :root {{
      --panel: rgba(9, 20, 39, 0.84);
      --panelAlt: rgba(5, 13, 26, 0.92);
      --card: rgba(5, 13, 26, 0.84);
      --chip: rgba(7, 16, 32, 0.62);
      --input: rgba(5, 13, 26, 0.92);
      --text: #eef5ff;
      --muted: #97aecd;
      --border: rgba(121, 166, 255, 0.22);
      --line: rgba(255,255,255,0.08);
      --cyan: #79d8ff;
      --mint: #69ebcf;
      --gold: #ffd86c;
      --accentSoft: rgba(105, 235, 207, 0.20);
      --accentLine: rgba(121, 216, 255, 0.24);
      --primaryBg: linear-gradient(135deg, var(--gold), var(--mint));
      --primaryText: #07101f;
      --heroGlow: rgba(255,216,108,0.18);
      --shadow: 0 24px 70px rgba(1, 7, 20, 0.45);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      font-family: "Bahnschrift", "Trebuchet MS", sans-serif;
      background:
        radial-gradient(620px circle at 12% 12%, rgba(70, 115, 255, 0.30), transparent 50%),
        radial-gradient(460px circle at 88% 18%, rgba(255, 140, 176, 0.12), transparent 42%),
        radial-gradient(720px circle at 52% 120%, rgba(76, 223, 209, 0.18), transparent 45%),
        linear-gradient(180deg, #081220 0%, #040911 100%);
      padding: 28px;
    }}
    body.lowPower {{
      background: linear-gradient(180deg, #0a1220 0%, #050b14 100%);
    }}
    body.lowPower * {{
      animation: none !important;
      transition: none !important;
    }}
    .shell {{
      width: min(1280px, 100%);
      margin: 0 auto;
      display: grid;
      gap: 18px;
    }}
    .hero {{
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) minmax(320px, 0.9fr);
      gap: 18px;
    }}
    .heroMain, .heroSide, .section, .panel {{
        background:
          linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0)),
        var(--panel);
        border: 1px solid var(--border);
        border-radius: 28px;
        box-shadow: var(--shadow);
        overflow: hidden;
        position: relative;
      }}
    .heroMain {{
        padding: 34px;
        min-height: 340px;
        background:
          radial-gradient(420px circle at 14% 8%, rgba(121, 216, 255, 0.16), transparent 58%),
          linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0)),
        var(--panel);
        overflow: visible;
      }}
    .heroMain::after {{
      content: "";
      position: absolute;
      right: -60px;
      top: -40px;
      width: 240px;
      height: 240px;
      border-radius: 50%;
      background: radial-gradient(circle, var(--heroGlow), transparent 65%);
      pointer-events: none;
    }}
    .eyebrow {{
      font-size: 12px;
      letter-spacing: 1.6px;
      text-transform: uppercase;
      color: var(--cyan);
      margin-bottom: 12px;
    }}
    .brand {{
      margin: 0;
      font-size: clamp(46px, 7vw, 92px);
      line-height: 0.92;
      font-weight: 800;
      font-family: "Georgia", "Times New Roman", serif;
    }}
    .tagline {{
      margin-top: 14px;
      max-width: 620px;
      font-size: 16px;
      line-height: 1.6;
      color: var(--muted);
    }}
    .heroMeta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 18px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 14px;
      border-radius: 999px;
      border: 1px solid var(--accentLine);
      background: var(--chip);
      font-size: 13px;
      color: var(--text);
    }}
    .searchWrap {{
        margin-top: 24px;
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(142px, 156px);
        gap: 14px;
        align-items: stretch;
        position: relative;
        z-index: 4;
      }}
      .searchStack {{
        position: relative;
        z-index: 5;
      }}
    .search {{
      width: 100%;
      min-height: 68px;
      border-radius: 24px;
      border: 1px solid rgba(121, 166, 255, 0.26);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0)),
        rgba(5, 13, 26, 0.94);
      color: var(--text);
      padding: 0 22px;
      font-size: 20px;
      font-weight: 600;
      letter-spacing: 0.01em;
      outline: none;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
      transition: border-color 140ms ease, box-shadow 140ms ease, transform 140ms ease;
    }}
    .search::placeholder {{
      color: rgba(151, 174, 205, 0.88);
      font-weight: 500;
    }}
    .search:focus {{
      border-color: rgba(105, 235, 207, 0.82);
      box-shadow: 0 0 0 4px rgba(105, 235, 207, 0.12), 0 16px 36px rgba(2, 8, 20, 0.28);
      transform: translateY(-1px);
    }}
    .suggestions {{
        position: absolute;
        top: calc(100% + 12px);
        left: 0;
        right: 0;
        display: none;
        gap: 8px;
        padding: 12px;
        border-radius: 20px;
        background:
          linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0)),
          rgba(4, 10, 21, 0.98);
        border: 1px solid rgba(121, 216, 255, 0.30);
        box-shadow: 0 24px 52px rgba(0, 0, 0, 0.32);
        z-index: 80;
        max-height: 320px;
        overflow-y: auto;
      }}
    .suggestions.show {{
      display: grid;
    }}
      .suggestionItem {{
        width: 100%;
        text-align: left;
        border: 1px solid transparent;
        background: rgba(255,255,255,0.025);
        color: var(--text);
        border-radius: 16px;
        padding: 12px 14px;
        cursor: pointer;
      }}
    .suggestionItem:hover,
    .suggestionItem.active {{
      border-color: var(--accentLine);
      background: rgba(121, 216, 255, 0.10);
    }}
    .suggestionValue {{
      display: block;
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .suggestionMeta {{
      display: block;
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .go {{
        min-width: 142px;
        min-height: 68px;
        border: none;
        border-radius: 24px;
        padding: 0 24px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        align-self: stretch;
        font-size: 18px;
        font-weight: 900;
        letter-spacing: 0.01em;
        color: var(--primaryText);
        background: var(--primaryBg);
        cursor: pointer;
        box-shadow: 0 18px 36px var(--accentSoft);
        transition: transform 140ms ease, box-shadow 140ms ease, filter 140ms ease;
      }}
      .go:hover {{
        transform: translateY(-1px);
        box-shadow: 0 22px 42px rgba(105, 235, 207, 0.24);
        filter: saturate(1.03);
      }}
    .heroSide {{
      padding: 28px;
      display: grid;
      gap: 12px;
      align-content: start;
      background:
        radial-gradient(320px circle at 88% 10%, rgba(255,140,176,0.14), transparent 52%),
        linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0)),
        var(--panel);
    }}
    .sectionLabel {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 1.4px;
      color: var(--cyan);
    }}
    .statGrid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 10px;
    }}
    .stat {{
      padding: 16px;
      border-radius: 18px;
      border: 1px solid rgba(255,255,255,0.08);
      background: rgba(5, 13, 26, 0.82);
    }}
    .statValue {{
      font-size: 26px;
      font-weight: 800;
      margin-bottom: 6px;
    }}
    .statLabel {{
      color: var(--muted);
      font-size: 12px;
    }}
    .shortcutCard {{
      padding: 16px 18px;
      border-radius: 20px;
      background: var(--card);
      border: 1px solid var(--line);
    }}
    .shortcutTitle {{
      font-size: 15px;
      font-weight: 700;
      margin-bottom: 8px;
    }}
    .shortcutRow {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      font-size: 13px;
      padding: 6px 0;
      border-top: 1px solid var(--line);
    }}
    .shortcutRow:first-of-type {{
      border-top: none;
      padding-top: 0;
    }}
    .layout {{
      display: grid;
      grid-template-columns: minmax(0, 1.55fr) minmax(300px, 0.95fr);
      gap: 18px;
    }}
    .section {{
      padding: 28px;
    }}
    .sectionTitle {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 16px;
    }}
    .sectionTitle h2 {{
      margin: 0;
      font-size: 30px;
      font-family: "Georgia", "Times New Roman", serif;
    }}
    .sectionHint {{
      color: var(--muted);
      font-size: 13px;
    }}
    .siteGrid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 14px;
    }}
    .siteCard {{
      text-decoration: none;
      color: inherit;
      min-height: 150px;
      border-radius: 22px;
      padding: 18px;
      border: 1px solid rgba(255,255,255,0.08);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0)),
        var(--card);
      transition: transform 120ms ease, border-color 120ms ease;
    }}
    .siteCard:hover {{
      transform: translateY(-2px);
      border-color: var(--accentLine);
    }}
    .siteCard.gold .siteBadge {{
      background: linear-gradient(135deg, var(--gold), var(--mint));
      color: #06101d;
    }}
    .siteCard.cool .siteBadge {{
      background: linear-gradient(135deg, var(--cyan), #8c9dff);
      color: #06101d;
    }}
    .siteTop {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 18px;
    }}
    .siteBadge {{
      width: 38px;
      height: 38px;
      border-radius: 12px;
      display: grid;
      place-items: center;
      font-weight: 800;
      font-size: 13px;
    }}
    .siteHost {{
      color: var(--muted);
      font-size: 13px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .siteTitle {{
      font-size: 18px;
      font-weight: 700;
      margin-bottom: 8px;
    }}
    .siteMeta {{
      color: var(--muted);
      font-size: 13px;
    }}
    .panel {{
      padding: 24px;
    }}
    .recentList {{
      display: grid;
      gap: 10px;
    }}
    .recentRow {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
      text-decoration: none;
      color: inherit;
      padding: 14px 16px;
      border-radius: 16px;
      background: var(--card);
      border: 1px solid var(--line);
    }}
    .recentRow:hover {{
      border-color: var(--accentLine);
    }}
    .recentName {{
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .recentHost {{
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }}
    .recentEmpty, .emptyPanel {{
      padding: 18px;
      border-radius: 18px;
      background: var(--card);
      color: var(--muted);
      border: 1px dashed var(--line);
    }}
    .footerNote {{
      margin-top: 16px;
      color: var(--muted);
      font-size: 13px;
    }}
    .kbd {{
      display: inline-block;
      padding: 2px 7px;
      border-radius: 8px;
      border: 1px solid var(--accentLine);
      background: var(--input);
      color: var(--text);
      font-size: 12px;
    }}
    @media (max-width: 980px) {{
      .hero, .layout {{ grid-template-columns: 1fr; }}
      .statGrid {{ grid-template-columns: repeat(3, 1fr); }}
    }}
    @media (max-width: 720px) {{
      body {{ padding: 14px; }}
      .heroMain, .heroSide, .section, .panel {{ border-radius: 22px; }}
      .heroMain, .heroSide, .section, .panel {{ padding-left: 20px; padding-right: 20px; }}
      .searchWrap {{ grid-template-columns: 1fr; }}
      .go {{ width: 100%; }}
      .statGrid {{ grid-template-columns: 1fr; }}
      .siteGrid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body class="{body_class}">
  <main class="shell">
    <section class="hero">
      <div class="heroMain">
        <div class="eyebrow">Aurora Home</div>
        <h1 class="brand">Aurora</h1>
        <div class="tagline">Search fast, jump back into the sites that matter, and keep each browsing profile clearly separated.</div>
        <div class="heroMeta">
          <div class="pill">Profile: {profile_label}</div>
          <div class="pill">{search_engine_name} is your default search</div>
          <div class="pill">Quick access managed from Tools</div>
        </div>
        <div class="searchWrap">
          <div class="searchStack">
            <input id="searchInput" class="search" placeholder="Search with {search_engine_name} or paste a URL" autocomplete="off" />
            <div id="suggestions" class="suggestions"></div>
          </div>
          <button id="goBtn" class="go">Search</button>
        </div>
      </div>
      <aside class="heroSide">
        <div class="sectionLabel">Profile Snapshot</div>
        <div class="statGrid">
          <div class="stat"><div class="statValue">{quick_count}</div><div class="statLabel">Quick links</div></div>
          <div class="stat"><div class="statValue">{bookmark_count}</div><div class="statLabel">Bookmarks</div></div>
          <div class="stat"><div class="statValue">{history_count}</div><div class="statLabel">History items</div></div>
        </div>
        <div class="shortcutCard">
          <div class="shortcutTitle">Useful shortcuts</div>
          <div class="shortcutRow"><span>Focus address bar</span><span class="kbd">Ctrl+L</span></div>
          <div class="shortcutRow"><span>New incognito window</span><span class="kbd">Ctrl+Shift+N</span></div>
          <div class="shortcutRow"><span>Developer tools</span><span class="kbd">F12</span></div>
        </div>
      </aside>
    </section>

    <section class="layout">
      <div class="section">
        <div class="sectionTitle">
          <h2>Quick Access</h2>
          <div class="sectionHint">Pinned destinations for this profile</div>
        </div>
        <div class="siteGrid">
          {quick_cards_html}
        </div>
        <div class="footerNote">Use <span class="kbd">Tools</span> -> <span class="kbd">Add Quick Access Link</span> to pin more destinations.</div>
      </div>

      <div class="panel">
        <div class="sectionTitle">
          <h2>Recent Flow</h2>
          <div class="sectionHint">Your latest pages</div>
        </div>
        <div class="recentList">
          {recent_rows_html}
        </div>
      </div>
    </section>

    <section class="section">
      <div class="sectionTitle">
        <h2>Spotlight</h2>
        <div class="sectionHint">Bookmarks and fresh history blended together</div>
      </div>
      <div class="siteGrid">
        {spotlight_html}
      </div>
    </section>
  </main>
  <script>
    const input = document.getElementById("searchInput");
      const goBtn = document.getElementById("goBtn");
      const suggestionsNode = document.getElementById("suggestions");
        const searchBase = {search_base};
      const suggestionItems = {suggestions_json};
      const recentSearchStorageKey = "auroraRecentSearches";
      const minQueryLength = 3;
      const normalizedItems = suggestionItems.map((item) => ({{
        value: item.value || "",
        meta: item.meta || "",
        valueLower: (item.value || "").toLowerCase(),
        metaLower: (item.meta || "").toLowerCase()
      }}));
      let activeSuggestionIndex = -1;
      let suggestionTimer = null;
    function looksLikeUrl(raw) {{
      return raw.startsWith("http://") || raw.startsWith("https://") || raw.includes(".") || raw.includes("/");
    }}
      function openQuery() {{
        const raw = input.value.trim();
        if (!raw) return;
        const hasScheme = raw.startsWith("http://") || raw.startsWith("https://");
        if (hasScheme || looksLikeUrl(raw)) {{
          location.href = hasScheme ? raw : "https://" + raw;
        }} else {{
          saveRecentSearch(raw);
          location.href = searchBase + encodeURIComponent(raw);
        }}
      }}
      function loadRecentSearches() {{
        try {{
          const parsed = JSON.parse(localStorage.getItem(recentSearchStorageKey) || "[]");
          if (Array.isArray(parsed)) {{
            return parsed.filter((item) => typeof item === "string" && item.trim()).slice(0, 8);
          }}
        }} catch (err) {{}}
        return [];
      }}
      function saveRecentSearch(query) {{
        const clean = (query || "").trim();
        if (clean.length < minQueryLength) return;
        const recent = loadRecentSearches().filter((item) => item.toLowerCase() !== clean.toLowerCase());
        recent.unshift(clean);
        try {{
          localStorage.setItem(recentSearchStorageKey, JSON.stringify(recent.slice(0, 8)));
        }} catch (err) {{}}
      }}
      function hideSuggestions() {{
        suggestionsNode.innerHTML = "";
        suggestionsNode.classList.remove("show");
        activeSuggestionIndex = -1;
      }}
    function applySuggestion(index) {{
      const buttons = suggestionsNode.querySelectorAll(".suggestionItem");
      if (!buttons.length) return;
      activeSuggestionIndex = (index + buttons.length) % buttons.length;
      buttons.forEach((button, buttonIndex) => {{
        button.classList.toggle("active", buttonIndex === activeSuggestionIndex);
      }});
    }}
      function suggestionScore(item, query) {{
        let score = 0;
        if (item.metaLower === "recent search") score += 3;
        if (item.valueLower.startsWith(query)) score += 5;
        if (item.metaLower.startsWith(query)) score += 2;
        if (item.valueLower.includes(query)) score += 1;
        if (item.metaLower.includes(query)) score += 1;
        return score;
      }}
      function renderSuggestions() {{
        const rawText = input.value.trim();
        if (rawText.length < minQueryLength) {{
          hideSuggestions();
          return;
        }}
        const raw = rawText.toLowerCase();
        const recentItems = loadRecentSearches().map((item) => ({{
          value: item,
          meta: "Recent search",
          valueLower: item.toLowerCase(),
          metaLower: "recent search"
        }}));
        const mergedItems = [...recentItems, ...normalizedItems];
        const seenValues = new Set();
        const matches = mergedItems
          .filter((item) => {{
            const key = item.valueLower;
            if (seenValues.has(key)) return false;
            if (!(item.valueLower.includes(raw) || item.metaLower.includes(raw))) return false;
            seenValues.add(key);
            return true;
          }})
          .sort((left, right) => suggestionScore(right, raw) - suggestionScore(left, raw) || left.value.localeCompare(right.value))
          .slice(0, 6);
        if (!matches.length) {{
          hideSuggestions();
          return;
        }}
        suggestionsNode.innerHTML = matches.map((item, index) => `
          <button class="suggestionItem" type="button" data-index="${{index}}" data-value="${{item.value.replace(/"/g, '&quot;')}}">
            <span class="suggestionValue">${{item.value}}</span>
            <span class="suggestionMeta">${{item.meta}}</span>
          </button>
        `).join("");
        suggestionsNode.classList.add("show");
        activeSuggestionIndex = -1;
      suggestionsNode.querySelectorAll(".suggestionItem").forEach((button) => {{
        button.addEventListener("click", () => {{
          input.value = button.dataset.value || "";
          hideSuggestions();
          openQuery();
        }});
      }});
      }}
      function scheduleSuggestions() {{
        if (suggestionTimer) {{
          clearTimeout(suggestionTimer);
        }}
        suggestionTimer = setTimeout(renderSuggestions, 150);
      }}
      input.focus();
      input.addEventListener("input", scheduleSuggestions);
      input.addEventListener("focus", scheduleSuggestions);
    input.addEventListener("keydown", (ev) => {{
      const buttons = suggestionsNode.querySelectorAll(".suggestionItem");
      if (ev.key === "ArrowDown" && buttons.length) {{
        ev.preventDefault();
        applySuggestion(activeSuggestionIndex + 1);
        return;
      }}
      if (ev.key === "ArrowUp" && buttons.length) {{
        ev.preventDefault();
        applySuggestion(activeSuggestionIndex - 1);
        return;
      }}
      if (ev.key === "Enter") {{
        if (buttons.length && activeSuggestionIndex >= 0) {{
          ev.preventDefault();
          buttons[activeSuggestionIndex].click();
          return;
        }}
        openQuery();
      }}
      if (ev.key === "Escape") {{
        hideSuggestions();
      }}
    }});
    goBtn.addEventListener("click", openQuery);
    document.addEventListener("click", (event) => {{
      if (!suggestionsNode.contains(event.target) && event.target !== input) {{
        hideSuggestions();
      }}
    }});
  </script>
</body>
</html>
        """

    def parse_internal_settings_section(self, target_str):
        lowered = (target_str or "").strip().lower()
        if lowered.startswith("aurora://settings/"):
            return lowered.split("aurora://settings/", 1)[1].split("?", 1)[0].strip("/") or "profiles"
        if lowered in ("aurora://settings", "aurora:settings"):
            return "profiles"
        if "aurora.settings" in lowered:
            parsed = urlparse(lowered)
            section = parsed.path.strip("/") or "profiles"
            return section
        return "profiles"

    def settings_nav_items(self):
        return [
            ("profiles", "Profiles"),
            ("appearance", "Appearance"),
            ("privacy", "Privacy"),
            ("downloads", "Downloads"),
            ("media", "Media"),
            ("extensions", "Extensions (Beta)"),
            ("about", "About"),
        ]

    def settings_nav_groups(self):
        return [
            ("Browser", [("profiles", "Profiles"), ("appearance", "Appearance"), ("privacy", "Privacy")]),
            ("Data", [("downloads", "Downloads"), ("media", "Media")]),
            ("Support", [("extensions", "Extensions (Beta)"), ("about", "About")]),
        ]

    def get_profile_avatar_data_uri(self, profile_name):
        avatar_path = get_profile_avatar_path(profile_name)
        if not avatar_path or not os.path.exists(avatar_path):
            return ""
        extension = os.path.splitext(avatar_path)[1].lower().lstrip(".") or "png"
        mime = "jpeg" if extension == "jpg" else extension
        if mime not in {"png", "jpeg", "bmp", "webp"}:
            mime = "png"
        try:
            with open(avatar_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("ascii")
            return f"data:image/{mime};base64,{encoded}"
        except Exception:
            return ""

    def settings_nav_link(self, key):
        section_keys = {name for name, _ in self.settings_nav_items()}
        if key in section_keys:
            return f"https://aurora.settings/{key}"
        return "https://aurora.settings/profiles"

    def build_settings_page_html(self, section="appearance"):
        palette = {
            "page_background": "radial-gradient(680px circle at 6% 8%, rgba(81, 127, 255, 0.22), transparent 54%), radial-gradient(520px circle at 92% 12%, rgba(124, 217, 255, 0.10), transparent 38%), linear-gradient(180deg, #07101d 0%, #03070e 100%)",
            "panel": "rgba(13, 24, 42, 0.9)",
            "panel_alt": "rgba(8, 16, 30, 0.96)",
            "text": "#edf4ff",
            "muted": "#90a8cb",
            "line": "rgba(132, 172, 255, 0.18)",
            "accent": "#7cd9ff",
            "accent_2": "#73efd0",
            "accent_3": "#ffd86b",
            "danger": "#ff8ea5",
            "nav_bg": "rgba(255,255,255,0.03)",
            "nav_hover": "rgba(255,255,255,0.05)",
            "nav_active": "linear-gradient(135deg, rgba(124,217,255,0.16), rgba(115,239,208,0.10))",
            "nav_text": "#dce9ff",
            "nav_active_text": "#ffffff",
            "primary_bg": "linear-gradient(135deg, #ffd86b, #73efd0)",
            "primary_text": "#07101b",
            "badge_bg": "rgba(255,255,255,0.05)",
            "badge_live_bg": "rgba(115,239,208,0.12)",
            "badge_live_text": "#dffff6",
            "shadow": "0 30px 80px rgba(0, 0, 0, 0.35)",
        }
        current_section = section if section in {name for name, _ in self.settings_nav_items()} else "appearance"
        self.current_settings_section = current_section
        theme_label = {
            "light": "Light",
            "dark": "Dark",
            "blue": "Blue",
            "graphite": "Graphite",
            "forest": "Forest",
            "sunset": "Sunset",
            "accent": "Accent Color",
            "wallpaper": "Wallpaper",
        }.get(self.current_theme, self.current_theme.title())
        profiles = [p for p in discover_profiles() if p]
        profile_avatar_uri = self.get_profile_avatar_data_uri(self.profile)
        profile_initials = html.escape((self.profile[:2] or "AU").upper())
        active_downloads = sum(1 for entry in self.download_manager.download_entries.values() if entry["status"] in ("Starting", "Downloading", "Paused"))
        finished_downloads = sum(1 for entry in self.download_manager.download_entries.values() if entry["status"] in ("Completed", "Cancelled", "Failed", "Interrupted"))
        section_content = ""
        if current_section == "profiles":
            avatar_markup = (
                f"<img class='avatarImage' src='{profile_avatar_uri}' alt='Profile avatar' />"
                if profile_avatar_uri else
                f"<div class='avatarFallback'>{profile_initials}</div>"
            )
            section_content = f"""
            <div class='contentHeader'>
              <div>
                <div class='contentTitle'>Profiles</div>
                <div class='contentHint'>Manage identities for this browser and keep data separated per profile.</div>
              </div>
              <a class='primaryBtn' href='https://aurora.settings/action/manage-profiles'>Open Profile Manager</a>
            </div>
            <div class='settingsGrid'>
              <div class='settingCard solo'>
                <div class='settingLabel'>Current Profile</div>
                <div class='profileHero'>
                  <div class='profileHeroAvatar'>{avatar_markup}</div>
                  <div class='profileHeroBody'>
                    <div class='settingValue'>{html.escape(self.profile)}</div>
                    <div class='settingMeta'>Launch picker, in-browser manager, bookmarks, downloads, homepage, storage, and encrypted passwords stay isolated by profile.</div>
                    <div class='badgeRow'>
                      <span class='badge'>{len(self.load_profile_bookmarks())} bookmarks</span>
                      <span class='badge'>{len(self.history_manager.history_data)} history entries</span>
                      <span class='badge'>{len(profiles)} total profiles</span>
                    </div>
                  </div>
                </div>
                <div class='settingActions'>
                  <a class='miniBtn' href='https://aurora.settings/action/manage-profiles'>Open profile manager</a>
                  <a class='miniBtn subtle' href='https://aurora.settings/action/change-profile-picture'>Change current profile picture</a>
                </div>
              </div>
            </div>
            """
        elif current_section == "appearance":
            bookmark_bar_status = "On" if self.bookmark_bar_visible else "Off"
            low_power_status = "On" if self.low_power_mode else "Off"
            section_content = f"""
            <div class='contentHeader'>
              <div>
                <div class='contentTitle'>Appearance</div>
                <div class='contentHint'>Control the browser shell, homepage look, and visual defaults for this profile.</div>
              </div>
            </div>
            <div class='settingsGrid'>
              <div class='settingCard'>
                <div class='settingLabel'>Theme</div>
                <div class='settingValue'>{html.escape(theme_label)}</div>
                <div class='settingMeta'>Switch between light, dark, blue, graphite, forest, sunset, accent color, or wallpaper themes.</div>
                <div class='settingActions'>
                  <a class='miniBtn' href='https://aurora.settings/action/toggle-theme'>Toggle theme</a>
                  <a class='miniBtn subtle' href='https://aurora.settings/action/customize-ui'>Customize UI</a>
                </div>
              </div>
              <div class='settingCard'>
                <div class='settingLabel'>Low Power Mode</div>
                <div class='settingValue'>{low_power_status}</div>
                <div class='settingMeta'>Reduces animations and refresh frequency to save resources.</div>
                <div class='settingActions'>
                  <a class='miniBtn' href='https://aurora.settings/action/toggle-low-power-mode'>Toggle low power</a>
                </div>
              </div>
              <div class='settingCard'>
                <div class='settingLabel'>Bookmark Bar</div>
                <div class='settingValue'>{bookmark_bar_status}</div>
                <div class='settingMeta'>Keep your top bookmarks visible under the toolbar.</div>
                <div class='settingActions'>
                  <a class='miniBtn' href='https://aurora.settings/action/toggle-bookmark-bar'>Toggle bookmark bar</a>
                  <a class='miniBtn subtle' href='https://aurora.settings/action/open-bookmarks'>Manage bookmarks</a>
                </div>
              </div>
              <div class='settingCard'>
                <div class='settingLabel'>Homepage</div>
                <div class='settingValue'>{html.escape(self.homepage)}</div>
                <div class='settingMeta'>Choose the page that opens for new tabs and home navigation.</div>
                <div class='settingActions'>
                  <a class='miniBtn' href='https://aurora.settings/action/set-homepage'>Set homepage</a>
                  <a class='miniBtn subtle' href='https://aurora.home/'>Open home</a>
                </div>
              </div>
            </div>
            """
        elif current_section == "downloads":
            section_content = f"""
            <div class='contentHeader'>
              <div>
                <div class='contentTitle'>Downloads</div>
                <div class='contentHint'>See download status and control where files land for this profile.</div>
              </div>
              <a class='primaryBtn' href='https://aurora.settings/action/open-downloads'>Open downloads manager</a>
            </div>
            <div class='statsStrip'>
              <div class='statTile'><span>{active_downloads}</span><label>Active</label></div>
              <div class='statTile'><span>{finished_downloads}</span><label>Finished</label></div>
              <div class='statTile path'><span>{html.escape(self.download_manager.default_download_dir)}</span><label>Folder</label></div>
            </div>
            <div class='settingsGrid'>
              <div class='settingCard'>
                <div class='settingLabel'>Downloads Shelf</div>
                <div class='settingValue'>Toolbar popup + full manager</div>
                <div class='settingMeta'>The popup shows recent items and the full manager keeps persisted history.</div>
                <div class='settingActions'>
                  <a class='miniBtn' href='https://aurora.settings/action/open-downloads'>Show downloads</a>
                </div>
              </div>
              <div class='settingCard'>
                <div class='settingLabel'>Download Folder</div>
                <div class='settingValue'>{html.escape(self.download_manager.default_download_dir)}</div>
                <div class='settingMeta'>Aurora saves downloads directly and keeps a history per profile.</div>
                <div class='settingActions'>
                  <a class='miniBtn' href='https://aurora.settings/action/open-downloads-folder'>Open folder</a>
                </div>
              </div>
            </div>
            """
        elif current_section == "privacy":
            reading_status = "On for current page" if self.current_page_reading_mode_enabled() else "Off for current page"
            weather_status = "On" if self.weather_enabled else "Off"
            restore_status = "On" if self.restore_session_enabled else "Off"
            section_content = f"""
            <div class='contentHeader'>
              <div>
                <div class='contentTitle'>Privacy & Browsing</div>
                <div class='contentHint'>Security controls and browsing behaviors tied to this profile.</div>
              </div>
            </div>
            <div class='settingsGrid'>
              <div class='settingCard'>
                <div class='settingLabel'>Security Options</div>
                <div class='settingValue'>Request blocking, DNT, and page restrictions</div>
                <div class='settingMeta'>Open the security panel to tune request interception and privacy flags.</div>
                <div class='settingActions'><a class='miniBtn' href='https://aurora.settings/action/open-security'>Open security options</a></div>
              </div>
              <div class='settingCard'>
                <div class='settingLabel'>Session Restore</div>
                <div class='settingValue'>{restore_status}</div>
                <div class='settingMeta'>When enabled, Aurora can restore your last session on startup.</div>
                <div class='settingActions'><a class='miniBtn' href='https://aurora.settings/action/toggle-session-restore'>Toggle session restore</a></div>
              </div>
              <div class='settingCard'>
                <div class='settingLabel'>Reading Mode</div>
                <div class='settingValue'>{reading_status}</div>
                <div class='settingMeta'>Per-tab only. Open the page you want, switch to it, then use View to toggle reading mode.</div>
                <div class='settingActions'><a class='miniBtn' href='https://aurora.settings/action/open-reading-mode-help'>How to use it</a></div>
              </div>
              <div class='settingCard'>
                <div class='settingLabel'>Weather in Status Bar</div>
                <div class='settingValue'>{weather_status}</div>
                <div class='settingMeta'>Shows live weather for {html.escape(self.weather_city)} in the footer. Aurora caches weather for 10 minutes unless you refresh it manually.</div>
                <div class='settingActions'>
                  <a class='miniBtn' href='https://aurora.settings/action/toggle-weather'>Toggle weather</a>
                  <a class='miniBtn subtle' href='https://aurora.settings/action/set-weather-city'>Set city</a>
                  <a class='miniBtn subtle' href='https://aurora.settings/action/refresh-weather'>Refresh now</a>
                </div>
              </div>
            </div>
            """
        elif current_section == "media":
            soundscape_status = "On" if self.soundscape_enabled else "Off"
            capsule_status = "On" if self.time_capsule_enabled else "Off"
            section_content = f"""
            <div class='contentHeader'>
              <div>
                <div class='contentTitle'>Media & Ambient Tools</div>
                <div class='contentHint'>Soundscape, time capsules, and video-adjacent features for this profile.</div>
              </div>
            </div>
            <div class='settingsGrid'>
              <div class='settingCard'>
                <div class='settingLabel'>Video Playback</div>
                <div class='settingValue'>Native Player</div>
                <div class='settingMeta'>Try Aurora's built-in native player for the current tab when embedded site players fail with source errors.</div>
                <div class='settingActions'>
                  <a class='miniBtn' href='https://aurora.settings/action/open-native-media'>Open native player for current tab</a>
                  <a class='miniBtn' href='https://aurora.settings/action/trust-site-popups'>Trust current site's popups</a>
                </div>
              </div>
              <div class='settingCard'>
                <div class='settingLabel'>Soundscape</div>
                <div class='settingValue'>{soundscape_status}</div>
                <div class='settingMeta'>Plays tracks from {html.escape(self.music_folder)} when enabled.</div>
                <div class='settingActions'>
                  <a class='miniBtn' href='https://aurora.settings/action/toggle-soundscape'>Toggle soundscape</a>
                  <a class='miniBtn subtle' href='https://aurora.settings/action/select-music-folder'>Choose music folder</a>
                </div>
              </div>
              <div class='settingCard'>
                <div class='settingLabel'>Time Capsule</div>
                <div class='settingValue'>{capsule_status}</div>
                <div class='settingMeta'>Store URLs with future unlock dates and reopen them automatically.</div>
                <div class='settingActions'>
                  <a class='miniBtn' href='https://aurora.settings/action/toggle-time-capsule'>Toggle time capsule</a>
                  <a class='miniBtn subtle' href='https://aurora.settings/action/open-time-capsule'>Manage capsules</a>
                </div>
              </div>
            </div>
            """
        elif current_section == "extensions":
            extension_count = len(self.extensions)
            section_content = f"""
            <div class='contentHeader'>
              <div>
                <div class='contentTitle'>Script Extensions (Beta)</div>
                <div class='contentHint'>Aurora supports custom JavaScript script injections, not Chrome Web Store extensions.</div>
              </div>
              <a class='primaryBtn' href='https://aurora.settings/action/open-extensions'>Open extension manager</a>
            </div>
            <div class='settingsGrid'>
              <div class='settingCard'>
                <div class='settingLabel'>Installed Scripts</div>
                <div class='settingValue'>{extension_count}</div>
                <div class='settingMeta'>Use with care. Script extensions can still break sites if they mutate the DOM badly.</div>
                <div class='settingActions'>
                  <a class='miniBtn' href='https://aurora.settings/action/open-extensions'>Manage scripts</a>
                </div>
              </div>
            </div>
            """
        else:
            section_content = f"""
            <div class='contentHeader'>
              <div>
                <div class='contentTitle'>About Aurora</div>
                <div class='contentHint'>Browser summary, active profile state, and support details.</div>
              </div>
            </div>
            <div class='statsStrip'>
              <div class='statTile'><span>{html.escape(self.profile)}</span><label>Active profile</label></div>
              <div class='statTile'><span>{len(self.history_manager.history_data)}</span><label>History entries</label></div>
              <div class='statTile'><span>{len(self.load_profile_bookmarks())}</span><label>Bookmarks</label></div>
            </div>
            <div class='settingCard solo'>
              <div class='settingLabel'>Aurora Browser</div>
              <div class='settingValue'>Desktop browser shell built on PyQt6 and Qt WebEngine</div>
              <div class='settingMeta'>Profiles, downloads, custom homepage, themes, and script extensions are all managed locally per profile.</div>
            </div>
            """

        sidebar = "".join(
            "<div class='navSection'>"
            f"<div class='navSectionTitle'>{group_label}</div>"
            + "".join(
                f"<a class='navItem {'active' if key == current_section else ''}' href='{self.settings_nav_link(key)}'>{label}</a>"
                for key, label in entries
            )
            + "</div>"
            for group_label, entries in self.settings_nav_groups()
        )
        body_class = "lowPower" if self.low_power_mode else ""
        return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Aurora Settings</title>
  <style>
    :root {{
      --panel: {palette["panel"]};
      --panel2: {palette["panel_alt"]};
      --text: {palette["text"]};
      --muted: {palette["muted"]};
      --line: {palette["line"]};
      --cyan: {palette["accent"]};
      --mint: {palette["accent_2"]};
      --gold: {palette["accent_3"]};
      --danger: {palette["danger"]};
      --navBg: {palette["nav_bg"]};
      --navHover: {palette["nav_hover"]};
      --navActive: {palette["nav_active"]};
      --navText: {palette["nav_text"]};
      --navActiveText: {palette["nav_active_text"]};
      --primaryBg: {palette["primary_bg"]};
      --primaryText: {palette["primary_text"]};
      --badgeBg: {palette["badge_bg"]};
      --badgeLiveBg: {palette["badge_live_bg"]};
      --badgeLiveText: {palette["badge_live_text"]};
      --shadow: {palette["shadow"]};
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", "Trebuchet MS", sans-serif;
      color: var(--text);
      background: {palette["page_background"]};
      padding: 14px;
    }}
    body.lowPower {{
      background: linear-gradient(180deg, #0a1220 0%, #050b14 100%);
    }}
    body.lowPower * {{
      animation: none !important;
      transition: none !important;
    }}
    .shell {{
      width: min(1380px, 100%);
      margin: 0 auto;
      display: grid;
      grid-template-columns: 230px minmax(0, 1fr);
      gap: 12px;
    }}
    .sidebar, .content {{
      border: 1px solid var(--line);
      border-radius: 28px;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0)),
        var(--panel);
      box-shadow: var(--shadow);
    }}
    .sidebar {{
      padding: 18px 14px;
      position: sticky;
      top: 22px;
      height: fit-content;
    }}
    .brand {{
      font-size: 28px;
      font-weight: 800;
      letter-spacing: -1px;
      margin-bottom: 8px;
    }}
    .sub {{
      color: var(--muted);
      line-height: 1.5;
      font-size: 12px;
      margin-bottom: 16px;
    }}
    .navGroup {{
      display: grid;
      gap: 8px;
    }}
    .navSection {{
      display: grid;
      gap: 8px;
      margin-bottom: 14px;
    }}
    .navSectionTitle {{
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 1.4px;
      font-size: 11px;
      padding: 2px 4px;
    }}
    .navItem {{
      text-decoration: none;
      color: var(--navText);
      background: var(--navBg);
      border: 1px solid transparent;
      border-radius: 14px;
      padding: 10px 12px;
      font-weight: 600;
      transition: .16s ease;
    }}
    .navItem:hover {{ border-color: var(--line); background: var(--navHover); }}
    .navItem.active {{
      border-color: var(--line);
      background: var(--navActive);
      color: var(--navActiveText);
    }}
    .content {{
      padding: 16px;
      min-height: 620px;
    }}
    .hero {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      padding: 2px 2px 12px;
      border-bottom: 1px solid var(--line);
      margin-bottom: 12px;
    }}
    .eyebrow {{
      text-transform: uppercase;
      letter-spacing: 1.5px;
      font-size: 12px;
      color: var(--cyan);
      margin-bottom: 8px;
    }}
    .heroTitle {{
      font-size: 21px;
      line-height: 1.05;
      font-weight: 800;
      margin: 0;
    }}
    .heroText {{
      margin-top: 8px;
      max-width: 780px;
      color: var(--muted);
      line-height: 1.45;
      font-size: 13px;
    }}
    .heroActions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 14px;
    }}
    .primaryBtn, .ghostBtn, .miniBtn {{
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 14px;
      padding: 8px 12px;
      font-weight: 700;
      border: 1px solid var(--line);
      color: var(--text);
      background: var(--navBg);
    }}
    .primaryBtn {{
      border: none;
      color: var(--primaryText);
      background: var(--primaryBg);
    }}
    .ghostBtn:hover, .miniBtn:hover {{ background: var(--navHover); }}
    .miniBtn.subtle {{ color: var(--muted); }}
    .miniBtn.danger {{ color: var(--danger); border-color: rgba(255,142,165,0.24); }}
    .contentHeader {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }}
    .contentTitle {{
      font-size: 22px;
      font-weight: 800;
      margin-bottom: 8px;
    }}
    .contentHint {{
      color: var(--muted);
      line-height: 1.5;
      font-size: 13px;
      max-width: 760px;
    }}
    .quickActionRow, .statsStrip, .settingsGrid, .cardStack {{
      display: grid;
      gap: 10px;
    }}
    .quickAction {{
      text-decoration: none;
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px 14px;
      background: var(--panel2);
      font-weight: 600;
    }}
    .statsStrip {{
      grid-template-columns: repeat(3, minmax(0, 1fr));
      margin-bottom: 14px;
    }}
    .statTile {{
      padding: 14px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: var(--panel2);
      display: grid;
      gap: 8px;
    }}
    .statTile span {{
      font-size: 20px;
      font-weight: 800;
      word-break: break-word;
    }}
    .statTile.path span {{ font-size: 12px; line-height: 1.4; font-weight: 600; }}
    .statTile label {{
      color: var(--muted);
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 1px;
    }}
    .settingsGrid {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .settingCard, .profileCard {{
      border: 1px solid var(--line);
      border-radius: 16px;
      background: var(--panel2);
      padding: 14px;
    }}
    .settingCard.solo {{ margin-top: 8px; }}
    .settingLabel {{
      color: var(--cyan);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 1.3px;
      margin-bottom: 10px;
    }}
    .settingValue {{
      font-size: 18px;
      font-weight: 800;
      margin-bottom: 8px;
    }}
    .settingMeta {{
      color: var(--muted);
      line-height: 1.45;
      margin-bottom: 12px;
      font-size: 13px;
    }}
    .settingActions, .profileActions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .profileHero {{
      display: grid;
      grid-template-columns: 88px minmax(0, 1fr);
      gap: 14px;
      align-items: center;
      margin-bottom: 12px;
    }}
    .profileHeroAvatar {{
      width: 88px;
      height: 88px;
      border-radius: 22px;
      overflow: hidden;
      border: 1px solid var(--line);
      background: linear-gradient(135deg, var(--navActive), rgba(255,255,255,0.03));
      display: grid;
      place-items: center;
    }}
    .avatarImage {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }}
    .avatarFallback {{
      font-size: 28px;
      font-weight: 800;
      color: var(--text);
    }}
    .badgeRow {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 8px;
    }}
    .profileCard {{
      display: grid;
      grid-template-columns: 56px minmax(0, 1fr);
      gap: 12px;
      align-items: start;
    }}
    .profileAvatar {{
      width: 56px;
      height: 56px;
      border-radius: 16px;
      display: grid;
      place-items: center;
      font-weight: 800;
      background: linear-gradient(135deg, var(--navActive), rgba(255,255,255,0.02));
      border: 1px solid var(--line);
    }}
    .profileName {{
      font-size: 17px;
      font-weight: 800;
      margin-bottom: 6px;
    }}
    .profileMeta {{
      color: var(--muted);
      margin-bottom: 10px;
      line-height: 1.45;
      font-size: 13px;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      padding: 7px 11px;
      border-radius: 999px;
      background: var(--badgeBg);
      border: 1px solid var(--line);
      font-size: 12px;
      font-weight: 700;
    }}
    .badge.live {{
      background: var(--badgeLiveBg);
      color: var(--badgeLiveText);
      border-color: var(--line);
    }}
    @media (max-width: 1080px) {{
      .shell {{ grid-template-columns: 1fr; }}
      .sidebar {{ position: static; }}
      .settingsGrid, .statsStrip {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body class="{body_class}">
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">Aurora</div>
      <div class="sub">Profile-aware controls for appearance, privacy, downloads, media, and support.</div>
      <div class="navGroup">{sidebar}</div>
    </aside>
    <main class="content">
      <div class="hero">
        <div>
          <div class="eyebrow">Aurora Settings</div>
          <h1 class="heroTitle">Settings</h1>
          <div class="heroText">Use the sidebar for sections. Use the action buttons for managers and dialogs when you need them.</div>
          <div class="heroActions">
            <a class="ghostBtn" href="https://aurora.home/">Open Home</a>
            <a class="ghostBtn" href="https://aurora.settings/action/manage-profiles">Profile Manager</a>
            <a class="ghostBtn" href="https://aurora.settings/action/open-downloads">Downloads</a>
            <a class="ghostBtn" href="https://aurora.settings/action/open-bookmarks">Bookmarks</a>
          </div>
        </div>
      </div>
      {section_content}
    </main>
  </div>
</body>
</html>
        """

    def handle_internal_action(self, action_url):
        log_debug(f"Handle action: {action_url}")
        refresh_settings = False
        parsed = urlparse(action_url)
        path_parts = [part for part in parsed.path.strip("/").split("/") if part]
        if parsed.scheme == "aurora" and parsed.netloc == "action":
            action_name = path_parts[0] if path_parts else ""
        elif parsed.scheme == "aurora":
            action_name = parsed.netloc or (path_parts[0] if path_parts else "")
        else:
            action_name = parsed.netloc or (path_parts[0] if path_parts else "")
        query = parse_qs(parsed.query)
        target = (query.get("target", [""])[0] or "").strip()
        if action_name == "toggle-theme":
            self.toggle_theme()
            refresh_settings = True
        elif action_name == "google-auth-back":
            self.go_back_from_google_auth_fallback()
        elif action_name == "toggle-bookmark-bar":
            self.bookmark_bar_visible = not self.bookmark_bar_visible
            self.apply_bookmark_bar_visibility()
            self.save_browser_settings()
            self.refresh_internal_pages()
            refresh_settings = True
        elif action_name == "toggle-reading-mode":
            self.toggle_reading_mode()
            refresh_settings = True
        elif action_name == "open-reading-mode-help":
            self.open_reading_mode_help()
        elif action_name == "open-appearance":
            self.customize_ui()
            refresh_settings = True
        elif action_name == "open-privacy":
            self.open_security_options()
        elif action_name == "open-media":
            self.open_media_tools_dialog()
        elif action_name == "open-about":
            self.show_about_dialog()
        elif action_name == "change-profile-picture":
            self.change_current_profile_picture()
            refresh_settings = True
        elif action_name == "toggle-weather":
            self.toggle_weather(not self.weather_enabled)
            refresh_settings = True
        elif action_name == "set-weather-city":
            self.set_weather_city()
            refresh_settings = True
        elif action_name == "refresh-weather":
            self.refresh_weather()
        elif action_name == "toggle-time-capsule":
            self.toggle_time_capsule(not self.time_capsule_enabled)
            refresh_settings = True
        elif action_name == "open-time-capsule":
            self.show_time_capsule_dialog()
        elif action_name == "toggle-soundscape":
            self.toggle_soundscape(not self.soundscape_enabled)
            refresh_settings = True
        elif action_name == "select-music-folder":
            self.select_music_folder()
            refresh_settings = True
        elif action_name == "set-homepage":
            self.set_homepage()
            refresh_settings = True
        elif action_name == "toggle-low-power-mode":
            self.toggle_low_power_mode()
            refresh_settings = True
        elif action_name == "toggle-session-restore":
            self.toggle_session_restore()
            refresh_settings = True
        elif action_name == "open-security":
            self.open_security_options()
        elif action_name == "customize-ui":
            self.customize_ui()
            refresh_settings = True
        elif action_name == "open-downloads":
            self.show_download_manager()
        elif action_name == "open-downloads-folder":
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.download_manager.default_download_dir))
        elif action_name == "open-external-url":
            if target:
                QDesktopServices.openUrl(QUrl(target))
        elif action_name == "open-native-media":
            self.open_native_media_player_for_browser(self.get_media_target_browser(), target)
        elif action_name == "trust-site-popups":
            self.add_current_site_to_security_allowlist()
        elif action_name == "open-bookmarks":
            self.show_bookmark_manager()
        elif action_name == "open-extensions":
            self.open_extension_manager()
        elif action_name == "open-home":
            self.open_target_in_browser(self.current_browser(), "aurora://home")
        elif action_name == "manage-profiles":
            self.switch_profile()
        elif action_name == "switch-profile" and target:
            profile_file = os.path.join(os.path.expanduser("~"), "profile.txt")
            with open(profile_file, "w", encoding="utf-8") as f:
                f.write(target)
            if not restart_application([f"--profile={target}", "--skip-profile-picker"]):
                QMessageBox.warning(self, "Profile Change Error", "Could not restart Aurora automatically.")
        elif action_name == "delete-profile" and target:
            if self.styled_question("Delete Profile", f"Delete profile '{target}' and its saved data?\n\nThis removes bookmarks, session, homepage, quick access, extensions, and browser storage for that profile.") == QMessageBox.Yes:
                success, error_message = delete_profile_data(target)
                if not success:
                    QMessageBox.warning(self, "Delete Profile", error_message)
        elif action_name == "rename-profile" and target:
            new_name, ok = show_styled_text_input(self, "Rename Profile", "New profile name:", target)
            if ok and new_name.strip():
                success, error_message = rename_profile_data(target, new_name.strip())
                if not success:
                    QMessageBox.warning(self, "Rename Profile", error_message)
        elif action_name == "duplicate-profile" and target:
            new_name, ok = show_styled_text_input(self, "Duplicate Profile", "New profile name:")
            if ok and new_name.strip():
                success, error_message = duplicate_profile_data(target, new_name.strip())
                if not success:
                    QMessageBox.warning(self, "Duplicate Profile", error_message)
        return refresh_settings

    def render_settings_page(self, browser, section):
        if not browser:
            return
        log_debug(f"Render settings section: {section}")
        self.current_settings_section = section
        html = self.get_cached_settings_html(section)
        browser.setProperty("aurora_internal_page", f"settings:{section}")
        settings_path = os.path.join(self.profile_base_dir, "settings_page.html")
        loaded_from_file = False
        try:
            with open(settings_path, "w", encoding="utf-8") as f:
                f.write(html)
            if os.path.exists(settings_path):
                browser.setUrl(QUrl.fromLocalFile(settings_path))
                loaded_from_file = True
        except Exception:
            loaded_from_file = False
        if not loaded_from_file:
            try:
                browser.page().stop()
            except Exception:
                pass
            browser.setHtml(html, QUrl("about:blank"))
        self.apply_bookmark_bar_visibility(f"https://aurora.settings/{section}")
        if browser == self.current_browser():
            self.url_bar.setText(f"aurora://settings/{section}")

    def styled_question(self, title, text):
        return show_styled_question(self, title, text)

    def open_profile_manager_from_settings(self):
        dialog = ProfilePickerDialog(discover_profiles(), self.profile, self, active_profile=self.profile)
        if dialog.exec_() != QDialog.Accepted:
            return
        selected_profile = dialog.selected_profile()
        if not selected_profile or selected_profile == self.profile:
            return
        profile_file = os.path.join(os.path.expanduser("~"), "profile.txt")
        try:
            with open(profile_file, "w") as f:
                f.write(selected_profile)
            reply = QMessageBox.question(
                self,
                "Restart Required",
                f"Profile set to: {selected_profile}. Restart Aurora now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                if not restart_application():
                    QMessageBox.warning(self, "Profile Change Error", "Could not restart Aurora automatically.")
        except Exception as e:
            QMessageBox.warning(self, "Profile Change Error", f"Could not switch profile: {e}")

    def handle_aurora_navigation(self, target_url):
        log_debug(f"Handle aurora navigation: {target_url}")
        lowered = (target_url or "").strip().lower()
        parsed = urlparse(target_url)
        host = (parsed.hostname or "").lower()
        if lowered.startswith("aurora://action") or (host == "aurora.settings" and parsed.path.startswith("/action/")):
            action_target = target_url
            if host == "aurora.settings" and parsed.path.startswith("/action/"):
                action_target = f"aurora://{parsed.path.lstrip('/')}"
                if parsed.query:
                    action_target += f"?{parsed.query}"
            should_refresh = self.handle_internal_action(action_target)
            if should_refresh:
                QTimer.singleShot(0, self.refresh_settings_page)
            return
        if lowered.startswith("aurora://settings") or lowered.startswith("aurora:settings") or host == "aurora.settings":
            if not self.use_internal_settings_page:
                self.open_legacy_settings_dialog()
                self.open_target_in_browser(self.current_browser(), "aurora://home")
                return
            self.open_target_in_browser(self.current_browser(), target_url)
            return
        if lowered.startswith("aurora://home") or lowered.startswith("aurora:home") or host == "aurora.home":
            self.open_target_in_browser(self.current_browser(), "aurora://home")
            return

    def open_target_in_browser(self, browser, target):
        target_str = target.toString() if isinstance(target, QUrl) else str(target).strip()
        if not target_str:
            target_str = self.homepage
        lowered = target_str.lower()
        if lowered in ("aurora://home", "about:aurora", "about:newtab", "aurora:home") or "aurora.home" in lowered:
            browser.setHtml(self.get_cached_homepage_html(), QUrl("https://aurora.home/"))
            self.apply_bookmark_bar_visibility("https://aurora.home/")
            return
        if lowered.startswith("aurora://settings") or lowered.startswith("aurora:settings") or "aurora.settings" in lowered:
            if not self.use_internal_settings_page:
                self.open_legacy_settings_dialog()
                self.open_target_in_browser(browser, "aurora://home")
                return
            section = self.parse_internal_settings_section(target_str)
            self.render_settings_page(browser, section)
            return
        self.apply_bookmark_bar_visibility(target_str)
        browser.setUrl(QUrl(target_str))

    def refresh_settings_page(self):
        if not self.use_internal_settings_page:
            return
        browser = self.current_browser()
        if not browser:
            return
        url_text = browser.url().toString().lower()
        internal_marker = str(browser.property("aurora_internal_page") or "")
        if ("aurora.settings" not in url_text and not url_text.startswith("aurora://settings") and not url_text.startswith("aurora:settings")
                and not internal_marker.startswith("settings:")):
            return
        section = getattr(self, "current_settings_section", "profiles")
        self.render_settings_page(browser, section)

    def refresh_internal_pages(self):
        for index in range(self.tab_widget.count()):
            browser = self.tab_widget.widget(index)
            if browser is None:
                continue
            url_text = browser.url().toString().strip()
            lowered = url_text.lower()
            if lowered in ("aurora://home", "about:aurora", "about:newtab", "aurora:home") or "aurora.home" in lowered:
                browser.setHtml(self.get_cached_homepage_html(), QUrl("https://aurora.home/"))
                continue
            if self.use_internal_settings_page and ("aurora.settings" in lowered or lowered.startswith("aurora://settings") or lowered.startswith("aurora:settings")):
                section = self.parse_internal_settings_section(url_text)
                self.render_settings_page(browser, section)
        current = self.current_browser()
        self.apply_bookmark_bar_visibility(current.url().toString() if current else "")

    def register_observed_media_request(self, payload):
        if not isinstance(payload, dict):
            return
        url = (payload.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return
        record = {
            "url": url,
            "first_party_url": (payload.get("first_party_url") or "").strip(),
            "host": (payload.get("host") or "").strip().lower(),
            "first_party_host": (payload.get("first_party_host") or "").strip().lower(),
            "referer": (payload.get("referer") or "").strip(),
            "origin": (payload.get("origin") or "").strip(),
            "captured_at": float(payload.get("captured_at") or time.time()),
        }
        self.media_request_history = [
            existing for existing in self.media_request_history
            if existing.get("url") != record["url"]
        ]
        self.media_request_history.append(record)
        self.media_request_history = self.media_request_history[-120:]
        log_debug(
            "Observed media request: "
            f"url={record['url']} first_party={record['first_party_url']} referer={record['referer']}"
        )
        self.maybe_auto_open_native_media(record)

    def handle_media_console_payload(self, payload_text, browser=None):
        try:
            data = json.loads(payload_text)
        except Exception as exc:
            log_debug(f"Failed to parse media console payload: {exc} | payload={payload_text}")
            return
        url = str(data.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return
        page_url = ""
        if browser is not None:
            try:
                page_url = browser.url().toString()
            except Exception:
                page_url = ""
        page_url = str(data.get("page_url") or page_url or "").strip()
        page_host = (urlparse(page_url).hostname or "").lower()
        record = {
            "url": url,
            "first_party_url": page_url,
            "host": (urlparse(url).hostname or "").lower(),
            "first_party_host": page_host,
            "referer": page_url,
            "origin": str(data.get("origin") or (f"{urlparse(page_url).scheme}://{urlparse(page_url).hostname}" if urlparse(page_url).hostname else "")).strip(),
            "captured_at": time.time(),
        }
        log_debug(f"Observed media candidate from page JS: url={url} page={page_url}")
        self.register_observed_media_request(record)

    def probe_media_candidates_on_page(self, browser=None, callback=None):
        browser = browser or self.current_browser()
        if browser is None:
            if callback:
                callback([])
            return
        script = """
        (() => {
            const seen = new Set();
            const found = [];
            const add = (raw, kind) => {
                try {
                    const value = String(raw || '').trim();
                    if (!value || value.startsWith('data:') || value.startsWith('blob:')) {
                        return;
                    }
                    const absolute = new URL(value, location.href).toString();
                    if (seen.has(absolute)) {
                        return;
                    }
                    seen.add(absolute);
                    found.push({ url: absolute, kind: kind || 'unknown' });
                } catch (e) {}
            };
            try {
                document.querySelectorAll('video, audio, source').forEach((element) => {
                    add(element.currentSrc || '', 'media-current');
                    add(element.src || '', 'media-src');
                    add(element.getAttribute('src') || '', 'media-attr');
                    add(element.getAttribute('data-src') || '', 'media-data-src');
                });
            } catch (e) {}
            try {
                document.querySelectorAll('iframe').forEach((element) => {
                    add(element.src || '', 'iframe-src');
                    add(element.getAttribute('src') || '', 'iframe-attr');
                    add(element.getAttribute('data-src') || '', 'iframe-data-src');
                });
            } catch (e) {}
            try {
                if (typeof window.jwplayer === 'function') {
                    const inspectPlayer = (player) => {
                        if (!player) {
                            return;
                        }
                        try {
                            const playlist = player.getPlaylist ? player.getPlaylist() : [];
                            (playlist || []).forEach((item) => {
                                add(item.file || '', 'jw-file');
                                (item.sources || []).forEach((source) => add(source.file || source.src || '', 'jw-source'));
                            });
                        } catch (e) {}
                        try {
                            const item = player.getPlaylistItem ? player.getPlaylistItem() : null;
                            if (item) {
                                add(item.file || '', 'jw-current-file');
                                (item.sources || []).forEach((source) => add(source.file || source.src || '', 'jw-current-source'));
                            }
                        } catch (e) {}
                        try {
                            const config = player.getConfig ? player.getConfig() : null;
                            if (config) {
                                add(config.file || '', 'jw-config-file');
                                (config.sources || []).forEach((source) => add(source.file || source.src || '', 'jw-config-source'));
                                (config.playlist || []).forEach((item) => {
                                    add(item.file || '', 'jw-config-playlist-file');
                                    (item.sources || []).forEach((source) => add(source.file || source.src || '', 'jw-config-playlist-source'));
                                });
                            }
                        } catch (e) {}
                    };
                    for (let index = 0; index < 8; index += 1) {
                        try {
                            inspectPlayer(window.jwplayer(index));
                        } catch (e) {}
                    }
                    try {
                        inspectPlayer(window.jwplayer());
                    } catch (e) {}
                }
            } catch (e) {}
            return {
                page_url: location.href,
                origin: location.origin || '',
                host: location.hostname || '',
                candidates: found
            };
        })();
        """
        def handle_result(result, b=browser, done=callback):
            page_url = ""
            try:
                page_url = b.url().toString()
            except Exception:
                page_url = ""
            records = []
            if isinstance(result, dict):
                page_url = str(result.get("page_url") or page_url or "")
                origin = str(result.get("origin") or "").strip()
                page_host = (urlparse(page_url).hostname or "").lower()
                for item in result.get("candidates", []) or []:
                    url = str((item or {}).get("url") or "").strip()
                    if not url.startswith(("http://", "https://")):
                        continue
                    record = {
                        "url": url,
                        "first_party_url": page_url,
                        "host": (urlparse(url).hostname or "").lower(),
                        "first_party_host": page_host,
                        "referer": page_url,
                        "origin": origin or (f"{urlparse(page_url).scheme}://{urlparse(page_url).hostname}" if urlparse(page_url).hostname else ""),
                        "captured_at": time.time(),
                    }
                    records.append(record)
                    self.register_observed_media_request(record)
            if records:
                log_debug(f"Direct page probe found {len(records)} media candidate(s) for {page_url}")
            else:
                log_debug(f"Direct page probe found no media candidates for {page_url}")
            if done:
                done(records)
        try:
            browser.page().runJavaScript(script, handle_result)
        except Exception as exc:
            log_debug(f"Direct media probe failed: {exc}")
            if callback:
                callback([])

    def find_browser_for_media_record(self, record):
        page_url = (record.get("first_party_url") or "").strip()
        page_host = (record.get("first_party_host") or "").strip().lower()
        for browser in iter_window_audio_browsers(self):
            try:
                browser_url = browser.url().toString()
                browser_host = (urlparse(browser_url).hostname or "").lower()
            except Exception:
                browser_url = ""
                browser_host = ""
            if page_url and browser_url == page_url:
                return browser
            if page_host and browser_host == page_host:
                return browser
        current = self.current_browser()
        return current

    def maybe_auto_open_native_media(self, record):
        url_value = (record.get("url") or "").lower()
        first_party_host = (record.get("first_party_host") or "").lower()
        if not host_matches_fragment(first_party_host, MEDIA_COMPATIBILITY_HOST_FRAGMENTS):
            return
        if ".m3u8" not in url_value and ".mp4" not in url_value:
            return
        token = f"{first_party_host}|{record.get('url', '')}"
        if token in self.native_media_autoplay_tokens:
            return
        self.native_media_autoplay_tokens.add(token)
        browser = self.find_browser_for_media_record(record)
        log_debug(f"Scheduling native media fallback for {record.get('url', '')}")
        QTimer.singleShot(
            1200,
            lambda b=browser, target=record.get("first_party_url", ""): self.open_native_media_player_for_browser(
                b if b is not None else self.current_browser(),
                target,
                auto_triggered=True,
            ),
        )

    def find_media_candidate_for_url(self, page_url):
        target_host = (urlparse(page_url or "").hostname or "").lower()
        if not target_host:
            return None
        candidates = []
        for entry in self.media_request_history:
            url_value = (entry.get("url") or "").lower()
            score = 0
            if entry.get("first_party_host") == target_host:
                score += 100
            elif entry.get("host") == target_host:
                score += 80
            elif target_host in (entry.get("first_party_host") or ""):
                score += 60
            elif host_matches_fragment(target_host, MEDIA_COMPATIBILITY_HOST_FRAGMENTS):
                score += 40
            if ".m3u8" in url_value:
                score += 30
            elif ".mp4" in url_value:
                score += 20
            elif ".mpd" in url_value:
                score += 10
            score += min(int(entry.get("captured_at", 0) or 0), int(time.time())) / 1000000000.0
            if score > 0:
                candidates.append((score, entry))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def open_native_media_player_for_browser(self, browser=None, target_hint="", auto_triggered=False):
        browser = browser or self.get_media_target_browser() or self.current_browser()
        current_url = ""
        if browser is not None:
            try:
                current_url = browser.url().toString()
            except Exception:
                current_url = ""
        if browser is None or self.is_internal_browser_url(current_url):
            message = (
                "Aurora couldn't find the last real video tab from Settings. "
                "Switch to the episode tab once, then try Native Player again."
            )
            if auto_triggered or target_hint:
                self.show_runtime_status(message, 5000)
            else:
                QMessageBox.information(self, "Aurora Native Player", message)
            return
        hint = (target_hint or "").strip()
        direct_media_hint = any(marker in hint.lower() for marker in (".m3u8", ".mp4", ".mpd", ".webm"))
        iframe_host_hint = any(marker in hint.lower() for marker in ("megacloud", "megaf", "streamsb", "streamtape", "filemoon", "videodelivery.net"))
        candidate = None
        if direct_media_hint and hint.startswith(("http://", "https://")):
            candidate = {
                "url": hint,
                "referer": current_url,
                "origin": f"{urlparse(current_url).scheme}://{urlparse(current_url).hostname}" if urlparse(current_url).hostname else "",
            }
        elif iframe_host_hint and hint.startswith(("http://", "https://")):
            candidate = {
                "url": hint,
                "referer": current_url,
                "origin": f"{urlparse(current_url).scheme}://{urlparse(current_url).hostname}" if urlparse(current_url).hostname else "",
            }
        else:
            candidate = self.find_media_candidate_for_url(hint or current_url)
        if not candidate:
            if browser is not None:
                self.probe_media_candidates_on_page(
                    browser,
                    callback=lambda records, b=browser, h=target_hint, auto=auto_triggered: self._retry_native_player_after_probe(b, h, auto, records),
                )
                return
            message = (
                "Aurora has not seen a playable stream URL for this page yet. "
                "Reload the episode, let the embedded player fail once, then try Native Player again."
            )
            if auto_triggered or target_hint:
                self.show_runtime_status(message, 5000)
            else:
                QMessageBox.information(self, "Aurora Native Player", message)
            return
        player_title = f"Aurora Native Player - {(urlparse(current_url).hostname or 'Video').strip()}"
        log_debug(f"Opening native media player for {candidate.get('url', '')}")
        dialog = NativeMediaPlayerDialog(
            candidate.get("url", ""),
            title_text=player_title,
            referer=candidate.get("referer", current_url),
            origin=candidate.get("origin", ""),
            parent=self,
        )
        self.native_media_windows.append(dialog)
        dialog.finished.connect(lambda _result, d=dialog: self.native_media_windows.remove(d) if d in self.native_media_windows else None)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _retry_native_player_after_probe(self, browser, target_hint, auto_triggered, records):
        if records:
            self.open_native_media_player_for_browser(browser, target_hint, auto_triggered=auto_triggered)
            return
        message = (
            "Aurora still could not extract a playable stream from this page. "
            "This player may be using a protected blob-only source or a deeply sandboxed iframe."
        )
        if auto_triggered or target_hint:
            self.show_runtime_status(message, 5000)
        else:
            QMessageBox.information(self, "Aurora Native Player", message)

    def open_media_tools_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Media Tools")
        style_aux_window(dialog)
        dialog.setMinimumWidth(620)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        header = QLabel("Media & Ambient Tools")
        header.setStyleSheet("font-size: 22px; font-weight: 800; color: #f5f9ff;")
        sub = QLabel("Manage weather, soundscape, and time capsule with grouped controls instead of one long mixed list.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #9db5d8;")
        layout.addWidget(header)
        layout.addWidget(sub)

        sections = [
            (
                "Video Playback",
                "Use Aurora Native Player when embedded site players fail with source errors like 102630.",
                [("Open Native Player For Current Tab", lambda: self.open_native_media_player_for_browser(self.current_browser()))],
            ),
            (
                "Weather",
                f"Status: {'On' if self.weather_enabled else 'Off'} for {self.weather_city}",
                [("Toggle Weather", lambda: self.toggle_weather(not self.weather_enabled)), ("Set Weather City", self.set_weather_city)],
            ),
            (
                "Soundscape",
                f"Status: {'On' if self.soundscape_enabled else 'Off'}",
                [("Toggle Soundscape", lambda: self.toggle_soundscape(not self.soundscape_enabled)), ("Choose Music Folder", self.select_music_folder)],
            ),
            (
                "Time Capsule",
                f"Status: {'On' if self.time_capsule_enabled else 'Off'} | Capsules: {len(self.time_capsule_data)}",
                [("Toggle Time Capsule", lambda: self.toggle_time_capsule(not self.time_capsule_enabled)), ("Manage Time Capsules", self.show_time_capsule_dialog)],
            ),
        ]
        for section_title, section_meta, actions in sections:
            card = QFrame()
            card.setStyleSheet("QFrame { background: #0d1a30; border: 1px solid #24395f; border-radius: 16px; }")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 14, 16, 14)
            card_layout.setSpacing(10)
            title_label = QLabel(section_title)
            title_label.setStyleSheet("font-size: 16px; font-weight: 800; color: #edf4ff;")
            meta_label = QLabel(section_meta)
            meta_label.setStyleSheet("font-size: 12px; color: #9db5d8;")
            action_row = QHBoxLayout()
            action_row.setSpacing(10)
            for label, handler in actions:
                button = QPushButton(label)
                button.clicked.connect(handler)
                action_row.addWidget(button)
            action_row.addStretch()
            card_layout.addWidget(title_label)
            card_layout.addWidget(meta_label)
            card_layout.addLayout(action_row)
            layout.addWidget(card)

        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec_()

    def change_current_profile_picture(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Choose Profile Picture", "", "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not file_path:
            return
        success, result = set_profile_avatar(self.profile, file_path)
        if not success:
            QMessageBox.warning(self, "Profile Picture", f"Could not save picture: {result}")
            return
        self.show_runtime_status("Profile picture updated", 2500)
        browser = self.current_browser()
        if not browser:
            return
        current_url = browser.url().toString()
        if "aurora.settings" in current_url:
            self.refresh_settings_page()
        elif "aurora.home" in current_url:
            self.open_target_in_browser(browser, "aurora://home")

    def is_reading_mode_supported_url(self, url_value):
        host = (urlparse(url_value or "").hostname or "").lower()
        protected_hosts = {
            "google.com", "www.google.com", "bing.com", "www.bing.com",
            "duckduckgo.com", "www.duckduckgo.com", "search.yahoo.com",
            "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
            "music.youtube.com",
        }
        if not host:
            return False
        return not any(host == blocked or host.endswith("." + blocked) for blocked in protected_hosts)

    def current_page_reading_mode_enabled(self, browser=None):
        browser = browser or self.current_browser()
        if browser is None:
            return False
        try:
            return bool(browser.property("aurora_reading_mode_active"))
        except Exception:
            return False

    def is_internal_browser_url(self, url_value):
        lowered = (url_value or "").strip().lower()
        return (
            not lowered
            or lowered.startswith("aurora://")
            or "aurora.home" in lowered
            or "aurora.settings" in lowered
            or lowered == "about:blank"
        )

    def remember_last_content_browser(self, browser=None, url_value=""):
        candidate = browser or self.current_browser()
        if candidate is None:
            return
        try:
            current_url = url_value or candidate.url().toString()
        except Exception:
            return
        if self.is_internal_browser_url(current_url):
            return
        try:
            if self.tab_widget.indexOf(candidate) == -1:
                return
        except Exception:
            return
        self.last_content_browser = candidate

    def get_reading_mode_target_browser(self):
        browser = self.current_browser()
        if browser is not None:
            current_url = browser.url().toString()
            if not self.is_internal_browser_url(current_url):
                return browser
        candidate = getattr(self, "last_content_browser", None)
        if candidate is not None:
            try:
                if self.tab_widget.indexOf(candidate) != -1:
                    candidate_url = candidate.url().toString()
                    if not self.is_internal_browser_url(candidate_url):
                        return candidate
            except Exception:
                pass
        try:
            current_index = self.tab_widget.currentIndex()
        except Exception:
            current_index = -1
        preferred_indexes = []
        if current_index > 0:
            preferred_indexes.extend(range(current_index - 1, -1, -1))
        if current_index >= 0:
            preferred_indexes.extend(range(current_index + 1, self.tab_widget.count()))
        else:
            preferred_indexes.extend(range(self.tab_widget.count()))
        seen = set()
        for index in preferred_indexes:
            if index in seen:
                continue
            seen.add(index)
            candidate = self.tab_widget.widget(index)
            if candidate is None:
                continue
            try:
                url_text = candidate.url().toString()
            except Exception:
                continue
            if self.is_internal_browser_url(url_text):
                continue
            return candidate
        return None

    def apply_reading_mode_to_browser(self, browser=None, callback=None):
        browser = browser or self.current_browser()
        if browser is None:
            if callback:
                callback(False)
            return
        current_url = browser.url().toString()
        lowered = current_url.lower()
        if not self.current_page_reading_mode_enabled(browser):
            if callback:
                callback(False)
            return
        if lowered.startswith("aurora://") or "aurora.home" in lowered or "aurora.settings" in lowered:
            if callback:
                callback(False)
            return
        if not self.is_reading_mode_supported_url(current_url):
            if callback:
                callback(False)
            return
        script = """
        (() => {
            try {
                if (document.documentElement.dataset.auroraReadingApplied === '1') {
                    return { applied: true, reason: 'already-applied' };
                }
                const textLength = (node) => ((node && node.innerText) || '').trim().length;
                const selectorList = [
                    'article',
                    'main',
                    '[role="main"]',
                    '.mw-parser-output',
                    '#mw-content-text',
                    '#bodyContent',
                    '.article-content',
                    '.entry-content',
                    '.post-content',
                    '.post',
                    '.content',
                    '.markdown-body'
                ];
                const candidates = Array.from(document.querySelectorAll(selectorList.join(', ')));
                let primary = null;
                let bestScore = 0;
                for (const node of candidates) {
                    if (!node || !node.isConnected) {
                        continue;
                    }
                    const rect = node.getBoundingClientRect();
                    const score = textLength(node) + Math.max(0, rect.width * rect.height * 0.002);
                    if (score > bestScore) {
                        bestScore = score;
                        primary = node;
                    }
                }
                if (!primary) {
                    primary = Array.from(document.querySelectorAll('section, div, table, tbody'))
                        .filter(node => node && node.isConnected && textLength(node) > 700)
                        .sort((a, b) => textLength(b) - textLength(a))[0] || document.body;
                }
                if (!primary) {
                    return { applied: false, reason: 'no-primary' };
                }
                const cloneMarkup = (() => {
                    if (primary === document.body) {
                        const wrapper = document.createElement('div');
                        const children = Array.from(document.body.children).filter(node =>
                            node &&
                            node.id !== 'aurora-reading-shell' &&
                            node.tagName !== 'SCRIPT' &&
                            node.tagName !== 'STYLE' &&
                            node.tagName !== 'LINK' &&
                            node.tagName !== 'NOSCRIPT'
                        );
                        for (const child of children) {
                            wrapper.appendChild(child.cloneNode(true));
                        }
                        return wrapper.innerHTML;
                    }
                    return primary.innerHTML || primary.outerHTML || '';
                })();
                if (!cloneMarkup.trim()) {
                    return { applied: false, reason: 'empty-markup' };
                }
                document.documentElement.dataset.auroraReadingApplied = '1';
                document.documentElement.classList.add('aurora-reading-mode');
                let style = document.getElementById('aurora-reading-style');
                if (!style) {
                    style = document.createElement('style');
                    style.id = 'aurora-reading-style';
                    style.textContent = `
                        html.aurora-reading-mode, html.aurora-reading-mode body {
                            background: #f6f0e4 !important;
                            color: #241d16 !important;
                        }
                        html.aurora-reading-mode body * {
                            animation: none !important;
                            box-shadow: none !important;
                            text-shadow: none !important;
                        }
                        html.aurora-reading-mode body > *:not(#aurora-reading-shell):not(script):not(style):not(link) {
                            display: none !important;
                        }
                        #aurora-reading-shell {
                            min-height: 100vh;
                            padding: 48px 24px 80px;
                            background: #f6f0e4;
                        }
                        #aurora-reading-article {
                            max-width: 860px;
                            margin: 0 auto;
                            background: rgba(255,255,255,0.72);
                            border: 1px solid rgba(63, 54, 46, 0.10);
                            border-radius: 24px;
                            padding: 42px 44px;
                            line-height: 1.82 !important;
                            font-size: 20px !important;
                            color: #241d16 !important;
                        }
                        #aurora-reading-article img, #aurora-reading-article video, #aurora-reading-article iframe {
                            max-width: 100% !important;
                            height: auto !important;
                            border-radius: 16px;
                            margin: 18px auto;
                            display: block;
                        }
                        #aurora-reading-article p, #aurora-reading-article li, #aurora-reading-article blockquote {
                            font-size: 1em !important;
                            color: #2d241b !important;
                        }
                        #aurora-reading-article h1, #aurora-reading-article h2, #aurora-reading-article h3, #aurora-reading-article h4 {
                            color: #17110c !important;
                            line-height: 1.25 !important;
                            margin-top: 1.4em !important;
                        }
                        #aurora-reading-article a {
                            color: #8a4b19 !important;
                        }
                    `;
                    document.head.appendChild(style);
                }
                let shell = document.getElementById('aurora-reading-shell');
                if (!shell) {
                    shell = document.createElement('div');
                    shell.id = 'aurora-reading-shell';
                    document.body.appendChild(shell);
                }
                let article = document.getElementById('aurora-reading-article');
                if (!article) {
                    article = document.createElement('div');
                    article.id = 'aurora-reading-article';
                    shell.appendChild(article);
                }
                article.innerHTML = cloneMarkup;
                return { applied: true, reason: 'ok' };
            } catch (e) {
                console.error('Error in reading mode:', e);
                return { applied: false, reason: String(e) };
            }
        })();
        """
        try:
            browser.page().runJavaScript(
                script,
                lambda result, b=browser, url=current_url, done=callback: self._handle_reading_mode_apply_result(b, url, result, done)
            )
        except Exception as exc:
            log_debug(f"Reading mode apply failed for {current_url}: {exc}")
            browser.setProperty("aurora_reading_mode_active", False)
            if callback:
                callback(False)

    def _handle_reading_mode_apply_result(self, browser, current_url, result, callback=None):
        log_debug(f"Reading mode apply result for {current_url}: {result}")
        applied = bool(result) if isinstance(result, bool) else bool((result or {}).get("applied"))
        if not applied:
            browser.setProperty("aurora_reading_mode_active", False)
            self.show_runtime_status("Reading mode could not be applied to this page", 2600)
        if callback:
            callback(applied)

    def remove_reading_mode_from_browser(self, browser=None, callback=None):
        browser = browser or self.current_browser()
        if browser is None:
            if callback:
                callback(False)
            return
        browser.setProperty("aurora_reading_mode_active", False)
        script = """
        (() => {
            try {
                document.documentElement.classList.remove('aurora-reading-mode');
                delete document.documentElement.dataset.auroraReadingApplied;
                const shell = document.getElementById('aurora-reading-shell');
                if (shell) shell.remove();
                const style = document.getElementById('aurora-reading-style');
                if (style) style.remove();
                return true;
            } catch (e) {
                return false;
            }
        })();
        """
        try:
            browser.page().runJavaScript(script, lambda result, done=callback: done(bool(result)) if done else None)
        except Exception:
            if callback:
                callback(False)

    def open_legacy_settings_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        style_aux_window(dialog)
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        title = QLabel("Aurora Settings")
        title.setStyleSheet("font-size: 22px; font-weight: 800; color: #f5f9ff;")
        subtitle = QLabel("Stable dialog-based settings for Aurora.")
        subtitle.setStyleSheet("color: #9db5d8;")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        actions = [
            ("Manage Profiles", self.switch_profile),
            ("Change Profile Picture", self.change_current_profile_picture),
            ("Customize UI", self.customize_ui),
            ("Downloads", self.show_download_manager),
            ("Security Options", self.open_security_options),
            ("Media Tools", self.open_media_tools_dialog),
            ("Script Extensions (Beta)", self.open_extension_manager),
            ("About", self.show_about_dialog),
        ]
        for label, handler in actions:
            button = QPushButton(label)
            button.clicked.connect(handler)
            layout.addWidget(button)
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec_()

    def on_tab_load_finished(self, ok, tab):
        if ok:
            if self.stabilize_search_page(tab):
                return
            self.maybe_render_google_auth_fallback(tab)
            self.apply_extensions_to_tab(tab)
            if self.current_page_reading_mode_enabled(tab):
                QTimer.singleShot(120, lambda browser=tab: self.apply_reading_mode_to_browser(browser))
            try:
                host = (urlparse(tab.url().toString()).hostname or "").lower()
            except Exception:
                host = ""
            if host.endswith("google.com") and not is_google_auth_host(host):
                tab.page().runJavaScript(
                    "try{Object.defineProperty(navigator,'webdriver',{get:()=>undefined});}catch(e){}"
                )
            if host_matches_fragment(host, MEDIA_COMPATIBILITY_HOST_FRAGMENTS):
                QTimer.singleShot(1600, lambda browser=tab: self.probe_media_candidates_on_page(browser))
        self.update_bookmark_button_state(tab)

    def stabilize_search_page(self, tab):
        return False

    def is_google_auth_url(self, url_text):
        try:
            parsed = urlparse(url_text or "")
            host = (parsed.hostname or "").lower()
            path = (parsed.path or "").lower()
        except Exception:
            return False
        if host not in {"accounts.google.com", "signin.google.com"}:
            return False
        auth_markers = (
            "/servicelogin",
            "/signin",
            "/interactivelogin",
            "/embedded/setup",
            "/v3/signin",
        )
        return any(marker in path for marker in auth_markers)

    def maybe_render_google_auth_fallback(self, tab):
        if tab is None:
            return
        current_url = tab.url().toString()
        if not self.is_google_auth_url(current_url):
            return
        inspect_js = """
        (() => {
            try {
                const bodyText = (document.body && document.body.innerText) ? document.body.innerText : '';
                return {
                    title: document.title || '',
                    text: bodyText.slice(0, 8000)
                };
            } catch (e) {
                return { title: '', text: '' };
            }
        })();
        """
        def handle_probe(result, browser_tab=tab, page_url=current_url):
            if browser_tab is None:
                return
            page_text = ""
            page_title = ""
            if isinstance(result, dict):
                page_text = str(result.get("text", "") or "")
                page_title = str(result.get("title", "") or "")
            combined = f"{page_title}\n{page_text}".lower()
            failure_markers = (
                "couldn't sign you in",
                "this browser or app may not be secure",
                "try using a different browser",
            )
            if not any(marker in combined for marker in failure_markers):
                return
            browser_tab.setHtml(self.build_google_auth_fallback_html(page_url), QUrl("https://aurora.notice/google-auth"))
        try:
            tab.page().runJavaScript(inspect_js, handle_probe)
        except Exception:
            pass

    def build_google_auth_fallback_html(self, failed_url):
        safe_target = html.escape(failed_url or "https://accounts.google.com/")
        open_target = html.escape(quote_plus(failed_url or "https://accounts.google.com/"))
        return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Google Sign-In</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: 'Segoe UI', Arial, sans-serif;
      background: radial-gradient(700px circle at 10% 10%, rgba(86, 137, 255, 0.18), transparent 45%), linear-gradient(180deg, #07101d 0%, #03070e 100%);
      color: #edf4ff;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 28px;
    }}
    .shell {{
      width: min(780px, 100%);
      background: rgba(10, 18, 32, 0.96);
      border: 1px solid rgba(124, 217, 255, 0.18);
      border-radius: 26px;
      box-shadow: 0 28px 90px rgba(0, 0, 0, 0.45);
      padding: 34px;
    }}
    .eyebrow {{
      color: #7cd9ff;
      font-size: 12px;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      margin-bottom: 10px;
      font-weight: 700;
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: clamp(32px, 5vw, 48px);
      line-height: 1.02;
    }}
    p {{
      margin: 0;
      color: #a9bedf;
      font-size: 16px;
      line-height: 1.65;
    }}
    .callout {{
      margin-top: 22px;
      padding: 16px 18px;
      border-radius: 18px;
      background: rgba(124, 217, 255, 0.08);
      border: 1px solid rgba(124, 217, 255, 0.16);
      color: #d9ecff;
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 26px;
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 48px;
      padding: 0 18px;
      border-radius: 14px;
      text-decoration: none;
      font-weight: 700;
      border: 1px solid rgba(132, 172, 255, 0.25);
      color: #edf4ff;
      background: rgba(18, 34, 60, 0.96);
    }}
    .btn.primary {{
      color: #05101d;
      border: none;
      background: linear-gradient(135deg, #ffe26f 0%, #73efd0 100%);
    }}
    .meta {{
      margin-top: 18px;
      font-size: 13px;
      color: #87a3cc;
      word-break: break-all;
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="eyebrow">Google Sign-In</div>
    <h1>Open this login in your default browser.</h1>
    <p>Google often blocks account login inside embedded browsers like Aurora even when normal browsing works fine. Your browsing session in Aurora is still okay, but this login flow is safer and more reliable in your main browser.</p>
    <div class="callout">Aurora will open the exact Google sign-in link externally so you can finish the sign-in flow there without guessing or retyping the URL.</div>
    <div class="actions">
      <a class="btn" href="aurora://action/google-auth-back">Go Back</a>
      <a class="btn primary" href="aurora://action/open-external-url?target={open_target}">Open In Default Browser</a>
      <a class="btn" href="{safe_target}">Try Again Here</a>
    </div>
    <div class="meta">{safe_target}</div>
  </div>
</body>
</html>
        """

    def _recover_from_google_auth_history(self, browser, attempts_left=2):
        if browser is None:
            return
        current_url = browser.url().toString().lower()
        blocked_markers = ("aurora.notice/google-auth", "accounts.google.com")
        if not any(marker in current_url for marker in blocked_markers):
            return
        if attempts_left > 0:
            try:
                if browser.history().canGoBack():
                    browser.back()
                    QTimer.singleShot(420, lambda b=browser, remaining=attempts_left - 1: self._recover_from_google_auth_history(b, remaining))
                    return
            except Exception:
                pass
        self.open_target_in_browser(browser, "aurora://home")

    def go_back_from_google_auth_fallback(self, browser=None):
        browser = browser or self.current_browser()
        if browser is None:
            return
        try:
            if browser.history().canGoBack():
                browser.back()
                QTimer.singleShot(420, lambda b=browser: self._recover_from_google_auth_history(b, 1))
                return
        except Exception:
            pass
        self.open_target_in_browser(browser, "aurora://home")

    def apply_extensions_to_tab(self, tab):
        if not tab:
            return
        tab_host = ""
        try:
            tab_host = (urlparse(tab.url().toString()).hostname or "").lower()
        except Exception:
            tab_host = ""
        if tab_host and any(
            tab_host == blocked or tab_host.endswith("." + blocked)
            for blocked in self.extension_blocked_hosts
        ):
            return
        for ext in self.extensions:
            if not ext.get("enabled", True):
                continue
            path = ext.get("path", "")
            if not path or not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    extension_code = f.read()
                tab.page().runJavaScript(extension_code)
            except Exception as e:
                print(f"Extension apply error ({path}): {e}")

    def apply_extensions_to_all_tabs(self):
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, BrowserTab):
                self.apply_extensions_to_tab(tab)

    def add_new_tab(self, url, label="New Tab", pinned=False):
        new_tab = BrowserTab(self.history_manager, self.download_manager, qprofile=self.qprofile)
        new_tab.setProperty("pinned", bool(pinned))
        self.bind_tab_signals(new_tab)
        new_tab.loadFinished.connect(lambda ok, tab=new_tab: self.on_tab_load_finished(ok, tab))
        self.open_target_in_browser(new_tab, url)
        new_tab.urlChanged.connect(self.on_url_changed)
        current_index = self.tab_widget.currentIndex()
        insert_index = self.tab_widget.count() if current_index < 0 else current_index + 1
        index = self.tab_widget.insertTab(insert_index, new_tab, label)
        self.tab_widget.setCurrentIndex(index)
        if pinned:
            self.set_tab_pinned(new_tab, True)

    def bind_tab_signals(self, tab):
        tab.titleChanged.connect(lambda title, current_tab=tab: self.update_tab_title(current_tab, title))
        tab.urlChanged.connect(lambda url, current_tab=tab: self.remember_last_content_browser(current_tab, url.toString()))
        try:
            tab.iconChanged.connect(lambda icon, current_tab=tab: self.update_tab_icon(current_tab, icon))
        except Exception:
            pass
        try:
            tab.page().recentlyAudibleChanged.connect(lambda *_args: sync_window_soundscape_with_browser_audio(self))
        except Exception:
            pass

    def apply_tab_visual_state(self, tab, title_text=""):
        index = self.tab_widget.indexOf(tab)
        if index == -1:
            return
        display_title = (title_text or "").strip()
        if not display_title:
            url_value = tab.url().toString()
            display_title = prettify_host_label(url_value) if url_value.startswith("http") else "New Tab"
        if tab.property("pinned"):
            self.tab_widget.setTabText(index, " ")
            self.tab_widget.setTabToolTip(index, display_title)
        else:
            self.tab_widget.setTabText(index, display_title[:28])
            self.tab_widget.setTabToolTip(index, display_title)

    def update_tab_icon(self, tab, icon):
        index = self.tab_widget.indexOf(tab)
        if index == -1:
            return
        self.tab_widget.setTabIcon(index, icon if isinstance(icon, QIcon) else QIcon())
        self.apply_tab_visual_state(tab, tab.title())

    def update_tab_title(self, tab, title):
        index = self.tab_widget.indexOf(tab)
        if index == -1:
            return
        cleaned = (title or "").strip()
        if not cleaned:
            url_value = tab.url().toString()
            cleaned = prettify_host_label(url_value) if url_value.startswith("http") else "New Tab"
        self.apply_tab_visual_state(tab, cleaned)

    def on_url_changed(self, url):
        url_text = url.toString()
        current_tab = self.current_browser()
        self.remember_last_content_browser(current_tab, url_text)
        internal_marker = ""
        if current_tab is not None:
            internal_marker = str(current_tab.property("aurora_internal_page") or "")
        if internal_marker.startswith("settings:"):
            section = internal_marker.split(":", 1)[-1] or "profiles"
            self.url_bar.setText(f"aurora://settings/{section}")
            self.apply_bookmark_bar_visibility(f"https://aurora.settings/{section}")
            self.update_bookmark_button_state()
            return
        if url_text.lower().startswith("aurora://"):
            self.handle_aurora_navigation(url_text)
            return
        lowered = url_text.lower()
        if "aurora.settings/action/" in lowered:
            self.handle_aurora_navigation(url_text)
            return
        if "aurora.settings" in lowered:
                self.url_bar.setText(f"aurora://settings/{getattr(self, 'current_settings_section', 'profiles')}")
        elif "aurora.home" in lowered:
            self.url_bar.setText("aurora://home")
        else:
            self.url_bar.setText(url_text)
        self.apply_bookmark_bar_visibility(url_text)
        self.update_bookmark_button_state()

    def load_profile_bookmarks(self):
        bookmarks_file = os.path.join(self.profile_base_dir, "bookmarks.json")
        if not os.path.exists(bookmarks_file):
            return []
        try:
            with open(bookmarks_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return []
            cleaned = []
            seen_urls = set()
            for bookmark in data:
                if not isinstance(bookmark, dict):
                    continue
                url = (bookmark.get("url") or "").strip()
                if not url:
                    continue
                if not url.startswith(("http://", "https://")):
                    url = "https://" + url
                url_key = url.lower()
                if url_key in seen_urls:
                    continue
                seen_urls.add(url_key)
                cleaned.append({
                    "title": (bookmark.get("title") or "").strip() or prettify_host_label(url),
                    "url": url,
                })
            return cleaned
        except Exception:
            return []

    def save_profile_bookmarks(self, bookmarks):
        bookmarks_file = os.path.join(self.profile_base_dir, "bookmarks.json")
        cleaned = []
        seen_urls = set()
        for bookmark in bookmarks:
            if not isinstance(bookmark, dict):
                continue
            url = (bookmark.get("url") or "").strip()
            if not url:
                continue
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            url_key = url.lower()
            if url_key in seen_urls:
                continue
            seen_urls.add(url_key)
            cleaned.append({
                "title": (bookmark.get("title") or "").strip() or prettify_host_label(url),
                "url": url,
            })
        with open(bookmarks_file, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, indent=2)
        self.invalidate_internal_cache()

    def current_page_bookmark(self, browser=None):
        browser = browser or self.current_browser()
        if not browser:
            return None
        current_url = browser.url().toString().strip()
        if not current_url.startswith(("http://", "https://")):
            return None
        for bookmark in self.load_profile_bookmarks():
            if bookmark.get("url", "").strip() == current_url:
                return bookmark
        return None

    def update_bookmark_button_state(self, browser=None):
        browser = browser or self.current_browser()
        if not hasattr(self, "bookmark_button"):
            return
        bookmark = self.current_page_bookmark(browser)
        is_saved = bookmark is not None
        self.bookmark_button.setText("\u2605" if is_saved else "\u2606")
        self.bookmark_button.setToolTip("Remove bookmark" if is_saved else "Add bookmark")

    def toggle_current_page_bookmark(self):
        browser = self.current_browser()
        if not browser:
            return
        current_url = browser.url().toString().strip()
        if not current_url.startswith(("http://", "https://")):
            self.status.showMessage("Only normal web pages can be bookmarked.", 3000)
            return
        bookmarks = self.load_profile_bookmarks()
        existing_index = next((i for i, item in enumerate(bookmarks) if item.get("url", "").strip() == current_url), None)
        if existing_index is not None:
            removed = bookmarks.pop(existing_index)
            self.save_profile_bookmarks(bookmarks)
            self.status.showMessage(f"Removed bookmark: {removed.get('title', prettify_host_label(current_url))}", 3000)
        else:
            page_title = (browser.title() or "").strip() or prettify_host_label(current_url)
            bookmarks.insert(0, {"title": page_title, "url": current_url})
            self.save_profile_bookmarks(bookmarks)
            self.status.showMessage(f"Bookmarked: {page_title}", 3000)
        if hasattr(self, "bookmark_manager") and self.bookmark_manager:
            try:
                self.bookmark_manager.bookmarks = bookmarks
                self.bookmark_manager.refresh_list()
            except Exception:
                pass
        self.refresh_bookmark_bar()
        self.update_bookmark_button_state(browser)

    def close_tab(self, index):
        if self.tab_widget.count() > 1:
            closed_tab = self.tab_widget.widget(index)
            if closed_tab.property("pinned"):
                if QMessageBox.question(self, "Pinned Tab", "This tab is pinned. Close it anyway?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return
            self.closed_tabs.append(closed_tab.url().toString())
            self.tab_widget.removeTab(index)
            self.save_pinned_tabs()
            stop_and_dispose_webview(closed_tab)
            sync_window_soundscape_with_browser_audio(self)

    def current_search_engine_name(self):
        if hasattr(self, "search_engine_combo"):
            engine = self.search_engine_combo.currentText()
            if engine in self.search_engines:
                return engine
        return "Google"

    def search_url_for_query(self, query, engine_name=None):
        engine = engine_name or self.current_search_engine_name()
        search_base = self.search_engines.get(engine, self.search_engines["Google"])
        return search_base + quote_plus(query)

    def on_search_engine_changed(self, _engine_name):
        self.save_browser_settings()
        self.refresh_internal_pages()

    def load_url(self):
        url = self.url_bar.text().strip()
        if url.lower() in ("aurora://home", "about:aurora", "about:newtab", "aurora:home", "home"):
            self.open_target_in_browser(self.current_browser(), "aurora://home")
            return
        if url.lower().startswith("aurora://settings") or url.lower().startswith("aurora:settings"):
            self.open_target_in_browser(self.current_browser(), url)
            return
        if not url.startswith("http") and not url.startswith("ftp"):
            if " " not in url and "." in url:
                url = "https://" + url
            else:
                url = self.search_url_for_query(url)
        self.open_target_in_browser(self.current_browser(), url)

    def current_browser(self):
        return self.tab_widget.currentWidget()

    def open_external_browser_window(self, source_tab=None):
        profile = None
        if source_tab is not None:
            try:
                profile = source_tab.page().profile()
            except Exception:
                profile = None
        child_window = DetachedBrowserWindow(
            self.history_manager,
            self.download_manager,
            qprofile=profile or self.qprofile,
            theme_snapshot={
                "stylesheet": self.styleSheet(),
                "window_icon": self.windowIcon(),
            },
            parent_window=self,
            incognito=False,
        )
        self.child_windows.append(child_window)
        child_window.destroyed.connect(lambda *_args, win=child_window: self.child_windows.remove(win) if win in self.child_windows else None)
        child_window.show()
        child_window.raise_()
        child_window.activateWindow()
        return child_window.current_browser()

    def show_history(self):
        self.history_manager.show()

    def show_bookmark_manager(self):
        if getattr(self, "bookmark_manager", None) and self.bookmark_manager.isVisible():
            self.bookmark_manager.raise_()
            self.bookmark_manager.activateWindow()
            return
        self.bookmark_manager = BookmarkManager(self.profile)
        self.bookmark_manager.bookmarkClicked.connect(self.open_history_url)
        self.bookmark_manager.bookmarksChanged.connect(self.refresh_bookmark_bar)
        self.bookmark_manager.bookmarksChanged.connect(self.invalidate_internal_cache)
        self.bookmark_manager.show()

    def open_incognito_window(self):
        try:
            self.incognito_window = IncognitoWindow({
                "current_theme": getattr(self, "current_theme", "dark"),
                "custom_color": getattr(self, "custom_color", None),
                "wallpaper_path": getattr(self, "wallpaper_path", None),
                "stylesheet": self.styleSheet(),
            })
            self.incognito_window.setWindowIcon(self.windowIcon())
            self.incognito_window.show()
            self.incognito_window.raise_()
            self.incognito_window.activateWindow()
        except Exception as e:
            QMessageBox.warning(self, "Incognito Error", f"Failed to open incognito window: {e}")

    def toggle_theme(self):
        if self.current_theme == "light":
            self.set_dark_theme()
        else:
            self.set_light_theme()

    def set_dark_theme(self):
        self.reset_theme_surface_overrides()
        dark_style = """
        QMainWindow, QWidget {
            background-color: #2e2e2e;
            color: #f0f0f0;
        }
        QPushButton {
            background-color: #3f51b5;
            color: white;
            border-radius: 5px;
            padding: 6px 10px;
            border: none;
        }
        QPushButton:hover {
            background-color: #1c3f8a;
        }
        QPushButton#navBtn {
            background-color: #353b4e;
            border-radius: 16px;
            padding: 0;
            border: 1px solid #4f5871;
            color: #f8fbff;
            font-size: 14px;
            font-weight: 700;
        }
        QPushButton#navBtn:hover {
            background-color: #44506a;
            border-color: #6880b0;
        }
        QPushButton#navBtn:pressed {
            background-color: #2f384d;
        }
        QPushButton#newTabBtn {
            background-color: #5a6fde;
            border-color: #7e90f0;
            color: #ffffff;
            font-size: 18px;
        }
        QPushButton#themeBtn {
            background-color: #222938;
            color: #dce7ff;
            border: 1px solid #4f5871;
            border-radius: 16px;
            font-size: 12px;
            font-weight: 700;
            padding: 0 14px;
        }
        QPushButton#themeBtn:hover {
            background-color: #30384b;
            border-color: #6880b0;
        }
        QPushButton#bookmarkBtn {
            background-color: #222938;
            color: #ffd76a;
            border: 1px solid #4f5871;
            border-radius: 16px;
            font-size: 17px;
            font-weight: 700;
            padding: 0;
        }
        QPushButton#bookmarkBtn:hover {
            background-color: #30384b;
            border-color: #6880b0;
        }
        QFrame#bookmarkBar {
            background-color: rgba(255,255,255,0.03);
            border: 1px solid #3f485d;
            border-radius: 10px;
        }
        QPushButton#bookmarkChip {
            background-color: #1d2533;
            color: #e8f1ff;
            border: 1px solid #46516a;
            border-radius: 10px;
            padding: 3px 10px;
            min-height: 24px;
            max-height: 24px;
            text-align: left;
        }
        QPushButton#bookmarkChip:hover {
            background-color: #273247;
            border-color: #6880b0;
        }
        QPushButton#bookmarkOverflowBtn {
            background-color: #1b2d3e;
            color: #d9f2ff;
            border: 1px solid #4c6e8f;
            border-radius: 10px;
            padding: 3px 10px;
            min-height: 24px;
            max-height: 24px;
            text-align: left;
            font-weight: 700;
        }
        QPushButton#bookmarkOverflowBtn:hover {
            background-color: #24415a;
            border-color: #77b7e8;
        }
        QPushButton#bookmarkManageBtn {
            background-color: #223149;
            color: #e8f1ff;
            border: 1px solid #5d7398;
            border-radius: 10px;
            padding: 3px 10px;
            min-height: 24px;
            max-height: 24px;
            text-align: left;
            font-weight: 700;
        }
        QPushButton#bookmarkManageBtn:hover {
            background-color: #2c3d58;
            border-color: #7e9bca;
        }
        QPushButton#bookmarkOverflowBtn {
            background-color: #1b2d3e;
            color: #d9f2ff;
            border: 1px solid #4c6e8f;
            border-radius: 10px;
            padding: 3px 10px;
            min-height: 24px;
            max-height: 24px;
            text-align: left;
            font-weight: 700;
        }
        QPushButton#bookmarkOverflowBtn:hover {
            background-color: #24415a;
            border-color: #77b7e8;
        }
        QPushButton#bookmarkManageBtn {
            background-color: #223149;
            color: #e8f1ff;
            border: 1px solid #5d7398;
            border-radius: 10px;
            padding: 3px 10px;
            min-height: 24px;
            max-height: 24px;
            text-align: left;
            font-weight: 700;
        }
        QPushButton#bookmarkManageBtn:hover {
            background-color: #2c3d58;
            border-color: #7e9bca;
        }
        QPushButton#bookmarkOverflowBtn {
            background-color: #1b2d3e;
            color: #d9f2ff;
            border: 1px solid #4c6e8f;
            border-radius: 10px;
            padding: 3px 10px;
            min-height: 24px;
            max-height: 24px;
            text-align: left;
            font-weight: 700;
        }
        QPushButton#bookmarkOverflowBtn:hover {
            background-color: #24415a;
            border-color: #77b7e8;
        }
        QPushButton#bookmarkManageBtn {
            background-color: #223149;
            color: #e8f1ff;
            border: 1px solid #5d7398;
            border-radius: 10px;
            padding: 3px 10px;
            min-height: 24px;
            max-height: 24px;
            text-align: left;
            font-weight: 700;
        }
        QPushButton#bookmarkManageBtn:hover {
            background-color: #2c3d58;
            border-color: #7e9bca;
        }
        QScrollBar:vertical {
            background: #1a2130;
            width: 10px;
            margin: 2px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background: #5b6987;
            min-height: 28px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background: #7386aa;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: transparent;
        }
        QScrollBar:horizontal {
            background: #1a2130;
            height: 10px;
            margin: 2px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal {
            background: #5b6987;
            min-width: 28px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal:hover {
            background: #7386aa;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0;
        }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
            background: transparent;
        }
        QLineEdit {
            background-color: #444;
            color: white;
            border: 1px solid #ccc;
            border-radius: 10px;
            padding: 5px;
            font-size: 14px;
        }
        QLineEdit#addressBar {
            border-radius: 16px;
            padding: 7px 12px;
            border: 1px solid #5d6785;
            background-color: #2a3242;
        }
        QComboBox#engineBox {
            border-radius: 16px;
            padding: 6px 10px;
            border: 1px solid #5d6785;
            background-color: #2a3242;
            color: #f0f4ff;
        }
        QTabWidget::pane {
            border: 1px solid #4d4d4d;
            top: -1px;
        }
        QTabBar::tab {
            background: #3a3a3a;
            color: #d8d8d8;
            padding: 8px 12px;
            margin-right: 2px;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
        }
        QTabBar::tab:selected {
            background: #4a4a4a;
            color: #ffffff;
        }
        QMenuBar {
            background-color: #262626;
            color: #f0f0f0;
        }
        QMenuBar::item:selected {
            background-color: #3f51b5;
        }
        QMenu {
            background-color: #303030;
            color: #f0f0f0;
            border: 1px solid #4d4d4d;
        }
        QMenu::item {
            padding: 8px 18px;
            min-width: 180px;
        }
        QMenu::item:selected {
            background-color: #3f51b5;
        }
        QStatusBar {
            background-color: #252525;
            color: #f0f0f0;
            border-top: 1px solid #3a3a3a;
            font-weight: 600;
        }
        QLineEdit:hover {
            border: 1px solid #3f51b5;
        }
        QLineEdit:focus {
            border: 1px solid #3f51b5;
            background-color: #555;
        }
        QComboBox {
            background-color: #444;
            color: white;
            border: 1px solid #ccc;
            border-radius: 10px;
            padding: 5px;
        }
        QComboBox:hover {
            border: 1px solid #3f51b5;
        }
        QComboBox:focus {
            border: 1px solid #3f51b5;
            background-color: #555;
        }
        QCompleter {
            background-color: #444;
            color: white;
            border: 1px solid #ccc;
            border-radius: 5px;
        }
        """
        self.setStyleSheet(dark_style)
        self.current_theme = "dark"
        self.wallpaper = None
        self.wallpaper_path = None
        self.custom_color = None
        self.refresh_internal_pages()
        self.updateStatusBar()
        self.save_theme_preference()

    def set_light_theme(self):
        self.reset_theme_surface_overrides()
        light_style = """
        QMainWindow, QWidget {
            background-color: #f5f7fb;
            color: #1f1f1f;
        }
        QPushButton {
            background-color: #eef2ff;
            color: #22324d;
            border-radius: 8px;
            padding: 6px 10px;
            border: 1px solid #cad4f6;
        }
        QPushButton:hover {
            background-color: #e2e9ff;
            border-color: #bfcaf0;
        }
        QPushButton#navBtn {
            background-color: #f4f6ff;
            border-radius: 16px;
            padding: 0;
            border: 1px solid #d8def2;
            color: #1f2a44;
            font-size: 14px;
            font-weight: 700;
        }
        QPushButton#navBtn:hover {
            background-color: #e9eeff;
            border-color: #bfcaf0;
        }
        QPushButton#navBtn:pressed {
            background-color: #dde5ff;
        }
        QPushButton#newTabBtn {
            background-color: #2f5cff;
            border-color: #5e81ff;
            color: #ffffff;
            font-size: 18px;
        }
        QPushButton#themeBtn {
            background-color: #eef2ff;
            color: #24304a;
            border: 1px solid #cad4f6;
            border-radius: 16px;
            font-size: 12px;
            font-weight: 700;
            padding: 0 14px;
        }
        QPushButton#themeBtn:hover {
            background-color: #e2e9ff;
            border-color: #bfcaf0;
        }
        QPushButton#bookmarkBtn {
            background-color: #eef2ff;
            color: #d08a00;
            border: 1px solid #cad4f6;
            border-radius: 16px;
            font-size: 17px;
            font-weight: 700;
            padding: 0;
        }
        QPushButton#bookmarkBtn:hover {
            background-color: #e2e9ff;
            border-color: #bfcaf0;
        }
        QFrame#bookmarkBar {
            background-color: #f6f8ff;
            border: 1px solid #d7def3;
            border-radius: 10px;
        }
        QPushButton#bookmarkChip {
            background-color: #ffffff;
            color: #24304a;
            border: 1px solid #cad4f6;
            border-radius: 10px;
            padding: 3px 10px;
            min-height: 24px;
            max-height: 24px;
            text-align: left;
        }
        QPushButton#bookmarkChip:hover {
            background-color: #edf2ff;
            border-color: #bfcaf0;
        }
        QPushButton#bookmarkOverflowBtn {
            background-color: #edf2ff;
            color: #30405f;
            border: 1px solid #c7d2f0;
            border-radius: 10px;
            padding: 3px 10px;
            min-height: 24px;
            max-height: 24px;
            text-align: left;
            font-weight: 700;
        }
        QPushButton#bookmarkOverflowBtn:hover {
            background-color: #e2e9ff;
            border-color: #b7c4ea;
        }
        QPushButton#bookmarkManageBtn {
            background-color: #f4f7ff;
            color: #324463;
            border: 1px solid #d4dcf3;
            border-radius: 10px;
            padding: 3px 10px;
            min-height: 24px;
            max-height: 24px;
            text-align: left;
            font-weight: 700;
        }
        QPushButton#bookmarkManageBtn:hover {
            background-color: #eaf0ff;
            border-color: #c0ccee;
        }
        QScrollBar:vertical {
            background: #eef2fb;
            width: 10px;
            margin: 2px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background: #c1cbe3;
            min-height: 28px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background: #a9b7d8;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: transparent;
        }
        QScrollBar:horizontal {
            background: #eef2fb;
            height: 10px;
            margin: 2px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal {
            background: #c1cbe3;
            min-width: 28px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal:hover {
            background: #a9b7d8;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0;
        }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
            background: transparent;
        }
        QLineEdit {
            background-color: #ffffff;
            color: black;
            border: 1px solid #ccc;
            border-radius: 10px;
            padding: 5px;
            font-size: 14px;
        }
        QLineEdit#addressBar {
            border-radius: 16px;
            padding: 7px 12px;
            border: 1px solid #cad4f6;
            background-color: #f7f9ff;
        }
        QComboBox#engineBox {
            border-radius: 16px;
            padding: 6px 10px;
            border: 1px solid #cad4f6;
            background-color: #f7f9ff;
            color: #1e2a45;
        }
        QTabWidget::pane {
            border: 1px solid #d9d9d9;
            top: -1px;
        }
        QTabBar::tab {
            background: #f2f2f2;
            color: #4a4a4a;
            padding: 8px 12px;
            margin-right: 2px;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
        }
        QTabBar::tab:selected {
            background: #ffffff;
            color: #111111;
            border: 1px solid #d9d9d9;
            border-bottom-color: #ffffff;
        }
        QMenuBar {
            background-color: #f8f8f8;
            color: #1f1f1f;
        }
        QMenuBar::item:selected {
            background-color: #e8eeff;
        }
        QMenu {
            background-color: #ffffff;
            color: #1f1f1f;
            border: 1px solid #d9d9d9;
        }
        QMenu::item {
            padding: 8px 18px;
            min-width: 180px;
        }
        QMenu::item:selected {
            background-color: #e8eeff;
        }
        QStatusBar {
            background-color: #f3f3f3;
            color: #222222;
            border-top: 1px solid #d9d9d9;
            font-weight: 600;
        }
        QLineEdit:hover {
            border: 1px solid #6200ea;
        }
        QLineEdit:focus {
            border: 1px solid #6200ea;
            background-color: #f5f5f5;
        }
        QComboBox {
            background-color: #ffffff;
            color: black;
            border: 1px solid #ccc;
            border-radius: 10px;
            padding: 5px;
        }
        QComboBox:hover {
            border: 1px solid #6200ea;
        }
        QComboBox:focus {
            border: 1px solid #6200ea;
            background-color: #f5f5f5;
        }
        QCompleter {
            background-color: #ffffff;
            color: black;
            border: 1px solid #ccc;
            border-radius: 5px;
        }
        """
        self.setStyleSheet(light_style)
        self.current_theme = "light"
        self.wallpaper = None
        self.wallpaper_path = None
        self.custom_color = None
        self.refresh_internal_pages()
        self.updateStatusBar()
        self.save_theme_preference()

    def set_blue_theme(self):
        self.reset_theme_surface_overrides()
        custom_style = """
                QMainWindow, QWidget {
                    background-color: #e0f7fa;
                    color: #0d2b34;
                }
                QPushButton {
                    background-color: #0288d1;
                    color: white;
                    border-radius: 5px;
                    padding: 6px 10px;
                    border: none;
                }
                QPushButton:hover {
                    background-color: #0277bd;
                }
                QPushButton#navBtn {
                    background-color: #e7f8fb;
                    border-radius: 16px;
                    padding: 0;
                    border: 1px solid #b9e2ea;
                    color: #12526a;
                    font-size: 14px;
                    font-weight: 700;
                }
                QPushButton#navBtn:hover {
                    background-color: #d7f0f5;
                    border-color: #8ac8d4;
                }
                QPushButton#navBtn:pressed {
                    background-color: #c3e6ee;
                }
                QPushButton#newTabBtn {
                    background-color: #0288d1;
                    border-color: #25a0e0;
                    color: #ffffff;
                    font-size: 18px;
                }
                QPushButton#themeBtn {
                    background-color: #eaf9fd;
                    color: #145065;
                    border: 1px solid #97d4df;
                    border-radius: 16px;
                    font-size: 12px;
                    font-weight: 700;
                    padding: 0 14px;
                }
                QPushButton#themeBtn:hover {
                    background-color: #dff4f9;
                    border-color: #8ac8d4;
                }
                QPushButton#bookmarkBtn {
                    background-color: #eaf9fd;
                    color: #c77e00;
                    border: 1px solid #97d4df;
                    border-radius: 16px;
                    font-size: 17px;
                    font-weight: 700;
                    padding: 0;
                }
                QPushButton#bookmarkBtn:hover {
                    background-color: #dff4f9;
                    border-color: #8ac8d4;
                }
                QFrame#bookmarkBar {
                    background-color: #eefbfd;
                    border: 1px solid #b8e0e7;
                    border-radius: 10px;
                }
                QPushButton#bookmarkChip {
                    background-color: #ffffff;
                    color: #145065;
                    border: 1px solid #97d4df;
                    border-radius: 10px;
                    padding: 3px 10px;
                    min-height: 24px;
                    max-height: 24px;
                    text-align: left;
                }
                QPushButton#bookmarkChip:hover {
                    background-color: #ebfbfd;
                    border-color: #8ac8d4;
                }
                QPushButton#bookmarkOverflowBtn {
                    background-color: #ddf5f8;
                    color: #175166;
                    border: 1px solid #9fd4dd;
                    border-radius: 10px;
                    padding: 3px 10px;
                    min-height: 24px;
                    max-height: 24px;
                    text-align: left;
                    font-weight: 700;
                }
                QPushButton#bookmarkOverflowBtn:hover {
                    background-color: #d2eef3;
                    border-color: #82c4d1;
                }
                QPushButton#bookmarkManageBtn {
                    background-color: #f2fbfd;
                    color: #20576a;
                    border: 1px solid #b7dfe7;
                    border-radius: 10px;
                    padding: 3px 10px;
                    min-height: 24px;
                    max-height: 24px;
                    text-align: left;
                    font-weight: 700;
                }
                QPushButton#bookmarkManageBtn:hover {
                    background-color: #e6f8fb;
                    border-color: #98d0db;
                }
                QScrollBar:vertical {
                    background: #e7f7fa;
                    width: 10px;
                    margin: 2px;
                    border-radius: 5px;
                }
                QScrollBar::handle:vertical {
                    background: #8ac8d4;
                    min-height: 28px;
                    border-radius: 5px;
                }
                QScrollBar::handle:vertical:hover {
                    background: #6db8c6;
                }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                    height: 0;
                }
                QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                    background: transparent;
                }
                QScrollBar:horizontal {
                    background: #e7f7fa;
                    height: 10px;
                    margin: 2px;
                    border-radius: 5px;
                }
                QScrollBar::handle:horizontal {
                    background: #8ac8d4;
                    min-width: 28px;
                    border-radius: 5px;
                }
                QScrollBar::handle:horizontal:hover {
                    background: #6db8c6;
                }
                QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                    width: 0;
                }
                QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                    background: transparent;
                }
                QLineEdit {
                    background-color: #ffffff;
                    color: black;
                    border: 1px solid #ccc;
                    border-radius: 10px;
                    padding: 5px;
                    font-size: 14px;
                }
                QLineEdit#addressBar {
                    border-radius: 16px;
                    padding: 7px 12px;
                    border: 1px solid #97d4df;
                    background-color: #f5feff;
                }
                QComboBox#engineBox {
                    border-radius: 16px;
                    padding: 6px 10px;
                    border: 1px solid #97d4df;
                    background-color: #f5feff;
                    color: #145065;
                }
                QLineEdit:hover {
                    border: 1px solid #0288d1;
                }
                QLineEdit:focus {
                    border: 1px solid #0288d1;
                    background-color: #f5f5f5;
                }
                QComboBox {
                    background-color: #ffffff;
                    color: black;
                    border: 1px solid #ccc;
                    border-radius: 10px;
                    padding: 5px;
                }
                QComboBox:hover {
                    border: 1px solid #0288d1;
                }
                QComboBox:focus {
                    border: 1px solid #0288d1;
                    background-color: #f5f5f5;
                }
                QCompleter {
                    background-color: #ffffff;
                    color: black;
                    border: 1px solid #ccc;
                    border-radius: 5px;
                }
                QTabWidget::pane {
                    border: 1px solid #9dd8e4;
                    top: -1px;
                }
                QTabBar::tab {
                    background: #cceff5;
                    color: #13566d;
                    padding: 8px 12px;
                    margin-right: 2px;
                    border-top-left-radius: 6px;
                    border-top-right-radius: 6px;
                }
                QTabBar::tab:selected {
                    background: #eafafe;
                    color: #0e4152;
                    border: 1px solid #9dd8e4;
                    border-bottom-color: #eafafe;
                }
                QMenuBar {
                    background-color: #d7f3f9;
                    color: #0d2b34;
                }
                QMenuBar::item:selected {
                    background-color: #bce9f4;
                }
                QMenu {
                    background-color: #eafafe;
                    color: #0d2b34;
                    border: 1px solid #9dd8e4;
                }
                QMenu::item {
                    padding: 8px 18px;
                    min-width: 180px;
                }
                QMenu::item:selected {
                    background-color: #bce9f4;
                }
                QStatusBar {
                    background-color: #d7f3f9;
                    color: #0d2b34;
                    border-top: 1px solid #9dd8e4;
                    font-weight: 600;
                }
                """
        self.setStyleSheet(custom_style)
        self.current_theme = "blue"
        self.wallpaper = None
        self.wallpaper_path = None
        self.custom_color = None
        self.refresh_internal_pages()
        self.updateStatusBar()
        self.save_theme_preference()

    def apply_wallpaper_theme(self, file_path):
        self.reset_theme_surface_overrides()
        file_url = file_path.replace("\\", "/")
        style = f"background-image: url('{file_url}'); background-repeat: no-repeat; background-position: center;"
        self.centralWidget().setStyleSheet(style)
        self.current_theme = "wallpaper"
        self.wallpaper = None
        self.wallpaper_path = file_path
        self.custom_color = None
        self.refresh_internal_pages()
        self.updateStatusBar()
        self.save_theme_preference()

    def apply_accent_color_theme(self, color_value, theme_name="accent"):
        color = color_value if isinstance(color_value, QColor) else QColor(color_value)
        if not color.isValid():
            return
        self.reset_theme_surface_overrides()
        accent = color.name()
        accent_hover = color.lighter(115).name()
        accent_border = color.darker(105).name()
        accent_soft = self.css_rgba(color, 0.10)
        theme_palette = {
            "accent": {
                "window_bg": "#101722",
                "window_text": "#edf4ff",
                "button_bg": "#182232",
                "button_hover": "#1d2b40",
                "button_border": "#314760",
                "nav_bg": "#172130",
                "nav_hover": "#1f2d43",
                "nav_pressed": "#122033",
                "bookmark_bar": "rgba(255,255,255,0.03)",
                "chip_bg": "#162130",
                "chip_hover": "#1c2b40",
                "manage_bg": "#1b273a",
                "manage_hover": "#223149",
                "input_bg": "#162130",
                "tab_bg": "#182232",
                "tab_selected": "#223149",
                "menu_bar": "#121a26",
                "menu_bg": "#172130",
                "status_bg": "#121a26",
                "status_line": "#223149",
                "scroll_bg": "#1a2130",
                "scroll_handle": "#5b6987",
                "scroll_hover": "#7386aa",
            },
            "graphite": {
                "window_bg": "#2a2f36",
                "window_text": "#eef1f5",
                "button_bg": "#39414b",
                "button_hover": "#444d58",
                "button_border": "#5f6a78",
                "nav_bg": "#343b45",
                "nav_hover": "#404854",
                "nav_pressed": "#2f3741",
                "bookmark_bar": "rgba(255,255,255,0.04)",
                "chip_bg": "#323943",
                "chip_hover": "#3d4651",
                "manage_bg": "#38424d",
                "manage_hover": "#46515d",
                "input_bg": "#323943",
                "tab_bg": "#3a424c",
                "tab_selected": "#4b5562",
                "menu_bar": "#272c33",
                "menu_bg": "#30363f",
                "status_bg": "#272c33",
                "status_line": "#414956",
                "scroll_bg": "#313741",
                "scroll_handle": "#77808d",
                "scroll_hover": "#919aa8",
            },
            "forest": {
                "window_bg": "#10271d",
                "window_text": "#edf9f2",
                "button_bg": "#1a3a2d",
                "button_hover": "#214738",
                "button_border": "#3c6f57",
                "nav_bg": "#163328",
                "nav_hover": "#1d4032",
                "nav_pressed": "#122b22",
                "bookmark_bar": "rgba(151,219,186,0.07)",
                "chip_bg": "#183328",
                "chip_hover": "#214033",
                "manage_bg": "#1f4131",
                "manage_hover": "#295240",
                "input_bg": "#173227",
                "tab_bg": "#1d3a2e",
                "tab_selected": "#295340",
                "menu_bar": "#102419",
                "menu_bg": "#173328",
                "status_bg": "#102419",
                "status_line": "#244635",
                "scroll_bg": "#173126",
                "scroll_handle": "#4e8b6d",
                "scroll_hover": "#66ad88",
            },
            "sunset": {
                "window_bg": "#2b1813",
                "window_text": "#fff1ea",
                "button_bg": "#47251b",
                "button_hover": "#5a2d1f",
                "button_border": "#8b523d",
                "nav_bg": "#3f2219",
                "nav_hover": "#51281c",
                "nav_pressed": "#351c15",
                "bookmark_bar": "rgba(255,188,143,0.08)",
                "chip_bg": "#43241a",
                "chip_hover": "#542d1f",
                "manage_bg": "#4b291d",
                "manage_hover": "#603426",
                "input_bg": "#43231a",
                "tab_bg": "#4a281c",
                "tab_selected": "#613224",
                "menu_bar": "#301a14",
                "menu_bg": "#3d2219",
                "status_bg": "#301a14",
                "status_line": "#573026",
                "scroll_bg": "#3b2218",
                "scroll_handle": "#a16043",
                "scroll_hover": "#c07655",
            },
        }.get(theme_name, None)
        if theme_palette is None:
            theme_palette = {
                "window_bg": accent.darker(260).name() if isinstance(color, QColor) else "#101722",
                "window_text": "#edf4ff",
                "button_bg": "#182232",
                "button_hover": "#1d2b40",
                "button_border": "#314760",
                "nav_bg": "#172130",
                "nav_hover": "#1f2d43",
                "nav_pressed": "#122033",
                "bookmark_bar": "rgba(255,255,255,0.03)",
                "chip_bg": "#162130",
                "chip_hover": "#1c2b40",
                "manage_bg": "#1b273a",
                "manage_hover": "#223149",
                "input_bg": "#162130",
                "tab_bg": "#182232",
                "tab_selected": "#223149",
                "menu_bar": "#121a26",
                "menu_bg": "#172130",
                "status_bg": "#121a26",
                "status_line": "#223149",
                "scroll_bg": "#1a2130",
                "scroll_handle": "#5b6987",
                "scroll_hover": "#7386aa",
            }
        accent_style = f"""
        QMainWindow, QWidget {{
            background-color: {theme_palette["window_bg"]};
            color: {theme_palette["window_text"]};
        }}
        QPushButton {{
            background-color: {theme_palette["button_bg"]};
            color: {theme_palette["window_text"]};
            border-radius: 8px;
            padding: 6px 10px;
            border: 1px solid {theme_palette["button_border"]};
        }}
        QPushButton:hover {{
            background-color: {theme_palette["button_hover"]};
            border-color: {accent_hover};
        }}
        QPushButton#navBtn {{
            background-color: {theme_palette["nav_bg"]};
            border-radius: 16px;
            padding: 0;
            border: 1px solid {theme_palette["button_border"]};
            color: #f8fbff;
            font-size: 14px;
            font-weight: 700;
        }}
        QPushButton#navBtn:hover {{
            background-color: {theme_palette["nav_hover"]};
            border-color: {accent_hover};
        }}
        QPushButton#navBtn:pressed {{
            background-color: {theme_palette["nav_pressed"]};
        }}
        QPushButton#newTabBtn {{
            background-color: {accent};
            border-color: {accent_hover};
            color: #ffffff;
            font-size: 18px;
        }}
        QPushButton#themeBtn {{
            background-color: {theme_palette["nav_bg"]};
            color: {theme_palette["window_text"]};
            border: 1px solid {theme_palette["button_border"]};
            border-radius: 16px;
            font-size: 12px;
            font-weight: 700;
            padding: 0 14px;
        }}
        QPushButton#themeBtn:hover {{
            background-color: {theme_palette["nav_hover"]};
            border-color: {accent_hover};
        }}
        QPushButton#bookmarkBtn {{
            background-color: {theme_palette["nav_bg"]};
            color: {accent_hover};
            border: 1px solid {theme_palette["button_border"]};
            border-radius: 16px;
            font-size: 17px;
            font-weight: 700;
            padding: 0;
        }}
        QPushButton#bookmarkBtn:hover {{
            background-color: {theme_palette["nav_hover"]};
            border-color: {accent_hover};
        }}
        QFrame#bookmarkBar {{
            background-color: {theme_palette["bookmark_bar"]};
            border: 1px solid {theme_palette["button_border"]};
            border-radius: 10px;
        }}
        QPushButton#bookmarkChip {{
            background-color: {theme_palette["chip_bg"]};
            color: {theme_palette["window_text"]};
            border: 1px solid {theme_palette["button_border"]};
            border-radius: 10px;
            padding: 3px 10px;
            min-height: 24px;
            max-height: 24px;
            text-align: left;
        }}
        QPushButton#bookmarkChip:hover {{
            background-color: {theme_palette["chip_hover"]};
            border-color: {accent_hover};
        }}
        QPushButton#bookmarkOverflowBtn {{
            background-color: {accent_soft};
            color: {theme_palette["window_text"]};
            border: 1px solid {accent_border};
            border-radius: 10px;
            padding: 3px 10px;
            min-height: 24px;
            max-height: 24px;
            text-align: left;
            font-weight: 700;
        }}
        QPushButton#bookmarkOverflowBtn:hover {{
            background-color: {self.css_rgba(color, 0.16)};
            border-color: {accent_hover};
        }}
        QPushButton#bookmarkManageBtn {{
            background-color: {theme_palette["manage_bg"]};
            color: {theme_palette["window_text"]};
            border: 1px solid {theme_palette["button_border"]};
            border-radius: 10px;
            padding: 3px 10px;
            min-height: 24px;
            max-height: 24px;
            text-align: left;
            font-weight: 700;
        }}
        QPushButton#bookmarkManageBtn:hover {{
            background-color: {theme_palette["manage_hover"]};
            border-color: {accent_hover};
        }}
        QLineEdit {{
            background-color: {theme_palette["input_bg"]};
            color: white;
            border: 1px solid {theme_palette["button_border"]};
            border-radius: 10px;
            padding: 5px;
            font-size: 14px;
        }}
        QLineEdit#addressBar {{
            border-radius: 16px;
            padding: 7px 12px;
            border: 1px solid {theme_palette["button_border"]};
            background-color: {theme_palette["input_bg"]};
        }}
        QComboBox#engineBox {{
            border-radius: 16px;
            padding: 6px 10px;
            border: 1px solid {theme_palette["button_border"]};
            background-color: {theme_palette["input_bg"]};
            color: {theme_palette["window_text"]};
        }}
        QTabWidget::pane {{
            border: 1px solid {theme_palette["button_border"]};
            top: -1px;
        }}
        QTabBar::tab {{
            background: {theme_palette["tab_bg"]};
            color: #d8e6ff;
            padding: 8px 12px;
            margin-right: 2px;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
        }}
        QTabBar::tab:selected {{
            background: {theme_palette["tab_selected"]};
            color: #ffffff;
        }}
        QMenuBar {{
            background-color: {theme_palette["menu_bar"]};
            color: {theme_palette["window_text"]};
        }}
        QMenuBar::item:selected {{
            background-color: {accent};
        }}
        QMenu {{
            background-color: {theme_palette["menu_bg"]};
            color: {theme_palette["window_text"]};
            border: 1px solid {theme_palette["button_border"]};
        }}
        QMenu::item {{
            padding: 8px 18px;
            min-width: 180px;
        }}
        QMenu::item:selected {{
            background-color: {accent};
        }}
        QStatusBar {{
            background-color: {theme_palette["status_bg"]};
            color: {theme_palette["window_text"]};
            border-top: 1px solid {theme_palette["status_line"]};
            font-weight: 600;
        }}
        QScrollBar:vertical {{
            background: {theme_palette["scroll_bg"]};
            width: 10px;
            margin: 2px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical {{
            background: {theme_palette["scroll_handle"]};
            min-height: 28px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {theme_palette["scroll_hover"]};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        QScrollBar:horizontal {{
            background: {theme_palette["scroll_bg"]};
            height: 10px;
            margin: 2px;
            border-radius: 5px;
        }}
        QScrollBar::handle:horizontal {{
            background: {theme_palette["scroll_handle"]};
            min-width: 28px;
            border-radius: 5px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {theme_palette["scroll_hover"]};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0;
        }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
            background: transparent;
        }}
        QLineEdit:hover, QLineEdit:focus, QComboBox:hover, QComboBox:focus {{
            border: 1px solid {accent_hover};
        }}
        """
        self.setStyleSheet(accent_style)
        self.current_theme = theme_name
        self.wallpaper = None
        self.wallpaper_path = None
        self.custom_color = color
        self.refresh_internal_pages()
        self.updateStatusBar()
        self.save_theme_preference()

    def apply_custom_color_theme(self, color_value):
        self.apply_accent_color_theme(color_value)

    def set_graphite_theme(self):
        self.apply_accent_color_theme("#8ea0b6", theme_name="graphite")

    def set_forest_theme(self):
        self.apply_accent_color_theme("#3fbf84", theme_name="forest")

    def set_sunset_theme(self):
        self.apply_accent_color_theme("#f08a5d", theme_name="sunset")

    def choose_accent_color(self):
        accent_options = [
            ("Aurora Blue", "#5f84ff"),
            ("Ocean", "#2e9bff"),
            ("Emerald", "#1dbf8b"),
            ("Mint", "#56d7b7"),
            ("Amber", "#f2b94b"),
            ("Sunset", "#ff8a5b"),
            ("Rose", "#e56a96"),
            ("Violet", "#8b74ff"),
            ("Crimson", "#d95757"),
            ("Custom...", "custom"),
        ]
        labels = [name for name, _ in accent_options]
        current_color_name = ""
        if isinstance(getattr(self, "custom_color", None), QColor) and self.custom_color.isValid():
            current_color_name = self.custom_color.name().lower()
        current_index = len(labels) - 1
        for index, (_label, value) in enumerate(accent_options):
            if value != "custom" and value.lower() == current_color_name:
                current_index = index
                break
        choice, ok = show_styled_item_picker(self, "Accent Color", "Choose Accent:", labels, current_index, False)
        if not ok or not choice:
            return
        selected_value = next((value for name, value in accent_options if name == choice), None)
        if selected_value == "custom":
            color = QColorDialog.getColor()
            if color.isValid():
                self.apply_accent_color_theme(color)
        elif selected_value:
            self.apply_accent_color_theme(selected_value)

    def print_page(self):
        try:
            printers = QPrinterInfo.availablePrinters()
            if printers:
                printer = QPrinter(QPrinter.HighResolution)
                printer.setOutputFormat(QPrinter.NativeFormat)
                dialog = QPrintDialog(printer, self)
                if dialog.exec_() == QPrintDialog.Accepted:
                    self.current_browser().page().print(printer, lambda ok: print("Print completed" if ok else "Print failed"))
                else:
                    print("Print dialog cancelled")
            else:
                print("No printers available, saving as PDF")
                file_path, _ = QFileDialog.getSaveFileName(self, "Save as PDF", "", "PDF Files (*.pdf)")
                if file_path:
                    printer = QPrinter(QPrinter.HighResolution)
                    printer.setOutputFormat(QPrinter.PdfFormat)
                    printer.setOutputFileName(file_path)
                    self.current_browser().page().print(printer, lambda ok: print("PDF save completed" if ok else "PDF save failed"))
                else:
                    print("PDF save cancelled")
                    QMessageBox.information(self, "Print", "No printers available and PDF save cancelled.")
        except Exception as e:
            print(f"Print error: {str(e)}")
            QMessageBox.warning(self, "Print Error", f"Failed to print: {str(e)}. Try saving as PDF.")

    def toggle_current_page_reading_mode(self, state=None):
        if isinstance(state, bool):
            sender = None
            try:
                sender = self.sender()
            except Exception:
                sender = None
            if isinstance(sender, QAction):
                state = None
        browser = self.current_browser()
        if browser is None:
            log_debug("Reading mode: no active browser")
            QMessageBox.information(self, "Reading Mode", "Open a normal article page first, then turn on reading mode.")
            return
        current_url = browser.url().toString()
        log_debug(f"Reading mode requested for URL: {current_url}")
        if self.is_internal_browser_url(current_url):
            log_debug(f"Reading mode blocked for internal page: {current_url}")
            QMessageBox.information(self, "Reading Mode", "Switch to the page you want to read, then toggle reading mode from View.")
            return
        def finish_enabled(applied):
            if applied:
                log_debug(f"Reading mode enabled for: {current_url}")
                self.show_runtime_status("Reading mode enabled", 2200)
            else:
                log_debug(f"Reading mode enable failed for: {current_url}")
                QMessageBox.information(self, "Reading Mode", "Aurora could not simplify this page into reading mode.")
            self.refresh_internal_pages()

        def finish_disabled(_removed):
            log_debug(f"Reading mode disabled for: {current_url}")
            self.show_runtime_status("Reading mode disabled", 2200)
            self.refresh_internal_pages()

        def enable_mode():
            browser.setProperty("aurora_reading_mode_active", True)
            self.apply_reading_mode_to_browser(browser, callback=finish_enabled)

        if state is not None:
            if bool(state):
                enable_mode()
            else:
                self.remove_reading_mode_from_browser(browser, callback=finish_disabled)
            return

        if not self.current_page_reading_mode_enabled(browser):
            enable_mode()
            return

        probe_script = "(() => document.documentElement.dataset.auroraReadingApplied === '1')();"
        try:
            browser.page().runJavaScript(
                probe_script,
                lambda applied, b=browser: self.remove_reading_mode_from_browser(b, callback=finish_disabled)
                if bool(applied)
                else enable_mode()
            )
        except Exception:
            enable_mode()

    def toggle_reading_mode(self, state=None):
        self.toggle_current_page_reading_mode(state=state)

    def open_reading_mode_help(self):
        QMessageBox.information(
            self,
            "Reading Mode",
            "Reading mode is per-tab. Open the article tab you want, switch to that tab, then use View -> Toggle Reading Mode For Current Tab."
        )

    def reopen_closed_tab(self):
        if self.closed_tabs:
            url = self.closed_tabs.pop()
            self.add_new_tab(QUrl(url), "Reopened Tab")
        else:
            QMessageBox.information(self, "Reopen Closed Tab", "No closed tabs to reopen.")

    def open_dev_tools(self):
        browser = self.current_browser()
        if browser:
            try:
                if not self.dev_tools_window:
                    self.dev_tools_window = QMainWindow()
                    self.dev_tools_window.setWindowTitle("Developer Tools")
                    self.dev_tools_window.setGeometry(100, 100, 800, 600)
                    dev_tools_view = QWebEngineView()
                    dev_tools_page = QWebEnginePage(browser.page().profile(), dev_tools_view)
                    dev_tools_view.setPage(dev_tools_page)
                    self.dev_tools_window.setCentralWidget(dev_tools_view)
                    browser.page().setDevToolsPage(dev_tools_page)
                self.dev_tools_window.show()
                print("Developer tools window opened")
            except Exception as e:
                print(f"Dev tools error: {str(e)}")
                QMessageBox.warning(self, "Developer Tools Error", f"Failed to open developer tools: {str(e)}")
        else:
            print("No active browser tab for dev tools")
            QMessageBox.warning(self, "Developer Tools Error", "No active browser tab.")

    def customize_ui(self):
        options = ["Light", "Dark", "Blue", "Graphite", "Forest", "Sunset", "Set Wallpaper", "Accent Color"]
        theme_key = (getattr(self, "current_theme", "") or "").strip().lower()
        theme_to_index = {
            "light": 0,
            "dark": 1,
            "blue": 2,
            "graphite": 3,
            "forest": 4,
            "sunset": 5,
            "wallpaper": 6,
            "accent": 7,
        }
        current_index = theme_to_index.get(theme_key, 0)
        choice, ok = show_styled_item_picker(self, "Customize UI", "Select Option:", options, current_index, False)
        if ok and choice:
            if choice.lower() == "light":
                self.set_light_theme()
            elif choice.lower() == "dark":
                self.set_dark_theme()
            elif choice.lower() == "blue":
                self.set_blue_theme()
            elif choice.lower() == "graphite":
                self.set_graphite_theme()
            elif choice.lower() == "forest":
                self.set_forest_theme()
            elif choice.lower() == "sunset":
                self.set_sunset_theme()
            elif choice.lower() == "set wallpaper":
                file_path, _ = QFileDialog.getOpenFileName(self, "Select Wallpaper", "", "Images (*.png *.jpg *.bmp)")
                if file_path:
                    self.apply_wallpaper_theme(file_path)
            elif choice.lower() == "accent color":
                self.choose_accent_color()

    def clear_incognito_data(self):
        QMessageBox.information(
            self,
            "Incognito",
            "Incognito windows use a separate in-memory browsing profile. Data is discarded when the incognito window closes."
        )

    def advanced_download_manager(self):
        self.show_download_manager()

    def show_download_manager(self):
        self.download_manager.show()

    def closeEvent(self, event):
        self.save_session_state()
        self.save_pinned_tabs()
        self.save_browser_settings()
        self.download_manager.finalize_active_downloads_on_shutdown()
        for child_window in list(getattr(self, "child_windows", []) or []):
            try:
                child_window.close()
            except Exception:
                pass
        for i in range(self.tab_widget.count()):
            stop_and_dispose_webview(self.tab_widget.widget(i), replace_page=False)
        if self.dev_tools_window:
            try:
                self.dev_tools_window.close()
            except Exception:
                pass
            self.dev_tools_window = None
        event.accept()

    def set_homepage(self):
        new_home, ok = show_styled_text_input(
            self,
            "Set Homepage",
            "Enter homepage URL or 'aurora://home' for the custom start page:",
            self.homepage
        )
        if ok and new_home:
            cleaned = new_home.strip()
            if cleaned.lower() in ("home", "aurora:home", "about:aurora", "about:newtab"):
                cleaned = "aurora://home"
            if not cleaned.startswith("http") and cleaned != "aurora://home":
                cleaned = "https://" + cleaned
            self.homepage = cleaned
            self.save_homepage_preference()
            self.refresh_internal_pages()
            QMessageBox.information(self, "Homepage Set", f"Homepage set to: {self.homepage}")

    def switch_profile(self):
        dialog = ProfilePickerDialog(discover_profiles(), self.profile, self, active_profile=self.profile)
        if dialog.exec_() != QDialog.Accepted:
            return
        selected_profile = dialog.selected_profile()
        if not selected_profile:
            QMessageBox.warning(self, "Profile", "Please enter a valid profile name.")
            return
        profile_file = os.path.join(os.path.expanduser("~"), "profile.txt")
        try:
            with open(profile_file, "w") as f:
                f.write(selected_profile)
            QMessageBox.information(
                self,
                "Profile Changed",
                f"Profile changed to: {selected_profile}. The application will now restart."
            )
            if not restart_application([f"--profile={selected_profile}", "--skip-profile-picker"]):
                QMessageBox.warning(self, "Profile Change Error", "Could not restart Aurora automatically.")
        except Exception as e:
            QMessageBox.warning(self, "Profile Change Error", f"Could not switch profile: {e}")

    def open_extension_manager(self):
        dialog = ExtensionManagerDialog(self)
        style_aux_window(dialog)
        dialog.exec_()

    def load_extension(self):
        # Backward compatibility for existing calls.
        self.open_extension_manager()

    def open_password_manager(self):
        if getattr(self, "password_manager_window", None) and self.password_manager_window.isVisible():
            self.password_manager_window.raise_()
            self.password_manager_window.activateWindow()
            return
        self.password_manager_window = PasswordManagerWindow(self)
        self.password_manager_window.show()

    def open_security_options(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Security Options")
        style_aux_window(dialog)
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout()
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        title = QLabel("Protection")
        title.setStyleSheet("font-size: 22px; font-weight: 800;")
        subtitle = QLabel("Block ads, trackers, and annoying popup tabs without breaking the sites you trust.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #94abd1;")
        do_not_track_checkbox = QCheckBox("Send Do Not Track header")
        do_not_track_checkbox.setChecked(self.interceptor.do_not_track)
        ad_blocker_checkbox = QCheckBox("Block ads and trackers")
        ad_blocker_checkbox.setChecked(self.ad_blocker_enabled)
        popup_blocker_checkbox = QCheckBox("Block annoying popup tabs and redirect traps")
        popup_blocker_checkbox.setChecked(self.popup_blocker_enabled)
        current_site = self.current_site_host()
        current_site_label = QLabel(f"Current site: {current_site or 'No active website'}")
        current_site_label.setStyleSheet("color: #94abd1;")
        blocked_count_label = QLabel(
            f"Blocked requests this session: {getattr(self.interceptor, 'blocked_request_count', 0)}"
        )
        blocked_count_label.setStyleSheet("color: #94abd1;")
        allowlist_label = QLabel("Trusted sites")
        allowlist_label.setStyleSheet("font-weight: 700;")
        allowlist_list = QListWidget()
        allowlist_list.setSelectionMode(QListWidget.ExtendedSelection)
        allowlist_list.addItems(self.site_security_allowlist)
        trust_current_button = QPushButton("Trust Current Site")
        remove_selected_button = QPushButton("Remove Selected")
        def add_current_site_to_dialog_allowlist():
            host = self.current_site_host()
            if not host:
                QMessageBox.information(dialog, "Security Options", "Open a website first, then trust it from here.")
                return
            existing = {
                allowlist_list.item(i).text().strip().lower()
                for i in range(allowlist_list.count())
            }
            if host not in existing:
                allowlist_list.addItem(host)
        trust_current_button.clicked.connect(add_current_site_to_dialog_allowlist)
        remove_selected_button.clicked.connect(
            lambda: (
                [allowlist_list.takeItem(allowlist_list.row(item)) for item in list(allowlist_list.selectedItems())]
            )
        )
        allowlist_actions = QHBoxLayout()
        allowlist_actions.setSpacing(10)
        allowlist_actions.addWidget(trust_current_button)
        allowlist_actions.addWidget(remove_selected_button)
        allowlist_actions.addStretch()
        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        save_button = QPushButton("Save")
        cancel_button = QPushButton("Cancel")
        save_button.clicked.connect(
            lambda: self.apply_security_options(
                do_not_track_checkbox.isChecked(),
                ad_blocker_checkbox.isChecked(),
                popup_blocker_checkbox.isChecked(),
                [allowlist_list.item(i).text() for i in range(allowlist_list.count())],
                dialog,
            )
        )
        cancel_button.clicked.connect(dialog.reject)
        button_row.addStretch()
        button_row.addWidget(cancel_button)
        button_row.addWidget(save_button)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(do_not_track_checkbox)
        layout.addWidget(ad_blocker_checkbox)
        layout.addWidget(popup_blocker_checkbox)
        layout.addWidget(current_site_label)
        layout.addWidget(blocked_count_label)
        layout.addWidget(allowlist_label)
        layout.addWidget(allowlist_list)
        layout.addLayout(allowlist_actions)
        layout.addLayout(button_row)
        dialog.setLayout(layout)
        dialog.exec_()

    def apply_security_options(self, do_not_track, ad_blocker, popup_blocker, allowlist_hosts, dialog):
        self.interceptor.do_not_track = do_not_track
        self.ad_blocker_enabled = ad_blocker
        self.popup_blocker_enabled = popup_blocker
        self.site_security_allowlist = sorted({
            (host or "").strip().lower()
            for host in (allowlist_hosts or [])
            if (host or "").strip()
        })
        self.interceptor.ad_blocker_enabled = self.ad_blocker_enabled
        self.interceptor.popup_blocker_enabled = self.popup_blocker_enabled
        self.interceptor.set_allowlist(self.site_security_allowlist)
        self.save_browser_settings()
        self.show_runtime_status("Security settings updated", 2500)
        dialog.accept()

    def take_screenshot(self):
        pixmap = self.current_browser().grab()
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Screenshot", "", "PNG Files (*.png);;JPEG Files (*.jpg)")
        if file_path:
            pixmap.save(file_path)

    def get_video_size(self, callback):
        js = """
        try {
            var attempts = 0;
            var maxAttempts = 60;
            var interval = setInterval(function() {
                var video = document.querySelector('video, .video-stream, .html5-main-video');
                if (video && video.videoWidth > 0 && video.videoHeight > 0) {
                    console.log('Video dimensions: ' + video.videoWidth + 'x' + video.videoHeight);
                    clearInterval(interval);
                    window.callback({width: video.videoWidth, height: video.videoHeight});
                } else if (attempts >= maxAttempts) {
                    console.log('Invalid or missing video dimensions after max attempts');
                    clearInterval(interval);
                    window.callback(null);
                }
                attempts++;
            }, 500);
        } catch (e) {
            console.error('Error in get_video_size:', e);
            window.callback(null);
        }
        """
        self.current_browser().page().runJavaScript(f"window.callback = arguments[0]; {js}", callback)

    def resize_window(self, result):
        screen_size = QApplication.primaryScreen().size()
        if result and 'width' in result and 'height' in result and result['width'] > 0 and result['height'] > 0:
            width = result['width']
            height = result['height']
            max_width = min(width, 450)
            max_height = int(max_width * height / width)
            if max_height < 300: max_height = 300
            self.resize(max_width, max_height)
            self.current_browser().setGeometry(0, 0, max_width, max_height)
            self.move(screen_size.width() - max_width - 10, screen_size.height() - max_height - 10)
            print(f"Mini-player resized to {max_width}x{max_height}")
        else:
            max_width, max_height = 450, 300
            self.resize(max_width, max_height)
            self.current_browser().setGeometry(0, 0, max_width, max_height)
            self.move(screen_size.width() - max_width - 10, screen_size.height() - max_height - 10)
            print("Using fallback size for mini-player: 450x300")
        if self.video_page_tweaks_enabled and is_youtube_url(self.current_browser().url()):
            js = """
            try {
                function tryWebGL() {
                    var canvas = document.createElement('canvas');
                    var contexts = ['webgl', 'webgl2', 'experimental-webgl'];
                    for (var i = 0; i < contexts.length; i++) {
                        var ctx = canvas.getContext(contexts[i], { failIfMajorPerformanceCaveat: true });
                        if (ctx) {
                            console.log('WebGL initialized: ' + contexts[i]);
                            return true;
                        }
                    }
                    console.log('WebGL not supported or blacklisted, forcing software');
                    var ctx = canvas.getContext('webgl', { failIfMajorPerformanceCaveat: false });
                    if (ctx) {
                        console.log('Software WebGL initialized');
                        return true;
                    }
                    return false;
                }
                document.addEventListener('DOMContentLoaded', function() {
                    tryWebGL();
                    var videoProgress = 0;
                    function maximizeVideo() {
                        var video = document.querySelector('video, .video-stream, .html5-main-video, .ytp-video');
                        if (video) {
                            videoProgress = video.currentTime;
                            var player = document.querySelector('#movie_player, .html5-video-player, #player, .ytd-player, .ytp-player-content');
                            if (player) {
                                player.style.position = 'fixed';
                                player.style.top = '0';
                                player.style.left = '0';
                                player.style.width = '100%';
                                player.style.height = '100%';
                                player.style.background = 'black';
                                player.style.margin = '0';
                                player.style.padding = '0';
                                video.style.width = '100%';
                                video.style.height = '100%';
                                video.style.objectFit = 'contain';
                                video.style.zIndex = '9999';
                                video.controls = false;
                                document.querySelectorAll('#masthead-container, #container, .ytp-chrome-top, .ytp-chrome-bottom, .ytp-gradient-bottom, .ytp-gradient-top, .ytp-title, .ytp-watermark, .annotation, #ytd-player, #movie_player > *:not(video), .html5-video-player > *:not(video), .ytp-player-content > *:not(video), .ad-container, .ytp-ad-module, .ytp-ad-overlay, .ytp-ad-text').forEach(el => el.style.display = 'none');
                                document.body.style.overflow = 'hidden';
                                document.documentElement.style.overflow = 'hidden';
                                video.currentTime = videoProgress;
                                video.play();
                                console.log('Video maximized successfully at ' + videoProgress + 's');
                                return true;
                            } else {
                                console.log('Player not found, retrying...');
                                setTimeout(maximizeVideo, 1000);
                                return false;
                            }
                        } else {
                            console.log('Video element not found, retrying...');
                            setTimeout(maximizeVideo, 1000);
                            return false;
                        }
                    }
                    maximizeVideo();
                });
            } catch (e) {
                console.error('Error in maximizeVideo:', e);
            }
            """
            self.current_browser().page().runJavaScript(js)
            QTimer.singleShot(1000, self.current_browser().update)

    def toggle_mini_player(self):
        try:
            if self.is_fullscreen:
                self.exit_app_fullscreen()
            if is_youtube_url(self.current_browser().url()):
                js = """
                (() => {
                    const miniBtn = document.querySelector('.ytp-miniplayer-button');
                    if (miniBtn) {
                        miniBtn.click();
                        return 'youtube-miniplayer';
                    }
                    return 'unsupported';
                })();
                """
                self.current_browser().page().runJavaScript(
                    js,
                    lambda result: self.show_runtime_status(
                        "YouTube mini player toggled" if result == "youtube-miniplayer" else "Mini player unavailable on this page"
                    )
                )
                return
            if not self.is_mini_player:
                self.set_browser_chrome_visible(False)
                self.normal_geometry = self.geometry()
                self.setWindowFlags(Qt.FramelessWindowHint)
                self.get_video_size(self.resize_window)
                self.is_mini_player = True
                self.show()
            else:
                self.restore_window_from_mini_player()
                if self.video_page_tweaks_enabled and is_youtube_url(self.current_browser().url()):
                    js = """
                    try {
                        var video = document.querySelector('video, .video-stream, .html5-main-video');
                        var videoProgress = video ? video.currentTime : 0;
                        document.querySelectorAll('#masthead-container, #container, .ytp-chrome-top, .ytp-chrome-bottom, .ytp-gradient-bottom, .ytp-gradient-top, .ytp-title, .ytp-watermark, .annotation, #ytd-player').forEach(el => el.style.display = '');
                        var player = document.querySelector('#movie_player, .html5-video-player, #player, .ytd-player, .ytp-player-content');
                        if (player) {
                            player.style.position = '';
                            player.style.top = '';
                            player.style.left = '';
                            player.style.width = '';
                            player.style.height = '';
                            player.style.background = '';
                            player.style.margin = '';
                            player.style.padding = '';
                            player.style.zIndex = '';
                        }
                        document.body.style.overflow = '';
                        document.documentElement.style.overflow = '';
                        if (video) {
                            video.style.width = '';
                            video.style.height = '';
                            video.style.objectFit = '';
                            video.style.zIndex = '';
                            video.currentTime = videoProgress;
                            video.play();
                            console.log('Restored video at ' + videoProgress + 's');
                        }
                    } catch (e) {
                        console.error('Error restoring page:', e);
                    }
                    """
                    self.current_browser().page().runJavaScript(js)
                    QTimer.singleShot(1000, self.current_browser().update)
        except Exception as e:
            print(f"Mini-player error: {str(e)}")
            QMessageBox.warning(self, "Mini-Player Error", f"Failed to toggle mini-player: {str(e)}")

    def adjust_zoom(self, delta, reset=False):
        if reset:
            self.zoom_factor = 1.0
        else:
            self.zoom_factor = max(0.1, min(5.0, self.zoom_factor + delta))
        self.current_browser().setZoomFactor(self.zoom_factor)
        self.show_runtime_status(f"Zoom: {int(self.zoom_factor * 100)}%")

    def toggle_weather(self, state):
        self.weather_enabled = bool(state)
        self.weather_cache["timestamp"] = 0
        self.save_browser_settings()
        self.updateStatusBar()
        self.refresh_internal_pages()

    def refresh_weather(self):
        self.weather_cache["timestamp"] = 0
        if self.weather_cache.get("value") in ("Weather disabled", "Weather data unavailable"):
            self.weather_cache["value"] = ""
        status = self.get_weather_status()
        self.updateStatusBar()
        self.refresh_internal_pages()
        self.show_runtime_status(f"Weather refreshed: {status}", 3000)

    def get_weather_status(self, force=False):
        cached_value = (self.weather_cache.get("value") or "").strip()
        if requests is None:
            if cached_value and cached_value not in ("Weather disabled", "Weather data unavailable"):
                return cached_value
            return "Weather unavailable (install requests + urllib3)"
        now = time.time()
        if not force and cached_value and now - self.weather_cache["timestamp"] < 600:
            return self.weather_cache["value"]
        try:
            city = self.weather_city or "London"
            headers = {"User-Agent": "AuroraBrowser/1.0"}
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={quote_plus(city)}&count=1&language=en&format=json"
            geo_response = requests.get(geo_url, timeout=6, headers=headers)
            geo_response.raise_for_status()
            geo_data = geo_response.json()
            results = geo_data.get("results") or []
            if results:
                first = results[0]
                latitude = first.get("latitude")
                longitude = first.get("longitude")
                resolved_city = first.get("name") or city
                admin = first.get("admin1") or first.get("country") or ""
                weather_url = (
                    "https://api.open-meteo.com/v1/forecast"
                    f"?latitude={latitude}&longitude={longitude}"
                    "&current=temperature_2m,weather_code&timezone=auto"
                )
                weather_response = requests.get(weather_url, timeout=6, headers=headers)
                weather_response.raise_for_status()
                weather_data = weather_response.json()
                current = weather_data.get("current", {})
                temp_c = current.get("temperature_2m")
                code = current.get("weather_code")
                weather_labels = {
                    0: "Clear",
                    1: "Mainly clear",
                    2: "Partly cloudy",
                    3: "Overcast",
                    45: "Fog",
                    48: "Rime fog",
                    51: "Light drizzle",
                    53: "Drizzle",
                    55: "Dense drizzle",
                    61: "Light rain",
                    63: "Rain",
                    65: "Heavy rain",
                    71: "Light snow",
                    73: "Snow",
                    75: "Heavy snow",
                    80: "Rain showers",
                    81: "Heavy showers",
                    82: "Violent showers",
                    95: "Thunderstorm",
                }
                description = weather_labels.get(code, "Weather")
                if temp_c is not None:
                    location = resolved_city if not admin else f"{resolved_city}, {admin}"
                    value = f"{sanitize_weather_label(location)}: {temp_c}C, {description}"
                    self.weather_cache = {"timestamp": now, "value": value}
                    self.save_weather_preferences()
                    return value
            if cached_value and cached_value not in ("Weather disabled", "Weather data unavailable"):
                self.weather_cache["timestamp"] = now
                return cached_value
            self.weather_cache = {"timestamp": now, "value": "Weather data unavailable"}
            self.save_weather_preferences()
            return self.weather_cache["value"]
        except Exception as e:
            print(f"Weather error: {e}")
            if cached_value and cached_value not in ("Weather disabled", "Weather data unavailable"):
                self.weather_cache["timestamp"] = now
                return cached_value
            self.weather_cache = {"timestamp": now, "value": "Weather data unavailable"}
            self.save_weather_preferences()
            return self.weather_cache["value"]

    def toggle_time_capsule(self, state):
        self.time_capsule_enabled = bool(state)
        self.save_browser_settings()
        if self.time_capsule_enabled:
            self.show_time_capsule_dialog()

    def show_time_capsule_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Virtual Time Capsule")
        dialog.resize(680, 560)
        style_aux_window(dialog)
        layout = QVBoxLayout()
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        title = QLabel("Virtual Time Capsule")
        title.setStyleSheet("font-size: 22px; font-weight: 800;")
        subtitle = QLabel("Save pages with an unlock date and reopen them automatically once they are due.")
        subtitle.setStyleSheet("color: #94abd1;")
        url_input = QLineEdit()
        url_input.setPlaceholderText("Enter URL to save")
        url_input.setStyleSheet("QLineEdit { color: #ffffff; background-color: #10203a; } QLineEdit::placeholder { color: #b7c9e6; }")
        date_input = QLineEdit()
        date_input.setPlaceholderText("Unlock date (YYYY-MM-DD)")
        date_input.setStyleSheet("QLineEdit { color: #ffffff; background-color: #10203a; } QLineEdit::placeholder { color: #b7c9e6; }")
        capsule_list = QListWidget()
        capsule_list.setAlternatingRowColors(True)
        capsule_list.setStyleSheet(
            "QListWidget { color: #edf4ff; background-color: #0d1a30; }"
            "QListWidget::item { color: #edf4ff; min-height: 44px; }"
            "QListWidget::item:selected { color: #ffffff; background-color: rgba(121, 216, 255, 0.22); }"
        )
        for capsule in self.time_capsule_data:
            capsule_list.addItem(self.build_time_capsule_item(capsule))
        if capsule_list.count() == 0:
            empty_item = QListWidgetItem("No saved capsules yet.\nAdd a page and a future unlock date.")
            empty_item.setFlags(Qt.NoItemFlags)
            empty_item.setForeground(QColor("#94abd1"))
            empty_item.setSizeHint(QSize(0, 52))
            capsule_list.addItem(empty_item)
        add_btn = QPushButton("Save Capsule")
        remove_btn = QPushButton("Remove Selected")
        open_due_btn = QPushButton("Open Due Now")
        close_btn = QPushButton("Close")
        add_btn.clicked.connect(lambda: self.save_capsule(url_input.text(), date_input.text(), dialog, capsule_list))
        remove_btn.clicked.connect(lambda: self.remove_selected_capsule(capsule_list))
        open_due_btn.clicked.connect(self.check_due_time_capsules)
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(url_input)
        layout.addWidget(date_input)
        layout.addWidget(capsule_list)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        row.addWidget(open_due_btn)
        row.addStretch()
        row.addWidget(close_btn)
        layout.addLayout(row)
        dialog.setLayout(layout)
        dialog.exec_()

    def save_time_capsule(self):
        try:
            with open(self.time_capsule_file, "w", encoding="utf-8") as f:
                json.dump(self.time_capsule_data, f, indent=4)
        except Exception as e:
            QMessageBox.warning(self, "Time Capsule Save Error", f"Could not save time capsule: {e}")

    def build_time_capsule_item(self, capsule):
        item = QListWidgetItem(f"{capsule['unlock_date']}\n{capsule['url']}")
        item.setData(Qt.UserRole, capsule)
        item.setForeground(QColor("#edf4ff"))
        item.setSizeHint(QSize(0, 52))
        return item

    def save_capsule(self, url, date, dialog=None, capsule_list=None):
        url = (url or "").strip()
        date = (date or "").strip()
        if not url or not date:
            QMessageBox.warning(self, "Time Capsule", "Please enter both URL and unlock date.")
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        from datetime import datetime
        try:
            unlock_date = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            QMessageBox.warning(self, "Time Capsule", "Date must be in YYYY-MM-DD format.")
            return
        if unlock_date <= datetime.now():
            QMessageBox.warning(self, "Time Capsule", "Unlock date must be in the future.")
            return
        capsule = {"url": url, "unlock_date": date}
        self.time_capsule_data.append(capsule)
        self.save_time_capsule()
        if capsule_list is not None:
            if capsule_list.count() == 1 and not capsule_list.item(0).flags():
                capsule_list.clear()
            capsule_list.addItem(self.build_time_capsule_item(capsule))
        if dialog is not None:
            self.show_runtime_status(f"Time capsule saved for {date}", 2500)

    def remove_selected_capsule(self, list_widget):
        item = list_widget.currentItem()
        if item is None or not item.data(Qt.UserRole):
            return
        capsule = item.data(Qt.UserRole)
        self.time_capsule_data = [c for c in self.time_capsule_data if c != capsule]
        self.save_time_capsule()
        list_widget.takeItem(list_widget.row(item))
        if list_widget.count() == 0:
            empty_item = QListWidgetItem("No saved capsules yet.\nAdd a page and a future unlock date.")
            empty_item.setFlags(Qt.NoItemFlags)
            empty_item.setForeground(QColor("#94abd1"))
            empty_item.setSizeHint(QSize(0, 52))
            list_widget.addItem(empty_item)

    def hide_time_capsule(self):
        self.time_capsule_enabled = False
        self.save_browser_settings()

    def get_soundscape_files(self):
        audio_extensions = ['*.mp3', '*.wav', '*.ogg']
        audio_files = []
        for ext in audio_extensions:
            audio_files.extend(glob.glob(os.path.join(self.music_folder, ext)))
        return audio_files

    def toggle_soundscape(self, state):
        self.soundscape_enabled = bool(state)
        self.save_browser_settings()
        if not self.soundscape_enabled:
            self.soundscape_timer.stop()
            self.soundscape_paused_for_browser_audio = False
            try:
                if pygame and pygame.mixer.get_init() and pygame.mixer.music.get_busy():
                    pygame.mixer.music.stop()
            except Exception:
                pass
            self.show_runtime_status("Soundscape disabled")
            return
        if pygame is None:
            self.soundscape_enabled = False
            self.show_runtime_status("Soundscape unavailable: pygame is not installed", 3500)
            return
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)
        except Exception as e:
            self.soundscape_enabled = False
            self.show_runtime_status(f"Soundscape unavailable: {e}", 3500)
            return
        audio_files = self.get_soundscape_files()
        if not audio_files:
            self.soundscape_enabled = False
            self.soundscape_timer.stop()
            self.current_music_track = None
            self.soundscape_paused_for_browser_audio = False
            QMessageBox.information(self, "Soundscape", f"No music found in:\n{self.music_folder}")
            return
        self.soundscape_timer.start(15000)
        self.update_soundscape()

    def update_soundscape(self):
        if not self.soundscape_enabled or pygame is None:
            return
        if getattr(self, "browser_audio_active", False):
            return
        try:
            audio_files = self.get_soundscape_files()
            if not audio_files:
                self.soundscape_timer.stop()
                self.soundscape_enabled = False
                self.soundscape_paused_for_browser_audio = False
                return
            if pygame.mixer.music.get_busy():
                return
            choices = [track for track in audio_files if track != self.current_music_track] or audio_files
            new_track = random.choice(choices)
            pygame.mixer.music.load(new_track)
            pygame.mixer.music.set_volume(1.0)
            pygame.mixer.music.play(loops=0)
            self.current_music_track = new_track
            QTimer.singleShot(250, self._verify_soundscape_playback)
            self.show_runtime_status(f"Soundscape: {os.path.basename(new_track)}", 8000)
        except Exception as e:
            print(f"Soundscape error: {e}")
            self.soundscape_timer.stop()
            self.soundscape_enabled = False
            self.current_music_track = None
            self.soundscape_paused_for_browser_audio = False
            try:
                if pygame and pygame.mixer.get_init():
                    pygame.mixer.music.stop()
            except Exception:
                pass
            self.show_runtime_status(f"Soundscape failed: {e}", 4000)

    def _verify_soundscape_playback(self):
        try:
            if not self.soundscape_enabled or pygame is None or not pygame.mixer.get_init():
                return
            if not pygame.mixer.music.get_busy():
                self.show_runtime_status("Soundscape loaded but playback did not start", 6000)
        except Exception as e:
            print(f"Soundscape verification error: {e}")

    def open_settings_window(self):
        if not self.use_internal_settings_page:
            self.open_legacy_settings_dialog()
            return
        section = getattr(self, "current_settings_section", "profiles")
        try:
            browser = self.current_browser()
            use_new_tab = True
            if browser is not None:
                current_url = browser.url().toString().lower()
                if "aurora.settings" in current_url or current_url.startswith("aurora://settings") or current_url.startswith("aurora:settings"):
                    use_new_tab = False
            if browser is None or use_new_tab:
                browser = BrowserTab(self.history_manager, self.download_manager, qprofile=self.qprofile, initial_url=None)
                self.bind_tab_signals(browser)
                index = self.tab_widget.addTab(browser, "Settings")
                self.tab_widget.setCurrentIndex(index)
            self.render_settings_page(browser, section)
        except Exception:
            self.open_legacy_settings_dialog()

    def open_settings_window_action(self):
        try:
            self.show_runtime_status("Opening settings...", 1500)
            section = getattr(self, "current_settings_section", "profiles")
            self.current_settings_section = section
            browser = self.current_browser()
            use_new_tab = True
            if browser is not None:
                current_url = browser.url().toString().lower()
                if "aurora.settings" in current_url or current_url.startswith("aurora://settings") or current_url.startswith("aurora:settings"):
                    use_new_tab = False
            if browser is None or use_new_tab:
                browser = BrowserTab(self.history_manager, self.download_manager, qprofile=self.qprofile, initial_url=None)
                self.bind_tab_signals(browser)
                index = self.tab_widget.addTab(browser, "Settings")
                self.tab_widget.setCurrentIndex(index)
            if self.use_internal_settings_page:
                self.render_settings_page(browser, section)
            else:
                self.open_legacy_settings_dialog()
        except Exception as exc:
            QMessageBox.warning(self, "Settings", f"Failed to open settings: {exc}")

    def select_music_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Music Folder", self.music_folder)
        if folder:
            self.music_folder = folder
            self.save_browser_settings()
            print(f"Music folder set to: {self.music_folder}")
            audio_count = len(self.get_soundscape_files())
            self.show_runtime_status(f"Music folder updated: {audio_count} track(s) found", 3000)
            if self.soundscape_enabled:
                self.update_soundscape()

def launch_browser(settings_variant=None):
    _install_debug_hooks()
    configure_windows_app_id()
    app = QApplication(sys.argv)
    app_icon_path = resolve_app_resource("Browser Icon 1.ico")
    app_icon = QIcon(app_icon_path) if os.path.exists(app_icon_path) else QIcon()
    if app_icon.isNull():
        app_icon = QIcon(sys.executable)
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    profile_file = os.path.join(os.path.expanduser("~"), "profile.txt")
    current_profile = "default"
    if os.path.exists(profile_file):
        try:
            with open(profile_file, "r") as f:
                current_profile = f.read().strip() or "default"
        except Exception:
            current_profile = "default"
    skip_picker = False
    forced_profile = None
    for idx, arg in enumerate(sys.argv[1:]):
        if arg == "--skip-profile-picker":
            skip_picker = True
        elif arg.startswith("--profile="):
            forced_profile = arg.split("=", 1)[1].strip()
        elif arg == "--profile" and idx + 2 <= len(sys.argv[1:]):
            forced_profile = sys.argv[1:][idx + 1].strip()
    if forced_profile:
        current_profile = forced_profile
        skip_picker = True
    if not skip_picker:
        profile_picker = ProfilePickerDialog(discover_profiles(), current_profile)
        result = profile_picker.exec_()
        if int(result) != int(QDialog.Accepted):
            sys.exit(0)
        selected_profile = profile_picker.selected_profile()
        if not selected_profile:
            QMessageBox.warning(None, "Profile", "Please enter a valid profile name.")
            sys.exit(0)
        current_profile = selected_profile
    with open(profile_file, "w") as f:
        f.write(current_profile)
    variant = settings_variant or os.environ.get("AURORA_SETTINGS_VARIANT", "modern").strip().lower() or "modern"
    browser = MyBrowser(settings_variant=variant)
    browser.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    launch_browser()
