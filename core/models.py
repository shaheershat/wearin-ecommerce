# core/models.py
from django.db import models
from django.contrib.auth.models import User # Or your custom user model
from django.utils.text import slugify
from cloudinary.models import CloudinaryField
from django.utils import timezone
import random
import uuid
from django.contrib.auth import get_user_model


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
    # Added stock_quantity for better inventory management (optional but highly recommended)
    stock_quantity = models.PositiveIntegerField(default=1)

    def __str__(self):
        return self.name

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
        ('Delivered', 'Delivered'),
        ('Cancelled', 'Cancelled'), # Added Cancelled status
    ]
    PAYMENT_STATUS_CHOICES = [ # Added payment status choices
        ('Pending', 'Pending'),
        ('Success', 'Success'),
        ('Failed', 'Failed'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    address = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True, blank=True)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='Pending') # New field
    razorpay_order_id = models.CharField(max_length=100, blank=True, null=True) # New field
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True) # New field
    razorpay_signature = models.CharField(max_length=255, blank=True, null=True) # New field
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True) # Track last update

    def save(self, *args, **kwargs):
        # Update is_sold status when order is delivered (existing logic)
        if self.pk:
            old_order = Order.objects.get(pk=self.pk)
            # Only if status changes to Delivered and was not Delivered before
            if old_order.status != 'Delivered' and self.status == 'Delivered':
                for item in self.items.all():
                    if item.product:
                        # Assuming 'is_sold' means the product is no longer available
                        # If you have stock quantity, you'd decrement that instead
                        item.product.is_sold = True
                        item.product.save()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Order #{self.id} by {self.user.username}"

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    quantity = models.PositiveIntegerField(default=1) # Added quantity
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)

    def __str__(self):
        return f"{self.product.name if self.product else 'N/A'} (x{self.quantity}) in Order #{self.order.id}"

    def save(self, *args, **kwargs):
        if not self.pk and self.product:
            self.price_at_purchase = self.product.price
        super().save(*args, **kwargs)

class Cart(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='cart') # Added related_name
    created_at = models.DateTimeField(auto_now_add=True) # Track creation
    updated_at = models.DateTimeField(auto_now=True) # Track last update

    def __str__(self):
        return f"Cart of {self.user.username}"

    def get_total_price(self):
        return sum(item.product.price * item.quantity for item in self.items.all())

class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1) # IMPORTANT: Added quantity field

    class Meta:
        unique_together = ('cart', 'product') # Ensures a product is only once per cart

    def __str__(self):
        return f"{self.product.name} (x{self.quantity}) in {self.cart.user.username}'s cart"

class Wishlist(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='wishlist_items') # Added related_name
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    added_at = models.DateTimeField(auto_now_add=True) # Track when added

    class Meta:
        unique_together = ('user', 'product') # Ensures a product is only once per wishlist

    def __str__(self):
        return f"{self.product.name} in {self.user.username}'s wishlist"

# --- NEW MODEL FOR NEWSLETTER ---
class NewsletterSubscriber(models.Model):
    email = models.EmailField(unique=True)
    subscribed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.email

    class Meta:
        ordering = ['-subscribed_at']
        verbose_name = "Newsletter Subscriber"
        verbose_name_plural = "Newsletter Subscribers"