import os
import base64
import logging
import requests

from django.core.mail.backends.base import BaseEmailBackend


logger = logging.getLogger('movies.email')


class BrevoEmailBackend(BaseEmailBackend):
    """
    Django email backend that sends emails through
    Brevo's HTTPS Transactional Email API.

    This avoids SMTP completely.
    """

    API_URL = 'https://api.brevo.com/v3/smtp/email'

    def send_messages(self, email_messages):
        """
        Send a list of Django EmailMessage objects.

        Returns the number of successfully submitted emails.
        """

        if not email_messages:
            return 0

        sent_count = 0

        for message in email_messages:

            if not message.recipients():
                continue

            try:
                if self._send(message):
                    sent_count += 1

            except Exception:
                logger.exception(
                    'Failed to send email through Brevo: %s',
                    message.subject
                )

                if not self.fail_silently:
                    raise

        return sent_count

    def _send(self, message):
        """
        Convert Django EmailMessage into Brevo API format.
        """

        api_key = os.environ.get('BREVO_API_KEY')
        sender_email = os.environ.get('BREVO_SENDER_EMAIL')
        sender_name = os.environ.get(
            'BREVO_SENDER_NAME',
            'BookMySeat'
        )

        if not api_key:
            raise RuntimeError(
                'BREVO_API_KEY is not configured'
            )

        if not sender_email:
            raise RuntimeError(
                'BREVO_SENDER_EMAIL is not configured'
            )

        # ---------------------------------------------------------
        # Sender
        # ---------------------------------------------------------

        sender = {
            'email': sender_email,
            'name': sender_name,
        }

        # ---------------------------------------------------------
        # Recipients
        # ---------------------------------------------------------

        to = [
            {
                'email': email,
            }
            for email in message.to
        ]

        # ---------------------------------------------------------
        # CC
        # ---------------------------------------------------------

        cc = [
            {
                'email': email,
            }
            for email in message.cc
        ]

        # ---------------------------------------------------------
        # BCC
        # ---------------------------------------------------------

        bcc = [
            {
                'email': email,
            }
            for email in message.bcc
        ]

        # ---------------------------------------------------------
        # Body
        # ---------------------------------------------------------

        text_content = message.body or ''

        html_content = None

        if hasattr(message, 'alternatives'):
            for alternative in message.alternatives:
                try:
                    content, mimetype = alternative

                    if mimetype == 'text/html':
                        html_content = content
                        break

                except (ValueError, TypeError):
                    continue

        # ---------------------------------------------------------
        # Build Brevo request
        # ---------------------------------------------------------

        payload = {
            'sender': sender,
            'to': to,
            'subject': message.subject,
            'textContent': text_content,
        }

        if html_content:
            payload['htmlContent'] = html_content

        if cc:
            payload['cc'] = cc

        if bcc:
            payload['bcc'] = bcc

        # ---------------------------------------------------------
        # Reply-To
        # ---------------------------------------------------------

        if message.reply_to:
            reply_email = message.reply_to[0]

            payload['replyTo'] = {
                'email': reply_email,
            }

        # ---------------------------------------------------------
        # Attachments
        # ---------------------------------------------------------

        if message.attachments:

            attachments = []

            for attachment in message.attachments:

                # Django can store attachments as:
                # (filename, content, mimetype)

                filename, content, mimetype = attachment

                if isinstance(content, str):
                    content = content.encode()

                encoded_content = base64.b64encode(
                    content
                ).decode('utf-8')

                attachments.append(
                    {
                        'name': filename,
                        'content': encoded_content,
                    }
                )

            payload['attachment'] = attachments

        # ---------------------------------------------------------
        # Send through Brevo HTTPS API
        # ---------------------------------------------------------

        response = requests.post(
            self.API_URL,
            headers={
                'accept': 'application/json',
                'api-key': api_key,
                'content-type': 'application/json',
            },
            json=payload,
            timeout=20,
        )

        # ---------------------------------------------------------
        # Handle Brevo response
        # ---------------------------------------------------------

        if not response.ok:

            logger.error(
                'Brevo email failed. Status=%s Response=%s',
                response.status_code,
                response.text
            )

            response.raise_for_status()

        result = response.json()

        logger.info(
            'Email sent through Brevo. Subject="%s" '
            'To=%s MessageID=%s',
            message.subject,
            message.to,
            result.get('messageId')
        )

        return True