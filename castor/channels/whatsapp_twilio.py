"""
WhatsApp channel integration via Twilio (legacy).

For the recommended QR-code-based WhatsApp integration, use the default
``whatsapp`` channel (powered by neonize).  This Twilio-based channel is
kept for users who already have a Twilio setup.

Setup:
    1. Create a Twilio account at https://twilio.com
    2. Enable the WhatsApp Sandbox or connect your own number
    3. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER in .env
    4. Point the Twilio webhook to: http://<your-host>:8000/webhooks/whatsapp
"""

import logging
from typing import Callable, Optional

from castor.channels.base import BaseChannel

logger = logging.getLogger("OpenCastor.Channel.WhatsApp.Twilio")

try:
    from twilio.rest import Client as TwilioClient

    HAS_TWILIO = True
except ImportError:
    HAS_TWILIO = False


class WhatsAppTwilioChannel(BaseChannel):
    """WhatsApp messaging via Twilio API (legacy)."""

    name = "whatsapp_twilio"

    def __init__(self, config: dict, on_message: Optional[Callable] = None):
        super().__init__(config, on_message)

        if not HAS_TWILIO:
            raise ImportError(
                "Twilio SDK required for WhatsApp (Twilio). Install with: "
                "pip install 'opencastor[whatsapp-twilio]'"
            )

        account_sid = config.get("account_sid")
        auth_token = config.get("auth_token")
        if not account_sid or not auth_token:
            raise ValueError(
                "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN are required. "
                "Set them in your .env file."
            )

        self.client = TwilioClient(account_sid, auth_token)
        self.from_number = config.get("whatsapp_number", "whatsapp:+14155238886")
        self.logger.info(f"WhatsApp (Twilio) channel initialized (from: {self.from_number})")

    async def start(self):
        """WhatsApp uses webhooks -- no persistent connection needed.
        Register the webhook URL in your Twilio console:
            POST http://<host>:8000/webhooks/whatsapp
        """
        self.logger.info(
            "WhatsApp channel ready. "
            "Ensure your Twilio webhook points to /webhooks/whatsapp"
        )

    async def stop(self):
        self.logger.info("WhatsApp channel stopped")

    async def send_message(self, chat_id: str, text: str):
        """Send a WhatsApp message to a phone number.

        Args:
            chat_id: Recipient in 'whatsapp:+1234567890' format.
            text: Message body.
        """
        try:
            message = self.client.messages.create(
                body=text[:1600],  # WhatsApp limit
                from_=self.from_number,
                to=chat_id,
            )
            self.logger.info(f"Sent WhatsApp message {message.sid} to {chat_id}")
        except Exception as e:
            self.logger.error(f"Failed to send WhatsApp message: {e}")

    async def handle_webhook(self, form_data: dict) -> Optional[str]:
        """Process an incoming Twilio webhook POST.

        Args:
            form_data: Parsed form body from the Twilio webhook.

        Returns:
            Reply text or None.
        """
        sender = form_data.get("From", "")
        body = form_data.get("Body", "").strip()

        if not body:
            return None

        reply = await self.handle_message(sender, body)

        if reply:
            await self.send_message(sender, reply)

        return reply
