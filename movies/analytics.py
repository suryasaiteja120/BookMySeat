"""
All analytics queries for the admin dashboard (Task 6).

Design principles followed throughout this file:
1. Every query uses DB-level aggregation (Sum/Count/Avg + GROUP BY via
   .values().annotate()) -- nothing here ever does `list(SomeModel.objects.all())`
   and computes totals in Python. At 50,000+ rows, that distinction is the
   difference between a query that finishes in milliseconds and one that
   loads megabytes into the Django process on every dashboard view.
2. Every result is wrapped in the Django cache (see settings.py CACHES) --
   the dashboard is read far more often than the underlying data changes,
   so recomputing these aggregates on every single page load is wasted
   work. Cache keys are versioned by TTL, not manually invalidated, which
   is a deliberate simplicity/staleness trade-off explained in task6_notes.md.
"""
from django.core.cache import cache
from django.db.models import Avg, Count, F, FloatField, Q, Sum
from django.db.models.functions import ExtractHour, TruncDate, TruncMonth, TruncWeek

from .models import Booking, Movie, Order, Theater, seats

CACHE_TTL_SECONDS = 300  # 5 minutes -- see task6_notes.md for why this value


def _cached(key, compute_fn):
    result = cache.get(key)
    if result is None:
        result = compute_fn()
        cache.set(key, result, CACHE_TTL_SECONDS)
    return result


def revenue_by_period(period='day', limit=30):
    """
    Total revenue from PAID orders, grouped by day/week/month.
    Single query: TruncDate/Week/Month + Sum, entirely at the DB level.
    """
    trunc_fn = {'day': TruncDate, 'week': TruncWeek, 'month': TruncMonth}[period]

    def compute():
        qs = (
            Order.objects.filter(status='paid')
            .annotate(period=trunc_fn('created_at'))
            .values('period')
            .annotate(total_revenue=Sum('amount'), order_count=Count('id'))
            .order_by('-period')[:limit]
        )
        return list(qs)  # small result set (<= `limit` rows) -- safe to materialize

    return _cached(f'dashboard:revenue:{period}:{limit}', compute)


def most_popular_movies(limit=10):
    """Movies ranked by booking count -- single annotated query."""
    def compute():
        qs = (
            Movie.objects.annotate(booking_count=Count('booking'))
            .filter(booking_count__gt=0)
            .order_by('-booking_count')
            .values('id', 'name', 'booking_count')[:limit]
        )
        return list(qs)

    return _cached(f'dashboard:popular_movies:{limit}', compute)


def busiest_theaters(limit=10):
    """
    Theaters ranked by seat occupancy rate (booked seats / total seats).
    Computed entirely in the database via conditional Count + an
    expression division -- no per-theater Python loop.
    """
    def compute():
        qs = (
            Theater.objects.annotate(
                total_seats=Count('seats', distinct=True),
                booked_seats=Count('seats', filter=Q(seats__is_booked=True), distinct=True),
            )
            .filter(total_seats__gt=0)
            .annotate(
                occupancy_rate=F('booked_seats') * 100.0 / F('total_seats')
            )
            .order_by('-occupancy_rate')
            .values('id', 'name', 'total_seats', 'booked_seats', 'occupancy_rate')[:limit]
        )
        return list(qs)

    return _cached(f'dashboard:busiest_theaters:{limit}', compute)


def peak_booking_hours():
    """
    Booking count by hour-of-day (0-23) -- reveals when demand peaks.
    ExtractHour + Count, one query, 24 rows max.
    """
    def compute():
        qs = (
            Booking.objects.annotate(hour=ExtractHour('booked_at'))
            .values('hour')
            .annotate(count=Count('id'))
            .order_by('hour')
        )
        # Fill in hours with zero bookings so the chart has all 24 slots.
        counts = {row['hour']: row['count'] for row in qs}
        return [{'hour': h, 'count': counts.get(h, 0)} for h in range(24)]

    return _cached('dashboard:peak_hours', compute)


def cancellation_rate():
    """
    Percentage of Orders that did NOT end in a successful payment.
    ONE query using conditional aggregation (Count with filter=) rather
    than two separate .count() calls -- halves the DB round trips.
    """
    def compute():
        result = Order.objects.aggregate(
            total=Count('id'),
            cancelled=Count('id', filter=Q(status__in=['cancelled', 'failed', 'expired'])),
        )
        total = result['total'] or 0
        cancelled = result['cancelled'] or 0
        rate = (cancelled / total * 100) if total else 0.0
        return {'total_orders': total, 'cancelled_orders': cancelled, 'rate_percent': round(rate, 2)}

    return _cached('dashboard:cancellation_rate', compute)


def summary_stats():
    """Top-line numbers for the dashboard header. One aggregate query."""
    def compute():
        result = Order.objects.filter(status='paid').aggregate(
            total_revenue=Sum('amount'),
            paid_order_count=Count('id'),
            avg_order_value=Avg('amount'),
        )
        return {
            'total_revenue': result['total_revenue'] or 0,
            'paid_order_count': result['paid_order_count'] or 0,
            'avg_order_value': round(result['avg_order_value'] or 0, 2),
            'total_bookings': Booking.objects.count(),
        }

    return _cached('dashboard:summary', compute)


def invalidate_dashboard_cache():
    """
    Called after a payment is finalized (see views._finalize_paid_order)
    so the dashboard doesn't show stale numbers for a full 5-minute TTL
    right after a booking that an admin might be actively watching for.
    Cheap: just a handful of cache.delete() calls, not a query.
    """
    for period in ('day', 'week', 'month'):
        cache.delete(f'dashboard:revenue:{period}:30')
    cache.delete('dashboard:summary')
    cache.delete('dashboard:cancellation_rate')
    cache.delete('dashboard:peak_hours')
    cache.delete('dashboard:popular_movies:10')
    cache.delete('dashboard:busiest_theaters:10')
