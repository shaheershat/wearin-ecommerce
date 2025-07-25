from django.db import models
from django.utils.text import slugify
from cloudinary.models import CloudinaryField
from django.utils import timezone
from django.contrib.auth import get_user_model
from decimal import Decimal
from datetime import timedelta
from django.urls import reverse 
import logging
from django.core.exceptions import ValidationError
from django.db.models import Sum

logger = logging.getLogger(__name__) 

class WalletTransaction(models.Model):
    wallet = models.ForeignKey('Wallet', on_delete=models.CASCADE, related_name='transactions')
    TRANSACTION_TYPE_CHOICES = (
        ('credit', 'Credit'),
        ('debit', 'Debit'),
        ('refund', 'Refund'),
    )
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.transaction_type.capitalize()} ₹{self.amount} for {self.wallet.user.username}"

User = get_user_model()

class EmailOTP(models.Model):
    email = models.EmailField()
    otp = models.CharField(max_length=6)
    purpose = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.email} - {self.otp} ({self.purpose})"

class OTP(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    purpose = models.CharField(max_length=20, choices=(('register', 'Register'), ('forgot', 'Forgot Password')))

    def is_expired(self):
        return timezone.now() > self.created_at + timezone.timedelta(minutes=5)

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    def __str__(self):
        return self.user.username

class Coupon(models.Model):
    code = models.CharField(max_length=50, unique=True, help_text="The unique code users will enter")
    discount = models.DecimalField(max_digits=10, decimal_places=2, help_text="Discount amount")
    min_purchase = models.DecimalField(max_digits=10, decimal_places=2, default=0,
                                       help_text="Minimum total price required to use this coupon")
    valid_from = models.DateTimeField(help_text="Date and time from which the coupon is valid")
    valid_to = models.DateTimeField(help_text="Date and time until which the coupon is valid")
    is_active = models.BooleanField(default=True, help_text="Whether the coupon is currently active")
    usage_limit = models.PositiveIntegerField(default=0, help_text="Maximum number of times this coupon can be used (0 for unlimited)")
    used_count = models.PositiveIntegerField(default=0, editable=False, help_text="Number of times this coupon has been used")

    applies_to_new_users_only = models.BooleanField(default=False,
                                                    help_text="If checked, only users with no prior orders can use this coupon.")
    min_orders_for_user = models.PositiveIntegerField(default=0,
                                                      help_text="Minimum number of successful orders a user must have to use this coupon. (0 means no minimum).")
    min_unique_products_in_cart = models.PositiveIntegerField(default=0,
                                                              help_text="Minimum number of different products in the cart to use this coupon. (0 means no minimum).")
    min_total_items_in_cart = models.PositiveIntegerField(default=0,
                                                          help_text="Minimum total quantity of items in the cart to use this coupon. (0 means no minimum).")
    

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Coupon"
        verbose_name_plural = "Coupons"
        ordering = ['-created_at']

    def __str__(self):
        return self.code

    def is_valid(self, cart_total_price, user=None, cart_items=None):
        now = timezone.now()
        if not self.is_active:
            return False, "This coupon is not active."
        if self.valid_from > now:
            return False, "This coupon is not yet valid."
        if self.valid_to < now:
            return False, "This coupon has expired."
        if self.usage_limit > 0 and self.used_count >= self.usage_limit:
            return False, "This coupon has reached its usage limit."
        if cart_total_price < self.min_purchase:
            return False, f"Minimum purchase of ₹{self.min_purchase} required."

        if user and user.is_authenticated: 
            if self.applies_to_new_users_only:
                from core.models import Order  
                if Order.objects.filter(user=user, status__in=['Delivered', 'Returned', 'Shipped', 'Processing', 'Pending']).exists():
                    return False, "This coupon is for new users only."

            if self.min_orders_for_user > 0:
                from core.models import Order  
                user_successful_orders_count = Order.objects.filter(user=user, status__in=['Delivered', 'Returned']).count()
                if user_successful_orders_count < self.min_orders_for_user:
                    return False, f"You must have at least {self.min_orders_for_user} previous successful orders to use this coupon."

        if cart_items is not None: 
            if self.min_unique_products_in_cart > 0:
                unique_products_count = cart_items.values('product').distinct().count()
                if unique_products_count < self.min_unique_products_in_cart:
                    return False, f"Your cart must contain at least {self.min_unique_products_in_cart} different products."

            if self.min_total_items_in_cart > 0:
                total_items_quantity = cart_items.aggregate(Sum('quantity'))['quantity__sum'] or 0
                if total_items_quantity < self.min_total_items_in_cart:
                    return False, f"Your cart must contain at least {self.min_total_items_in_cart} items in total."

        return True, "Coupon is valid."

    def apply_discount(self, total_price):
        if total_price <= self.discount:
            return Decimal('0.00')
        return total_price - self.discount


class Category(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.ForeignKey('Category', on_delete=models.CASCADE)
    size = models.CharField(max_length=10)
    is_sold = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    stock_quantity = models.PositiveIntegerField(default=1)

    reserved_by_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reserved_products')
    reservation_expires_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('product_detail', args=[self.id])

    @property
    def is_currently_reserved(self):
        if self.is_sold:
            return False

        return self.reserved_by_user is not None and \
               self.reservation_expires_at is not None and \
               self.reservation_expires_at > timezone.now()

    @property
    def is_available_for_purchase(self):
        return not self.is_sold and not self.is_currently_reserved

    def get_reservation_time_left_seconds(self):
        if self.is_currently_reserved:
            time_left = self.reservation_expires_at - timezone.now()
            return max(0, int(time_left.total_seconds()))
        return 0

    def release_reservation(self):
        logger.info(f"Attempting to release reservation for product: {self.name} (ID: {self.id})")

        if self.reserved_by_user and not self.is_sold:
            from core.emails import send_reservation_expired_email, send_product_available_email
            

            previous_reserved_user = self.reserved_by_user

            self.reserved_by_user = None
            self.reservation_expires_at = None
            self.save(update_fields=['reserved_by_user', 'reservation_expires_at'])
            logger.info(f"Reservation fields cleared for product {self.name}.")

            if previous_reserved_user:
                from core.models import CartItem
                deleted_count, _ = CartItem.objects.filter(
                    user=previous_reserved_user,
                    product=self,
                    is_reserved=True
                ).delete()

                if deleted_count > 0:
                    logger.info(f"Removed {deleted_count} cart item(s) for product {self.name} from {previous_reserved_user.username}'s cart after reservation expiry.")
                else:
                    logger.warning(f"No reserved cart item found for product {self.name} in {previous_reserved_user.username}'s cart after reservation expiry.")

                if previous_reserved_user.email:
                    send_reservation_expired_email.delay(
                        previous_reserved_user.email,
                        self.name,
                        self.id
                    )
                    logger.info(f"Queued reservation expired email for {previous_reserved_user.email}.")

            if self.is_available_for_purchase:
                from core.models import NotificationSubscription 
                subscribers = NotificationSubscription.objects.filter(
                    product=self,
                    event_type='available',
                    notified_at__isnull=True
                )
                if subscribers.exists():
                    logger.info(f"Found {subscribers.count()} pending subscribers for product {self.name}.")
                    for sub in subscribers:
                        if sub.user and sub.user.email and sub.user != previous_reserved_user:
                            send_product_available_email.delay(
                                sub.user.email,
                                self.name,
                                self.id
                            )
                            sub.notified_at = timezone.now()
                            sub.save(update_fields=['notified_at'])
                            logger.info(f"Queued product available email for subscriber {sub.user.email}.")
                        elif sub.user == previous_reserved_user:
                             logger.info(f"Skipping product available email for {sub.user.email} as their reservation just expired.")
                else:
                    logger.info(f"No pending subscribers for product {self.name} or already notified.")
            else:
                logger.info(f"Product {self.name} is not fully available for purchase yet after reservation expiry (e.g., stock=0 or another reservation placed).")
        else:
            logger.info(f"Product {self.name} was not actively reserved by a user or was already sold. No reservation to release.")

        logger.info(f"Finished attempting to release reservation for product: {self.name} (ID: {self.id}).")


class NotificationSubscription(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='product_notifications')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='subscribers')
    event_type = models.CharField(max_length=50, default='available')
    notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'product', 'event_type')

    def __str__(self):
        return f"{self.user.username} subscribed to {self.product.name} for {self.event_type}"


