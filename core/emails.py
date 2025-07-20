from core.models import Product, Cart, CartItem, Order, OrderItem, Address, Wallet # Make sure Order is here
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings
from django.utils.html import strip_tags
from celery import shared_task
from django.utils import timezone
import logging
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse
import logging
from django.conf import settings

# Initialize logger at the top
logger = logging.getLogger(__name__)

User = get_user_model() # Make sure this is defined for tasks that need it

# CONSOLIDATED _send_email helper function (removed the duplicate)
def _send_email(subject, template_name, context, recipient_list):
    """Helper function to send HTML emails with enhanced logging."""
    if not recipient_list:
        logger.info(f"No recipients specified for email '{subject}'. Skipping.")
        return False # Indicate failure due to no recipients

    html_message = render_to_string(template_name, context)
    plain_message = strip_tags(html_message)
    
    try:
        # Using EmailMultiAlternatives is generally better than send_mail directly for HTML
        msg = EmailMultiAlternatives(subject, plain_message, settings.DEFAULT_FROM_EMAIL, recipient_list)
        msg.attach_alternative(html_message, "text/html") # Attach HTML version
        msg.send()
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
    
    product_url = f"{getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')}/product-detail/{product_id}/" # Adjust URL as per your actual URL patterns
    
    subject = f"Good News! '{product_name}' is Now Available on Wearin"
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
    
    subject = f"Your Reservation for '{product_name}' on Wearin Has Expired"
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
        subscribers = NotificationSubscription.objects.filter(product=product, event_type='available', notified_at__isnull=True)
        
        recipient_emails = []
        for sub in subscribers:
            if sub.user and sub.user.email and sub.user.email != purchaser_email: # Exclude purchaser if provided
                recipient_emails.append(sub.user.email)
        
        if not recipient_emails:
            logger.info(f"No subscribers to notify for product '{product_name}' being sold.")
            return

        subject = f"Update: '{product_name}' Has Been Sold on Wearin"
        product_url = f"{getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')}/product-detail/{product_id}/"
        context = {
            'product_name': product_name,
            'product_url': product_url,
        }
        
        if _send_email(subject, 'emails/product_sold.html', context, recipient_emails):
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


@shared_task
def send_order_confirmation_email(order_id):
    """
    Sends an order confirmation email to the user.
    """
    logger.info(f"Attempting to send order confirmation email for Order ID: {order_id}.")

    try:
        order = Order.objects.get(id=order_id)
        # Force refresh to ensure the latest database schema is used
        order.refresh_from_db() # ADDED THIS LINE
        user = order.user

        if not user.email:
            logger.warning(f"User {user.username} (ID: {user.id}) has no email. Skipping order confirmation email for order {order_id}.")
            return

        invoice_absolute_url = settings.SITE_URL + reverse('download_invoice', args=[order.id])

        context = {
            'order': order,
            'user': user,
            'order_items': order.items.all(),
            'shipping_address': order.address,
            'site_name': settings.SITE_NAME,
            'site_url': getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000'),
            'invoice_download_url': invoice_absolute_url,
        }

        subject = f"Your Order #{order.custom_order_id} Confirmation from {settings.SITE_NAME}"
        template_name = 'emails/order_confirmation.html'

        if _send_email(subject, template_name, context, [user.email]):
            logger.info(f"Successfully queued order confirmation email for order {order.custom_order_id} to {user.email}.")
        else:
            logger.error(f"Failed to queue order confirmation email for order {order.custom_order_id} to {user.email}.")

    except Order.DoesNotExist:
        logger.error(f"Order with ID {order_id} not found for sending confirmation email. Skipping.")
    except Exception as e:
        logger.error(f"Unexpected error in send_order_confirmation_email for order {order_id}: {e}", exc_info=True)

@shared_task
def send_order_cancelled_email(order_id):
    """
    Sends an email to the user when their order has been cancelled.
    """
    logger.info(f"Attempting to send order cancellation email for Order ID: {order_id}.")
    try:
        order = Order.objects.get(id=order_id)
        order.refresh_from_db() # ADDED THIS LINE
        user = order.user

        if not user.email:
            logger.warning(f"User {user.username} (ID: {user.id}) has no email. Skipping order cancellation email for order {order_id}.")
            return

        context = {
            'order': order,
            'user': user,
            'order_items': order.items.all(), # Pass order items to the template
            'site_name': settings.SITE_NAME,
        }

        subject = f"Your Order #{order.custom_order_id} has been Cancelled - {settings.SITE_NAME}"
        template_name = 'emails/order_cancelled.html'

        if _send_email(subject, template_name, context, [user.email]):
            logger.info(f"Successfully queued order cancellation email for order {order.custom_order_id} to {user.email}.")
        else:
            logger.error(f"Failed to queue order cancellation email for order {order.custom_order_id} to {user.email}.")

    except Order.DoesNotExist:
        logger.error(f"Order with ID {order_id} not found for sending cancellation email. Skipping.")
    except Exception as e:
        logger.error(f"Unexpected error in send_order_cancelled_email for order {order_id}: {e}", exc_info=True)


@shared_task
def send_return_processed_email(return_request_id, status): # <--- Note argument names
    """
    Sends an email to the user when their return request has been processed (approved/rejected).
    """
    logger.info(f"Attempting to send return processed email for Return Request ID: {return_request_id} with status: {status}.")
    try:
        from core.models import ReturnRequest # Local import to avoid circular dependency
        return_request = ReturnRequest.objects.get(id=return_request_id)
        return_request.refresh_from_db()
        order = return_request.order
        user = return_request.user

        if not user.email:
            logger.warning(f"User {user.username} (ID: {user.id}) has no email. Skipping return processed email for request {return_request_id}.")
            return

        context = {
            'return_request': return_request,
            'order': order,
            'user': user,
            'returned_items': return_request.requested_items.all(), # <--- This is the correct way
            'status': status,
            'site_name': settings.SITE_NAME,
        }

        subject = f"Your Return Request #{return_request.id} for Order #{order.custom_order_id} has been {status} - {settings.SITE_NAME}"
        template_name = 'emails/return_processed.html'

        if _send_email(subject, template_name, context, [user.email]):
            logger.info(f"Successfully queued return processed email for request {return_request.id} to {user.email}.")
        else:
            logger.error(f"Failed to queue return processed email for request {return_request.id} to {user.email}.")

    except ReturnRequest.DoesNotExist:
        logger.error(f"ReturnRequest with ID {return_request_id} not found for sending processed email. Skipping.")
    except Exception as e:
        logger.error(f"Unexpected error in send_return_processed_email for request {return_request_id}: {e}", exc_info=True)