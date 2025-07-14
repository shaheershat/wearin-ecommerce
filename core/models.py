# core/models.py
from django.db import models
from django.utils.text import slugify
from cloudinary.models import CloudinaryField
from django.utils import timezone
from django.contrib.auth import get_user_model
from decimal import Decimal
from datetime import timedelta # Import timedelta
from django.urls import reverse # Import reverse for get_absolute_url
import logging

logger = logging.getLogger(__name__) # Setup logger for models.py

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

User = get_user_model() # Define User here once

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
    code = models.CharField(max_length=20, unique=True)
    discount = models.DecimalField(max_digits=5, decimal_places=2)
    limit = models.PositiveIntegerField()
    min_purchase = models.DecimalField(max_digits=8, decimal_places=2)
    redeemable_price = models.DecimalField(max_digits=8, decimal_places=2)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField()
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.code

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
        return reverse('product_detail', args=[self.id]) # Make sure 'product_detail' exists in your urls.py

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

        # Only proceed if it was actually reserved by a user and not already sold
        if self.reserved_by_user and not self.is_sold:
            # Import tasks/emails locally to avoid circular import issues
            from core.emails import send_reservation_expired_email, send_product_available_email
            from core.models import NotificationSubscription, CartItem # Make sure CartItem is imported

            previous_reserved_user = self.reserved_by_user
            
            # 1. Clear reservation fields on the Product
            self.reserved_by_user = None
            self.reservation_expires_at = None
            self.save(update_fields=['reserved_by_user', 'reservation_expires_at']) # Save specific fields
            logger.info(f"Reservation fields cleared for product {self.name}.")

            # 2. Remove the product from the expired user's cart
            if previous_reserved_user:
                deleted_count, _ = CartItem.objects.filter(
                    user=previous_reserved_user,
                    product=self,
                    is_reserved=True # Only delete the specific reserved instance
                ).delete()
                
                if deleted_count > 0:
                    logger.info(f"Removed {deleted_count} cart item(s) for product {self.name} from {previous_reserved_user.username}'s cart after reservation expiry.")
                else:
                    logger.warning(f"No reserved cart item found for product {self.name} in {previous_reserved_user.username}'s cart after reservation expiry.")

                # 3. Send email to the user whose reservation expired
                if previous_reserved_user.email: # Ensure user has an email
                    send_reservation_expired_email.delay(
                        previous_reserved_user.email, # Pass recipient_email (string)
                        self.name,                    # Pass product_name (string)
                        self.id                       # Pass product_id (int)
                    )
                    logger.info(f"Queued reservation expired email for {previous_reserved_user.email}.")
            
            # 4. Notify subscribers for 'available' event
            if self.is_available_for_purchase: # This property should now be True
                subscribers = NotificationSubscription.objects.filter(
                    product=self,
                    event_type='available',
                    notified_at__isnull=True # Only notify those who haven't been notified yet
                )
                if subscribers.exists():
                    logger.info(f"Found {subscribers.count()} pending subscribers for product {self.name}.")
                    for sub in subscribers:
                        if sub.user and sub.user.email and sub.user != previous_reserved_user: # Avoid notifying the user whose reservation just expired again for availability
                            send_product_available_email.delay(
                                sub.user.email, # Pass recipient_email
                                self.name,      # Pass product_name
                                self.id         # Pass product_id
                            )
                            sub.notified_at = timezone.now() # Mark as notified
                            sub.save(update_fields=['notified_at']) # Save only the 'notified_at' field
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
        return True


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
        ('Shipped', 'Shipped'),
        ('Out of delivery', 'Out of delivery'),
        ('Delivered', 'Delivered'),
        ('Cancelled', 'Cancelled'),
        ('Returned', 'Returned'),
    ]
    PAYMENT_STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Success', 'Success'),
        ('Failed', 'Failed'),
    ]
    PAYMENT_METHOD_CHOICES = [
    ('COD', 'Cash on Delivery'),
    ('Razorpay', 'Razorpay'),
    ]
    RETURN_STATUS_CHOICES = [
    ('None', 'None'),
    ('Requested', 'Requested'),
    ('Approved', 'Approved'),
    ('Rejected', 'Rejected'),
    ]
    return_status = models.CharField(
    max_length=10,
    choices=RETURN_STATUS_CHOICES,
    default='None'
    )

    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default='COD')
    return_status = models.CharField(max_length=10, choices=RETURN_STATUS_CHOICES, default='None')

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    address = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True, blank=True)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='Pending')
    razorpay_order_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_signature = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self.pk:
            old_order = Order.objects.get(pk=self.pk)
            if old_order.status != 'Delivered' and self.status == 'Delivered':
                for item in self.items.all():
                    if item.product:
                        item.product.is_sold = True
                        item.product.reserved_by_user = None # Clear reservation on sale
                        item.product.reservation_expires_at = None # Clear reservation on sale
                        item.product.save()

                        # Also, remove from any carts if it's sold
                        CartItem.objects.filter(product=item.product).delete()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"Order #{self.id} by {self.user.username}"

class OrderItem(models.Model):
    RETURN_STATUS_CHOICES = [
        ('None', 'None'),
        ('Requested', 'Requested'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    quantity = models.PositiveIntegerField(default=1)
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    return_status = models.CharField(max_length=10, choices=RETURN_STATUS_CHOICES, default='None')

    def __str__(self):
        product_name = self.product.name if self.product else "N/A"
        return f"{product_name} (x{self.quantity}) in Order #{self.order.id}"

    def save(self, *args, **kwargs):
        if not self.pk and self.product:
            self.price_at_purchase = self.product.price
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