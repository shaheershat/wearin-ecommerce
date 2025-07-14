# core/emails.py

from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings
from django.utils.html import strip_tags
from celery import shared_task
from django.utils import timezone
import logging
from core.models import Product # Import Product for specific tasks
from django.contrib.auth import get_user_model

# Initialize logger at the top
logger = logging.getLogger(__name__)

User = get_user_model() # Make sure this is defined for tasks that need it

def _send_email(subject, template_name, context, recipient_list):
    """Helper function to send HTML emails with enhanced logging."""
    if not recipient_list:
        logger.info(f"No recipients specified for email '{subject}'. Skipping.")
        return False # Indicate failure due to no recipients

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
        logger.info(f"Email '{subject}' sent successfully to {', '.join(recipient_list)}")
        return True # Indicate success
    except Exception as e:
        logger.error(f"Error sending email '{subject}' to {', '.join(recipient_list)}: {e}", exc_info=True)
        return False # Indicate failure

@shared_task
def send_product_available_email(recipient_email, product_name, product_id):
    """
    Sends an email to a single user informing them that a product is now available.
    """
    logger.info(f"Attempting to send product available email to {recipient_email} for product {product_name} (ID: {product_id}).")
    
    product_url = f"{getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')}/shop/{product_id}/" # Adjust URL as per your actual URL patterns
    
    subject = f"Good News! '{product_name}' is Now Available on Wearin'"
    context = {
        'product_name': product_name, 
        'product_url': product_url
    }
    
    if _send_email(subject, 'emails/product_available.html', context, [recipient_email]):
        logger.info(f"Successfully queued 'product available' email for {recipient_email}.")
    else:
        logger.error(f"Failed to queue 'product available' email for {recipient_email}.")


@shared_task
def send_reservation_expired_email(recipient_email, product_name, product_id):
    """
    Sends an email to the user whose product reservation has expired.
    """
    logger.info(f"Attempting to send reservation expired email to {recipient_email} for product {product_name} (ID: {product_id}).")
    
    product_url = f"{getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')}/product-detail/{product_id}/" # Adjust URL
    
    subject = f"Your Reservation for '{product_name}' on Wearin' Has Expired"
    context = {
        'product_name': product_name, 
        'product_url': product_url
    }
    
    if _send_email(subject, 'emails/reservation_expired.html', context, [recipient_email]):
        logger.info(f"Successfully queued 'reservation expired' email for {recipient_email}.")
    else:
        logger.error(f"Failed to queue 'reservation expired' email for {recipient_email}.")


@shared_task
def send_product_sold_email(product_name, product_id, purchaser_email=None):
    """
    Sends an email to users subscribed to know when a product is sold.
    (This is an *alternative* or *additional* notification type).
    """
    # Local import to prevent potential circular dependency if Product imports emails
    from core.models import NotificationSubscription
    
    logger.info(f"Attempting to send 'product sold' email for product {product_name} (ID: {product_id}).")

    try:
        product = Product.objects.get(id=product_id)
        
        # Filter for subscribers who want to know about 'sold' status or 'availability'
        # and haven't been notified for this event type yet.
        # It's usually better to have a specific 'event_type' like 'sold_notification'
        # if this is a distinct event from 'available'.
        # Assuming 'available' subscriptions also get notified when sold, or adjust event_type as needed.
        subscribers = NotificationSubscription.objects.filter(product=product, event_type='available', notified_at__isnull=True)
        
        recipient_emails = []
        for sub in subscribers:
            if sub.user and sub.user.email and sub.user.email != purchaser_email: # Exclude purchaser if provided
                recipient_emails.append(sub.user.email)
        
        if not recipient_emails:
            logger.info(f"No subscribers to notify for product '{product_name}' being sold.")
            return

        subject = f"Update: '{product_name}' Has Been Sold on Wearin'"
        product_url = f"{getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')}/product-detail/{product_id}/"
        context = {
            'product_name': product_name,
            'product_url': product_url,
        }
        
        if _send_email(subject, 'emails/product_sold.html', context, recipient_emails):
            # Mark these specific subscriptions as notified for the 'available' type
            # (assuming 'available' subscriptions also cover 'sold' if not distinct).
            # If you introduce `event_type='sold'`, you'd update those instead.
            subscribers.update(notified_at=timezone.now())
            logger.info(f"Successfully sent 'product sold' emails and updated {len(recipient_emails)} subscriber(s) for product {product_name}.")
        else:
            logger.error(f"Failed to send 'product sold' emails for product {product_name}.")

    except Product.DoesNotExist:
        logger.error(f"Product with ID {product_id} not found for 'product sold' email. Skipping notification.")
    except Exception as e:
        logger.error(f"Unexpected error in send_product_sold_email for product {product_id}: {e}", exc_info=True)


@shared_task
def send_product_removed_from_cart_email(user_id, product_id):
    """
    Sends an email to a user when a product is removed from their cart.
    This is distinct from reservation expiry. E.g., if a user manually removes it.
    """
    logger.info(f"Attempting to send 'product removed from cart' email to user ID {user_id} for product ID {product_id}.")
    try:
        user = User.objects.get(id=user_id)
        product = Product.objects.get(id=product_id)

        subject = f"Item Removed from Your Cart: {product.name}"
        context = {
            'user': user,
            'product': product,
            'product_url': f"{getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')}/productdetail/{product_id}/"
        }
        
        if _send_email(subject, 'emails/product_removed_from_cart.html', context, [user.email]):
            logger.info(f"Successfully sent 'product removed from cart' email to {user.email} for product {product.name}.")
        else:
            logger.error(f"Failed to send 'product removed from cart' email to {user.email} for product {product.name}.")

    except (User.DoesNotExist, Product.DoesNotExist) as e:
        logger.error(f"Failed to send product removed from cart email. User ID {user_id} or Product ID {product_id} not found: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error sending product removed from cart email for user {user_id}, product {product_id}: {e}", exc_info=True)