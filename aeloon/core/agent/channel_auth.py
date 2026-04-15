"""Channel authentication utilities for WeChat and Feishu."""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from aeloon.core.bus.events import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from aeloon.channels.manager import ChannelManager


class WeChatAuthManager:
    """Manages WeChat QR code authentication flow."""

    def __init__(self) -> None:
        self._login_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._login_status: dict[tuple[str, str], dict] = {}

    @staticmethod
    def set_enabled(enabled: bool) -> None:
        """Persist ``channels.wechat.enabled`` in config.json."""
        from aeloon.core.config.loader import get_config_path

        config_path = get_config_path()
        if not config_path.exists():
            return

        try:
            with open(config_path, encoding="utf-8") as f:
                data = json.load(f)
            channels = data.setdefault("channels", {})
            wechat = channels.setdefault("wechat", {})
            wechat["enabled"] = enabled
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.warning("Failed to persist wechat.enabled={}: {}", enabled, exc)

    def has_pending_login(self, request_channel: str, request_chat_id: str) -> bool:
        """Check if there's a pending WeChat login for the given channel/chat."""
        key = (request_channel, request_chat_id)
        return key in self._login_tasks and not self._login_tasks[key].done()

    def get_login_status(self, request_channel: str, request_chat_id: str) -> dict | None:
        """Get the status of a pending WeChat login."""
        key = (request_channel, request_chat_id)
        return self._login_status.get(key)

    def update_login_status(
        self,
        request_channel: str,
        request_chat_id: str,
        updates: dict,
    ) -> None:
        """Merge updates into the stored WeChat login status."""
        key = (request_channel, request_chat_id)
        if key not in self._login_status:
            self._login_status[key] = {}
        self._login_status[key].update(updates)

    def clear_login_status(self, request_channel: str, request_chat_id: str) -> None:
        """Remove stored WeChat login status for one request target."""
        self._login_status.pop((request_channel, request_chat_id), None)

    def register_login_task(
        self,
        request_channel: str,
        request_chat_id: str,
        task: asyncio.Task,
        status: dict,
    ) -> None:
        """Register a WeChat login task and its status."""
        key = (request_channel, request_chat_id)
        self._login_tasks[key] = task
        self._login_status[key] = status

        # Clean up when task completes
        def _cleanup(t: asyncio.Task) -> None:
            if key in self._login_tasks and self._login_tasks[key] is t:
                del self._login_tasks[key]
            # Keep status for a while to allow status queries

        task.add_done_callback(_cleanup)

    def cancel_login(self, request_channel: str, request_chat_id: str) -> bool:
        """Cancel a pending WeChat login task."""
        key = (request_channel, request_chat_id)
        task = self._login_tasks.get(key)
        if task and not task.done():
            task.cancel()
            self.clear_login_status(request_channel, request_chat_id)
            return True
        return False

    async def cancel_all_logins(self) -> int:
        """Cancel all pending WeChat login tasks. Returns count cancelled."""
        count = 0
        for key, task in list(self._login_tasks.items()):
            if not task.done():
                task.cancel()
                count += 1
        self._login_tasks.clear()
        self._login_status.clear()
        return count

    @staticmethod
    def render_ascii_qrcode(data: str) -> str | None:
        """Render QR payload as ASCII art using the ``qrcode`` library."""
        if not data:
            return None
        try:
            import qrcode

            qr = qrcode.QRCode(border=1)
            qr.add_data(data)
            qr.make(fit=True)
            from io import StringIO

            buf = StringIO()
            qr.print_ascii(out=buf, invert=True)
            return buf.getvalue().rstrip()
        except ImportError:
            return None


