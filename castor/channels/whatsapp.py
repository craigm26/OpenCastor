"""
WhatsApp channel -- re-exports the default neonize-based implementation.

For the legacy Twilio-based channel, import from ``castor.channels.whatsapp_twilio``.
"""

from castor.channels.whatsapp_neonize import WhatsAppChannel

__all__ = ["WhatsAppChannel"]
