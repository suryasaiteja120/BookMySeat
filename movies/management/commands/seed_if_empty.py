from django.core.management import call_command
from django.core.management.base import BaseCommand

from movies.models import Movie


class Command(BaseCommand):
    help = (
        'Seeds a small amount of demo data (movies + a modest number of '
        'bookings), but ONLY if the database is currently empty of movies. '
        'Safe to include in a startup command that runs on every deploy '
        '(e.g. on Render free tier, which has no Shell access) -- it will '
        'not duplicate data on subsequent restarts.'
    )

    def handle(self, *args, **options):
        if Movie.objects.exists():
            self.stdout.write('Movies already exist -- skipping seed (safe no-op).')
            return

        self.stdout.write('Database is empty -- seeding a small demo dataset...')
        call_command('seed_movies', count=25)
        call_command('seed_bookings', theaters=60, seats_per_theater=20)
        self.stdout.write(self.style.SUCCESS('Demo data seeded.'))