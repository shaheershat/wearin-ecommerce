from django.template.loader import render_to_string
from celery import shared_task
from django.utils import timezone
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template import Template, Context
from django.conf import settings
from django.db import transaction
import logging
from django.utils.html import strip_tags

# Local imports from your core.models, keep them here
from core.models import (
    Product, CartItem, NewsletterCampaign,
    NewsletterSubscriber, User
)

# Assuming these are other tasks you import/use in this file
from core.emails import (
    send_reservation_expired_email,
    send_product_available_email
)

logger = logging.getLogger(__name__)


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
                # Assuming send_reservation_expired_email is a Celery task itself
                send_reservation_expired_email.delay(
                    user_who_reserved.email,
                    product.name,
                    product.id
                )
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
                    if subscriber != user_who_reserved: # Assuming subscriber is a User instance
                        logger.info(f"Queuing product available email for subscriber {subscriber.email}.")
                        # Assuming send_product_available_email is a Celery task itself
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


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def send_newsletter_task(self, campaign_id):
    try:
        campaign = NewsletterCampaign.objects.get(id=campaign_id)
        if not campaign.email_template:
            logger.error(f"Campaign {campaign.id} has no associated email template.")
            campaign.status = 'failed'
            campaign.save()
            return

        if campaign.status in ['sent', 'sending']:
            logger.warning(f"Campaign {campaign.id} is already '{campaign.status}'. Skipping.")
            return

        campaign.status = 'sending'
        campaign.save()

        # Build recipient list
        if campaign.recipients_type == 'all_users':
            recipient_list = list(User.objects.filter(is_active=True, is_superuser=False).values_list('email', flat=True))
        elif campaign.recipients_type == 'newsletter_subscribers':
            recipient_list = list(NewsletterSubscriber.objects.filter(is_active=True).values_list('email', flat=True))
        elif campaign.recipients_type == 'custom_list' and campaign.custom_recipient_emails:
            recipient_list = [email.strip() for email in campaign.custom_recipient_emails.split(',') if email.strip()]
        else:
            logger.warning(f"No recipients found for campaign {campaign.id}")
            campaign.status = 'failed'
            campaign.save()
            return

        recipient_list = list(set(recipient_list))

        subject = campaign.email_template.subject
        html_content = campaign.email_template.html_content
        text_content = f"{subject}\n\nVisit: {getattr(settings, 'SITE_URL', '')}"

        email_context = {
            'campaign': campaign,
            'site_name': getattr(settings, 'SITE_NAME', 'Your Site'),
            'site_url': getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000'),
        }

        template = Template(html_content)
        rendered_html_content = template.render(Context(email_context))

        messages_sent = 0
        batch_size = 50

        for i in range(0, len(recipient_list), batch_size):
            batch = recipient_list[i:i + batch_size]
            try:
                msg = EmailMultiAlternatives(
                    subject=subject,
                    body=text_content,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[],
                    bcc=batch,
                )
                msg.attach_alternative(rendered_html_content, "text/html")
                msg.send(fail_silently=False)
                messages_sent += len(batch)
                logger.info(f"Sent batch {i//batch_size + 1} for campaign {campaign.id}")
            except Exception as e:
                logger.error(f"Error sending batch {i//batch_size + 1} for campaign {campaign.id}: {e}", exc_info=True)
                campaign.status = 'failed'
                campaign.save()
                raise self.retry(exc=e)

        campaign.status = 'sent'
        campaign.sent_count = messages_sent
        campaign.sent_at = timezone.now()
        campaign.save()
        logger.info(f"Campaign {campaign.id} completed. {messages_sent} emails sent.")

    except NewsletterCampaign.DoesNotExist:
        logger.error(f"Campaign ID {campaign_id} not found.")
    except Exception as e:
        logger.error(f"Unexpected error in campaign {campaign_id}: {e}", exc_info=True)
        if 'campaign' in locals():
            campaign.status = 'failed'
            campaign.save()
        raise self.retry(exc=e)


def _run_scheduled_campaigns():
    now = timezone.now()
    campaigns = NewsletterCampaign.objects.filter(
        status='pending',
        scheduled_at__lte=now
    )

    for campaign in campaigns:
        try:
            send_newsletter_task.delay(campaign.id)
            campaign.status = 'queued'
            campaign.save()
        except Exception as e:
            campaign.status = 'failed'
            campaign.save()

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

# The CORRECTED version of send_return_processed_email task that you should be using
@shared_task
def send_return_processed_email(return_request_id, status):
    """
    Sends an email to the user when their return request has been processed (approved/rejected).
    """
    logger.info(f"Attempting to send return processed email for Return Request ID: {return_request_id} with status: {status}.")
    print(f"DEBUGGING: Task received return_request_id={return_request_id}, status='{status}'") # <--- DEBUG PRINT
    try:
        # Local import to avoid circular dependency
        # Ensure core.models.Order is imported here
        from core.models import ReturnRequest, Order, OrderItem, Wallet, WalletTransaction # Assuming Wallet and WalletTransaction are also in core.models and might be used in a larger context

        return_request = ReturnRequest.objects.get(id=return_request_id)
        return_request.refresh_from_db() # Ensure freshest data

        order = return_request.order # This is the line that *should* be causing the error (line 190 in your file)

        # --- ENSURE THIS CHECK IS PRESENT ---
        if not order:
            logger.error(f"Order for ReturnRequest ID {return_request_id} (associated Order ID: {return_request.order_id if hasattr(return_request, 'order_id') else 'N/A'}) does not exist. Skipping email.")
            print(f"DEBUGGING: SKIPPING EMAIL: Order for ReturnRequest ID {return_request_id} not found.") # <--- DEBUG PRINT
            return # Exit the task gracefully if the order is missing
        # --- END ENSURE CHECK ---

        user = return_request.user

        if not user.email:
            logger.warning(f"User {user.username} (ID: {user.id}) has no email. Skipping return processed email for request {return_request_id}.")
            return

        context = {
            'return_request': return_request,
            'order': order,
            'user': user,
            'returned_items': return_request.requested_items.all(),
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

@shared_task
def send_order_cancelled_email(order_id):
    from core.models import Order # Ensure Order is imported here

    order = Order.objects.get(id=order_id)
    subject = f"Order Cancelled - #{order.id}"
    message = render_to_string("emails/order_cancelled.html", {
        "order": order,
        "user": order.user,
    })
    send_mail(subject, "", settings.DEFAULT_FROM_EMAIL, [order.user.email], html_message=message)