class FeishuAuthManager:
    """Manages Feishu app credential authentication."""

    def __init__(self, channel_manager: ChannelManager | None = None) -> None:
        self._channel_manager = channel_manager
        self._login_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._login_status: dict[tuple[str, str], dict[str, Any]] = {}

    def set_channel_manager(self, channel_manager: ChannelManager | None) -> None:
        """Set the channel manager reference."""
        self._channel_manager = channel_manager

    def has_pending_login(self, request_channel: str, request_chat_id: str) -> bool:
        """Check if a Feishu QR login is still running."""
        key = (request_channel, request_chat_id)
        return key in self._login_tasks and not self._login_tasks[key].done()

    def get_login_status(self, request_channel: str, request_chat_id: str) -> dict[str, Any] | None:
        """Return last known Feishu QR login status."""
        return self._login_status.get((request_channel, request_chat_id))

    def update_login_status(
        self,
        request_channel: str,
        request_chat_id: str,
        updates: dict[str, Any],
    ) -> None:
        """Merge updates into stored Feishu login status."""
        key = (request_channel, request_chat_id)
        if key not in self._login_status:
            self._login_status[key] = {}
        self._login_status[key].update(updates)

    def clear_login_status(self, request_channel: str, request_chat_id: str) -> None:
        """Clear stored Feishu login status."""
        self._login_status.pop((request_channel, request_chat_id), None)

    def register_login_task(
        self,
        request_channel: str,
        request_chat_id: str,
        task: asyncio.Task,
        status: dict[str, Any],
    ) -> None:
        """Register a Feishu login task and initial status."""
        key = (request_channel, request_chat_id)
        self._login_tasks[key] = task
        self._login_status[key] = status

        def _cleanup(done_task: asyncio.Task) -> None:
            if key in self._login_tasks and self._login_tasks[key] is done_task:
                del self._login_tasks[key]

        task.add_done_callback(_cleanup)

    def cancel_login(self, request_channel: str, request_chat_id: str) -> bool:
        """Cancel a running Feishu login task."""
        key = (request_channel, request_chat_id)
        task = self._login_tasks.get(key)
        if task and not task.done():
            task.cancel()
            self.clear_login_status(request_channel, request_chat_id)
            return True
        return False

    def has_credentials(self) -> bool:
        """Check if Feishu credentials are configured."""
        config = self.get_config()
        if isinstance(config, dict):
            return bool(config.get("appId", ""))
        if config:
            return bool(getattr(config, "app_id", ""))
        return False

    def get_config(self) -> Any:
        """Get Feishu configuration from channel manager."""
        if self._channel_manager is None:
            return None
        return self._channel_manager._get_channel_config("feishu")

    @staticmethod
    def get_app_id(config: Any) -> str:
        """Return Feishu app id from dict or model config."""
        if isinstance(config, dict):
            return str(config.get("appId", "") or "")
        return getattr(config, "app_id", "") if config else ""

    @staticmethod
    async def validate_credentials(app_id: str, app_secret: str) -> bool:
        """Validate Feishu credentials by calling the bot info API."""
        try:
            import lark_oapi as lark
            from lark_oapi.api.bot.v3 import GetBotInfoRequest

            client = (
                lark.Client.builder()
                .app_id(app_id)
                .app_secret(app_secret)
                .log_level(lark.LogLevel.ERROR)
                .build()
            )

            request = GetBotInfoRequest.builder().build()
            response = client.bot.v3.info.get(request)

            return response.success()
        except ImportError:
            # lark_oapi not installed, skip validation
            logger.warning("lark_oapi not installed, skipping Feishu credential validation")
            return True
        except Exception as exc:
            logger.warning("Feishu credential validation failed: {}", exc)
            return False

    @staticmethod
    def set_credentials(app_id: str, app_secret: str, enabled: bool = True) -> None:
        """Persist Feishu credentials in config.json."""
        from aeloon.core.config.loader import get_config_path

        config_path = get_config_path()
        if not config_path.exists():
            return

        try:
            with open(config_path, encoding="utf-8") as f:
                data = json.load(f)
            channels = data.setdefault("channels", {})
            feishu = channels.setdefault("feishu", {})
            feishu["enabled"] = enabled
            feishu["appId"] = app_id
            feishu["appSecret"] = app_secret
            feishu.pop("app_id", None)
            feishu.pop("app_secret", None)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.warning("Failed to persist Feishu credentials: {}", exc)

    def sync_runtime_config(self, app_id: str, app_secret: str, enabled: bool) -> None:
        """Keep in-memory Feishu config aligned with persisted credentials."""
        if self._channel_manager is None:
            return

        channels = getattr(self._channel_manager.config, "channels", None)
        if channels is None:
            return

        section = getattr(channels, "feishu", None)
        if section is None:
            return

        if isinstance(section, dict):
            section["enabled"] = enabled
            section["appId"] = app_id
            section["appSecret"] = app_secret
            section["app_id"] = app_id
            section["app_secret"] = app_secret
            return

        setattr(section, "enabled", enabled)
        setattr(section, "app_id", app_id)
        setattr(section, "app_secret", app_secret)


