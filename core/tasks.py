from django.template.loader import render_to_string
from celery import shared_task
from django.utils import timezone
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template import Template, Context
from django.conf import settings
from django.db import transaction
import logging

from core.models import (
    Product, CartItem, NewsletterCampaign,
    NewsletterSubscriber, User
)
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



@shared_task
def send_return_processed_email(order_id, returned_item_ids):
    from core.models import Order, OrderItem  # Lazy import to avoid circular import

    order = Order.objects.get(id=order_id)
    returned_items = OrderItem.objects.filter(id__in=returned_item_ids)

    subject = f"Return Processed for Order #{order.id}"
    message = render_to_string("emails/return_processed.html", {
        "order": order,
        "user": order.user,
        "returned_items": returned_items,
    })
    send_mail(subject, "", settings.DEFAULT_FROM_EMAIL, [order.user.email], html_message=message)

@shared_task
def send_order_cancelled_email(order_id):
    from core.models import Order

    order = Order.objects.get(id=order_id)
    subject = f"Order Cancelled - #{order.id}"
    message = render_to_string("emails/order_cancelled.html", {
        "order": order,
        "user": order.user,
    })
    send_mail(subject, "", settings.DEFAULT_FROM_EMAIL, [order.user.email], html_message=message)

