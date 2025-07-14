# test_reservation.py
# Place this file in your project's root directory or in a 'scripts' directory.

from django.contrib.auth import get_user_model
from core.models import Product, CartItem, Cart # Import Cart model
from django.utils import timezone
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)

User = get_user_model()

# --- Configuration for your test ---
test_product_id = 95
test_user_email = 'hehshqbe@gmail.com' # Use an email you can access

print(f"--- Attempting to set up test product {test_product_id} for user {test_user_email} ---")

# Initialize product to None to avoid NameError if Product.DoesNotExist
product = None
test_user = None

try:
    test_user = User.objects.get(email=test_user_email)
    print(f"Found test user: {test_user.username}")
except User.DoesNotExist:
    print(f"Error: User with email '{test_user_email}' not found. Please create this user or change 'test_user_email' to an existing one.")

if test_user: # Only proceed if the user exists
    try:
        product = Product.objects.get(id=test_product_id)
        print(f"Found product: {product.name}")
    except Product.DoesNotExist:
        print(f"Error: Product with ID {test_product_id} not found. Please ensure it exists.")

    if product: # Only proceed if both user and product exist
        # 1. Set the product as reserved by the test user and make it expired
        product.reserved_by_user = test_user
        product.reservation_expires_at = timezone.now() - timedelta(minutes=10) # Set to an expired time
        product.save()
        print(f"Product '{product.name}' (ID: {product.id}) manually set as reserved by '{test_user.email}' and expired at '{product.reservation_expires_at}'.")

        # --- NEW: Get or Create the Cart for the test_user ---
        user_cart, created_cart = Cart.objects.get_or_create(user=test_user)
        if created_cart:
            print(f"Created new Cart (ID: {user_cart.id}) for user '{test_user.email}'.")
        else:
            print(f"Found existing Cart (ID: {user_cart.id}) for user '{test_user.email}'.")

        # --- UPDATED: Ensure a corresponding CartItem exists and is marked as reserved ---
        cart_item, created_item = CartItem.objects.get_or_create(
            user=test_user,
            product=product,
            cart=user_cart,
            defaults={'quantity': 1, 'is_reserved': True, 'reserved_until': timezone.now() + timedelta(minutes=15)}
        )
        if not created_item:
            if not cart_item.is_reserved:
                cart_item.is_reserved = True
                cart_item.save()
                print(f"Updated existing CartItem (ID: {cart_item.id}) for '{product.name}' to be reserved.")
            else:
                print(f"Existing CartItem (ID: {cart_item.id}) for '{product.name}' is already reserved.")
        else:
            print(f"Created new CartItem (ID: {cart_item.id}) for '{product.name}' and '{test_user.email}' within Cart (ID: {user_cart.id}).")

        # --- Manually trigger the Celery task directly from the shell ---
        from core.tasks import release_expired_reservations_task
        print("\n--- Manually triggering 'release_expired_reservations_task.delay()' ---")
        release_expired_reservations_task.delay()
        print("Task sent to Celery. Check your Celery worker logs and email inbox!")

    else:
        print("\n--- Skipping task trigger as product was not found. ---")
else:
    print("\n--- Skipping task trigger as user was not found. ---")

print("\n--- Shell script finished ---")