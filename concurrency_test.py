"""
Genuine concurrency test for Task 5 -- fires N real threads, each with its
own DB connection, all attempting to hold the SAME seat at nearly the same
instant (synchronized with a threading.Barrier so they start together, not
sequentially). This is the only way to actually prove a race condition is
prevented, as opposed to asserting it in a comment.

Run with: python concurrency_test.py
"""
import os
import threading

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bookmyseat.settings')
import django
django.setup()

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import connections
from django.utils import timezone
import datetime

from movies.models import Movie, Theater, seats as Seats, Order
from movies.booking import create_seat_hold, SeatUnavailableError

NUM_THREADS = 15
results = []
results_lock = threading.Lock()


def attempt_hold(user, theater_obj, seat_id, barrier, thread_num):
    # Each thread must use its own DB connection -- Django connections are
    # NOT safe to share across threads.
    connections.close_all()
    barrier.wait()  # <-- all threads release here at (as close to) the same instant
    try:
        order = create_seat_hold(user, theater_obj, [seat_id], f'concurrency-test-{thread_num}')
        with results_lock:
            results.append(('SUCCESS', thread_num, order.id))
    except SeatUnavailableError:
        with results_lock:
            results.append(('REJECTED', thread_num, None))
    except Exception as exc:
        with results_lock:
            results.append(('ERROR', thread_num, repr(exc)))
    finally:
        connections.close_all()


def main():
    # --- setup ---
    User.objects.filter(username__startswith='racer').delete()
    users = [User.objects.create_user(f'racer{i}', f'racer{i}@test.com', 'pass') for i in range(NUM_THREADS)]

    movie = Movie(name='Concurrency Test Movie', rating=7.0, cast='X', description='d', language='en')
    movie.image.save('c.png', ContentFile(b'x'), save=True)
    theater_obj = Theater.objects.create(
        name='Test Theater', movie=movie,
        time=timezone.now() + datetime.timedelta(days=1), price_per_seat=200,
    )
    seat = Seats.objects.create(Theater=theater_obj, seat_number='Z1')

    print(f'Launching {NUM_THREADS} threads, all targeting seat {seat.seat_number} (id={seat.id}) simultaneously...\n')

    barrier = threading.Barrier(NUM_THREADS)
    threads = []
    for i in range(NUM_THREADS):
        t = threading.Thread(target=attempt_hold, args=(users[i], theater_obj, seat.id, barrier, i))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r[0] == 'SUCCESS']
    rejections = [r for r in results if r[0] == 'REJECTED']
    errors = [r for r in results if r[0] == 'ERROR']

    print(f'Results: {len(successes)} SUCCESS, {len(rejections)} REJECTED, {len(errors)} ERROR (out of {NUM_THREADS} threads)\n')
    for r in sorted(results, key=lambda x: x[1]):
        print(f"  Thread {r[1]}: {r[0]}" + (f" (order {r[2]})" if r[0] == "SUCCESS" else (f" -- {r[2]}" if r[0]=="ERROR" else "")))

    print()
    seat.refresh_from_db()
    print(f'Final seat state: held_by_order_id={seat.held_by_order_id}, is_booked={seat.is_booked}')

    print()
    if len(successes) == 1 and len(rejections) == NUM_THREADS - 1 and len(errors) == 0:
        print('PASS: exactly one thread won the seat, all others were correctly and cleanly rejected. No errors, no double-hold.')
    else:
        print('FAIL: unexpected outcome -- see results above.')


if __name__ == '__main__':
    main()
