from django.db import models
from django.utils.text import slugify
from cloudinary.models import CloudinaryField
from django.utils import timezone
from django.contrib.auth import get_user_model
from decimal import Decimal
from datetime import timedelta # Import timedelta

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
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    size = models.CharField(max_length=10)
    is_sold = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    stock_quantity = models.PositiveIntegerField(default=1) # Keep this for general stock, but our focus is 1
    
    # Existing fields for reservation logic (Good, you already have these)
    is_reserved = models.BooleanField(default=False) 
    reserved_by_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reserved_products')
    reservation_expires_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name

    # --- New/Updated Properties for Reservation Logic ---
    @property
    def is_currently_reserved(self):
        """
        Checks if the product is actively reserved by any user (i.e., not expired).
        This replaces the simple `is_reserved` boolean with time-based logic.
        """
        # If is_sold is True, it's no longer 'reserved' in the active sense.
        if self.is_sold:
            return False
        
        # Check if a user has reserved it AND the reservation hasn't expired.
        return self.reserved_by_user is not None and \
               self.reservation_expires_at is not None and \
               self.reservation_expires_at > timezone.now()

    @property
    def is_available_for_purchase(self):
        """
        Checks if the product can be added to cart or bought by anyone.
        """
        return not self.is_sold and not self.is_currently_reserved

    def get_reservation_time_left_seconds(self):
        """
        Returns the time left in seconds for the current reservation,
        or 0 if expired/not reserved/sold.
        """
        if self.is_currently_reserved:
            return max(0, int((self.reservation_expires_at - timezone.now()).total_seconds()))
        return 0

    def release_reservation(self):
        """
        Releases the reservation on this product, making it available again.
        Triggers notifications if necessary. This should primarily be called by the Celery task.
        """
        # Only proceed if it was actually reserved by a user and not already sold
        if self.reserved_by_user and not self.is_sold:
            # Import tasks/emails locally to avoid circular import issues
            from core.emails import send_reservation_expired_email, send_product_available_email
            from core.models import NotificationSubscription, CartItem # Make sure CartItem is imported

            # 1. Send email to the user whose reservation expired
            if self.reserved_by_user.email:
                send_reservation_expired_email.delay(self.reserved_by_user.email, self.name, self.id)
                
            # Store the user who *was* reserving before clearing the fields
            previous_reserved_user = self.reserved_by_user

            # 2. Clear reservation fields
            self.is_reserved = False # Set this to False as well for consistency
            self.reserved_by_user = None
            self.reservation_expires_at = None
            self.save()

            # 3. Notify subscribers for 'available' event and clear their subscriptions for this product
            subscribers = NotificationSubscription.objects.filter(product=self, event_type='available')
            for sub in subscribers:
                if sub.user and sub.user.email:
                    send_product_available_email.delay(sub.user.email, self.name, self.id)
            subscribers.delete() # Clear subscriptions after notifying

            # 4. Remove the product from the expired user's cart
            if previous_reserved_user:
                CartItem.objects.filter(cart__user=previous_reserved_user, product=self).delete()
                # Consider adding logging here: logger.info(f"Removed product {self.name} from {previous_reserved_user.username}'s cart after reservation expiry.")

class NotificationSubscription(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='product_notifications')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='subscribers')
    # Use a specific event type if you plan to notify for different things (e.g., 'available', 'sold')
    event_type = models.CharField(max_length=50, default='available') # e.g., 'available', 'sold'
    notified_at = models.DateTimeField(null=True, blank=True) # To track when notification was sent
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # A user can subscribe for multiple event types for a product
        # Ensure 'available' is the primary event type for your current need
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
            # When an order changes to 'Delivered'
            if old_order.status != 'Delivered' and self.status == 'Delivered':
                for item in self.items.all():
                    if item.product:
                        item.product.is_sold = True
                        # Crucially, if sold, it should also clear any reservation if it somehow exists
                        # (though ideally, it wouldn't be reserved if being purchased)
                        item.product.is_reserved = False
                        item.product.reserved_by_user = None
                        item.product.reservation_expires_at = None
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

    # Updated get_total_price to be reservation-aware
    def get_total_price(self):
        total = Decimal('0.00')
        for item in self.items.all():
            # Only include items that are valid (not sold, and either available or reserved by THIS user)
            if item.product and (item.product.is_available_for_purchase or \
               (item.product.reserved_by_user == self.user and item.product.is_currently_reserved)):
                total += item.product.price * item.quantity
        return total

class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)

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
        amount = Decimal(amount) # Ensure amount is Decimal
        if self.balance >= amount:
            self.balance -= amount
            self.save()
            WalletTransaction.objects.create(wallet=self, transaction_type='debit', amount=amount, reason="Wallet debited")
            return True
        return False

    def __str__(self):
        return f"{self.user.username}'s Wallet"