class ProductImage(models.Model):
    product = models.ForeignKey(Product, related_name='images', on_delete=models.CASCADE)
    image = CloudinaryField('image', folder='products')
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"Image for {self.product.name} (Order: {self.order})"

class Address(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='addresses')
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20)
    house_name = models.CharField(max_length=255, verbose_name="House Name/Thalavala")
    street = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    pincode = models.CharField(max_length=10)
    country = models.CharField(max_length=100, default='India')
    is_default = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "Addresses"

    def __str__(self):
        return f"{self.full_name}, {self.street}, {self.city}"

class Order(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Placed', 'Placed'), 
        ('Confirmed', 'Confirmed'),
        ('Processing', 'Processing'), 
        ('Shipped', 'Shipped'),
        ('Out of delivery', 'Out of delivery'),
        ('Delivered', 'Delivered'),
        ('Cancelled', 'Cancelled'),
        ('Returned', 'Returned'), 
    ]
    PAYMENT_STATUS_CHOICES = [
        ('Pending', 'Pending'), 
        ('Paid', 'Paid'),    
        ('Failed', 'Failed'),   
        ('Refunded', 'Refunded'), 
        ('Partially Refunded', 'Partially Refunded'), 
    ]
    PAYMENT_METHOD_CHOICES = [
        ('COD', 'Cash on Delivery'),
        ('Razorpay', 'Razorpay'),
        ('Wallet', 'Wallet'), 
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='orders') 
    address = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders') 
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00')) 
    
    coupon_discount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    shipping_charge = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='Pending')
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default='COD')
    razorpay_order_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_signature = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    custom_order_id = models.CharField(max_length=20, unique=True, blank=True, null=True)

    class Meta:
        ordering = ['-created_at'] 

    def save(self, *args, **kwargs):
        is_new = not self.pk
        old_order_status = None
        if not is_new and self.pk: 
            try:
                old_order = Order.objects.get(pk=self.pk)
                old_order_status = old_order.status
            except Order.DoesNotExist:
                pass 

        if is_new and not self.custom_order_id:
            prefix = "WN"
            last_custom_id_num = 0
            latest_order_with_custom_id = Order.objects.filter(
                custom_order_id__startswith=prefix,
                custom_order_id__regex=r'WN\d{4}' 
            ).order_by('-custom_order_id').first() 

            if latest_order_with_custom_id and latest_order_with_custom_id.custom_order_id:
                try:
                    last_custom_id_num = int(latest_order_with_custom_id.custom_order_id[len(prefix):])
                except ValueError:
                    logger.warning(f"Non-numeric part found in custom_order_id: {latest_order_with_custom_id.custom_order_id}. Falling back to PK for next ID generation.")
                    last_custom_id_num = Order.objects.all().order_by('-pk').values_list('pk', flat=True).first() or 0

            next_num = last_custom_id_num + 1

            for _ in range(10): 
                formatted_num = f"{next_num:04d}"
                new_custom_id = f"{prefix}{formatted_num}"

                if not Order.objects.filter(custom_order_id=new_custom_id).exists():
                    self.custom_order_id = new_custom_id
                    break
                next_num += 1

            if not self.custom_order_id:
                logger.error("Failed to generate a unique custom_order_id after multiple attempts.")

        super().save(*args, **kwargs)

        if not is_new and old_order_status != 'Delivered' and self.status == 'Delivered':
            for item in self.items.all(): 
                if item.product:
                    item.product.is_sold = True 
                    item.product.reserved_by_user = None
                    item.product.reservation_expires_at = None
                    item.product.save()

                    CartItem.objects.filter(product=item.product).delete()


    def __str__(self):
        return f"Order #{self.custom_order_id or self.id} by {self.user.username}"


