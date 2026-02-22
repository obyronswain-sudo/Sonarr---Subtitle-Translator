try:
    from flask import Flask, request, jsonify
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    Flask = None
    request = None
    jsonify = None

import threading
import json
import os
import secrets
from pathlib import Path
from .processor import VideoProcessor
from .logger import Logger


def _generate_webhook_token() -> str:
    """Generate a cryptographically secure random token."""
    return secrets.token_hex(32)


class WebhookServer:
    def __init__(self, port=8080, keys=None, api_type='Ollama', bind_host='127.0.0.1'):
        if not FLASK_AVAILABLE:
            raise ImportError("Flask not installed. Install with: pip install flask")

        self.port = port
        self.keys = keys or {}
        self.api_type = api_type
        # Default to localhost only — set bind_host='0.0.0.0' only if Sonarr runs on another machine
        self.bind_host = bind_host
        self.app = Flask(__name__)
        self.server_thread = None
        self.logger = Logger()

        # Load or generate webhook token for request authentication
        self.webhook_token = self.keys.get('webhook_token') or _generate_webhook_token()

        # Allowed media root directories (path traversal protection)
        # Populated from the Sonarr library paths in keys, if available
        self._allowed_roots: list[str] = []
        sonarr_root = self.keys.get('sonarr_root_path') or self.keys.get('sonarr_url', '')
        if sonarr_root and os.path.isdir(sonarr_root):
            self._allowed_roots.append(str(Path(sonarr_root).resolve()))

        self.setup_routes()

    # ── Security helpers ──────────────────────────────────────────────────────

    def _verify_token(self) -> bool:
        """Verify the X-Sonarr-Token header matches the configured token."""
        incoming = request.headers.get('X-Sonarr-Token', '')
        # Use secrets.compare_digest to prevent timing attacks
        return secrets.compare_digest(incoming, self.webhook_token)

    def _is_path_allowed(self, file_path: str) -> bool:
        """
        Validate that the given path is inside an allowed media root.
        If no allowed roots are configured, allow any absolute path that exists
        (best-effort protection — configure sonarr_root_path for full enforcement).
        """
        try:
            resolved = Path(file_path).resolve()
        except (OSError, ValueError):
            return False

        if not self._allowed_roots:
            # No roots configured — accept any existing absolute path
            return resolved.is_absolute()

        for root in self._allowed_roots:
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    # ── Routes ────────────────────────────────────────────────────────────────

    def setup_routes(self):
        @self.app.route('/webhook/sonarr', methods=['POST'])
        def sonarr_webhook():
            try:
                # Token authentication — reject if token is set and doesn't match
                if not self._verify_token():
                    self.logger.log('warning', 'Webhook: unauthorized request (invalid token)')
                    return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

                self.logger.log('info', 'Webhook request received')

                try:
                    data = request.get_json(silent=True) or {}
                except Exception as json_error:
                    self.logger.log('warning', f'Webhook JSON parse error: {json_error}')
                    data = {}

                # Handle test webhook from Sonarr
                if not data or data.get('eventType') == 'Test' or not data.get('eventType'):
                    self.logger.log('info', 'Webhook test received from Sonarr')
                    return jsonify({'status': 'success', 'message': 'Webhook test successful'}), 200

                if data.get('eventType') == 'Download':
                    series_info = data.get('series', {})
                    episode_file = data.get('episodeFile', {})

                    raw_path = episode_file.get('path', '')
                    if not raw_path:
                        return jsonify({'status': 'ignored', 'message': 'No file path'}), 200

                    # Path traversal protection
                    if not self._is_path_allowed(raw_path):
                        self.logger.log('warning', f'Webhook: rejected path outside allowed roots: {raw_path}')
                        return jsonify({'status': 'error', 'message': 'Path not allowed'}), 403

                    file_path = Path(raw_path)
                    series_path = file_path.parent

                    self.logger.log('info', f"Webhook: new episode downloaded — {series_info.get('title')}")

                    threading.Thread(
                        target=self.process_series_async,
                        args=(str(series_path), series_info.get('title', 'Unknown')),
                        daemon=True,
                    ).start()

                    return jsonify({'status': 'accepted', 'message': 'Processing started'}), 200

                return jsonify({'status': 'ignored', 'message': 'Event not relevant'}), 200

            except Exception as e:
                self.logger.log('error', f'Webhook error: {e}')
                return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

        @self.app.route('/webhook/status', methods=['GET'])
        def webhook_status():
            return jsonify({
                'status': 'running',
                'port': self.port,
                'api_type': self.api_type,
                'endpoints': ['/webhook/sonarr', '/webhook/status', '/health'],
            })

        @self.app.route('/health', methods=['GET'])
        def health_check():
            return jsonify({'status': 'healthy', 'server': 'webhook'}), 200

        @self.app.route('/', methods=['GET'])
        def root():
            return jsonify({
                'message': 'Sonarr Subtitle Translator Webhook Server',
                'status': 'running',
                'endpoints': {
                    'webhook': '/webhook/sonarr',
                    'status': '/webhook/status',
                    'health': '/health',
                },
            })

    # ── Processing ────────────────────────────────────────────────────────────

    def process_series_async(self, series_path: str, series_title: str):
        try:
            self.logger.log('info', f'Auto-processing: {series_title}')
            processor = VideoProcessor(
                series_path,
                self.keys,
                self.logger,
                api_type=self.api_type,
            )
            processor.process_all()
            self.logger.log('info', f'Auto-processing completed: {series_title}')
        except Exception as e:
            self.logger.log('error', f'Auto-processing failed for {series_title}: {e}')

    # ── Server lifecycle ──────────────────────────────────────────────────────

    def start(self):
        if self.server_thread and self.server_thread.is_alive():
            return

        self.server_thread = threading.Thread(
            target=self._run_server,
            daemon=True,
        )
        self.server_thread.start()

        import time
        time.sleep(0.5)
        self.logger.log('info', f'Webhook server started on {self.bind_host}:{self.port}')

    def _run_server(self):
        try:
            self.logger.log('info', f'Starting Flask server on {self.bind_host}:{self.port}')
            self.app.run(host=self.bind_host, port=self.port, debug=False, use_reloader=False)
        except Exception as e:
            self.logger.log('error', f'Flask server error: {e}')

    def stop(self):
        self.logger.log('info', 'Webhook server stopping')


class WebhookManager:
    def __init__(self):
        self.server = None

    def start_webhook(self, port=8080, keys=None, api_type='Ollama'):
        if not FLASK_AVAILABLE:
            return None

        if self.server:
            self.server.stop()

        try:
            self.server = WebhookServer(port, keys, api_type)
            self.server.start()
            return self.server
        except ImportError:
            return None

    def stop_webhook(self):
        if self.server:
            self.server.stop()
            self.server = None

    def get_webhook_url(self, port=8080):
        return f"http://localhost:{port}/webhook/sonarr"

    def get_webhook_token(self) -> str:
        """Return the current webhook token for display in Settings."""
        if self.server:
            return self.server.webhook_token
        return ''

    def is_available(self):
        return FLASK_AVAILABLE
