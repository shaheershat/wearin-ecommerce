# core/tasks.py
from celery import shared_task # Import shared_task decorator
from django.utils import timezone
from django.db import transaction # Import for atomic operations
from core.models import Product # Import Product at the top for clarity

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

    # Wrap the entire query and processing in a single atomic transaction.
    # This ensures that select_for_update is used within a transaction,
    # and all subsequent database operations for this task are atomic.
    with transaction.atomic(): # <--- Move this block UP to wrap the query
        products_to_release = Product.objects.select_for_update().filter(
            reserved_by_user__isnull=False,  # Must be reserved by someone
            reservation_expires_at__lte=now, # Reservation has expired or passed
            is_sold=False                   # And not already marked as sold
        )

        if not products_to_release.exists():
            logger.info("No expired product reservations found.")
            return # Exit the task if no products found

        logger.info(f"Found {products_to_release.count()} product(s) with expired reservations.")

        for product in products_to_release:
            # Each product's release operation is now part of the outer transaction.
            # The product.release_reservation() method itself does not need its own
            # @transaction.atomic decorator if it's always called within this outer one.
            logger.info(f"Processing expired reservation for product: {product.name} (ID: {product.id})")

            try:
                # The release_reservation method on the Product model handles
                # clearing reservation fields, sending emails, and removing cart items.
                product.release_reservation()
                logger.info(f"Successfully released reservation for product: {product.name}.")

            except Exception as e:
                # Log any errors that occur during the release_reservation process for a specific product
                logger.error(f"Error releasing reservation for product {product.name} (ID: {product.id}): {e}", exc_info=True)

    logger.info("Finished release_expired_reservations_task.")