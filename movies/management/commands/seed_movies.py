import random
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction

from movies.models import Movie, Genre, LANGUAGE_CHOICES

GENRE_NAMES = [
    'Action', 'Comedy', 'Drama', 'Thriller', 'Romance', 'Horror',
    'Sci-Fi', 'Fantasy', 'Mystery', 'Animation', 'Documentary', 'Musical',
]

# A tiny 1x1 transparent PNG used as a placeholder so ImageField doesn't
# error out on missing files during a bulk seed. Real movies would have
# real posters uploaded through the admin.
PLACEHOLDER_PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000001000000010802000000907753'
    'de0000000c4944415408d763f8ffff3f0005fe02fea1399e1e0000000049454e'
    '44ae426082'
)


class Command(BaseCommand):
    help = 'Seed the database with N test movies for performance testing (default 5000).'

    def add_arguments(self, parser):
        parser.add_argument('--count', type=int, default=5000)

    def handle(self, *args, **options):
        count = options['count']

        genres = []
        for name in GENRE_NAMES:
            genre, _ = Genre.objects.get_or_create(name=name)
            genres.append(genre)

        self.stdout.write(f'Creating {count} movies...')

        language_codes = [code for code, _ in LANGUAGE_CHOICES]
        BATCH_SIZE = 500
        created_movies = []

        with transaction.atomic():
            for i in range(count):
                movie = Movie(
                    name=f'Test Movie {i:05d}',
                    rating=Decimal(random.randrange(10, 100)) / 10,
                    cast=f'Actor {i}, Actor {i+1}, Actor {i+2}',
                    description='Auto-generated movie for load/performance testing.',
                    language=random.choice(language_codes),
                )
                movie.image.save(f'seed_movie_{i}.png', ContentFile(PLACEHOLDER_PNG), save=False)
                created_movies.append(movie)

                if len(created_movies) >= BATCH_SIZE:
                    Movie.objects.bulk_create(created_movies)
                    created_movies = []
                    self.stdout.write(f'  ...{i + 1} created')

            if created_movies:
                Movie.objects.bulk_create(created_movies)

        # bulk_create doesn't let us set M2M in the same step (no PKs are
        # returned reliably pre-save for all backends), so genres are
        # attached in a second pass over freshly created rows.
        self.stdout.write('Assigning genres...')
        all_movies = list(Movie.objects.filter(name__startswith='Test Movie'))
        for movie in all_movies:
            movie.genres.set(random.sample(genres, k=random.randint(1, 3)))

        self.stdout.write(self.style.SUCCESS(f'Done. {len(all_movies)} test movies in the database.'))
