from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        'Creates (or resets) a dedicated demo admin account for grading/report '
        'purposes. Use THIS account\'s credentials in your report -- never your '
        'own personal login, even if it also has staff/superuser access.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--username', default='demo_admin')
        parser.add_argument('--password', default='Demo@12345')
        parser.add_argument('--email', default='demo_admin@example.com')

    def handle(self, *args, **options):
        username = options['username']
        password = options['password']
        email = options['email']

        user, created = User.objects.get_or_create(username=username, defaults={'email': email})
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        # set_password() hashes with Django's default PBKDF2-SHA256 hasher --
        # the plaintext value is never stored, here or anywhere else.
        user.set_password(password)
        user.save()

        action = 'Created' if created else 'Reset'
        self.stdout.write(self.style.SUCCESS(f'{action} demo admin account.'))
        self.stdout.write(f'  Username: {username}')
        self.stdout.write(f'  Password: {password}')
        self.stdout.write(self.style.WARNING(
            '\nUse these credentials in your report/demo -- this account exists '
            'specifically so you never have to share your real personal password.'
        ))
