from django.contrib import admin
from .models import Genre, Movie, Theater, seats, Booking

# Register your models here.
@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ['name']
    search_fields = ['name']

@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ['name', 'rating', 'cast', 'description']
    list_filter = ['language', 'genres']
    filter_horizontal = ['genres']

@admin.register(Theater)
class TheaterAdmin(admin.ModelAdmin):
    list_display = ['name', 'movie', 'time']

@admin.register(seats)
class SeatsAdmin(admin.ModelAdmin):
    list_display = ['Theater', 'seat_number', 'is_booked']

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ['user', 'seat', 'Movie', 'theatre', 'booked_at']