class GatewayManager:
    """Manages the aeloon gateway background process."""

    @staticmethod
    def _looks_like_gateway_command(command_line: str) -> bool:
        """Return True when a process command line looks like ``aeloon gateway``."""
        normalized = (
            re.sub(r"\s+", " ", command_line.replace('"', " ").replace("'", " ")).strip().lower()
        )
        return bool(
            re.search(r"(^|\s)-m\s+aeloon\s+gateway(\s|$)", normalized)
            or re.search(r"(^|\s)aeloon(?:\.exe)?\s+gateway(\s|$)", normalized)
        )

    @staticmethod
    def _decode_process_output(data: bytes) -> str:
        """Decode subprocess output across different platform encodings."""
        for encoding in ("utf-8", "utf-8-sig", "gb18030", sys.getdefaultencoding()):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    @classmethod
    def _find_gateway_pids(cls) -> list[int]:
        """Return PIDs for running gateway processes."""
        try:
            if sys.platform == "win32":
                result = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
                    ],
                    capture_output=True,
                    timeout=5,
                )
                stdout = cls._decode_process_output(result.stdout or b"")
                if result.returncode != 0 or not stdout.strip():
                    return []
                payload = json.loads(stdout)
                rows = payload if isinstance(payload, list) else [payload]
                pids: list[int] = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    command_line = str(row.get("CommandLine") or "")
                    process_id = row.get("ProcessId")
                    if not command_line or process_id is None:
                        continue
                    if cls._looks_like_gateway_command(command_line):
                        pids.append(int(process_id))
                return pids

            result = subprocess.run(
                ["ps", "-ax", "-o", "pid=", "-o", "command="],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return []
            pids = []
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                parts = stripped.split(None, 1)
                if len(parts) != 2:
                    continue
                pid_text, command_line = parts
                if pid_text.isdigit() and cls._looks_like_gateway_command(command_line):
                    pids.append(int(pid_text))
            return pids
        except Exception:
            return []

    @staticmethod
    def is_current_process_gateway() -> bool:
        """Return True when current process is running ``aeloon gateway``."""
        return any(str(arg).strip().lower() == "gateway" for arg in sys.argv[1:])

    @classmethod
    def is_running(cls) -> bool:
        """Check if an ``aeloon gateway`` process is currently running."""
        return bool(cls._find_gateway_pids())

    @classmethod
    def stop(cls, *, exclude_current: bool = False) -> bool:
        """Kill any running gateway process and return True if anything was stopped."""
        current_pid = os.getpid()
        killed = False
        for pid in cls._find_gateway_pids():
            if exclude_current and pid == current_pid:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                killed = True
            except (ProcessLookupError, PermissionError):
                pass
        return killed

    @classmethod
    def start_background(cls) -> bool:
        """Start ``aeloon gateway`` as a detached background process."""
        if cls.is_current_process_gateway() or cls.is_running():
            return True
        try:
            cmd = [sys.executable, "-m", "aeloon", "gateway"]
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except Exception as exc:
            logger.warning("Failed to start gateway: {}", exc)
            return False


class ChannelAuthHelper:
    """Helper class that combines all channel auth managers."""

    def __init__(self, channel_manager: ChannelManager | None = None) -> None:
        self.wechat = WeChatAuthManager()
        self.feishu = FeishuAuthManager(channel_manager)
        self.gateway = GatewayManager()
        self._channel_manager = channel_manager

    def set_channel_manager(self, channel_manager: ChannelManager | None) -> None:
        """Update channel manager reference in all managers."""
        self._channel_manager = channel_manager
        self.feishu.set_channel_manager(channel_manager)

    def _wechat_accounts_dir(self) -> str | None:
        """Resolve the configured accounts directory for the WeChat channel, if any."""
        if self._channel_manager is None:
            return None
        channel = self._channel_manager.get_channel("wechat")
        if channel is not None:
            return getattr(channel.config, "accounts_dir", None)
        config = self._channel_manager._get_channel_config("wechat")
        if config is not None:
            if isinstance(config, dict):
                return config.get("accounts_dir")
            return getattr(config, "accounts_dir", None)
        return None

    async def handle_wechat_command(
        self,
        msg: InboundMessage,
        args: list[str],
        agent_loop: Any,
    ) -> OutboundMessage:
        """Handle /wechat slash command."""
        if not args:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Usage: /wechat login|logout|status",
            )

        subcommand = args[0].lower()

        if subcommand == "login":
            return await self._handle_wechat_login(msg, agent_loop)
        elif subcommand == "logout":
            return await self._handle_wechat_logout(msg)
        elif subcommand == "status":
            return await self._handle_wechat_status(msg)
        else:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"Unknown subcommand: {subcommand}. Use: /wechat login|logout|status",
            )

    async def _handle_wechat_login(self, msg: InboundMessage, agent_loop: Any) -> OutboundMessage:
        """Handle /wechat login command."""
        from aeloon.channels.wechat_ilink.auth import (
            download_qr_image,
            fetch_qrcode,
            get_qr_code_dir,
            has_valid_credentials,
            poll_qrcode_until_confirmed,
            save_credentials,
        )

        accounts_dir = self._wechat_accounts_dir()

        # Check if already logged in
        if has_valid_credentials(accounts_dir):
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Already logged in to WeChat. Use /wechat logout first if you want to switch accounts.",
            )

        # Check if there's already a pending login for this channel/chat
        if self.wechat.has_pending_login(msg.channel, msg.chat_id):
            status = self.wechat.get_login_status(msg.channel, msg.chat_id)
            qr_status = status.get("status", "pending") if status else "pending"
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"Login already in progress. Current status: {qr_status}. Please scan the previously sent QR code.",
            )

        try:
            # Step 1: Fetch QR code
            qr_data = await fetch_qrcode()
            qrcode_id = qr_data["qrcode"]
            qrcode_img_content = qr_data["qrcode_img_content"]

            # Step 2: Download QR code image for channels that support media
            qr_image_path: Path | None = None
            try:
                qr_dir = get_qr_code_dir()
                qr_image_path = qr_dir / "wechat_login.png"
                await download_qr_image(qrcode_img_content, qr_image_path)
            except Exception:
                logger.warning("Failed to download QR image, ASCII fallback will be used")
                qr_image_path = None

            # Step 3: Start background polling task
            async def _login_task():
                try:
                    self.wechat.update_login_status(
                        msg.channel,
                        msg.chat_id,
                        {"status": "waiting"},
                    )

                    # Poll until confirmed or error
                    credentials = await poll_qrcode_until_confirmed(qrcode_id)

                    # Save credentials
                    save_credentials(credentials, accounts_dir)

                    # Update status
                    self.wechat.update_login_status(
                        msg.channel,
                        msg.chat_id,
                        {
                            "status": "confirmed",
                            "ilink_bot_id": credentials.ilink_bot_id,
                        },
                    )

                    # Notify user of success
                    await agent_loop.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=f"✅ WeChat login successful! Bot ID: {credentials.ilink_bot_id}",
                        )
                    )

                    # Persist wechat.enabled = true in config
                    self.wechat.set_enabled(True)

                    # Start or reload wechat channel
                    if self._channel_manager:
                        reloaded = await self._channel_manager.reload_channel("wechat")
                        await agent_loop.bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content=(
                                    "WeChat channel is starting with the new credentials."
                                    if reloaded
                                    else "WeChat credentials saved. The wechat channel is not enabled in config."
                                ),
                            )
                        )

                    # Ensure gateway is running
                    if not self.gateway.is_running():
                        self.gateway.start_background()

                except asyncio.CancelledError:
                    await agent_loop.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content="WeChat login was cancelled.",
                        )
                    )
                    raise
                except TimeoutError:
                    self.wechat.update_login_status(
                        msg.channel, msg.chat_id, {"status": "timed_out"}
                    )
                    await agent_loop.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content="❌ WeChat login timed out. Please try again with /wechat login",
                        )
                    )
                except Exception as e:
                    logger.exception("WeChat login failed")
                    self.wechat.update_login_status(
                        msg.channel,
                        msg.chat_id,
                        {"status": "failed", "error": str(e)},
                    )
                    await agent_loop.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=f"❌ WeChat login failed: {e}",
                        )
                    )

            # Create and register the task
            task = asyncio.create_task(_login_task())
            self.wechat.register_login_task(
                msg.channel,
                msg.chat_id,
                task,
                {
                    "qrcode": qrcode_id,
                    "status": "pending",
                    "started_at": asyncio.get_running_loop().time(),
                },
            )

            # Build response — PNG image (primary) + ASCII QR (fallback)
            qr_ascii = self.wechat.render_ascii_qrcode(qrcode_img_content)
            content_parts = ["Please scan this QR code with WeChat within 5 minutes."]
            if msg.channel == "cli" and qr_image_path and qr_image_path.exists():
                content_parts.append(f"\nImage saved to: {qr_image_path}")
            if qr_ascii:
                content_parts.append("")
                content_parts.append(f"```\n{qr_ascii}\n```")

            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="\n".join(content_parts),
                media=[str(qr_image_path)] if qr_image_path and qr_image_path.exists() else [],
            )

        except Exception as e:
            logger.exception("Failed to initiate WeChat login")
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"Failed to initiate WeChat login: {e}",
            )

    async def _handle_wechat_logout(self, msg: InboundMessage) -> OutboundMessage:
        """Handle /wechat logout command."""
        from aeloon.channels.wechat_ilink.auth import (
            has_valid_credentials,
            remove_all_credentials,
        )

        accounts_dir = self._wechat_accounts_dir()

        # Cancel any pending login
        self.wechat.cancel_login(msg.channel, msg.chat_id)
        self.wechat.clear_login_status(msg.channel, msg.chat_id)

        # Check if logged in
        if not has_valid_credentials(accounts_dir):
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Not currently logged in to WeChat.",
            )

        # Remove credentials
        count = remove_all_credentials(accounts_dir)

        # Persist wechat.enabled = false in config
        self.wechat.set_enabled(False)

        # Stop wechat channel
        if self._channel_manager:
            await self._channel_manager.stop_channel("wechat")

        # Stop the gateway process
        gateway_killed = self.gateway.stop(
            exclude_current=self.gateway.is_current_process_gateway()
        )

        content = f"✅ WeChat logged out. Removed {count} credential file(s)."
        if gateway_killed:
            content += "\nGateway process stopped."

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
        )

    async def _handle_wechat_status(self, msg: InboundMessage) -> OutboundMessage:
        """Handle /wechat status command."""
        from aeloon.channels.wechat_ilink.auth import (
            get_first_credential,
            has_valid_credentials,
            load_all_credentials,
        )

        accounts_dir = self._wechat_accounts_dir()

        lines = ["📱 WeChat Status:"]

        # Check for login task status
        pending_status = self.wechat.get_login_status(msg.channel, msg.chat_id)
        if pending_status and self.wechat.has_pending_login(msg.channel, msg.chat_id):
            lines.append("\n🔄 Login in progress:")
            lines.append(f"  QR Code: {pending_status.get('qrcode', 'N/A')}")
            lines.append(f"  Status: {pending_status.get('status', 'unknown')}")
        elif pending_status:
            lines.append("\n📋 Last login attempt:")
            lines.append(f"  Status: {pending_status.get('status', 'unknown')}")
            if "error" in pending_status:
                lines.append(f"  Error: {pending_status['error']}")

        # Check credentials
        if has_valid_credentials(accounts_dir):
            creds = get_first_credential(accounts_dir)
            all_creds = load_all_credentials(accounts_dir)
            lines.append("\n✅ Logged in:")
            lines.append(f"  Bot ID: {creds.ilink_bot_id if creds else 'N/A'}")
            lines.append(f"  Accounts: {len(all_creds)}")

            # Check channel status
            if self._channel_manager:
                channel = self._channel_manager.get_channel("wechat")
                if channel:
                    lines.append(f"  Channel: {'Running' if channel.is_running else 'Stopped'}")
                else:
                    lines.append("  Channel: Not configured")
        else:
            lines.append("\n❌ Not logged in")

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="\n".join(lines),
        )

    async def handle_feishu_command(
        self,
        msg: InboundMessage,
        args: list[str],
        agent_loop: Any | None = None,
    ) -> OutboundMessage:
        """Handle /feishu slash command."""
        if not args:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Usage: /feishu login [<app_id> <app_secret>]|logout|status",
            )

        subcommand = args[0].lower()

        if subcommand == "login":
            return await self._handle_feishu_login(msg, args[1:], agent_loop)
        elif subcommand == "logout":
            return await self._handle_feishu_logout(msg)
        elif subcommand == "status":
            return await self._handle_feishu_status(msg)
        else:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"Unknown subcommand: {subcommand}. Use: /feishu login [<app_id> <app_secret>]|logout|status",
            )

    async def _handle_feishu_login(
        self,
        msg: InboundMessage,
        args: list[str],
        agent_loop: Any | None = None,
    ) -> OutboundMessage:
        """Handle /feishu login command with app_id and app_secret."""
        # Check if already logged in
        if self.feishu.has_credentials():
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Already logged in to Feishu. Use `/feishu logout` first if you want to switch accounts.",
            )

        if not args:
            return await self._handle_feishu_qr_login(msg, agent_loop)

        if len(args) < 2:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=(
                    "Usage: /feishu login [<app_id> <app_secret>]\n\n"
                    "Use `/feishu login` for QR onboarding, or pass App ID / App Secret manually.\n"
                    "Get manual credentials from https://open.feishu.cn/app"
                ),
            )

        app_id = args[0].strip()
        app_secret = args[1].strip()

        if not app_id or not app_secret:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="App ID and App Secret cannot be empty.",
            )

        # Validate credentials by attempting to connect
        try:
            is_valid = await self.feishu.validate_credentials(app_id, app_secret)
            if not is_valid:
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="❌ Failed to validate Feishu credentials. Please check your App ID and App Secret.",
                )
        except Exception as e:
            logger.exception("Feishu credential validation failed")
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"❌ Error validating credentials: {e}",
            )

        # Save credentials to config
        self.feishu.set_credentials(app_id, app_secret, enabled=True)
        self.feishu.sync_runtime_config(app_id, app_secret, enabled=True)

        # Start or reload Feishu channel
        if self._channel_manager:
            reloaded = await self._channel_manager.reload_channel("feishu")
            if reloaded:
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"✅ Feishu login successful!\nApp ID: {app_id}\nChannel is now starting.",
                )
            else:
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"✅ Feishu credentials saved!\nApp ID: {app_id}\n\nNote: Channel could not be started automatically. Please check config.",
                )

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=f"✅ Feishu credentials saved!\nApp ID: {app_id}",
        )

    async def _handle_feishu_qr_login(
        self,
        msg: InboundMessage,
        agent_loop: Any | None,
    ) -> OutboundMessage:
        """Handle `/feishu login` using QR onboarding."""
        from aeloon.channels.feishu_onboard import (
            create_login_session,
            render_ascii_qrcode,
            wait_for_login_confirmation,
        )

        if agent_loop is None:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Feishu QR login requires a live runtime context. Try `/feishu login` from agent or channel CLI.",
            )

        if self.feishu.has_pending_login(msg.channel, msg.chat_id):
            status = self.feishu.get_login_status(msg.channel, msg.chat_id) or {}
            qr_status = status.get("status", "pending")
            verification_url = status.get("verification_url")
            content = (
                f"Login already in progress. Current status: {qr_status}. "
                "Please scan the contextual QR code that was already sent."
            )
            if verification_url:
                content += f"\n\nVerification URL: {verification_url}"
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content)

        try:
            session = await create_login_session()
        except Exception as exc:
            logger.exception("Failed to initiate Feishu login")
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"Failed to initiate Feishu login: {exc}",
            )

        async def _login_task() -> None:
            try:
                self.feishu.update_login_status(
                    msg.channel,
                    msg.chat_id,
                    {
                        "status": "waiting",
                        "verification_url": session.verification_url,
                        "qr_image_path": session.qr_image_path,
                    },
                )
                confirmed = await wait_for_login_confirmation(session)
                self.feishu.set_credentials(confirmed.app_id, confirmed.app_secret, enabled=True)
                self.feishu.sync_runtime_config(
                    confirmed.app_id, confirmed.app_secret, enabled=True
                )
                self.feishu.update_login_status(
                    msg.channel,
                    msg.chat_id,
                    {
                        "status": "confirmed",
                        "app_id": confirmed.app_id,
                        "verification_url": confirmed.verification_url,
                    },
                )
                await agent_loop.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"✅ Feishu login successful!\nApp ID: {confirmed.app_id}",
                    )
                )
                if self._channel_manager:
                    reloaded = await self._channel_manager.reload_channel("feishu")
                    await agent_loop.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=(
                                "Feishu channel is starting with new credentials."
                                if reloaded
                                else "Feishu credentials saved. The feishu channel is not enabled in config."
                            ),
                        )
                    )
                if not self.gateway.is_running():
                    self.gateway.start_background()
            except asyncio.CancelledError:
                await agent_loop.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="Feishu login was cancelled.",
                    )
                )
                raise
            except TimeoutError:
                self.feishu.update_login_status(
                    msg.channel,
                    msg.chat_id,
                    {"status": "timed_out"},
                )
                await agent_loop.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="Feishu login timed out. Please try again with /feishu login",
                    )
                )
            except Exception as exc:
                logger.exception("Feishu login failed")
                self.feishu.update_login_status(
                    msg.channel,
                    msg.chat_id,
                    {"status": "failed", "error": str(exc)},
                )
                await agent_loop.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"Feishu login failed: {exc}",
                    )
                )

        task = asyncio.create_task(_login_task())
        self.feishu.register_login_task(
            msg.channel,
            msg.chat_id,
            task,
            {
                "status": "pending",
                "started_at": asyncio.get_running_loop().time(),
                "verification_url": session.verification_url,
                "qr_image_path": session.qr_image_path,
            },
        )

        qr_ascii = render_ascii_qrcode(session.verification_url)
        content_parts = [
            "Please scan this QR code with Feishu within 10 minutes.",
            f"\nVerification URL: {session.verification_url}",
        ]
        if msg.channel == "cli" and session.qr_image_path:
            content_parts.append(f"\nImage saved to: {session.qr_image_path}")
        if qr_ascii:
            content_parts.append("")
            content_parts.append(f"```\n{qr_ascii}\n```")

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="\n".join(content_parts),
            media=[session.qr_image_path] if session.qr_image_path else [],
        )

    async def _handle_feishu_logout(self, msg: InboundMessage) -> OutboundMessage:
        """Handle /feishu logout command."""
        self.feishu.cancel_login(msg.channel, msg.chat_id)
        self.feishu.clear_login_status(msg.channel, msg.chat_id)

        # Check if logged in
        if not self.feishu.has_credentials():
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Not currently logged in to Feishu.",
            )

        # Get app_id before clearing
        config = self.feishu.get_config()
        app_id = self.feishu.get_app_id(config) or "N/A"

        # Clear credentials
        self.feishu.set_credentials("", "", enabled=False)
        self.feishu.sync_runtime_config("", "", enabled=False)

        # Stop Feishu channel
        if self._channel_manager:
            await self._channel_manager.stop_channel("feishu")

        gateway_killed = self.gateway.stop(
            exclude_current=self.gateway.is_current_process_gateway()
        )

        content = f"✅ Feishu logged out.\nApp ID: {app_id} has been removed."
        if gateway_killed:
            content += "\nBackground gateway process stopped."

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
        )

    async def _handle_feishu_status(self, msg: InboundMessage) -> OutboundMessage:
        """Handle /feishu status command."""
        lines = ["Feishu Status:"]

        pending_status = self.feishu.get_login_status(msg.channel, msg.chat_id)
        if pending_status and self.feishu.has_pending_login(msg.channel, msg.chat_id):
            lines.append("\nLogin in progress:")
            lines.append(f"  Status: {pending_status.get('status', 'unknown')}")
            if pending_status.get("verification_url"):
                lines.append(f"  Verification URL: {pending_status['verification_url']}")
        elif pending_status:
            lines.append("\nLast login attempt:")
            lines.append(f"  Status: {pending_status.get('status', 'unknown')}")
            if pending_status.get("error"):
                lines.append(f"  Error: {pending_status['error']}")

        config = self.feishu.get_config()
        enabled = (
            config.get("enabled", False)
            if isinstance(config, dict)
            else getattr(config, "enabled", False)
            if config
            else False
        )
        app_id = self.feishu.get_app_id(config)
        has_credentials = bool(app_id)

        if has_credentials:
            lines.append("\nConfigured:")
            lines.append(f"  App ID: {app_id}")
            lines.append(f"  Enabled: {enabled}")

            # Check channel status
            if self._channel_manager:
                channel = self._channel_manager.get_channel("feishu")
                if channel:
                    lines.append(f"  Channel: {'Running' if channel.is_running else 'Stopped'}")
                else:
                    lines.append("  Channel: Not loaded (check config)")
        else:
            lines.append("\nNot configured")
            lines.append("\nTo configure, use:")
            lines.append("  `/feishu login`")
            lines.append("  `/feishu login <app_id> <app_secret>`")
            lines.append("\nGet credentials from: https://open.feishu.cn/app")

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="\n".join(lines),
        )
