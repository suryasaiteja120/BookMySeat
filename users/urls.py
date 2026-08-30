from django.contrib import auth
from django.urls import path
from django.contrib.auth import views as auth_views

from .views import (
    register,
    login_view,
    profile,
    reset_password,
    password_reset,
    home,
)


urlpatterns = [
    path('', home, name='home'),

    # Authentication
    path('register/', register, name='register'),
    path('login/', login_view, name='login'),
    path('logout/', auth_views.LogoutView.as_view(
        template_name='users/logout.html'
    ), name='logout'),

    # Change password / custom reset password
    path(
     'password-reset/',
     auth_views.PasswordResetView.as_view(
          template_name='users/password_reset.html',
          email_template_name='users/password_reset_email.txt',
          subject_template_name='users/password_reset_subject.txt',
          success_url='/password-reset/done/'
     ),
     name='password_reset'
     ),

    # Profile
    path('profile/', profile, name='profile'),

    # -------------------------------------------------
    # Forgot Password
    # -------------------------------------------------

    path(
     'password-reset/',
     password_reset,
     name='password_reset'
     ),

    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='users/password_reset_done.html'
        ),
        name='password_reset_done'
    ),

    path(
        'password-reset-confirm/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='users/password_reset_confirm.html',
            success_url='/password-reset-complete/'
        ),
        name='password_reset_confirm'
    ),

    path(
        'password-reset-complete/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='users/password_reset_complete.html'
        ),
        name='password_reset_complete'
    ),
]