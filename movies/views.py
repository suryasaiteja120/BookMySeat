import json
import logging
import uuid
import datetime

import stripe
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Booking, Genre, LANGUAGE_CHOICES, Movie, Order, Theater, WebhookEvent, seats
from .booking import create_seat_hold, SeatUnavailableError
from .payments import create_checkout_session, retrieve_checkout_session, verify_and_parse_webhook_event
from .tasks import send_booking_confirmation_email
from .analytics import invalidate_dashboard_cache

logger = logging.getLogger('movies.payments')

SORT_OPTIONS = {
    'name': 'name',
    '-name': '-name',
    'rating': 'rating',
    '-rating': '-rating',
}
PAGE_SIZE = 12
HOLD_MINUTES = getattr(settings, 'HOLD_MINUTES', 2)


def movie_list(request):
    search_query = request.GET.get('search', '').strip()
    selected_genre_ids = request.GET.getlist('genre')
    selected_languages = request.GET.getlist('language')
    sort_key = request.GET.get('sort', 'name')
    page_number = request.GET.get('page', 1)

    movies = Movie.objects.all()
    if search_query:
        movies = movies.filter(name__icontains=search_query)
    if selected_languages:
        movies = movies.filter(language__in=selected_languages)
    if selected_genre_ids:
        movies = movies.filter(genres__id__in=selected_genre_ids).distinct()

    order_field = SORT_OPTIONS.get(sort_key, 'name')
    movies = movies.order_by(order_field)

    base_for_language_counts = Movie.objects.all()
    if search_query:
        base_for_language_counts = base_for_language_counts.filter(name__icontains=search_query)
    if selected_genre_ids:
        base_for_language_counts = base_for_language_counts.filter(genres__id__in=selected_genre_ids).distinct()
    language_counts = base_for_language_counts.values('language').annotate(count=Count('id', distinct=True))
    language_count_map = {row['language']: row['count'] for row in language_counts}
    language_filters = [
        {'code': code, 'label': label, 'count': language_count_map.get(code, 0), 'selected': code in selected_languages}
        for code, label in LANGUAGE_CHOICES
    ]

    base_for_genre_counts = Movie.objects.all()
    if search_query:
        base_for_genre_counts = base_for_genre_counts.filter(name__icontains=search_query)
    if selected_languages:
        base_for_genre_counts = base_for_genre_counts.filter(language__in=selected_languages)
    genre_counts = (
        Genre.objects.filter(movies__in=base_for_genre_counts)
        .annotate(count=Count('movies', distinct=True)).order_by('name')
    )
    selected_genre_id_ints = {int(g) for g in selected_genre_ids if g.isdigit()}
    genre_filters = [
        {'id': g.id, 'name': g.name, 'count': g.count, 'selected': g.id in selected_genre_id_ints}
        for g in genre_counts
    ]

    paginator = Paginator(movies, PAGE_SIZE)
    page_obj = paginator.get_page(page_number)

    context = {
        'movies': page_obj, 'page_obj': page_obj,
        'language_filters': language_filters, 'genre_filters': genre_filters,
        'selected_languages': selected_languages, 'selected_genre_ids': selected_genre_id_ints,
        'sort_key': sort_key, 'search_query': search_query,
    }
    return render(request, 'movies/movie_list.html', context)


def theater(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id)
    theaters = Theater.objects.filter(movie=movie)
    return render(request, 'movies/theater_list.html', {'theaters': theaters, 'movie': movie})