class OrderItem(models.Model):
    order = models.ForeignKey('Order', on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('Product', on_delete=models.SET_NULL, null=True, blank=True) 
    quantity = models.PositiveIntegerField(default=1)
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)

    original_total_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'), 
        help_text="Original price (unit price * quantity) before item-level discounts."
    )
    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'), 
        help_text="Discount applied specifically to this item (e.g., product offers, BOGO)."
    )

    def __str__(self):
        product_name = self.product.name if self.product else "N/A"
        return f"{product_name} (x{self.quantity}) in Order #{self.order.custom_order_id or self.order.id}"

    def save(self, *args, **kwargs):
        if not self.pk and self.product:
            if self.price_at_purchase is None:
                self.price_at_purchase = self.product.price

            if self.original_total_price == Decimal('0.00'):
                self.original_total_price = self.product.price * self.quantity
            
            if self.discount_amount == Decimal('0.00'):
                self.discount_amount = self.original_total_price - (self.price_at_purchase * self.quantity)

        super().save(*args, **kwargs)



class Cart(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='cart')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Cart of {self.user.username}"

    def get_total_price(self):
        total = Decimal('0.00')
        for item in self.items.select_related('product').all():
            if item.product and (item.product.is_available_for_purchase or \
               (item.product.reserved_by_user == self.user and item.product.is_currently_reserved)):
                total += item.product.price * item.quantity
        return total

