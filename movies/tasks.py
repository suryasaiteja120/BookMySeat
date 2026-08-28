import logging

from celery import shared_task
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from .models import Booking

# Dedicated logger (configured in settings.py LOGGING) so failed deliveries
# show up in logs/email.log and aren't silently swallowed.
logger = logging.getLogger('movies.email')


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_booking_confirmation_email(self, booking_ids):
    """
    Sends ONE confirmation email covering every seat booked in a single
    checkout (booking_ids is a list, since a user can book multiple seats
    at once). Runs on a Celery worker, off the request/response cycle, so
    it can never slow down or fail the booking API response itself.

    Retries up to 3 times with a 60s delay on transient failures (e.g. SMTP
    server briefly unreachable). After the final retry is exhausted, the
    failure is logged at ERROR level for monitoring/alerting instead of
    being lost.
    """
    bookings = list(
        Booking.objects.select_related('user', 'Movie', 'theatre')
        .filter(id__in=booking_ids)
    )
    if not bookings:
        logger.error('send_booking_confirmation_email: no bookings found for ids %s', booking_ids)
        return

    first = bookings[0]
    user = first.user
    context = {
        'user': user,
        'movie': first.Movie,
        'theater': first.theatre,
        'seat_numbers': [b.seat.seat_number for b in bookings],
        'payment_id': first.payment_id,  # None until the payment-gateway task wires this up
        'booked_at': first.booked_at,
    }

    if not user.email:
        # Don't retry -- a missing email address will never fix itself.
        logger.warning('Booking %s: user %s has no email on file, skipping confirmation email.',
                        [b.id for b in bookings], user.username)
        return

    try:
        text_body = render_to_string('emails/booking_confirmation.txt', context)
        html_body = render_to_string('emails/booking_confirmation.html', context)

        msg = EmailMultiAlternatives(
            subject=f'Your booking for {first.Movie.name} is confirmed',
            body=text_body,
            to=[user.email],
        )
        msg.attach_alternative(html_body, 'text/html')
        msg.send(fail_silently=False)

        Booking.objects.filter(id__in=booking_ids).update(confirmation_email_sent=True)
        # Log only that a send happened, plus a non-sensitive identifier --
        # never log full email bodies or payment details.
        logger.info('Confirmation email sent for booking(s) %s to user %s', booking_ids, user.username)

    except Exception as exc:
        logger.warning('Attempt %s: failed to send confirmation email for booking(s) %s: %s',
                        self.request.retries + 1, booking_ids, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error('Giving up on confirmation email for booking(s) %s after %s attempts.',
                         booking_ids, self.max_retries + 1)
