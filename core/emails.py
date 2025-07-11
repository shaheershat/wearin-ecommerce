# core/emails.py
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings
from django.utils.html import strip_tags
from celery import shared_task # Import shared_task
from django.utils import timezone # Import timezone

# We will import Product and NotificationSubscription locally within the tasks
# to prevent potential circular import issues.
from django.contrib.auth import get_user_model

User = get_user_model() # Make sure this is defined
logger = logging.getLogger(__name__)
import logging
logger = logging.getLogger(__name__) # Set up logging

def _send_email(subject, template_name, context, recipient_list):
    """Helper function to send HTML emails."""
    if not recipient_list:
        logger.info(f"No recipients for email '{subject}'. Skipping.")
        return

    html_message = render_to_string(template_name, context)
    plain_message = strip_tags(html_message)
    
    try:
        send_mail(
            subject,
            plain_message,
            settings.DEFAULT_FROM_EMAIL,
            recipient_list,
            html_message=html_message,
            fail_silently=False,
        )
        logger.info(f"Email '{subject}' sent to {', '.join(recipient_list)}")
        return True # Indicate success
    except Exception as e:
        logger.error(f"Error sending email '{subject}' to {', '.join(recipient_list)}: {e}")
        return False # Indicate failure

@shared_task # Make this an asynchronous Celery task
def send_product_available_email(recipient_email, product_name, product_id):
    """
    Sends an email to a single user informing them that a product is now available.
    Called by product.release_reservation().
    """
    # product_url relies on settings.SITE_URL (configured in your settings.py for Celery)
    product_url = f"{getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')}/shop/{product_id}/" # Adjust URL as per your actual URL patterns
    
    subject = f"Good News! '{product_name}' is Now Available on Wearin'"
    context = {
        'product_name': product_name, 
        'product_url': product_url
    }
    
    _send_email(subject, 'emails/product_available.html', context, [recipient_email])


@shared_task # Make this an asynchronous Celery task
def send_reservation_expired_email(recipient_email, product_name, product_id):
    """
    Sends an email to the user whose product reservation has expired.
    Called by product.release_reservation().
    """
    product_url = f"{getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')}/shop/{product_id}/" # Adjust URL
    
    subject = f"Your Reservation for '{product_name}' on Wearin' Has Expired"
    context = {
        'product_name': product_name, 
        'product_url': product_url
    }
    
    _send_email(subject, 'emails/reservation_expired.html', context, [recipient_email])


# --- Reviewing your existing functions for Celery integration and best practices ---

# The original send_product_sold_email and send_product_removed_from_cart_email
# seem to assume a slightly different workflow for notifications.
# In the current setup, `release_reservation` handles making products available.
# If a product is *sold*, it becomes `is_sold=True`, and no further notifications
# for "availability" should happen.
# If you still want to explicitly notify users that an item they subscribed to
# for *availability* has now been *sold*, you would need to define that logic
# elsewhere (e.g., in your order processing view) and have a dedicated
# 'product_sold' event_type in NotificationSubscription.

# For the current primary goal (reservation expiry and availability notification),
# `send_product_available_email` and `send_reservation_expired_email` are key.

# If `send_product_sold_email` is genuinely needed, it should also be a @shared_task:
@shared_task
def send_product_sold_email(product_name, product_id, purchaser_email=None):
    """
    Sends an email to users subscribed to know when a product is sold.
    (This is an *alternative* or *additional* notification type).
    Note: The previous version of this function attempted to get subscribers
    for 'available' and mark them notified when sold. If you want separate
    'sold' notifications, you need a different `event_type` in NotificationSubscription.
    For simplicity with the current design, we'll make it notify all.
    """
    from core.models import Product, NotificationSubscription # Local import
    
    try:
        product = Product.objects.get(id=product_id)
        
        # Filter for subscribers who want to know about 'sold' status or 'availability'
        # and haven't been notified for this event type yet.
        # It's usually better to have a specific 'event_type' like 'sold_notification'
        # if this is a distinct event from 'available'.
        subscribers = NotificationSubscription.objects.filter(product=product, event_type='available', notified_at__isnull=True)
        
        recipient_emails = []
        for sub in subscribers:
            if sub.user and sub.user.email and sub.user.email != purchaser_email: # Exclude purchaser if provided
                recipient_emails.append(sub.user.email)
        
        if not recipient_emails:
            logger.info(f"No subscribers to notify for product '{product_name}' being sold.")
            return

        subject = f"Update: '{product_name}' Has Been Sold on Wearin'"
        product_url = f"{getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')}/shop/{product_id}/"
        context = {
            'product_name': product_name,
            'product_url': product_url,
        }
        
        if _send_email(subject, 'emails/product_sold.html', context, recipient_emails):
            # Mark these specific subscriptions as notified for the 'available' type
            # (assuming 'available' subscriptions also cover 'sold' if not distinct).
            # If you introduce `event_type='sold'`, you'd update those instead.
            subscribers.update(notified_at=timezone.now())

    except Product.DoesNotExist:
        logger.error(f"Product with ID {product_id} not found for 'product sold' email.")
    except Exception as e:
        logger.error(f"Error sending 'product sold' email for product {product_id}: {e}")

# The `send_product_removed_from_cart_email` seems redundant with the `release_reservation` logic
# which already triggers `send_product_available_email` to subscribers.
# If a product is removed from someone's cart due to reservation expiry, it becomes
# available for purchase, and the `send_product_available_email` is the correct notification.
# If it's removed by the user manually, no specific "removed from cart" notification for
# subscribers is typically needed, as the product remains available.
# I'm commenting it out for now to avoid confusion and redundancy in the primary flow.
# If you have a specific use case for it, let me know.

# @shared_task
# def send_product_removed_from_cart_email(product_id):
#     # ... (Similar logic, make it a shared_task)
#     pass
@shared_task
def send_product_removed_from_cart_email(user_id, product_id): # It should take user_id as well
    try:
        user = User.objects.get(id=user_id)
        product = Product.objects.get(id=product_id)

        subject = f"Item Removed from Your Cart: {product.name}"
        message = render_to_string('emails/product_removed_from_cart.html', {
            'user': user,
            'product': product,
        })

        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email], html_message=message)
        logger.info(f"Product removed from cart email sent to {user.email} for product {product.name}")
    except (User.DoesNotExist, Product.DoesNotExist) as e:
        logger.error(f"Failed to send product removed from cart email: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error sending product removed from cart email for user {user_id}, product {product_id}: {e}")