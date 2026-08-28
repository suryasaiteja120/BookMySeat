from django.db import models
from django.contrib.auth.models import User

import re
from urllib.parse import urlparse, parse_qs
from django.utils import timezone

# Only these hosts are ever trusted as trailer sources. Whitelisting the
# domain (not just pattern-matching "youtube.com" somewhere in the string)
# blocks tricks like "evil.com/youtube.com/watch?v=..." from passing.
ALLOWED_YOUTUBE_HOSTS = {'www.youtube.com', 'youtube.com', 'm.youtube.com', 'youtu.be'}

# A real YouTube video ID is always exactly 11 characters of this charset.
# Anything else is rejected outright, before it ever reaches a template.
_VIDEO_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')


def extract_youtube_id(url):
    """
    Validates a URL as a genuine YouTube link and returns its 11-character
    video ID, or None if the URL is missing, malformed, from an untrusted
    domain, or doesn't contain a well-formed ID.

    This is the ONLY function allowed to produce a value that ends up
    inside an iframe src -- keeping validation in one place, server-side,
    means a malicious/malformed trailer_url can never reach the page.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    if parsed.scheme not in ('http', 'https'):
        return None
    if parsed.netloc not in ALLOWED_YOUTUBE_HOSTS:
        return None

    video_id = None
    if parsed.netloc == 'youtu.be':
        # https://youtu.be/VIDEOID
        video_id = parsed.path.lstrip('/').split('/')[0]
    elif parsed.path.startswith('/watch'):
        # https://www.youtube.com/watch?v=VIDEOID
        video_id = parse_qs(parsed.query).get('v', [None])[0]
    elif parsed.path.startswith('/embed/'):
        # https://www.youtube.com/embed/VIDEOID
        video_id = parsed.path[len('/embed/'):].split('/')[0]

    if video_id and _VIDEO_ID_RE.match(video_id):
        return video_id
    return None

class Genre(models.Model):
    """
    Separate table instead of a CharField/choices on Movie because:
    - A movie can belong to multiple genres (Action + Thriller), so it needs
      a many-to-many relationship, not a single column.
    - Django automatically indexes the FK columns on the auto-generated
      M2M "through" table (movie_id, genre_id), which is exactly what we
      need for fast filtering at scale.
    - New genres can be added without a schema migration.
    """
    name = models.CharField(max_length=50, unique=True, db_index=True)

    def __str__(self):
        return self.name

LANGUAGE_CHOICES = [
    ('en', 'English'),
    ('hi', 'Hindi'),
    ('te', 'Telugu'),
    ('ta', 'Tamil'),
    ('kn', 'Kannada'),
    ('ml', 'Malayalam'),
    ('mr', 'Marathi'),
    ('bn', 'Bengali'),
]

class Movie(models.Model):
    name=models.CharField(max_length=255)
    image=models.ImageField(upload_to='movies/')
    rating=models.DecimalField(max_digits=3, decimal_places=1)
    cast=models.TextField()
    description=models.TextField(blank=True, null=True)
    genres=models.ManyToManyField(Genre, related_name='movies', blank=True)
    language=models.CharField(max_length=2, choices=LANGUAGE_CHOICES, default='en')
    trailer_url=models.URLField(blank=True, null=True, help_text='YouTube watch/embed/youtu.be link')

    @property
    def trailer_embed_id(self):
        """
        Server-side-validated video ID, safe to place in an iframe src.
        Returns None if there's no trailer or the URL didn't pass validation
        -- templates use this to decide whether to show the player or a
        fallback message, never trusting trailer_url directly.
        """
        return extract_youtube_id(self.trailer_url)

    class Meta:
        indexes = [
            models.Index(fields=['language', 'rating'], name='movie_lang_rating_idx'),
        ]
    
    def __str__(self):
        return self.name

class Theater(models.Model):
    name=models.CharField(max_length=255)
    movie=models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='theaters')
    time=models.DateTimeField()
    price_per_seat=models.DecimalField(max_digits=8, decimal_places=2, default=200.00)

    def __str__(self):
        return f"{self.name} - {self.movie.name} at {self.time}"
    
class seats(models.Model):
    Theater=models.ForeignKey(Theater, on_delete=models.CASCADE, related_name='seats')
    seat_number=models.CharField(max_length=10)
    is_booked=models.BooleanField(default=False)
    # Lightweight seat hold used during checkout, so two people can't pay
    # for the same seat at once. `held_until` is what makes the hold
    # temporary -- Task 5 replaces this with proper row-level locking and
    # an automatic release scheduler; this is the minimal version needed
    # to make payment integration safe on its own.
    held_by_order=models.ForeignKey('Order', null=True, blank=True, on_delete=models.SET_NULL, related_name='held_seats')
    held_until=models.DateTimeField(null=True, blank=True)

    def is_available(self):
        if self.is_booked:
            return False
        if self.held_by_order_id and self.held_until and self.held_until > timezone.now():
            return False
        return True

    def __str__(self):
        return f"{self.seat_number} - {'Booked' if self.is_booked else 'Available'}"

class Order(models.Model):
    """
    One row per checkout attempt (may cover multiple seats). This is the
    single source of truth for the payment lifecycle -- Bookings are only
    ever created once an Order transitions to PAID, and only via
    signature-verified server-side code (never trusting the browser alone).
    """
    STATUS_CHOICES = [
        ('created', 'Created'),       # Razorpay order created, awaiting payment
        ('paid', 'Paid'),             # signature/webhook verified, Bookings created
        ('failed', 'Failed'),         # payment failed or signature invalid
        ('cancelled', 'Cancelled'),   # user cancelled at checkout
        ('expired', 'Expired'),       # hold timed out before payment completed
    ]

    user=models.ForeignKey(User, on_delete=models.CASCADE)
    theatre=models.ForeignKey(Theater, on_delete=models.CASCADE)
    seats=models.ManyToManyField(seats, related_name='orders')
    amount=models.DecimalField(max_digits=10, decimal_places=2)
    status=models.CharField(max_length=20, choices=STATUS_CHOICES, default='created', db_index=True)

    # One idempotency key per logical checkout attempt. If the same key is
    # submitted twice (double-click, browser back+resubmit, retried
    # request), we reuse this row instead of creating a second Razorpay
    # order / duplicate hold / duplicate charge.
    idempotency_key=models.CharField(max_length=64, unique=True, db_index=True)

    gateway_session_id=models.CharField(max_length=200, blank=True, null=True, db_index=True)
    gateway_payment_id=models.CharField(max_length=200, blank=True, null=True)

    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    hold_expires_at=models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Order #{self.id} ({self.status}) - {self.user.username}"


class WebhookEvent(models.Model):
    """
    Logs every processed Razorpay webhook event ID. Razorpay retries
    webhook delivery on anything but a 200 response, and network issues can
    cause the same event to arrive more than once regardless -- this table
    is what makes webhook handling idempotent: if an event ID is already
    here, we acknowledge it with 200 immediately without reprocessing it
    (so a retried "payment.captured" can never create a second Booking).
    """
    event_id=models.CharField(max_length=100, unique=True, db_index=True)
    event_type=models.CharField(max_length=50)
    received_at=models.DateTimeField(auto_now_add=True)

class Booking(models.Model):
    user=models.ForeignKey(User, on_delete=models.CASCADE)
    seat=models.OneToOneField(seats, on_delete=models.CASCADE)
    Movie=models.ForeignKey(Movie, on_delete=models.CASCADE)
    theatre=models.ForeignKey(Theater, on_delete=models.CASCADE)
    booked_at=models.DateTimeField(auto_now_add=True)
    order=models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL, related_name='bookings')
    payment_id=models.CharField(max_length=100, blank=True, null=True)  # wired up properly in the payment-gateway task
    confirmation_email_sent=models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.username} booked {self.seat.seat_number} at {self.theatre.name}"
