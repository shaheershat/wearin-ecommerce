# core/tasks.py
from celery import shared_task # Import shared_task decorator
from django.utils import timezone
from django.db import transaction # Import for atomic operations

# We will import Product, CartItem, NotificationSubscription directly within
# the product.release_reservation() method to avoid circular imports if
# core.models imports core.tasks or core.emails.

import logging

logger = logging.getLogger(__name__)

@shared_task # Apply the decorator to make this a Celery task
def release_expired_reservations_task():
    """
    Celery task to find and release expired product reservations.
    This task will be scheduled by Celery Beat to run periodically.
    """
    logger.info("Starting release_expired_reservations_task...")
    now = timezone.now()

    # Get products that are reserved, their reservation has expired, and are not yet sold
    # Use select_for_update to lock rows during this critical operation to prevent race conditions.
    # This ensures that while we are processing a product's reservation, no other process
    # (e.g., an add_to_cart request) can modify it.
    from core.models import Product # Import Product here, within the function or at the top

    products_to_release = Product.objects.select_for_update().filter(
        reserved_by_user__isnull=False,  # Must be reserved by someone
        reservation_expires_at__lte=now, # Reservation has expired or passed
        is_sold=False                   # And not already marked as sold
    )

    if not products_to_release.exists():
        logger.info("No expired product reservations found.")
        return

    for product in products_to_release:
        with transaction.atomic(): # Ensure each product's release is atomic
            logger.info(f"Processing expired reservation for product: {product.name} (ID: {product.id})")
            
            # The release_reservation method on the Product model now handles
            # clearing reservation fields, sending emails, and removing cart items.
            # This design keeps the model responsible for its own state changes.
            product.release_reservation()
            
            logger.info(f"Successfully released reservation for product: {product.name}.")

    logger.info("Finished release_expired_reservations_task.")