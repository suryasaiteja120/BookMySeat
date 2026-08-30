import logging
import os
import requests

from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sites.shortcuts import get_current_site
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .forms import UserRegisterForm, UserUpdateForm
from movies.models import Movie, Booking

logger = logging.getLogger('movies.email')


def home(request):
    movies = Movie.objects.all()
    return render(request, 'users/home.html', {'movies': movies})


def register(request):
    if request.method == 'POST':
        form = UserRegisterForm(request.POST)

        if form.is_valid():
            form.save()

            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password1')

            user = authenticate(
                username=username,
                password=password
            )

            login(request, user)

            return redirect('profile')

    else:
        form = UserRegisterForm()

    return render(request, 'users/register.html', {'form': form})


def login_view(request):
    if request.method == 'POST':
        form = AuthenticationForm(
            request,
            data=request.POST
        )

        if form.is_valid():
            user = form.get_user()
            login(request, user)

            return redirect('/')

    else:
        form = AuthenticationForm()

    return render(request, 'users/login.html', {'form': form})


@login_required
def profile(request):
    bookings = Booking.objects.filter(
        user=request.user
    )

    if request.method == 'POST':
        form = UserUpdateForm(
            request.POST,
            instance=request.user
        )

        if form.is_valid():
            form.save()
            return redirect('profile')

    else:
        form = UserUpdateForm(
            instance=request.user
        )

    return render(
        request,
        'users/profile.html',
        {
            'u_form': form,
            'bookings': bookings
        }
    )


@login_required
def reset_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(
            user=request.user,
            data=request.POST
        )

        if form.is_valid():
            form.save()
            return redirect('login')

    else:
        form = PasswordChangeForm(
            user=request.user
        )

    return render(
        request,
        'users/reset_password.html',
        {'form': form}
    )


def password_reset(request):
    """
    Custom forgot-password view.

    Generates Django's secure password-reset token and sends
    the reset email through the Brevo API instead of SMTP.
    """

    if request.method == 'GET':
        return render(
            request,
            'users/password_reset.html'
        )

    email = request.POST.get('email', '').strip()

    if not email:
        return render(
            request,
            'users/password_reset.html',
            {
                'error': 'Please enter your email address.'
            }
        )

    from django.contrib.auth import get_user_model

    User = get_user_model()

    users = list(
        User._default_manager.filter(
            email__iexact=email,
            is_active=True
        )
    )

    # Always show the same success page, even when the email
    # does not exist. This prevents revealing whether an account
    # exists for a particular email address.
    for user in users:

        if not user.has_usable_password():
            continue

        uid = urlsafe_base64_encode(
            force_bytes(user.pk)
        )

        token = default_token_generator.make_token(user)

        domain = get_current_site(request).domain

        reset_url = (
            f'https://{domain}'
            f'/password-reset-confirm/{uid}/{token}/'
        )

        context = {
            'email': user.email,
            'user': user,
            'domain': domain,
            'uid': uid,
            'token': token,
            'protocol': 'https' if request.is_secure() else 'http',
            'reset_url': reset_url,
        }

        subject = render_to_string(
            'users/password_reset_subject.txt',
            context
        ).strip()

        text_body = render_to_string(
            'users/password_reset_email.txt',
            context
        )

        html_body = f"""
        <html>
        <body>
            <h2>Reset your BookMySeat password</h2>

            <p>Hello {user.get_full_name() or user.username},</p>

            <p>
                We received a request to reset your BookMySeat password.
            </p>

            <p>
                Click the button below to choose a new password:
            </p>

            <p>
                <a href="{reset_url}"
                   style="
                       display:inline-block;
                       padding:12px 20px;
                       background:#2563eb;
                       color:white;
                       text-decoration:none;
                       border-radius:6px;
                       font-weight:bold;
                   ">
                    Reset Password
                </a>
            </p>

            <p>
                If you did not request this password reset,
                you can safely ignore this email.
            </p>

            <p>
                This link will expire after Django's normal
                password-reset validity period.
            </p>

            <p>
                Regards,<br>
                BookMySeat Team
            </p>
        </body>
        </html>
        """

        try:
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
            )

            email.attach_alternative(
                html_body,
                'text/html'
            )

            email.send(fail_silently=False)

            logger.info(
                'Password reset email sent to user %s',
                user.username
            )

        except Exception:
            logger.exception(
                'Failed to send password reset email to user %s',
                user.username
            )

        except Exception:
            logger.exception(
                'Failed to send password reset email to user %s',
                user.username
            )

    return redirect('password_reset_done')