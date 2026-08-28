from django.contrib import admin
from django.contrib import auth
from django.urls import path
from .views import register, login_view, profile, reset_password, home
import django.contrib.auth.views  # needed so `auth.views.*` below resolves

urlpatterns = [
    path('', home, name='home'),
    path('register/', register, name='register'),
    path('login/', login_view, name='login'),
    path('profile/', profile, name='profile'),
    path('reset_password/', reset_password, name='password_reset'),
    path('logout/', auth.views.LogoutView.as_view(template_name="users/logout.html"), name='logout'),
    path('password-reset/',
         auth.views.PasswordResetView.as_view(template_name='users/password_reset.html'),
         name='password_reset'),
    path('password-reset/done/',
         auth.views.PasswordResetDoneView.as_view(template_name='users/password_reset_done.html'),
         name='password_reset_done'),
    path('password-reset-confirm/<uidb64>/<token>/',
         auth.views.PasswordResetConfirmView.as_view(template_name='users/password_reset_confirm.html'),
         name='password_reset_confirm'),
    path('password-reset-complete/',
         auth.views.PasswordResetCompleteView.as_view(template_name='users/password_reset_complete.html'),
         name='password_reset_complete'),
]