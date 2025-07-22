from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Product, CartItem # Make sure CartItem is imported
from django.db import transaction
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Expires overdue product reservations and notifies waiting users.'

    def handle(self, *args, **options):
        logger.info("Starting reservation expiry check.")
        self.stdout.write(self.style.SUCCESS("Starting reservation expiry check..."))

        now = timezone.now()

        expired_products = Product.objects.filter(
            reserved_by_user__isnull=False,
            reservation_expires_at__lt=now,
            is_sold=False
        ).select_for_update()

        if expired_products.exists():
            count = expired_products.count()
            logger.info(f"Found {count} product(s) with expired reservations.")
            self.stdout.write(self.style.WARNING(f"Found {count} product(s) with expired reservations."))

            for product in expired_products:
                logger.info(f"Processing expired reservation for product: {product.name} (ID: {product.id}). Reserved by: {product.reserved_by_user.email if product.reserved_by_user else 'N/A'}.")
                try:
                    with transaction.atomic(): 
                        initial_cart_items = CartItem.objects.filter(
                            user=product.reserved_by_user,
                            product=product,
                            is_reserved=True 
                        )
                        if initial_cart_items.exists():
                            logger.info(f"Found {initial_cart_items.count()} CartItem(s) for product {product.id} reserved by {product.reserved_by_user.email} before release.")
                        else:
                            logger.info(f"No active CartItem found for product {product.id} reserved by {product.reserved_by_user.email} before release.")

                        product.release_reservation()
                        logger.info(f"Successfully called release_reservation() for product: {product.name} (ID: {product.id}).")

                        remaining_cart_items = CartItem.objects.filter(
                            user=product.reserved_by_user,
                            product=product,
                            is_reserved=True
                        ).exists()
                        if not remaining_cart_items:
                            logger.info(f"Verified: CartItem for product {product.id} and user {product.reserved_by_user.email} appears to be removed/unreserved.")
                        else:
                            logger.warning(f"WARNING: CartItem for product {product.id} and user {product.reserved_by_user.email} *still exists* and is reserved after release_reservation().")


                except Exception as e:
                    logger.error(f"Error releasing reservation for product {product.name} (ID: {product.id}): {e}", exc_info=True)
                    self.stdout.write(self.style.ERROR(f"Error releasing reservation for {product.name}: {e}"))
        else:
            logger.info("No expired product reservations found.")
            self.stdout.write("No expired product reservations found.")

        logger.info("Finished reservation expiry check.")
        self.stdout.write(self.style.SUCCESS("Finished reservation expiry check."))