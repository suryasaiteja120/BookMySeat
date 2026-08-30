import datetime
import random

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from movies.models import Booking, Genre, LANGUAGE_CHOICES, Movie, Order, Theater, seats



# Hour-of-day weights (index 0-23) -- biased toward lunch and evening
# showtimes, so "peak booking hours" has a genuine, visible peak rather
# than a flat/random distribution that would make that chart meaningless.
HOUR_WEIGHTS = [1, 1, 1, 1, 1, 1, 2, 3, 4, 5, 6, 7, 9, 8, 6, 5, 6, 8, 12, 14, 13, 9, 5, 2]

MOVIE_NAME_WORDS = ['Shadow', 'Fire', 'Dawn', 'Storm', 'Legacy', 'Echo', 'Rising', 'Silent',
                     'Golden', 'Last', 'Broken', 'Hidden', 'Eternal', 'Crimson', 'Winter']
MOVIE_NAME_NOUNS = ['Kingdom', 'Warrior', 'Journey', 'Legends', 'Empire', 'Horizon', 'Chronicles',
                     'Redemption', 'Frontier', 'Voyage', 'Rebellion', 'Guardian', 'Odyssey']


class Command(BaseCommand):
    help = (
        'Seeds a large, realistic dataset (50,000+ bookings by default) for testing '
        'Task 6 dashboard query performance. NOT the same as the movies/seed_movies '
        'command from Task 1 -- this focuses on booking/order VOLUME with realistic '
        'time distributions, not catalog filtering.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--theaters', type=int, default=3200,
                             help='Number of theater/showtime rows to create.')
        parser.add_argument('--seats-per-theater', type=int, default=24)
        parser.add_argument('--days-back', type=int, default=120,
                             help='Spread booking dates across this many past days.')

    def handle(self, *args, **options):
        n_theaters = options['theaters']
        seats_per_theater = options['seats_per_theater']
        days_back = options['days_back']

        self.stdout.write('Seeding genres...')
        genre_names = ['Action', 'Comedy', 'Drama', 'Thriller', 'Romance', 'Horror',
                        'Sci-Fi', 'Fantasy', 'Mystery', 'Animation']
        genres = [Genre.objects.get_or_create(name=n)[0] for n in genre_names]

        self.stdout.write('Seeding movies...')
        movies = []
        for i in range(150):
            m = Movie(
                name=f'{random.choice(MOVIE_NAME_WORDS)} {random.choice(MOVIE_NAME_NOUNS)} {i}',
                rating=round(random.uniform(4.0, 9.5), 1),
                cast='Actor A, Actor B, Actor C',
                description='Seeded movie for dashboard performance testing.',
                language=random.choice([c for c, _ in LANGUAGE_CHOICES]),
            )
            m.image = 'movies/placeholder_550.png'
            movies.append(m)
        Movie.objects.bulk_create(movies, batch_size=500)
        movies = list(Movie.objects.filter(cast='Actor A, Actor B, Actor C'))
        for m in movies:
            m.genres.set(random.sample(genres, k=random.randint(1, 2)))

        self.stdout.write('Seeding customer users (bulk, shared password hash for speed)...')
        existing_usernames = set(User.objects.values_list('username', flat=True))
        shared_hash = make_password('not-a-real-login-seeded-user')
        new_users = []
        for i in range(1000):
            uname = f'seed_customer_{i}'
            if uname in existing_usernames:
                continue
            new_users.append(User(username=uname, email=f'{uname}@example.com', password=shared_hash))
        User.objects.bulk_create(new_users, batch_size=500)
        customers = list(User.objects.filter(username__startswith='seed_customer_'))
        self.stdout.write(f'  {len(customers)} customer users ready.')

        self.stdout.write(f'Seeding {n_theaters} theaters...')
        now = timezone.now()
        theaters = []
        for i in range(n_theaters):
            theaters.append(Theater(
                name=f'Screen {i % 40 + 1} - Cinema {i % 15 + 1}',
                movie=random.choice(movies),
                time=now + datetime.timedelta(days=random.randint(-30, 60)),
                price_per_seat=random.choice([150, 180, 200, 220, 250, 300]),
            ))
        Theater.objects.bulk_create(theaters, batch_size=1000)
        theaters = list(Theater.objects.filter(name__contains='Screen'))
        self.stdout.write(f'  {len(theaters)} theaters created.')

        self.stdout.write(f'Seeding seats ({seats_per_theater}/theater)...')
        all_seats = []
        for t in theaters:
            for s in range(seats_per_theater):
                row = chr(ord('A') + s // 12)
                num = s % 12 + 1
                all_seats.append(seats(Theater=t, seat_number=f'{row}{num}'))
        seats.objects.bulk_create(all_seats, batch_size=2000)
        self.stdout.write(f'  {len(all_seats)} seats created.')

        self.stdout.write('Building bookings and orders (this is the bulk of the work)...')
        seats_by_theater = {}
        for s in seats.objects.select_related('Theater').iterator(chunk_size=5000):
            seats_by_theater.setdefault(s.Theater_id, []).append(s)

        total_bookings = 0
        total_paid_orders = 0
        total_failed_orders = 0

        for t in theaters:
            theater_seats = seats_by_theater.get(t.id, [])
            fill_rate = random.uniform(0.3, 0.95)  # varies per theater -> meaningful occupancy differences
            random.shuffle(theater_seats)
            n_sold = int(len(theater_seats) * fill_rate)

            orders_batch = []
            for s in theater_seats[:n_sold]:
                orders_batch.append(Order(
                    user=random.choice(customers), theatre=t, amount=t.price_per_seat,
                    status='paid', idempotency_key=f'seed-{s.id}-{random.randint(100000,999999)}',
                ))
            with transaction.atomic():
                created_orders = Order.objects.bulk_create(orders_batch, batch_size=2000)

            bookings_batch = []
            for order, s in zip(created_orders, theater_seats[:n_sold]):
                bookings_batch.append(Booking(
                    user=order.user, seat=s, Movie=t.movie, theatre=t,
                    order=order, payment_id=f'seed_pi_{order.id}',
                ))
                s.is_booked = True
            with transaction.atomic():
                Booking.objects.bulk_create(bookings_batch, batch_size=2000)
                seats.objects.bulk_update(theater_seats[:n_sold], ['is_booked'], batch_size=2000)

            # A slice of the UNSOLD seats become abandoned/failed checkouts --
            # standalone Orders with no Booking, for cancellation-rate data.
            remaining = theater_seats[n_sold:]
            n_failed = int(len(remaining) * 0.2)
            failed_batch = []
            for _ in remaining[:n_failed]:
                failed_batch.append(Order(
                    user=random.choice(customers), theatre=t, amount=t.price_per_seat,
                    status=random.choice(['failed', 'cancelled', 'expired']),
                    idempotency_key=f'seed-fail-{t.id}-{random.randint(100000,999999)}',
                ))
            with transaction.atomic():
                Order.objects.bulk_create(failed_batch, batch_size=2000)

            total_bookings += len(bookings_batch)
            total_paid_orders += len(created_orders)
            total_failed_orders += len(failed_batch)

            if total_bookings % 5000 < seats_per_theater:
                self.stdout.write(f'  ...{total_bookings} bookings so far')

        self.stdout.write(f'Created {total_bookings} bookings, {total_paid_orders} paid orders, '
                           f'{total_failed_orders} failed/cancelled/expired orders.')

        # --- Backdate timestamps ---
        # auto_now_add fields ignore whatever you set at creation time via
        # bulk_create (Django forces them to "now"). bulk_update does NOT
        # trigger that logic -- it's a raw UPDATE -- so this is the correct
        # way to give seeded data a realistic historical spread.
        self.stdout.write('Backdating timestamps for realistic time-series data...')

        def random_timestamp():
            day_offset = random.randint(0, days_back)
            hour = random.choices(range(24), weights=HOUR_WEIGHTS, k=1)[0]
            minute = random.randint(0, 59)
            dt = now - datetime.timedelta(days=day_offset)
            return dt.replace(hour=hour, minute=minute, second=random.randint(0, 59), microsecond=0)

        all_bookings = list(Booking.objects.filter(payment_id__startswith='seed_pi_').only('id'))
        for b in all_bookings:
            b.booked_at = random_timestamp()
        for i in range(0, len(all_bookings), 2000):
            Booking.objects.bulk_update(all_bookings[i:i+2000], ['booked_at'], batch_size=2000)

        all_orders = list(Order.objects.filter(idempotency_key__startswith='seed-').only('id'))
        for o in all_orders:
            o.created_at = random_timestamp()
        for i in range(0, len(all_orders), 2000):
            Order.objects.bulk_update(all_orders[i:i+2000], ['created_at'], batch_size=2000)

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {total_bookings} bookings, {total_paid_orders + total_failed_orders} total orders, '
            f'{len(all_seats)} seats across {len(theaters)} theaters.'
        ))
