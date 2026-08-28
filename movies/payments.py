import logging

import stripe
from django.conf import settings

logger = logging.getLogger('movies.payments')

stripe.api_key = settings.STRIPE_SECRET_KEY


def create_checkout_session(order, success_url, cancel_url):
    """
    Creates a Stripe-hosted Checkout Session and returns it -- the user is
    redirected to `session.url` (Stripe's own domain) to enter payment
    details, so no card data or payment-widget JS ever touches our server
    or our page directly.

    `idempotency_key` is passed as a native Stripe request option (not a
    field we invented) -- if this exact request is retried (network hiccup,
    double form submit), Stripe returns the SAME session instead of
    creating a second one / charging twice. This is Stripe's own
    documented idempotency mechanism.
    """
    session = stripe.checkout.Session.create(
        payment_method_types=['card'],
        mode='payment',
        line_items=[{
            'price_data': {
                'currency': 'inr',
                'product_data': {
                    'name': f'{order.theatre.movie.name} ({order.theatre.name})',
                },
                'unit_amount': int(round(order.amount * 100)),  # paise
            },
            'quantity': 1,
        }],
        success_url=success_url + '?session_id={CHECKOUT_SESSION_ID}',
        cancel_url=cancel_url,
        metadata={'order_id': str(order.id)},
        idempotency_key=order.idempotency_key,
    )
    return session


def retrieve_checkout_session(session_id):
    """Fetches the session directly from Stripe's API -- used to verify a
    payment server-side rather than trusting the redirect URL's query
    params alone (those could in principle be replayed/guessed)."""
    return stripe.checkout.Session.retrieve(session_id)


def verify_and_parse_webhook_event(payload, sig_header):
    """
    Stripe's SDK handles HMAC signature verification internally here --
    `construct_event` recomputes the expected signature from the raw
    payload using STRIPE_WEBHOOK_SECRET and compares it (constant-time)
    against the `Stripe-Signature` header. Returns the verified Event
    object, or None if verification fails (forged/tampered request) or
    the payload isn't valid JSON.
    """
    try:
        return stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
    except ValueError:
        logger.warning('Stripe webhook: invalid payload (not valid JSON).')
        return None
    except stripe.error.SignatureVerificationError:
        logger.warning('Stripe webhook: signature verification failed -- possible forgery/replay attempt.')
        return None
