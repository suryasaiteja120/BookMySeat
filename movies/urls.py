from django.urls import path
from . import views
from . import dashboard_views
urlpatterns = [
    path('', views.movie_list, name='movie_list'),
    path('<int:movie_id>/theaters', views.theater, name='theater_list'),
    path('theater/<int:theater_id>/seats/book', views.book_seats, name='book_seats'),
    path('checkout/<int:order_id>/cancel/', views.payment_cancel, name='payment_cancel'),
    path('payment/callback/', views.payment_callback, name='payment_callback'),
    path('payment/webhook/', views.stripe_webhook, name='stripe_webhook'),
    path('admin-dashboard/', dashboard_views.dashboard_view, name='admin_dashboard'),
    path('admin-dashboard/api/', dashboard_views.dashboard_api, name='admin_dashboard_api'),
]
