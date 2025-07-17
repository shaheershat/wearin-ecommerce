# core/tasks.py

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
    # (same as your current implementation, no changes needed)
    ...


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



# @shared_task
# def send_scheduled_campaigns():
#     _run_scheduled_campaigns()