@login_required(login_url='/login/')
def book_seats(request, theater_id):
    """
    GET: shows the seat map with a fresh idempotency key embedded as a
    hidden field.
    POST: validates the selected seats are free, places a temporary hold,
    creates a Stripe Checkout Session, and redirects straight to Stripe's
    hosted payment page. No Booking rows are created here.
    """
    theater_obj = get_object_or_404(Theater, id=theater_id)
    seats_list = seats.objects.filter(Theater=theater_obj)

    if request.method == 'POST':
        selected_seat_ids = sorted(set(request.POST.getlist('seats')))
        idempotency_key = request.POST.get('idempotency_key') or uuid.uuid4().hex

        if not selected_seat_ids:
            return render(request, 'movies/seat_selection.html', {
                'theater': theater_obj, 'seats': seats_list,
                'error_message': "Please select at least one seat.",
                'idempotency_key': uuid.uuid4().hex,
            })

        existing = Order.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            if existing.status == 'created' and existing.hold_expires_at and existing.hold_expires_at > timezone.now() and existing.gateway_session_id:
                try:
                    session = retrieve_checkout_session(existing.gateway_session_id)
                    if session.status == 'open':
                        return render(request, 'movies/checkout_redirect.html', {
                            'order': existing, 'stripe_url': session.url,
                        })
                except Exception as exc:
                    logger.warning('Could not re-fetch Stripe session for existing Order %s: %s', existing.id, exc)
            idempotency_key = uuid.uuid4().hex

        # create_seat_hold commits its own short transaction and returns --
        # deliberately NOT wrapped in an outer atomic() block here, because
        # the Stripe API call below is a network round-trip. Holding a DB
        # lock open for the duration of an external HTTP call would block
        # every other seat-hold attempt on this row for however long
        # Stripe takes to respond -- bad for concurrency under load.
        try:
            order = create_seat_hold(request.user, theater_obj, selected_seat_ids, idempotency_key, HOLD_MINUTES)
        except SeatUnavailableError as exc:
            return render(request, 'movies/seat_selection.html', {
                'theater': theater_obj, 'seats': seats_list,
                'error_message': f"These seats are no longer available: {', '.join(exc.unavailable_seat_numbers)}. Please pick again.",
                'idempotency_key': uuid.uuid4().hex,
            })

        try:
            success_url = request.build_absolute_uri(reverse('payment_callback'))
            cancel_url = request.build_absolute_uri(reverse('payment_cancel', args=[order.id]))
            session = create_checkout_session(order, success_url, cancel_url)
            order.gateway_session_id = session.id
            order.save(update_fields=['gateway_session_id'])
        except Exception as exc:
            logger.error('Stripe checkout session creation failed for Order %s: %s', order.id, exc)
            order.status = 'failed'
            order.save(update_fields=['status'])
            for s in order.held_seats.all():
                s.held_by_order = None
                s.held_until = None
                s.save(update_fields=['held_by_order', 'held_until'])
            return render(request, 'movies/seat_selection.html', {
                'theater': theater_obj, 'seats': seats_list,
                'error_message': "Couldn't start payment right now. Please try again in a moment.",
                'idempotency_key': uuid.uuid4().hex,
            })

        # Show the countdown page (real hold_expires_at, not decorative)
        # instead of jumping straight to Stripe -- gives the user a clear
        # signal of how long their seats are actually reserved for.
        return render(request, 'movies/checkout_redirect.html', {
            'order': order, 'stripe_url': session.url,
        })

    return render(request, 'movies/seat_selection.html', {
        'theater': theater_obj, 'seats': seats_list, 'idempotency_key': uuid.uuid4().hex,
    })


def _finalize_paid_order(order, gateway_payment_id):
    """
    The single place that turns a verified payment into real Bookings.

    The Order and its held seats are locked inside one database transaction
    so that the Stripe callback and webhook cannot finalize the same order
    simultaneously.
    """
    try:
        with transaction.atomic():
            # Lock the order first so callback + webhook cannot finalize it
            # simultaneously.
            order = Order.objects.select_for_update().get(pk=order.pk)

            # Idempotency: if another request already completed this order,
            # simply return the existing bookings.
            if order.status == 'paid':
                return list(order.bookings.all())

            # IMPORTANT:
            # select_for_update() MUST be evaluated inside transaction.atomic().
            held_seats = list(
                order.held_seats.select_for_update()
            )

            created_bookings = []

            for seat in held_seats:
                # The seat is already locked, but refresh its latest values.
                seat.refresh_from_db()

                if seat.is_booked:
                    raise IntegrityError(
                        f"Seat {seat.seat_number} was already booked."
                    )

                booking = Booking.objects.create(
                    user=order.user,
                    seat=seat,
                    Movie=order.theatre.movie,
                    theatre=order.theatre,
                    order=order,
                    payment_id=gateway_payment_id,
                )

                seat.is_booked = True
                seat.held_by_order = None
                seat.held_until = None

                seat.save(
                    update_fields=[
                        'is_booked',
                        'held_by_order',
                        'held_until',
                    ]
                )

                created_bookings.append(booking)

            # Mark the order paid only after all bookings were created.
            order.status = 'paid'
            order.gateway_payment_id = gateway_payment_id

            order.save(
                update_fields=[
                    'status',
                    'gateway_payment_id',
                ]
            )

    except IntegrityError as exc:
        # Payment succeeded but booking could not be completed.
        # Refund the Stripe payment rather than leaving the customer charged.
        logger.error(
            'Booking creation failed after payment for Order %s: %s. '
            'Attempting refund.',
            order.id,
            exc,
        )

        try:
            stripe.Refund.create(
                payment_intent=gateway_payment_id
            )
            order.status = 'failed'

        except Exception as refund_exc:
            logger.critical(
                'REFUND FAILED for Order %s, payment %s: %s. '
                'Needs manual review.',
                order.id,
                gateway_payment_id,
                refund_exc,
            )
            order.status = 'failed'

        order.save(update_fields=['status'])
        return []

    if created_bookings:
        try:
            send_booking_confirmation_email.delay(
                [b.id for b in created_bookings]
            )
        except Exception as exc:
            logger.error(
                'Could not queue confirmation email for booking(s) %s: %s',
                [b.id for b in created_bookings],
                exc,
            )

        invalidate_dashboard_cache()

    return created_bookings