class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    product = models.ForeignKey('Product', on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)

    is_reserved = models.BooleanField(default=False)
    reserved_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('cart', 'product')

    def __str__(self):
        return f"{self.product.name} (x{self.quantity}) in {self.cart.user.username}'s cart"


class Wishlist(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='wishlist_items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'product')

    def __str__(self):
        return f"{self.product.name} in {self.user.username}'s wishlist"

class NewsletterSubscriber(models.Model):
    email = models.EmailField(unique=True)
    subscribed_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.email


    def __str__(self):
        return self.email

    class Meta:
        ordering = ['-subscribed_at']
        verbose_name = "Newsletter Subscriber"
        verbose_name_plural = "Newsletter Subscribers"

class Wallet(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='wallet')
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def credit(self, amount):
        self.balance += Decimal(amount)
        self.save()
        WalletTransaction.objects.create(wallet=self, transaction_type='credit', amount=amount, reason="Wallet credited")

    def debit(self, amount):
        amount = Decimal(amount)
        if self.balance >= amount:
            self.balance -= amount
            self.save()
            WalletTransaction.objects.create(wallet=self, transaction_type='debit', amount=amount, reason="Wallet debited")
            return True
        return False

    def __str__(self):
        return f"{self.user.username}'s Wallet"

class EmailTemplate(models.Model):
    name = models.CharField(max_length=100, unique=True, help_text="A unique name for this template (e.g., 'Promotional Offer', 'Welcome Newsletter')")
    subject = models.CharField(max_length=255, help_text="Email subject line. Can include {{ subject_var }} for dynamic content.")
    html_content = models.TextField(help_text="The HTML content of the email. Use Django template syntax for dynamic data. E.g., {{ user.username }}, {{ campaign.title }}.")
    plain_content = models.TextField(blank=True, null=True, help_text="Optional: Plain text version of the email for older clients or accessibility. Will be generated automatically if left blank.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.plain_content and self.html_content:
            from django.utils.html import strip_tags
            self.plain_content = strip_tags(self.html_content)
        super().save(*args, **kwargs)


class NewsletterCampaign(models.Model):
    CAMPAIGN_TYPE_CHOICES = [
        ('all_users', 'All Registered Users'),
        ('newsletter_subscribers', 'Newsletter Subscribers Only'),
        ('custom_list', 'Custom Email List'),
    ]

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('pending', 'Pending (Scheduled)'),
        ('sending', 'Sending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]

    title = models.CharField(max_length=255, help_text="Internal title for this newsletter campaign")
    email_template = models.ForeignKey(
        EmailTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name='campaigns'
    )
    recipients_type = models.CharField(max_length=50, choices=CAMPAIGN_TYPE_CHOICES)
    custom_recipient_emails = models.TextField(
        blank=True, null=True,
        help_text="Comma-separated list of emails if 'Custom Email List' is selected."
    )
    scheduled_at = models.DateTimeField(blank=True, null=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    sent_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='sent_newsletters'
    )
    total_recipients = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0, help_text="Number of emails actually sent")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-created_at']



class NewsletterSubscription(models.Model):
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.email

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Newsletter Subscriber"
        verbose_name_plural = "Newsletter Subscribers"

# ReturnReason Model
class ReturnReason(models.Model):
    reason_text = models.CharField(max_length=255, unique=True)
    requires_custom_input = models.BooleanField(default=False)

    def __str__(self):
        return self.reason_text

# ReturnRequest Model
class ReturnRequest(models.Model):
    STATUS_CHOICES = [
        ('Requested', 'Requested'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
        ('Processing Refund', 'Processing Refund'), # Optional intermediate state
        ('Refunded', 'Refunded'), # Final state after refund
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    order = models.ForeignKey(Order, on_delete=models.CASCADE)
    
    reason = models.ForeignKey(ReturnReason, on_delete=models.SET_NULL, null=True, blank=True)
    custom_reason = models.TextField(blank=True, null=True, help_text="User's custom reason if 'Other' was selected.")
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Requested')
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Return Request #{self.pk} for Order #{self.order.custom_order_id} ({self.status})"

class ReturnItem(models.Model):
    return_request = models.ForeignKey(ReturnRequest, on_delete=models.CASCADE, related_name='requested_items')
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ('return_request', 'order_item') # Prevent duplicate order_items in one request

    def __str__(self):
        product_name = self.order_item.product.name if self.order_item.product else "N/A"
        return f"{self.quantity} x {product_name} for Return Request #{self.return_request.pk}"

class OfferBanner(models.Model):
    """
    Model to store settings for a scrolling flash message banner.
    Designed to be a singleton (only one active instance at a time).
    """
    text_content = models.CharField(
        max_length=600,
        help_text="The text that will scroll on the banner."
    )
    text_color = models.CharField(
        max_length=7,
        default="#FFFFFF",
        help_text="Hex color code for the text (e.g., #FFFFFF)."
    )
    bg_color = models.CharField(
        max_length=7,
        default="#FF0000",
        help_text="Hex color code for the background (e.g., #FF0000)."
    )
    is_active = models.BooleanField(
        default=False,
        help_text="Enable or disable this banner. Only one banner can be active at a time."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Offer Banner"
        verbose_name_plural = "Offer Banners"
        ordering = ['-updated_at'] 

    def save(self, *args, **kwargs):
        if self.is_active:
            OfferBanner.objects.exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Banner: '{self.text_content[:30]}...' (Active: {self.is_active})"

class Offer(models.Model):
    """
    Model to define a specific offer that can be applied to multiple products.
    """
    OFFER_TYPE_CHOICES = [
        ('PERCENTAGE', 'Percentage Discount'),
        ('AMOUNT', 'Fixed Amount Discount'),
        ('BOGO1', 'Buy One Get One Free (B1G1)'), 
        ('BOGO2', 'Buy Two Get One Free (B2G1)'), 
        ('FREESHIP', 'Free Shipping (FS)'),
    ]

    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Name of the offer (e.g., 'Summer Sale', 'Flash Deal')."
    )
    offer_type = models.CharField(
        max_length=10,
        choices=OFFER_TYPE_CHOICES,
        default='PERCENTAGE', 
        help_text="Select the type of discount or offer."
    )

    tag_text = models.CharField(
        max_length=20,
        default="SALE",
        help_text="Text to display as a tag on product cards (e.g., 'SALE', '20% OFF', 'B1G1')."
    )
    background_color = models.CharField(
        max_length=7,
        default='#FF0000', 
        help_text="Hex color code for tag background (e.g., #FF0000 or a named color like 'red')"
    )
    text_color = models.CharField(
        max_length=7,
        default='#FFFFFF', 
        help_text="Hex color code for tag text (e.g., #FFFFFF or a named color like 'white')"
    )

    discount_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True, blank=True,
        help_text="Applicable for Percentage Discount. Discount percentage (e.g., 10.00 for 10%)."
    )
    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True, blank=True,
        help_text="Applicable for Fixed Amount Discount. Fixed discount amount (e.g., 50.00 for $50 off)."
    )
    buy_quantity = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="For 'Buy X Get Y Free' offers: quantity of products to buy."
    )
    get_quantity = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="For 'Buy X Get Y Free' offers: quantity of products to get free."
    )
    start_date = models.DateTimeField(
        default=timezone.now,
        help_text="Date and time when the offer becomes active."
    )
    end_date = models.DateTimeField(
        help_text="Date and time when the offer expires."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Overall status of the offer. Set to False to disable."
    )
    products = models.ManyToManyField(
        'Product', 
        related_name='offers',
        blank=True,
        help_text="Select products to apply this offer to."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Product Offer"
        verbose_name_plural = "Product Offers"
        ordering = ['-start_date']

    def __str__(self):
        return self.name

    @property
    def is_currently_active(self):
        """Checks if the offer is active based on dates and is_active flag."""
        now = timezone.now()
        return self.is_active and self.start_date <= now and self.end_date >= now

    def get_discounted_price(self, original_price):
        """Calculates the discounted price for a given original price based on PERCENTAGE/AMOUNT."""
        original_price = Decimal(original_price)
        if self.offer_type == 'PERCENTAGE' and self.discount_percentage:
            discount_value = original_price * (self.discount_percentage / Decimal(100))
            return max(Decimal('0.00'), original_price - discount_value)
        elif self.offer_type == 'AMOUNT' and self.discount_amount:
            return max(Decimal('0.00'), original_price - self.discount_amount)
        return original_price 

    def clean(self):
        if self.discount_percentage is not None and self.discount_amount is not None:
            raise ValidationError("An offer cannot have both a percentage and a fixed amount discount. Choose one.")
        if self.discount_percentage is None and self.discount_amount is None and self.offer_type not in ['BOGO1', 'BOGO2', 'FREESHIP']:
            raise ValidationError("An offer must have either a percentage or a fixed amount discount unless it's a BOGO or Free Shipping offer.")

        if self.discount_percentage is not None and (self.discount_percentage < 0 or self.discount_percentage > 100):
            raise ValidationError("Discount percentage must be between 0 and 100.")
        if self.discount_amount is not None and self.discount_amount < 0:
            raise ValidationError("Discount amount cannot be negative.")

        if self.start_date and self.end_date and self.start_date >= self.end_date:
            raise ValidationError("End date must be after start date.")

        if self.offer_type in ['BOGO1', 'BOGO2']:
            if not self.buy_quantity or not self.get_quantity:
                raise ValidationError(f"For '{self.get_offer_type_display()}' offer, 'Buy Quantity' and 'Get Quantity' must be specified.")
            if self.buy_quantity <= 0 or self.get_quantity <= 0:
                raise ValidationError("Buy quantity and Get quantity must be positive for BOGO offers.")
            if self.offer_type == 'BOGO1' and (self.buy_quantity != 1 or self.get_quantity != 1):
                pass 
            if self.offer_type == 'BOGO2' and (self.buy_quantity != 2 or self.get_quantity != 1):
                pass 
        else: 
            self.buy_quantity = None
            self.get_quantity = None

        if self.offer_type in ['BOGO1', 'BOGO2', 'FREESHIP']:
            self.discount_percentage = None
            self.discount_amount = None