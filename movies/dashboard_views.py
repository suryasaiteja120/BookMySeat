"""
Admin analytics dashboard (Task 6).

ACCESS CONTROL MODEL:
- `dashboard_view` (the HTML page) uses Django's own `staff_member_required`
  -- the exact same decorator Django's built-in /admin/ site uses. A
  non-staff user (including an unauthenticated one) is redirected to the
  admin login page, never shown any dashboard content or data.
- `dashboard_api` (the JSON endpoint) uses a custom `admin_api_required`
  decorator instead, because an API consumer isn't a browser following
  redirects -- it should get a clean 401/403 JSON response, not an HTML
  redirect. This directly satisfies "prevent unauthorized API access":
  a regular authenticated (non-staff) user hitting this URL gets 403,
  not the data.

Why this can't be bypassed by editing a cookie: Django's session cookie
only contains a signed session ID -- it does not contain "is_staff=true"
or anything else editable client-side. On every request, Django looks up
that session ID server-side and loads the real User row from the
database, and `is_staff`/`is_superuser` are read from THAT row, not from
anything the client sent. Tampering with the cookie either invalidates
the signature (session rejected entirely) or points at a session that
still resolves to the tamperer's own actual (non-staff) account -- there
is no client-controlled input that can grant staff status.
"""
from functools import wraps

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render

from . import analytics


def admin_api_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required.'}, status=401)
        if not request.user.is_staff:
            return JsonResponse({'error': 'You do not have permission to access this resource.'}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapped


@staff_member_required
def dashboard_view(request):
    """Renders the dashboard shell; the actual numbers are fetched by the
    page's JS from dashboard_api, so the page itself stays fast to render
    and the data can refresh without a full page reload."""
    return render(request, 'movies/admin_dashboard.html')


@admin_api_required
def dashboard_api(request):
    """
    Single JSON endpoint backing the dashboard. All five values below come
    from analytics.py, where every query is DB-level aggregation and
    cached -- repeated calls to this endpoint within the cache TTL hit
    the cache, not the database.
    """
    period = request.GET.get('revenue_period', 'day')
    if period not in ('day', 'week', 'month'):
        period = 'day'

    summary = analytics.summary_stats()
    summary['total_revenue'] = float(summary['total_revenue'])
    summary['avg_order_value'] = float(summary['avg_order_value'])

    data = {
        'summary': summary,
        'revenue': [
            {**row, 'period': row['period'].isoformat() if hasattr(row['period'], 'isoformat') else str(row['period']),
             'total_revenue': float(row['total_revenue'])}
            for row in analytics.revenue_by_period(period=period)
        ],
        'revenue_period': period,
        'popular_movies': list(analytics.most_popular_movies()),
        'busiest_theaters': [
            {**row, 'occupancy_rate': round(row['occupancy_rate'], 1)}
            for row in analytics.busiest_theaters()
        ],
        'peak_hours': analytics.peak_booking_hours(),
        'cancellation': analytics.cancellation_rate(),
    }
    return JsonResponse(data)