@login_required(login_url='/login/')
def payment_callback(request):
    """
    Stripe redirects the browser here (GET) after checkout, with
    ?session_id=... . We do NOT trust that alone -- we fetch the session
    back from Stripe's API server-side and check its actual payment_status
    before finalizing anything. The webhook (below) is still the
    authoritative backstop even if this view is never hit at all.
    """
    session_id = request.GET.get('session_id')
    if not session_id:
        return HttpResponseBadRequest('missing session_id')

    order = get_object_or_404(Order, gateway_session_id=session_id, user=request.user)

    try:
        session = retrieve_checkout_session(session_id)
    except Exception as exc:
        logger.error('Failed to retrieve Stripe session %s: %s', session_id, exc)
        return render(request, 'movies/payment_result.html', {'order': order, 'outcome': 'failed'})

    if session.payment_status == 'paid':
        _finalize_paid_order(order, session.payment_intent)
        order.refresh_from_db()
        outcome = 'success' if order.status == 'paid' else 'failed'
    else:
        outcome = 'failed'

    return render(request, 'movies/payment_result.html', {'order': order, 'outcome': outcome})


@login_required(login_url='/login/')
def payment_cancel(request, order_id):
    """User backed out of Stripe checkout -- release the hold immediately."""
    order = get_object_or_404(Order, id=order_id, user=request.user)
    if order.status == 'created':
        for s in order.held_seats.all():
            s.held_by_order = None
            s.held_until = None
            s.save(update_fields=['held_by_order', 'held_until'])
        order.status = 'cancelled'
        order.save(update_fields=['status'])
    return render(request, 'movies/payment_result.html', {'order': order, 'outcome': 'cancelled'})


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """
    Server-to-server notification from Stripe -- the authoritative source
    of truth for payment status. CSRF-exempt because Stripe's servers
    can't carry a Django CSRF token; `verify_and_parse_webhook_event`
    (Stripe SDK's signature check) protects this endpoint instead.
    """
    payload = request.body
    sig_header = request.headers.get('Stripe-Signature', '')

    event = verify_and_parse_webhook_event(payload, sig_header)
    if event is None:
        return HttpResponseBadRequest('invalid signature or payload')

    event_id = event['id']
    event_type = event['type']

    # Idempotent webhook handling: a retried/duplicated delivery of an
    # event we've already logged is acknowledged and skipped, so it can
    # never create a second booking.
    _, created = WebhookEvent.objects.get_or_create(
        event_id=event_id, defaults={'event_type': event_type},
    )
    if not created:
        return HttpResponse(status=200)

    try:
        if event_type == 'checkout.session.completed':
            session = event['data']['object']
            order_id = session.get('metadata', {}).get('order_id')
            order = Order.objects.filter(id=order_id).first() if order_id else None
            if order and session.get('payment_status') == 'paid':
                _finalize_paid_order(order, session.get('payment_intent'))
        elif event_type in ('checkout.session.expired', 'payment_intent.payment_failed'):
            session_or_intent = event['data']['object']
            order_id = session_or_intent.get('metadata', {}).get('order_id')
            order = Order.objects.filter(id=order_id).first() if order_id else None
            if order and order.status == 'created':
                for s in order.held_seats.all():
                    s.held_by_order = None
                    s.held_until = None
                    s.save(update_fields=['held_by_order', 'held_until'])
                order.status = 'failed'
                order.save(update_fields=['status'])
        # Other event types are acknowledged and ignored.
    except Exception:
        logger.exception('Error processing Stripe webhook event %s (%s)', event_id, event_type)

    return HttpResponse(status=200)
