import logging

from celery import shared_task
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.db import transaction
from django.utils import timezone

from .models import Booking, Order


# Dedicated logger configured in settings.py
logger = logging.getLogger('movies.email')


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_booking_confirmation_email(self, booking_ids):
    """
    Sends ONE confirmation email covering every seat booked in a single
    checkout. Runs through Celery so the booking request is not blocked.

    Retries up to 3 times with a 60-second delay on transient failures.
    """
    bookings = list(
        Booking.objects
        .select_related('user', 'Movie', 'theatre')
        .filter(id__in=booking_ids)
    )

    if not bookings:
        logger.error(
            'send_booking_confirmation_email: no bookings found for ids %s',
            booking_ids
        )
        return

    first = bookings[0]
    user = first.user

    context = {
        'user': user,
        'movie': first.Movie,
        'theater': first.theatre,
        'seat_numbers': [b.seat.seat_number for b in bookings],
        'payment_id': first.payment_id,
        'booked_at': first.booked_at,
    }

    if not user.email:
        logger.warning(
            'Booking %s: user %s has no email on file, skipping confirmation email.',
            [b.id for b in bookings],
            user.username
        )
        return

    try:
        text_body = render_to_string(
            'emails/booking_confirmation.txt',
            context
        )

        html_body = render_to_string(
            'emails/booking_confirmation.html',
            context
        )

        msg = EmailMultiAlternatives(
            subject=f'Your booking for {first.Movie.name} is confirmed',
            body=text_body,
            to=[user.email],
        )

        msg.attach_alternative(html_body, 'text/html')
        msg.send(fail_silently=False)

        Booking.objects.filter(
            id__in=booking_ids
        ).update(
            confirmation_email_sent=True
        )

        logger.info(
            'Confirmation email sent for booking(s) %s to user %s',
            booking_ids,
            user.username
        )

    except Exception as exc:
        logger.warning(
            'Attempt %s: failed to send confirmation email for booking(s) %s: %s',
            self.request.retries + 1,
            booking_ids,
            exc
        )

        try:
            raise self.retry(exc=exc)

        except self.MaxRetriesExceededError:
            logger.error(
                'Giving up on confirmation email for booking(s) %s after %s attempts.',
                booking_ids,
                self.max_retries + 1
            )


@shared_task
def release_expired_holds():
    """
    Release seats whose temporary booking hold has expired.

    The task runs periodically through Celery Beat.
    Database row locking prevents two workers from processing
    the same order simultaneously.
    """

    now = timezone.now()
    released_seats = 0
    expired_orders = 0

    expired_order_ids = list(
        Order.objects.filter(
            status='created',
            hold_expires_at__isnull=False,
            hold_expires_at__lte=now,
        ).values_list('id', flat=True)
    )

    for order_id in expired_order_ids:

        with transaction.atomic():

            try:
                order = (
                    Order.objects
                    .select_for_update()
                    .get(pk=order_id)
                )

            except Order.DoesNotExist:
                continue

            # Re-check after acquiring the database lock.
            # Another request may have completed or cancelled
            # the order while this task was waiting.
            if (
                order.status != 'created'
                or order.hold_expires_at is None
                or order.hold_expires_at > now
            ):
                continue

            locked_seats = (
                order.seats
                .select_for_update()
                .all()
            )

            for seat in locked_seats:

                if seat.held_by_order_id == order.id:

                    seat.held_by_order = None
                    seat.held_until = None

                    seat.save(
                        update_fields=[
                            'held_by_order',
                            'held_until',
                        ]
                    )

                    released_seats += 1

            order.hold_expires_at = None
            order.status = 'expired'
            order.save(
                update_fields=[
                    'hold_expires_at',
                    'status',
                ]
)

            expired_orders += 1

    logger.info(
        'Expired hold cleanup completed: %s orders expired, %s seats released',
        expired_orders,
        released_seats
    )

    return {
        'expired_orders': expired_orders,
        'released_seats': released_seats,
    }