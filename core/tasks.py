# core/tasks.py

from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from core.models import Product, CartItem, NewsletterCampaign, NewsletterSubscriber # Added NewsletterCampaign, NewsletterSubscriber
from core.emails import send_reservation_expired_email, send_product_available_email
from django.db import transaction
import logging
from django.core.mail import send_mail, EmailMultiAlternatives # Added EmailMultiAlternatives
from django.template import Template, Context # For rendering email content
from django.conf import settings # To access settings like DEFAULT_FROM_EMAIL

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


@shared_task(bind=True, max_retries=3, default_retry_delay=300) # Retry after 5 minutes
def send_newsletter_task(self, campaign_id):
    """
    Celery task to send a newsletter campaign to all active subscribers.
    """
    try:
        campaign = NewsletterCampaign.objects.get(id=campaign_id)
        if not campaign.email_template:
            logger.error(f"Campaign {campaign.id} has no associated email template. Aborting send.")
            campaign.status = 'failed'
            campaign.save()
            return

        if campaign.status in ['sent', 'sending']:
            logger.warning(f"Campaign {campaign.id} is already '{campaign.status}'. Skipping re-send.")
            return

        campaign.status = 'sending'
        campaign.save()
        logger.info(f"Starting newsletter send for campaign: {campaign.title} (ID: {campaign.id})")

        # Get all active subscribers
        # Assuming you want to send to users who subscribed via NewsletterSubscriber model
        # You might also want to send to all active User accounts if they implicitly agreed.
        # For now, let's target NewsletterSubscriber model.
        subscribers = NewsletterSubscriber.objects.filter(is_active=True).values_list('email', flat=True)
        recipient_list = list(subscribers)
        
        # Optionally, if you also want to send to all active users (not superusers)
        # active_users_emails = list(User.objects.filter(is_active=True, is_superuser=False).values_list('email', flat=True))
        # recipient_list.extend(active_users_emails)
        # recipient_list = list(set(recipient_list)) # Remove duplicates

        if not recipient_list:
            logger.warning(f"No active subscribers found for campaign {campaign.id}.")
            campaign.status = 'failed' # Or 'completed_no_recipients'
            campaign.save()
            return

        # Prepare email content
        subject = campaign.email_template.subject
        html_content = campaign.email_template.html_content

        # Default context variables for the template
        # You can expand this with more dynamic content if needed
        email_context = {
            'campaign': campaign,
            'site_name': settings.SITE_NAME,
            'site_url': getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000'), # Fallback for SITE_URL
            # Add placeholders that your template might expect, e.g., for user-specific data
            # For general newsletters, user-specific data is usually limited.
        }

        # Render the template content
        template = Template(html_content)
        rendered_html_content = template.render(Context(email_context))
        
        # Text content is usually derived from HTML, or kept simple
        text_content = f"{subject}\n\n{campaign.email_template.name}\n\nVisit our site: {email_context['site_url']}"


        messages_sent = 0
        total_recipients = len(recipient_list)

        # Sending in batches to avoid overwhelming the mail server and for better logging
        batch_size = 50
        for i in range(0, total_recipients, batch_size):
            batch_recipients = recipient_list[i:i + batch_size]
            try:
                msg = EmailMultiAlternatives(
                    subject=subject,
                    body=text_content,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[], # 'to' is empty as we'll use bcc
                    bcc=batch_recipients # Use BCC for mass emails
                )
                msg.attach_alternative(rendered_html_content, "text/html")
                msg.send(fail_silently=False)
                messages_sent += len(batch_recipients)
                logger.info(f"Batch {i//batch_size + 1} for campaign {campaign.id} sent successfully. Sent {messages_sent}/{total_recipients} emails.")
            except Exception as e:
                logger.error(f"Error sending email batch for campaign {campaign.id} (Recipients: {batch_recipients}): {e}", exc_info=True)
                # Decide if you want to fail the whole campaign or just log and continue
                # For now, we'll log and continue with the next batch.
                # You might want more sophisticated error handling or retry logic here.
                campaign.status = 'failed' # Mark as failed if any batch fails
                campaign.save()
                raise self.retry(exc=e) # Retry the entire task if a batch fails

        campaign.status = 'sent'
        campaign.sent_count = messages_sent
        campaign.save()
        logger.info(f"Newsletter campaign {campaign.id} completed. Total emails sent: {messages_sent}.")

    except NewsletterCampaign.DoesNotExist:
        logger.error(f"Newsletter Campaign with ID {campaign_id} does not exist.")
    except Exception as e:
        logger.error(f"An unexpected error occurred in send_newsletter_task for campaign {campaign_id}: {e}", exc_info=True)
        if campaign:
            campaign.status = 'failed'
            campaign.save()
        raise self.retry(exc=e) # Re-raise to allow Celery to retry if configured