import datetime
import logging
import random
import time

from django.db import transaction, OperationalError
from django.utils import timezone

from .models import Order, seats

logger = logging.getLogger('movies.payments')

HOLD_MINUTES_DEFAULT = 2
MAX_LOCK_RETRIES = 3


class SeatUnavailableError(Exception):
    """Raised when one or more requested seats can't be held."""
    def __init__(self, unavailable_seat_numbers):
        self.unavailable_seat_numbers = unavailable_seat_numbers
        super().__init__(f"Seats unavailable: {', '.join(unavailable_seat_numbers)}")


def create_seat_hold(user, theater_obj, selected_seat_ids, idempotency_key, hold_minutes=HOLD_MINUTES_DEFAULT):
    """
    The single place in the codebase that decides whether a set of seats
    can be held for checkout. Pulled out of the view function so it can be
    called directly -- by the view, AND by concurrency tests that fire
    real simultaneous requests from separate threads/DB connections, which
    is the only way to actually prove a race condition is prevented rather
    than just asserting it in a comment (see concurrency_test.py).

    CONSISTENCY MODEL: pessimistic locking via SELECT ... FOR UPDATE inside
    a single atomic transaction. `select_for_update()` makes any other
    transaction trying to lock the SAME rows BLOCK (wait) until this one
    commits or rolls back -- it does not let two transactions both believe
    they've successfully "checked" a seat as free. This closes the classic
    check-then-act race window: without it, two simultaneous requests could
    both read is_booked=False before either writes, and both proceed to
    hold/book the same seat.

    SQLite-specific note: SQLite has no true row-level locking --
    `select_for_update()` is silently a no-op on this backend. Instead,
    SQLite serializes ALL writes at the database-connection level: a
    second writer transaction is blocked (not permitted to proceed) until
    the first commits. The correctness guarantee (no lost updates, no
    double-booking) is identical to row-level locking for this specific
    access pattern; the difference is coarser concurrency (a writer to an
    unrelated row still queues behind this one on SQLite, whereas
    Postgres/MySQL would let it through). Under real concurrent load this
    can surface as a transient "database is locked" OperationalError if a
    writer can't acquire the lock within DATABASES['OPTIONS']['timeout']
    -- retried below rather than treated as a hard failure, since it's a
    "try again" condition, not a real error.

    Returns the created Order.
    Raises SeatUnavailableError if any requested seat can't be held.
    """
    for attempt in range(MAX_LOCK_RETRIES):
        try:
            return _attempt_hold(user, theater_obj, selected_seat_ids, idempotency_key, hold_minutes)
        except OperationalError as exc:
            if 'locked' not in str(exc).lower() or attempt == MAX_LOCK_RETRIES - 1:
                raise
            backoff = 0.05 * (2 ** attempt) + random.uniform(0, 0.05)
            logger.warning('Seat hold attempt %s hit a transient lock, retrying in %.2fs: %s',
                            attempt + 1, backoff, exc)
            time.sleep(backoff)


def _attempt_hold(user, theater_obj, selected_seat_ids, idempotency_key, hold_minutes):
    with transaction.atomic():
        locked_seats = list(
            seats.objects.select_for_update()
            .filter(id__in=selected_seat_ids, Theater=theater_obj)
        )

        unavailable = [s.seat_number for s in locked_seats if not s.is_available()]
        if len(locked_seats) != len(selected_seat_ids) or unavailable:
            missing = len(selected_seat_ids) - len(locked_seats)
            if missing:
                unavailable.append(f"{missing} seat(s) no longer exist in this theater")
            raise SeatUnavailableError(unavailable)

        amount = theater_obj.price_per_seat * len(locked_seats)
        hold_expires_at = timezone.now() + datetime.timedelta(minutes=hold_minutes)

        order = Order.objects.create(
            user=user, theatre=theater_obj, amount=amount,
            idempotency_key=idempotency_key, hold_expires_at=hold_expires_at,
        )
        order.seats.set(locked_seats)
        for s in locked_seats:
            s.held_by_order = order
            s.held_until = hold_expires_at
            s.save(update_fields=['held_by_order', 'held_until'])

        return order
