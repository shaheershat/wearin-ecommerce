# core/tasks.py

from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from core.models import Product, CartItem
from core.emails import send_reservation_expired_email, send_product_available_email
from django.db import transaction
import logging

logger = logging.getLogger(__name__) # Ensure this is at the top of the file

@shared_task
def release_expired_reservations_task():
    logger.info("Starting release_expired_reservations_task...")
    now = timezone.now()

    # Find products with expired reservations
    # Ensure to only get products that are actually reserved
    expired_products = Product.objects.filter(
        reserved_by_user__isnull=False,
        reservation_expires_at__lt=now
    )
    logger.info(f"Found {expired_products.count()} product(s) with expired reservations.")

    for product in expired_products:
        logger.info(f"Processing expired reservation for product: {product.name} (ID: {product.id})")
        user_who_reserved = product.reserved_by_user # Store user before clearing

        try:
            with transaction.atomic():
                # 1. Release the product reservation
                logger.info(f"Attempting to release reservation for product: {product.name} (ID: {product.id})")
                product.reserved_by_user = None
                product.reservation_expires_at = None
                product.save()
                logger.info(f"Reservation fields cleared for product {product.name}.")

                # 2. Remove corresponding CartItem(s) for the user whose reservation expired
                if user_who_reserved:
                    # Filter by both user and product to ensure we get the right CartItem(s)
                    # Use .all() to get a QuerySet that we can iterate and delete from
                    cart_items_to_remove = CartItem.objects.filter(
                        user=user_who_reserved,
                        product=product,
                        is_reserved=True # Only remove if it was marked as reserved
                    )
                    removed_count = cart_items_to_remove.count()
                    if removed_count > 0:
                        cart_items_to_remove.delete()
                        logger.info(f"Removed {removed_count} cart item(s) for product {product.name} from {user_who_reserved.username}'s cart after reservation expiry.")
                    else:
                        logger.warning(f"No reserved cart items found for product {product.name} and user {user_who_reserved.username} to remove, even though product was reserved by them.")
                else:
                    logger.warning(f"Product {product.name} had an expired reservation but no 'reserved_by_user' was found when processing. Skipping cart item removal for this product.")


            # 3. Send reservation expired email to the user whose reservation was released
            if user_who_reserved and user_who_reserved.email:
                logger.info(f"Queuing reservation expired email for {user_who_reserved.email}.")
                send_reservation_expired_email.delay(user_who_reserved.email, product.name)
            else:
                logger.warning(f"Skipping reservation expired email for product {product.name}: No user or email found.")


            # 4. Notify waiting users (subscribers) that the product is available
            # This logic depends on how you handle subscribers.
            # Assuming Product has a ManyToMany field 'subscribers' or similar
            # Or you might have a separate Notification model
            subscribers = product.subscribers.all() # Assuming you have a 'subscribers' field on Product
            if subscribers.exists():
                logger.info(f"Found {subscribers.count()} pending subscribers for product {product.name}.")
                for subscriber in subscribers:
                    # Check if the subscriber is *not* the user whose reservation just expired
                    # This prevents sending "available" email immediately after "expired" email
                    if subscriber != user_who_reserved:
                        logger.info(f"Queuing product available email for subscriber {subscriber.email}.")
                        send_product_available_email.delay(subscriber.email, product.name)
                    else:
                        logger.info(f"Skipping product available email for {subscriber.email} as their reservation just expired.")
            else:
                logger.info(f"No pending subscribers for product {product.name}.")

            logger.info(f"Finished attempting to release reservation for product: {product.name} (ID: {product.id}).")
            logger.info(f"Successfully released reservation for product: {product.name}.")

        except Exception as e:
            logger.error(f"Error releasing reservation for product {product.name} (ID: {product.id}): {e}", exc_info=True)

    logger.info("Finished release_expired_reservations_task.")